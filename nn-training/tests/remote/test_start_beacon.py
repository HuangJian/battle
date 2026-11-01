"""W2（`plan/ppo-worker-wallclock.plan.md` §4.4）：`/jobs/{id}/start` 的线程化形态。

钉住四件事：

1. **happy path 零语义损失**：`settle()` 成功返回，且 `computing_at` 的落地**早于**
   `job_ready()`（hub 侧的掉队阈值只认这一个时基）；
2. **超时不 hang**：线程卡住时 `settle(timeout)` 有界返回 False（由调用方先 ready
   再同步补发，绝不阻塞 PPO）；
3. **没发过 ≠ 失败**：备份副本（`on_ppo_start=None`）走 `settle()` 不该报失败；
4. **往返耗时留读数**：`ms` 是 `ppo_start_ms` 的源（慢路径可观测）。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_round as round_mod


def test_settle_happens_before_ready() -> None:
    """① 顺序：settle 返回时 start 已落地，调用方随后才报 ready。"""
    order: list[str] = []

    def _start() -> None:
        # sleep-ok: 夹具模拟的工作量：假装一次控制面往返（等的是 join，不是这个时长）
        time.sleep(0.01)
        order.append("start")

    beacon = round_mod._StartBeacon(_start)
    beacon.start()
    assert beacon.settle() is True
    order.append("ready")
    assert order == ["start", "ready"]
    assert beacon.ms >= 10.0, f"往返耗时没记到：{beacon.ms}"


def test_timeout_is_bounded_and_reported() -> None:
    """② 线程卡住 ⇒ settle 有界返回 False（不把 job 卡在 15s 的 HTTP timeout 上）。

    2026-11-01：旧写法是一条墙钟上界 `time.time() - t0 < 2.0`（settle 预算 50ms，2s 当余量），
    在门禁 8 worker 下假红过一次（实测 2.36s，单跑 / 16 个忙循环加压都只要 0.06s —— 那个
    2.36s 是**进程被调度器饿住**，不是 settle 慢）。墙钟上界在这里量的是操作系统的调度，
    而「有没有把预算交给 join」本来能确定性检验 ⇒ 拆成两条不靠墙钟的判据：

    ① 假线程记账：`settle(0.05)` 必须把 **0.05** 传给 `join`（硬编码成缺省 5s 就红）；
    ② 真线程：返回 False 时工作线程**仍在跑** ⇒ 返回的是「超时」，不是「等完了」。
    ③ 「会不会永久 hang」由 pytest 的 per-test 上限（60s）兜住 —— 不需要再拿墙钟钉。
    """
    gate = threading.Event()

    def _wait() -> None:
        gate.wait()

    class _Recorder:
        """只记账的假线程（不碰墙钟、不受调度器影响）。"""

        waited: float | None = None

        def join(self, timeout: float | None = None) -> None:
            self.waited = timeout

        def is_alive(self) -> bool:
            return True

    recorder = _Recorder()
    stub = round_mod._StartBeacon(_wait)
    stub._thread = cast(threading.Thread, recorder)
    assert stub.settle(0.05) is False
    assert recorder.waited == 0.05, f"预算没传到 join：{recorder.waited}"

    beacon = round_mod._StartBeacon(_wait)
    beacon.start()
    try:
        assert beacon.settle(0.05) is False
        assert beacon._thread is not None and beacon._thread.is_alive(), (
            "settle 返回时工作线程已结束 —— 它等的是「完成」，不是「预算」"
        )
    finally:
        gate.set()
        beacon.settle(1.0)


def test_thread_exception_is_a_failure_not_a_crash() -> None:
    """③ 线程报错只让 settle 返回 False（job_started 自己吞异常，这里防未来改实现）。"""

    def _boom() -> None:
        raise RuntimeError("boom")

    beacon = round_mod._StartBeacon(_boom)
    beacon.start()
    assert beacon.settle(1.0) is False


def test_without_start_it_is_not_a_failure() -> None:
    """③ 备份副本没有 start 可发 ⇒ 不算失败。"""
    assert round_mod._StartBeacon(lambda: None).settle(0.01) is True


def test_beacon_budget_is_shorter_than_the_http_timeout() -> None:
    """**契约**：join 预算与线程 HTTP timeout 都必须远小于 `job_started` 的 15s 缺省。

    否则失败路径会把 `job_ready()` 压后 15s —— 正好放大它要防的「备份副本重算一整轮」。
    """
    from remote.job_lifecycle import job_started

    assert round_mod.START_TIMEOUT_SEC < 15.0
    assert round_mod.START_JOIN_SEC <= 5.0
    # 尺子自证：`job_started` 自己的 timeout 缺省还是 15s（keyword-only）
    assert (job_started.__kwdefaults__ or {}).get("timeout") == 15.0
