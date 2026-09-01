"""rl/stream.py — wave-params and chunking pure-logic tests."""
from __future__ import annotations

from rl.stream import wave_params


def test_wave_params_normal() -> None:
    thr, cap = wave_params(cum_kl=0.0, kl_cap=0.1, wave_games=12, wave_cap=24)
    assert thr == 12
    assert cap == 24


def test_wave_params_soft_downshift_above_70pct() -> None:
    thr, cap = wave_params(cum_kl=0.08, kl_cap=0.1, wave_games=12, wave_cap=24)
    # 0.08 > 0.7 * 0.1 = 0.07 -> downshift
    assert thr < 12
    assert cap < 24


def test_wave_params_soft_downshift_at_boundary() -> None:
    # Floating-point: 0.7 * 0.1 = 0.069999... in IEEE-754, so 0.07 > 0.069999...
    # and the rule `cum_kl > 0.7 * kl_cap` trips into downshift.
    thr, cap = wave_params(cum_kl=0.07, kl_cap=0.1, wave_games=12, wave_cap=24)
    assert thr == 4
    assert cap == 8


def test_wave_params_remaining_caps_threshold() -> None:
    # Resumable iter: only 2 games remaining; threshold should clamp to 2.
    thr, cap = wave_params(cum_kl=0.0, kl_cap=0.1, wave_games=12, wave_cap=24, remaining=2)
    assert thr == 2
    assert cap >= thr


def test_wave_params_remaining_zero() -> None:
    thr, cap = wave_params(cum_kl=0.0, kl_cap=0.1, wave_games=12, wave_cap=24, remaining=0)
    # thr should be at least 1 even with remaining=0.
    assert thr >= 1
    assert cap >= thr
