"""进程级 eval 排队闸（`worker/eval_gate.py`）的行为用例（2026-11-03 用户口径）。

口径原文：「前一个 eval 未完成，不启动后面的 eval」「不管多少课程，rollout 和 eval 都是
排队」「所有 rollout 优先级都高于 eval … eval 进行过程中，可以被新到的 rollout 插队」。

为什么用**行为**而不是文本断言：闸的价值全在状态机的时序上（单飞 / FIFO / 插队 /
续跑留队头），文本断言只能证明「写了这些词」。本文件用 `Event` + 顺序账本钉时序；
每个等待都有 5s 上界（失败是红，不是挂死），整文件远低于 per-test 60s 护栏。
"""

from __future__ import annotations

import threading
import time

from worker.eval_gate import WAIT_TICK_SEC, EvalGate, Preempt, eval_gate


def _wait(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词，兜底只挡挂起）
        time.sleep(0.005)
    return bool(pred())


def test_submit_runs_jobs_single_flight_in_fifo_order() -> None:
    """单飞 + FIFO：同一时刻只有一票在跑；先到先跑，后到等前一张票真正结束。"""
    gate = EvalGate()
    order: list[str] = []
    active = [0]
    peak = [0]
    first_started = threading.Event()
    release_first = threading.Event()

    def job1(_token) -> bool:
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        order.append("start1")
        first_started.set()
        release_first.wait(5)
        order.append("end1")
        active[0] -= 1
        return False

    def job2(_token) -> bool:
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        order.append("start2")
        active[0] -= 1
        return False

    t1 = gate.submit(job1)
    assert first_started.wait(5), "第一票没起跑"
    t2 = gate.submit(job2)
    # 给第二票至少一个 WAIT_TICK 的机会去偷跑（它必须吃闭门羹）
    # sleep-ok: 轮询步长（等的是「后到票没被启动」这个否定谓词，兜底只挡挂起）
    time.sleep(WAIT_TICK_SEC + 0.1)
    assert order == ["start1"], f"前一张票未结束就启动了后一张：{order}"
    assert gate.status()["running"] is True

    release_first.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()
    assert order == ["start1", "end1", "start2"]
    assert peak[0] == 1, f"出现并发执行（峰值 {peak[0]}）"
    assert gate.status() == {"queued": 0, "running": False, "rollouts": 0, "completed": 2}


def test_rollout_defers_queued_eval_until_gap() -> None:
    """rollout 期间**不启动**排队中的 eval；rollout 结束的这个间隙立刻开跑。"""
    gate = EvalGate()
    ran = threading.Event()

    def job(_token) -> bool:
        ran.set()
        return False

    gate.begin_rollout()
    try:
        gate.submit(job)
        # sleep-ok: 轮询步长（等的是「rollout 期间没启动」这个否定谓词，兜底只挡挂起）
        time.sleep(WAIT_TICK_SEC + 0.1)
        assert not ran.is_set(), "rollout 进行中启动了 eval"
        assert gate.status()["queued"] == 1
    finally:
        gate.end_rollout()
    assert _wait(ran.is_set), "间隙到了 eval 仍未开跑"


def test_new_rollout_preempts_running_eval_and_ticket_resumes_at_head() -> None:
    """插队 + 续跑：rollout 一到，在跑的 eval 收手（返回真值）；票留在**队头**，
    下一个间隙先续完它，后提交的票才轮到（后一轮不得趁插队超车）。"""
    gate = EvalGate()
    order: list[str] = []
    calls: list[Preempt] = []
    first_started = threading.Event()

    def job1(token) -> bool:
        calls.append(token)
        if len(calls) == 1:
            order.append("job1#1")
            first_started.set()
            assert token.wait(5), "没等到插队令牌"
            order.append("job1#1-preempted")
            return True
        order.append("job1#2")
        return False

    t1 = gate.submit(job1)
    assert first_started.wait(5)

    def job2(_token) -> bool:
        order.append("job2")
        return False

    t2 = gate.submit(job2)
    # sleep-ok: 轮询步长（等的是「后到票没超车」这个否定谓词，兜底只挡挂起）
    time.sleep(WAIT_TICK_SEC + 0.1)
    assert order == ["job1#1"], f"后到票超车：{order}"

    gate.begin_rollout()
    assert _wait(lambda: order[-1] == "job1#1-preempted"), "在跑的 eval 没有被插队收手"
    # 插队期间后到票也不启动
    # sleep-ok: 轮询步长（等的是「插队期间后到票没启动」这个否定谓词，兜底只挡挂起）
    time.sleep(WAIT_TICK_SEC + 0.1)
    assert order == ["job1#1", "job1#1-preempted"]
    gate.end_rollout()

    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()
    assert order == ["job1#1", "job1#1-preempted", "job1#2", "job2"], order
    assert len(calls) == 2, "续跑必须拿到新令牌（旧令牌已置位）"
    assert calls[0] is not calls[1]
    assert calls[1].is_set() is False


def test_preempt_hooks_fire_once_and_late_hook_fires_immediately() -> None:
    """让位钩子：fire 前注册的随 fire 跑；fire 后注册的**立刻补跑**；重复 fire 不重复叫。"""
    token = Preempt()
    hits: list[str] = []
    token.add_hook(lambda: hits.append("a"))
    assert hits == []
    token.fire()
    assert hits == ["a"]
    token.add_hook(lambda: hits.append("b"))
    assert hits == ["a", "b"], "迟到钩子没有立刻补 fire"
    token.fire()  # 幂等：重叠 rollout 的第二次置位
    assert hits == ["a", "b"]


def test_hook_exception_does_not_block_other_hooks() -> None:
    """一个钩子抛错不得挡住其它钩子/置位（让位是尽力而为，不能反噬收手）。"""
    token = Preempt()
    hits: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    token.add_hook(boom)
    token.add_hook(lambda: hits.append("ok"))
    token.fire()
    assert token.is_set() is True
    assert hits == ["ok"]


def test_rollout_scope_context_manager_sets_and_clears_activity() -> None:
    """`with gate.rollout():` 的进出 = 计数增减（细粒度/组合两条驱动都只经它标记）。"""
    gate = EvalGate()
    with gate.rollout():
        assert gate.status()["rollouts"] == 1
        with gate.rollout():
            assert gate.status()["rollouts"] == 2
        assert gate.status()["rollouts"] == 1
    assert gate.status()["rollouts"] == 0


def test_job_exception_does_not_wedge_the_queue() -> None:
    """任务抛异常也要出队并叫醒队列（派发器已顶层吞异常；这是闸自己的防御性下限）。"""
    gate = EvalGate()

    def boom(_token) -> bool:
        raise RuntimeError("boom")

    t = gate.submit(boom)
    t.join(5)
    assert not t.is_alive()
    done = threading.Event()

    def job(_token) -> bool:
        done.set()
        return False

    gate.submit(job)
    assert done.wait(5), "前一票异常后队列卡死"
    assert _wait(lambda: gate.status()["completed"] == 2)


def test_gate_is_a_process_singleton() -> None:
    """生产口径：一个 trainer 进程只有一张闸（同进程所有课程共用，不做跨进程同步）。"""
    assert eval_gate() is eval_gate()
    assert isinstance(eval_gate(), EvalGate)
