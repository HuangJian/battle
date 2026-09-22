"""test_bulk_sched.py — bulk 单通道 + 控制面让路（plan/transfer-scheduling §2.2 / P0，2026-09-22）。

要钉住的三条不变量（现场依据：`docs/nn.progress.md` §104 —— 大 body 把控制环拖到分钟级、
两条大 body 并发让链路双侧静默）：

1. **单通道**：任意并发申请下 `inflight_bulk == 1`（P1/P2 一起申请也只有一条在途）。
2. **P1 不被抢断**：`post_result` 一旦开传就只能等它传完（POST 大 body 没有安全 Range，
   抢断 = 整份白传）；P2 预取相反——被高优/控制面挤到就该**丢半截**（幂等可重下）。
3. **让路预算有界**：单次让路 ≤ `PAUSE_BUDGET_SEC`；常量与两侧超时的**安全裕度断言**
   住在 `tests/test_pause_budget.py`（plan §5 点名的文件），这里只测「跑到预算就停」。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.bulk_sched import (
    BULK_P1_CRITICAL,
    BULK_P2_PREFETCH,
    BULK_YIELD_STEP_SEC,
    BulkPreemptError,
    BulkScheduler,
    control_path,
)


@pytest.fixture(autouse=True)
def _clean_bulk():
    """每个用例一份干净的调度器账（模块级 `_BULK` 跨用例共享）。"""
    W._BULK.reset()
    W._WIRE.clear()
    yield
    W._BULK.reset()
    W._WIRE.clear()


def _sched(**kw) -> BulkScheduler:
    """小步长调度器（用例里别真等 0.5s/步）。"""
    kw.setdefault("yield_step_sec", 0.01)
    kw.setdefault("wait_step_sec", 0.005)
    return BulkScheduler(**kw)


# --------------------------------------------------------------- 1. 单通道


def test_single_channel_under_concurrency():
    """N 个并发申请（P1/P2 混合）⇒ 临界区里**最多一个**持有者，且都跑完。"""
    s = _sched(wait_step_sec=0.001)
    inside = 0
    peak = 0
    lock = threading.Lock()
    done = threading.Event()
    left = 8

    def worker(i: int) -> None:
        nonlocal inside, peak, left
        prio = BULK_P1_CRITICAL if i % 2 == 0 else BULK_P2_PREFETCH
        with s.slot(prio, label=f"t{i}"):
            with lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.01)  # 模拟一段传输
            with lock:
                inside -= 1
                left -= 1
                if left == 0:
                    done.set()

    for i in range(8):
        threading.Thread(target=worker, args=(i,), daemon=True).start()
    assert done.wait(10), "并发申请没有全部完成（单通道死锁？）"
    assert peak == 1, f"同一时刻有 {peak} 条 bulk 在途（§2.2 要求恰好 1）"
    st = s.stats()
    assert st["inflight_bulk"] == 0
    assert st["queue_waits"] >= 6, "后到者必须排队（否则等于没有单通道）"


def test_p1_waits_for_p1_then_runs():
    """P1 等 P1：先到者传完，后到者才拿到通道（无抢断、无饿死）。"""
    s = _sched()
    order: list[str] = []
    release = threading.Event()

    def first() -> None:
        with s.slot(BULK_P1_CRITICAL, label="first"):
            order.append("first-enter")
            release.wait(5)
            order.append("first-exit")

    t = threading.Thread(target=first, daemon=True)
    t.start()
    while "first-enter" not in order:
        time.sleep(0.005)

    def second() -> None:
        with s.slot(BULK_P1_CRITICAL, label="second"):
            order.append("second-enter")

    t2 = threading.Thread(target=second, daemon=True)
    t2.start()
    time.sleep(0.1)
    assert order == ["first-enter"], "P1 被抢断了：第二条在第一条没传完时就进了通道"
    release.set()
    t.join(5)
    t2.join(5)
    assert order == ["first-enter", "first-exit", "second-enter"]


# --------------------------------------------------------------- 2. 抢占语义


def test_p2_preempted_by_p1():
    """P2 预取持有中来了 P1 ⇒ P2 在分片间隙（`pace`）看到并被中断。"""
    s = _sched()
    got: list[BaseException] = []
    holding = threading.Event()
    go = threading.Event()

    def prefetch() -> None:
        try:
            with s.slot(BULK_P2_PREFETCH, label="prefetch") as tok:
                holding.set()
                go.wait(5)
                s.pace(tok, BULK_P2_PREFETCH)  # 分片间隙
        except BaseException as e:
            got.append(e)

    t = threading.Thread(target=prefetch, daemon=True)
    t.start()
    assert holding.wait(5)

    def critical() -> None:
        with s.slot(BULK_P1_CRITICAL, label="critical"):
            time.sleep(0.05)

    t2 = threading.Thread(target=critical, daemon=True)
    t2.start()
    time.sleep(0.05)  # 让 P1 排上队（抢占标记已写）
    go.set()
    t.join(5)
    t2.join(5)
    assert got and isinstance(got[0], BulkPreemptError), f"P2 没被打断，拿到了 {got}"
    assert s.stats()["preempted"] >= 1


def test_p2_preempted_by_control():
    """控制面（P0）也算高优：预取让路时同样被挤走（否则 P0 会被预取拖到分钟级）。"""
    s = _sched()
    got: list[BaseException] = []
    holding = threading.Event()
    go = threading.Event()

    def prefetch() -> None:
        try:
            with s.slot(BULK_P2_PREFETCH, label="prefetch") as tok:
                holding.set()
                go.wait(5)
                s.pace(tok, BULK_P2_PREFETCH)
        except BaseException as e:
            got.append(e)

    t = threading.Thread(target=prefetch, daemon=True)
    t.start()
    assert holding.wait(5)
    with s.control(label="/jobs/x/status"):
        go.set()
        t.join(5)
    assert got and isinstance(got[0], BulkPreemptError)


def test_p1_not_preempted_by_control():
    """P1（在传的结果）遇控制面**不中断**：只暂停让路，绝不丢半截。"""
    s = _sched(yield_budget_sec=0.05, yield_step_sec=0.01)
    with s.slot(BULK_P1_CRITICAL, label="result") as tok:
        with s.control(label="/jobs/x/status"):
            s.pace(tok, BULK_P1_CRITICAL)  # 不得抛
        s.pace(tok, BULK_P1_CRITICAL)  # 控制面走了再调一次，也不得抛
    assert s.stats()["preempted"] == 0


# --------------------------------------------------------------- 3. 让路预算


def test_yield_stops_at_budget_even_if_control_stays():
    """控制面一直不走也不能停超预算：单次让路总时长 ≤ 预算（+一步）。"""
    budget, step = 0.12, 0.02
    s = _sched(yield_budget_sec=budget, yield_step_sec=step)
    with s.slot(BULK_P1_CRITICAL, label="result") as tok:
        stop = threading.Event()

        def p0() -> None:
            with s.control(label="/jobs/x/status"):
                stop.wait(2)  # 控制面「一直在途」

        t = threading.Thread(target=p0, daemon=True)
        t.start()
        time.sleep(0.05)
        t0 = time.time()
        spent = s.pause_if_needed(tok)
        elapsed = time.time() - t0
        stop.set()
        t.join(5)
    assert spent <= budget + step + 1e-6, f"让路超预算：{spent}"
    assert elapsed <= budget + step + 0.25, f"让路墙钟超预算：{elapsed}"
    assert s.stats()["yield_count"] >= 1


def test_no_yield_when_no_control():
    """没有控制面在途 ⇒ 让路点零成本（不能让每次分片都睡一格）。"""
    s = _sched()
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok:
        t0 = time.time()
        spent = s.pause_if_needed(tok)
    assert spent == 0.0
    assert time.time() - t0 < 0.05


# --------------------------------------------------------------- 4. 路径分流


def test_control_path_classification():
    """分流靠**路径**（一处实现）：控制面不排队，两处大 body（payload/result）才进队列。"""
    for p in (
        "/jobs/peek?n=3",
        "/jobs/priority",
        "/jobs/abc/claim",
        "/jobs/abc/start",
        "/jobs/abc/ready",
        "/jobs/abc/abandon",
        "/jobs/abc/status",
        "/jobs/abc/release",
        "/jobs/abc/fail",
        "/jobs/abc/heartbeat",
        "/admin/queue",
    ):
        assert control_path(p), f"{p} 应当被当作控制面"
    for p in ("/jobs/abc/payload", "/jobs/abc/result", "/jobs/abc/code", "/jobs/abc/ts_code"):
        assert not control_path(p), f"{p} 是大 body，必须进 bulk 队列"


# --------------------------------------------------------------- 5. 观测面


def test_wire_line_reports_scheduler_accounting():
    """每 job 的传输账要能读出排队/让路/控制面往返（DoD：观测可从日志读出）。"""
    W._wire_bucket("j" * 16)
    W._wire_add("j" * 16, "payload", 1024 * 1024, 1.0)
    lines: list[str] = []
    W._wire_flush("j" * 16, lines.append)
    assert len(lines) == 1
    assert "payload=" in lines[0]
    assert "wait=" in lines[0] and "yield=" in lines[0] and "p0_p95=" in lines[0]


def test_stats_has_dod_fields():
    """DoD 点名的三个读数必须在 `stats()` 里（P0.5 基线也靠它）。"""
    st = _sched().stats()
    for k in ("inflight_bulk", "queue_wait_sec", "yield_count", "p0_rt_ms_p95"):
        assert k in st, f"stats() 缺 {k}"
