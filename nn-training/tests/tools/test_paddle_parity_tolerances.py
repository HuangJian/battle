"""`paddle_parity.py::_weight_outlier_ok` —— 少量 v≈0 坐标的离群窄缝（白盒真值表）。

v7（`OBS_CHANNELS 16→17`）把 stem 输入从 18 加宽到 19 后，`legacy_plain` 有 5 个坐标
一步更新反向、幅度 ≈±lr（Adam 在 v≈0 处放大 f32 正负噪声）。这条判据必须：
  · 允收「少数坐标 ≤2·lr、均值极小」，且**不**把全局 atol 抬到 2·lr；
  · 拒绝「多坐标大偏」/「单坐标 >2·lr」/「均值不下沉」——任何一条都可能是真分叉。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load_tool() -> Any:
    """按文件路径加载（tools/ 无 __init__.py；与 test_build_demo_bank 同款）。"""
    spec = importlib.util.spec_from_file_location(
        "_paddle_parity_under_test", NN_ROOT / "tools" / "paddle_parity.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


TOOL = _load_tool()
ATOL = 1e-4


def _flat(*vals: float, tail: float = 1e-8, n_tail: int = 1000) -> np.ndarray:
    a = np.concatenate([np.array(vals, dtype=np.float64), np.full(n_tail, tail)])
    return np.sort(a)


def test_all_within_atol_is_ok() -> None:
    ok, n_big, mean = TOOL._weight_outlier_ok(_flat(9e-5), ATOL)
    assert ok and n_big == 0 and mean < ATOL / 10


def test_five_coord_outlier_is_allowed() -> None:
    """v7 实测形状：5 个坐标 ~5.8e-4，其余均值 5e-8。"""
    ok, n_big, _ = TOOL._weight_outlier_ok(
        _flat(5.8e-4, 1.88e-4, 1.62e-4, 1.52e-4, 1.47e-4, tail=5e-8, n_tail=30000),
        ATOL,
    )
    assert ok and n_big == 5


def test_outlier_above_two_lr_is_rejected() -> None:
    """超过 Adam 单步上界（2·lr=6e-4）⇒ 不是「归一化饱和」能解释的形状。"""
    ok, _, _ = TOOL._weight_outlier_ok(_flat(1.0e-3, tail=1e-8), ATOL)
    assert not ok


def test_too_many_outliers_is_rejected() -> None:
    """9 个 >atol ⇒ 超过 OUTLIER_MAX_ELEMS=8，按多坐标分叉处理。"""
    vals = [5e-4] * 9
    ok, n_big, _ = TOOL._weight_outlier_ok(_flat(*vals, tail=1e-8), ATOL)
    assert not ok and n_big == 9


def test_large_mean_is_rejected() -> None:
    """整片都偏 5e-5（每个都 ≤atol）⇒ 均值 5e-5 > atol/10，不许当「少数离群」放行。"""
    ok, n_big, mean = TOOL._weight_outlier_ok(np.full(1000, 5e-5), ATOL)
    assert not ok and n_big == 0 and mean > ATOL / 10
