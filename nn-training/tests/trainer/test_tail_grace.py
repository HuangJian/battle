"""tail-join grace 默认值回归（2026-09-19：all_settled 后默认 0，不等竞速输家）。"""

from __future__ import annotations

import threading

from trainer.dispatch import resolve_tail_join_sec


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
    """`timeout=0` 的 join 不阻塞（daemon 卡死线程不拖整轮）。

    判据是**线程还活着**，不是耗时（2026-11-15）：线程会卡在 `done.wait(60)` 上，如果
    `join(0)` 真的等了，它会等到线程退出 —— 那时 `is_alive()` 为 False。而正确实现里
    `is_alive()` 恒为 True，与机器多忙无关。（`elapsed < 0.5s` 在并行门禁满载时会红。）
    """
    done = threading.Event()

    def _stuck() -> None:
        done.wait(60)

    t = threading.Thread(target=_stuck, daemon=True)
    t.start()
    try:
        t.join(timeout=0.0)
        assert t.is_alive(), "join(0) 等到了线程结束 ⇒ 它没有走 timeout=0 那条路"
    finally:
        done.set()
