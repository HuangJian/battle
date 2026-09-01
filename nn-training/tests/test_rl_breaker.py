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
