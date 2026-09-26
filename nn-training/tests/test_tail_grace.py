"""tail-join grace 默认值回归（2026-09-19：all_settled 后默认 0，不等竞速输家）。"""

from __future__ import annotations

import threading
import time

from rl.dispatch import resolve_tail_join_sec


def test_tail_grace_default_zero_when_settled() -> None:
    # 计划对局已齐：竞速迟到副本注定被 dedup 丢弃 → 默认 0，不付 30s×N 波
    assert resolve_tail_join_sec({}, True, False) == 0.0
    assert resolve_tail_join_sec({}, True, True) == 0.0
    # policy 可覆写（e2e test_it_tail_join_grace_v317 用 2s 验有界）
    assert resolve_tail_join_sec({"tailGraceJoinSec": 2}, True, False) == 2.0
    assert resolve_tail_join_sec({"tailGraceJoinSec": 30}, True, False) == 30.0


def test_tail_grace_deadline_short_bound() -> None:
    # 窗口到期未齐：默认 5s 尝试补结算，绝不再用 window+taskTimeout（旧 2700s 洞）
    assert resolve_tail_join_sec({}, False, False) == 5.0
    assert resolve_tail_join_sec({"tailGraceJoinSecDeadline": 1}, False, False) == 1.0
    # 旧键不得把 30s 带回 deadline 路径
    assert resolve_tail_join_sec({"tailGraceJoinSec": 30}, False, False) == 5.0


def test_race_loser_wait_has_no_data_value() -> None:
    """语义钉子：all_settled 后 in-flight 只可能是输家——等待不产生新 (stage,seed)。"""
    settled = {(2000, 1), (2000, 2)}
    plan = {(2000, 1), (2000, 2)}
    inflight_race_copy = (2000, 1)
    assert plan <= settled
    assert inflight_race_copy in settled
    assert resolve_tail_join_sec({}, True, False) == 0.0


def test_join_timeout_zero_returns_immediately() -> None:
    """timeout=0 的 join 不阻塞（daemon 卡死线程不拖整轮）。"""
    done = threading.Event()

    def _stuck() -> None:
        done.wait(30)

    t = threading.Thread(target=_stuck, daemon=True)
    t.start()
    t0 = time.monotonic()
    t.join(timeout=0.0)
    dt = time.monotonic() - t0
    # timing-ok: 契约上界（join(0) 应立即返回，上界即契约）
    assert dt < 0.5, f"join(0) 应立即返回，实际 {dt:.3f}s"
    assert t.is_alive()
    done.set()
