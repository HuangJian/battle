"""动作空间/头布局契约（P0-4 / B5 第一块）。

这组用例钉住 PPO 引擎不再各自嗅探的三条判据：

1. **数据侧**：`a_hold` 存在 ⇔ v6；不存在/None ⇔ legacy。
2. **模型侧**：forward 返回 3 个头 ⇔ legacy，4 个头 ⇔ v6；其他 arity 响亮报错。
3. **切片**：`HeadLayout` 的 move/fire/hold 切片必须覆盖且只覆盖对应掩码列，
   宽度与 `common.schema` 的 7/9 同源。
"""

from __future__ import annotations

import sys
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from common.contracts import ActionSpace, HeadLayout, head_layout
from common.schema import FIRE3_DIM, FIRE_DIM, HOLD_DIM, MASK_DIM, MASK_V6_DIM, MOVE4_DIM, MOVE_DIM


def test_layouts_match_schema_constants() -> None:
    legacy = head_layout(ActionSpace.LEGACY)
    v6 = head_layout(ActionSpace.V6)
    assert (legacy.move_dim, legacy.fire_dim, legacy.hold_dim) == (MOVE_DIM, FIRE_DIM, 0)
    assert legacy.mask_dim == MASK_DIM
    assert not legacy.has_hold
    assert (v6.move_dim, v6.fire_dim, v6.hold_dim) == (MOVE4_DIM, FIRE3_DIM, HOLD_DIM)
    assert v6.mask_dim == MASK_V6_DIM
    assert v6.has_hold


def test_layout_slices_cover_exactly_the_mask() -> None:
    """切片互不重叠、无空洞，且 hold 只在 v6 存在。"""
    for space in ActionSpace:
        layout = head_layout(space)
        slices = [layout.move_slice, layout.fire_slice]
        if layout.hold_slice is not None:
            slices.append(layout.hold_slice)
        assert [s.start for s in slices] == [0, layout.move_dim, layout.move_dim + layout.fire_dim][
            : len(slices)
        ]
        assert slices[-1].stop == layout.mask_dim
        for left, right in pairwise(slices):
            assert left.stop == right.start
        if space is ActionSpace.LEGACY:
            assert layout.hold_slice is None


def test_from_batch_uses_hold_presence_not_truthiness() -> None:
    assert ActionSpace.from_batch({"a_hold": np.zeros(3, dtype=np.int64)}) is ActionSpace.V6
    assert ActionSpace.from_batch({"a_hold": None}) is ActionSpace.LEGACY
    assert ActionSpace.from_batch({}) is ActionSpace.LEGACY


def test_from_forward_arity_is_strict() -> None:
    assert ActionSpace.from_forward_arity(3) is ActionSpace.LEGACY
    assert ActionSpace.from_forward_arity(4) is ActionSpace.V6
    with pytest.raises(ValueError, match="3 或 4"):
        ActionSpace.from_forward_arity(5)


def test_head_layout_is_a_frozen_singleton() -> None:
    layout = head_layout(ActionSpace.V6)
    assert isinstance(layout, HeadLayout)
    assert head_layout(ActionSpace.V6) is layout
    field_name = "move_dim"
    with pytest.raises(AttributeError):
        setattr(layout, field_name, 5)
