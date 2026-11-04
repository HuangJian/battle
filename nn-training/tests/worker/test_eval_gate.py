"""进程级 eval 闸（`worker/eval_gate.py`）的行为用例（2026-11-04 任务级仲裁）。

口径（用户 2026-11-04）：「rollout 一起来，在飞 eval 局不动，只停止派发新的 eval 局，
直到 rollout 收尾；rollout 之间，多轮 eval 平等共享节点——不再排队」。

闸本体因此只剩两件事：
  * `rollout()` / `begin_rollout()` / `end_rollout()`：引用计数的冻结/解冻（转给
    `EvalArbiter.freeze/thaw`——派发暂停 + 窗口钟停走）；
  * `submit(job)`：起后台线程**立即**跑整轮 `job()`（旧 FIFO 槽位已退役）。

旧用例（单飞/FIFO/插队收手/续跑队头）随 `Preempt` 一起删除——那些时序判据已由
任务级仲裁接管，行为面在 `tests/trainer/test_eval_dispatch_resilience.py`
（多轮均分 / 冻结不撤在飞局）。

冻结是**进程级**的：每个用例都必须在 finally 里解冻，否则后面所有用例的仲裁器
都停在冻结态（挂死而不是红）。
"""

from __future__ import annotations

import threading
import time

from worker.eval_arbiter import eval_arbiter
from worker.eval_gate import EvalGate, eval_gate


def _wait(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词，兜底只挡挂起）
        time.sleep(0.005)
    return bool(pred())


def test_submit_starts_job_immediately_without_queue() -> None:
    """提交即开跑（无 FIFO 槽位）：`submit` 返回的线程 join 到整轮真正收工。"""
    gate = EvalGate()
    started = threading.Event()
    release = threading.Event()

    def job() -> None:
        started.set()
        release.wait(5)

    t = gate.submit(job)
    assert started.wait(5), "submit 没有立即开跑（旧 FIFO 行为已退役）"
    st = gate.status()
    assert st == {
        "queued": 0,
        "running": True,
        "active": 1,
        "rollouts": 0,
        "completed": 0,
    }, st

    release.set()
    t.join(5)
    assert not t.is_alive()
    assert gate.status()["completed"] == 1
    assert gate.status()["active"] == 0
    assert gate.status()["running"] is False


def test_submit_does_not_cap_concurrency() -> None:
    """闸不再限并发：两个整轮同时开跑（共享节点的仲裁在任务级进行）。"""
    gate = EvalGate()
    started = [threading.Event(), threading.Event()]
    release = threading.Event()

    def _job(i: int):
        def job() -> None:
            started[i].set()
            release.wait(5)

        return job

    t1 = gate.submit(_job(0))
    t2 = gate.submit(_job(1))
    try:
        assert started[0].wait(5) and started[1].wait(5), "两轮没有并发开跑"
        assert gate.status()["active"] == 2
    finally:
        release.set()
    t1.join(5)
    t2.join(5)
    assert _wait(lambda: gate.status()["completed"] == 2)


def test_rollout_refcount_freezes_and_thaws_the_arbiter() -> None:
    """嵌套 rollout：进入即冻结（新 eval 不派、窗口钟停走），计数归零才解冻。"""
    gate = EvalGate()
    arb = eval_arbiter()
    was_frozen = arb.frozen()
    try:
        with gate.rollout():
            assert gate.status()["rollouts"] == 1
            assert arb.frozen() is True
            with gate.rollout():
                assert gate.status()["rollouts"] == 2
            assert arb.frozen() is True, "内层退出不得提前解冻"
            assert gate.status()["rollouts"] == 1
        assert gate.status()["rollouts"] == 0
        assert arb.frozen() is was_frozen, "外层退出没有恢复冻结态"
    finally:
        while gate.status()["rollouts"] > 0:
            gate.end_rollout()


def test_rollout_scope_restores_count_on_exception() -> None:
    """`with gate.rollout():` 内抛错也要解冻（否则整进程 eval 永久停派）。"""
    gate = EvalGate()
    try:
        with gate.rollout():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert gate.status()["rollouts"] == 0
    assert eval_arbiter().frozen() is False


def test_job_exception_does_not_wedge_the_gate() -> None:
    """整轮异常不得留下 `running=True`（否则 `loop_eval` 的 gate_idle 判据永久为假）。"""
    gate = EvalGate()

    def boom() -> None:
        raise RuntimeError("boom")

    gate.submit(boom).join(5)
    assert _wait(lambda: gate.status()["active"] == 0)
    assert gate.status()["running"] is False
    assert gate.status()["completed"] == 1

    done = threading.Event()
    gate.submit(done.set).join(5)
    assert done.is_set(), "异常之后的新整轮没有开跑"
    assert _wait(lambda: gate.status()["completed"] == 2)


def test_gate_is_a_process_singleton() -> None:
    """生产口径：一个 trainer 进程只有一张闸（同进程所有课程共用，不做跨进程同步）。"""
    assert eval_gate() is eval_gate()
    assert isinstance(eval_gate(), EvalGate)
