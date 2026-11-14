"""动作空间与头布局的显式契约（P0-4 / B5 第一块）。

历史实现把「legacy 5/2」与「v6 4/3/2 + hold」的差异散落在 PPO 引擎里：
`len(out) >= 4` 推 ref 头数、`e.get("a_hold") is not None` 推数据动作空间、
再各自手写 `:5 / 5:7` 或 `:4 / 4:7 / 7:9` 的掩码切片。三处判据一旦不同源，
最坏形态不是崩溃而是**静默错位**（hold 被当 fire 的第 3 类，loss 照降）。

本模块只做两件事：

* `ActionSpace` —— 两个分支的**名字**，并提供两个边界上的唯一判据
  （数据侧 `from_batch`，模型侧 `from_forward_arity`）；
* `HeadLayout` —— 由动作空间派生的头宽与掩码切片，引擎不再手写数字。

纯 stdlib + `common.schema` 常量，符合 `common` 的 L0 契约；不 import numpy/torch。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from common.schema import (
    FIRE3_DIM,
    FIRE_DIM,
    HOLD_DIM,
    MASK_DIM,
    MASK_V6_DIM,
    MOVE4_DIM,
    MOVE_DIM,
)


class ActionSpace(Enum):
    """PPO 的两套动作头；`value` 不是动作头，故不计入 arity 判据。"""

    LEGACY = "legacy"
    V6 = "v6"

    @property
    def is_v6(self) -> bool:
        return self is ActionSpace.V6

    @classmethod
    def from_batch(cls, batch: Mapping[str, Any]) -> ActionSpace:
        """数据侧唯一判据：shard 是否带 `a_hold`（存在即 v6，None 视同缺席）。

        保留「存在性」而不是看值：`a_hold` 全 0 仍是合法的 v6 数据，不能把它
        误判成 legacy（否则 9 宽掩码会被按 7 宽切）。
        """
        return cls.V6 if batch.get("a_hold") is not None else cls.LEGACY

    @classmethod
    def from_forward_arity(cls, arity: int) -> ActionSpace:
        """模型侧唯一判据：forward 返回 3 元组（legacy）还是 4 元组（v6）。

        不用 `model.action_v6`：DataParallel 包装下属性代理不可靠（历史注释已记录）。
        """
        if arity == 3:
            return cls.LEGACY
        if arity == 4:
            return cls.V6
        raise ValueError(f"PPO forward 应返回 3 或 4 个头，实得 {arity}")


@dataclass(frozen=True)
class HeadLayout:
    """一套动作空间的头宽与掩码切片（掩码布局与 `common.schema` 同源）。"""

    space: ActionSpace
    move_dim: int
    fire_dim: int
    hold_dim: int
    move_slice: slice
    fire_slice: slice
    hold_slice: slice | None

    @property
    def mask_dim(self) -> int:
        return self.move_dim + self.fire_dim + self.hold_dim

    @property
    def has_hold(self) -> bool:
        return self.hold_dim > 0


def _layout(
    space: ActionSpace,
    move_dim: int,
    fire_dim: int,
    hold_dim: int,
) -> HeadLayout:
    move_end = move_dim
    fire_end = move_end + fire_dim
    hold_end = fire_end + hold_dim
    return HeadLayout(
        space=space,
        move_dim=move_dim,
        fire_dim=fire_dim,
        hold_dim=hold_dim,
        move_slice=slice(0, move_end),
        fire_slice=slice(move_end, fire_end),
        hold_slice=slice(fire_end, hold_end) if hold_dim else None,
    )


_LEGACY = _layout(ActionSpace.LEGACY, MOVE_DIM, FIRE_DIM, 0)
_V6 = _layout(ActionSpace.V6, MOVE4_DIM, FIRE3_DIM, HOLD_DIM)


def head_layout(space: ActionSpace) -> HeadLayout:
    """动作空间 → 头布局；两个返回值是不可变单例。"""
    return _V6 if space is ActionSpace.V6 else _LEGACY


# 与 schema 的自洽哨兵：常量改了一个却忘了另一个时，import 当场炸而不是训练时错位。
if _LEGACY.mask_dim != MASK_DIM or _V6.mask_dim != MASK_V6_DIM:
    raise AssertionError(
        f"HeadLayout 与 schema 不同源：legacy={_LEGACY.mask_dim}（期望 {MASK_DIM}）、"
        f"v6={_V6.mask_dim}（期望 {MASK_V6_DIM}）"
    )
