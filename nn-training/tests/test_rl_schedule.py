"""test_rl_schedule —— ppo_schedule 查表（M1c，plan §8 / §11 DoD）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.schedule import ScheduleError, resolve_ppo_schedule

ENTRIES = [
    {"until_iter": 10, "lr": 1e-4, "epochs": 2, "mb": 256, "kl_coef": 1.0},
    {"until_iter": 30, "lr": 3e-4, "epochs": 4, "mb": 512, "kl_coef": 0.3},
    {"lr": 3e-4, "epochs": 4, "mb": 512, "kl_coef": 0.0},
]


def test_segment_bounds_absolute() -> None:
    """按**绝对** iter 查表：it ∈ (prev_until, until]。"""
    assert resolve_ppo_schedule(ENTRIES, 1) == {
        "lr": 1e-4,
        "epochs": 2,
        "mb": 256,
        "kl_coef": 1.0,
    }
    assert resolve_ppo_schedule(ENTRIES, 10)["lr"] == 1e-4
    assert resolve_ppo_schedule(ENTRIES, 11)["lr"] == 3e-4
    assert resolve_ppo_schedule(ENTRIES, 30)["lr"] == 3e-4
    assert resolve_ppo_schedule(ENTRIES, 30)["kl_coef"] == 0.3
    # 末段兜底：31+ 永远命中
    assert resolve_ppo_schedule(ENTRIES, 31)["kl_coef"] == 0.0
    assert resolve_ppo_schedule(ENTRIES, 1000)["kl_coef"] == 0.0


def test_empty_entries_returns_empty() -> None:
    """缺省（无 schedule）→ 空 dict → 固定值路径（不触碰 engine 模块常量）。"""
    assert resolve_ppo_schedule([], 5) == {}


def test_partial_segments() -> None:
    """段可只覆盖部分键；未覆盖键由 args 固定值接管。"""
    assert resolve_ppo_schedule([{"until_iter": 8, "lr": 1e-4}], 3) == {"lr": 1e-4}
    assert resolve_ppo_schedule([{"until_iter": 8, "lr": 1e-4}], 9) == {}


def test_non_ascending_rejected() -> None:
    with pytest.raises(ScheduleError, match="未严格递增"):
        resolve_ppo_schedule([{"until_iter": 10, "lr": 1e-4}, {"until_iter": 5, "lr": 2e-4}], 3)
    with pytest.raises(ScheduleError, match="非末段"):
        resolve_ppo_schedule([{"lr": 1e-4}, {"until_iter": 5, "lr": 2e-4}], 1)
