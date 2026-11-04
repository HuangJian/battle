"""eval 闸（`worker/eval_gate.py`）的行为用例（2026-11-04 任务级仲裁）。

口径（用户 2026-11-04）：「rollout 一起来，在飞 eval 局不动，只停止派发新的 eval 局，
直到 rollout 收尾；rollout 之间，多轮 eval 平等共享节点——不再排队」。

闸本体因此只剩两件事：
  * `rollout(course)` / `begin_rollout(course)` / `end_rollout(course)`：引用计数的
    冻结/解冻（转给 `EvalArbiter.freeze/thaw`——派发暂停 + 窗口钟停走）；
  * `submit(job)`：起后台线程**立即**跑整轮 `job()`（旧 FIFO 槽位已退役）。

2026-11-04（§113）：冻结默认按**课程**作用域。旧实现是进程级——同一 Supervisor 进程里
A 课采集会把 B 课的 eval 一起冻住（现场：z7-maze-v2/kill 两课各 91 轮采集交替占满墙钟，
进程级冻结覆盖 75%，eval 只剩另一门课不采集的 25%）。`course=None` 仍保留进程级冻结
（手工 `evalA` / 旧调用方 / 没有课程身份的路径）。

冻结是**进程级单例**上的状态：每个用例都必须在 finally 里解冻，否则后面所有用例的
仲裁器都停在冻结态（挂死而不是红）。
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


def test_course_rollout_freezes_only_that_course() -> None:
    """A 课采集只冻结 A 课的 eval：B 课的轮不受影响（§111 口径 4 的跨课并行）。

    旧实现把冻结做成进程级 ⇒ 同一 Supervisor 里两门课交替采集时，任何一门采集都
    全停 eval（现场实测覆盖 75% 墙钟）。按课程冻结后，B 课在 A 课采集期间照常派局。
    """
    gate = EvalGate()
    arb = eval_arbiter()
    try:
        with gate.rollout("course-A"):
            assert gate.status()["rollouts"] == 1
            assert arb.frozen_for("course-A") is True
            assert arb.frozen_for("course-B") is False, "A 课的 rollout 冻住了 B 课的 eval"
            assert arb.frozen() is False, "按课冻结不得升级成进程级冻结"
        assert arb.frozen_for("course-A") is False
        assert gate.status()["rollouts"] == 0
    finally:
        gate.end_rollout("course-A")


def test_course_rollout_refcount_is_per_course() -> None:
    """同课嵌套各自计数；不同课的引用计数互不串门。"""
    gate = EvalGate()
    arb = eval_arbiter()
    try:
        with gate.rollout("A"):
            with gate.rollout("A"):
                assert gate.status()["rollouts"] == 2
            assert arb.frozen_for("A") is True, "内层退出不得提前解冻"
            with gate.rollout("B"):
                assert arb.frozen_for("B") is True
            assert arb.frozen_for("B") is False, "B 的作用域退出带走了 A"
            assert arb.frozen_for("A") is True
        assert arb.frozen_for("A") is False
        assert gate.status()["rollouts"] == 0
    finally:
        gate.end_rollout("A")
        gate.end_rollout("A")
        gate.end_rollout("B")


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
