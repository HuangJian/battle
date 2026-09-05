"""rl/breaker.py — F4 circuit-breaker pure-logic tests."""
from __future__ import annotations

from rl.breaker import breaker_update


def test_breaker_healthy_does_not_trip() -> None:
    kl_s, ent_s, trip = breaker_update(0, 0, kl=0.05, entropy=1.5, win_rate=0.3)
    assert trip is None


def test_breaker_kl_trips_after_consecutive() -> None:
    # KL >= 0.15 must persist 3 consecutive iterations before tripping.
    kl_s, ent_s, trip = breaker_update(0, 0, kl=0.20, entropy=1.5, win_rate=0.3)
    assert trip is None
    kl_s, ent_s, trip = breaker_update(kl_s, 0, kl=0.20, entropy=1.5, win_rate=0.3)
    assert trip is None
    kl_s, ent_s, trip = breaker_update(kl_s, 0, kl=0.20, entropy=1.5, win_rate=0.3)
    assert trip is not None
    assert "kl>=" in trip


def test_breaker_kl_streak_resets() -> None:
    kl_s, _, _ = breaker_update(2, 0, kl=0.05, entropy=1.5, win_rate=0.3)
    assert kl_s == 0  # below threshold resets


def test_breaker_entropy_trips_low_winrate() -> None:
    # entropy <= 0.60 for 8 consecutive iters, win_rate < 0.50 -> trip.
    kl_s, ent_s, trip = 0, 0, None
    for _ in range(7):
        kl_s, ent_s, trip = breaker_update(kl_s, ent_s, kl=0.05, entropy=0.55, win_rate=0.3)
        assert trip is None
    kl_s, ent_s, trip = breaker_update(kl_s, ent_s, kl=0.05, entropy=0.55, win_rate=0.3)
    assert trip is not None
    assert "entropy<=" in trip


def test_breaker_entropy_high_winrate_no_trip() -> None:
    # entropy collapse with high winrate should NOT trip (winrate guard).
    kl_s, ent_s, trip = 0, 0, None
    for _ in range(10):
        kl_s, ent_s, trip = breaker_update(kl_s, ent_s, kl=0.05, entropy=0.50, win_rate=0.7)
    assert trip is None


# ── 相对崩塌语义（DECISIONS §339，p4-onset it8 误熔断修正）──────────────────────


def test_breaker_warm_start_low_entropy_no_streak() -> None:
    """BC 热启动：熵天生在 0.36 带且缓升 —— 不是崩塌，不得计连击/熔断。

    复刻 p4-onset it1..it8 实测（0.254 → 0.365，winRate 5-9%）；旧绝对规则会在
    第 8 轮误停，新规则必须有历史峰值后按相对跌幅判定。
    """
    seq = [0.254, 0.361, 0.360, 0.371, 0.360, 0.368, 0.377, 0.365]
    kl_s, ent_s, trip = 0, 0, None
    peak: float | None = None
    for ent in seq:
        kl_s, ent_s, trip = breaker_update(
            kl_s, ent_s, kl=0.015, entropy=ent, win_rate=0.08, ent_peak=peak
        )
        peak = ent if peak is None else max(peak, ent)
        assert trip is None
    # 首轮 peak=None 走绝对判定记 1 次（0.254<=0.60 且 winRate<0.5），此后相对判定
    # 一路不计数 —— 连击停在 1，永远不会累到 8。
    assert ent_s <= 1


def test_breaker_relative_collapse_still_trips() -> None:
    """真崩塌：从峰值 0.90 一路跌到 0.40（跌幅 ≫0.10）→ 连续 8 轮后照旧熔断。"""
    kl_s, ent_s, trip = 0, 0, None
    peak: float | None = 0.90
    for _ in range(7):
        kl_s, ent_s, trip = breaker_update(
            kl_s, ent_s, kl=0.05, entropy=0.40, win_rate=0.2, ent_peak=peak
        )
        assert trip is None
    kl_s, ent_s, trip = breaker_update(
        kl_s, ent_s, kl=0.05, entropy=0.40, win_rate=0.2, ent_peak=peak
    )
    assert trip is not None
    assert "entropy<=" in trip


def test_breaker_course_ent_break_tightens_guard() -> None:
    """课程下调 ent_break=0.25：跌破 0.25（相对峰值跌幅 ≥0.10）才计连击。"""
    # 0.30 高于课程阈值 → 不计连击
    _, ent_s, trip = breaker_update(
        0, 0, kl=0.01, entropy=0.30, win_rate=0.1, ent_break=0.25, ent_peak=0.377
    )
    assert (ent_s, trip) == (0, None)
    # 崩到 0.20（峰值 0.377，跌幅 0.177）→ 计连击
    _, ent_s, trip = breaker_update(
        0, 0, kl=0.01, entropy=0.20, win_rate=0.1, ent_break=0.25, ent_peak=0.377
    )
    assert ent_s == 1
    assert trip is None


def test_breaker_entropy_consec_configurable() -> None:
    """ent_break_consec 可配：连击阈值从 8 降到 2 后第 2 轮即熔断。"""
    kl_s, ent_s, trip = breaker_update(
        0, 0, kl=0.05, entropy=0.20, win_rate=0.2, ent_peak=0.9, ent_consec=2
    )
    assert trip is None
    _, _, trip = breaker_update(
        kl_s, ent_s, kl=0.05, entropy=0.20, win_rate=0.2, ent_peak=0.9, ent_consec=2
    )
    assert trip is not None
