"""动作空间、头布局与数据面的显式契约（P0-4 / B5）。

历史实现把「legacy 5/2」与「v6 4/3/2 + hold」的差异散落在 PPO 引擎里：
`len(out) >= 4` 推 ref 头数、`e.get("a_hold") is not None` 推数据动作空间、
再各自手写 `:5 / 5:7` 或 `:4 / 4:7 / 7:9` 的掩码切片。三处判据一旦不同源，
最坏形态不是崩溃而是**静默错位**（hold 被当 fire 的第 3 类，loss 照降）。

本模块只做两件事：

* `ActionSpace` —— 两个分支的**名字**，并提供两个边界上的唯一判据
  （数据侧 `from_batch`，模型侧 `from_forward_arity`）；
* `HeadLayout` —— 由动作空间派生的头宽与掩码切片，引擎不再手写数字。

纯 stdlib + `common.schema` 常量，符合 `common` 的 L0 契约；不 import numpy/torch
（数据面契约里的 `np.ndarray` 只在 `TYPE_CHECKING` 下可见，运行时仍是纯 stdlib）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict

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


# ---------------- 数据面契约（B5 第二块）：shard / episode 的键 ----------------
#
# 这两个契约只干两件事：① 拼错的键名由 mypy **当场**报（`d["a_holdd"]` 今天不会报错，
# 只会静默走 legacy 分支 —— 即 P0-4 记的那种「y32 事故」形态）；② 让「v6 多哪两列」
# 「episode 的键与序是哪十个」成为可引用的事实，而不是散在几个手写元组里。
#
# **零运行时成本**：`TypedDict` 在运行期就是一个 dict，这些类只在类型检查时存在；
# numpy 也只在 `TYPE_CHECKING` 下可见，`import common.contracts` 仍不碰第三方包。

if TYPE_CHECKING:
    import numpy as np


class Shard(TypedDict):
    """per-tick RL shard（`worker/ppo/np_core.load_shard`）的**加载后**键集。

    盘上字段表是 `np_core._RL_SHARD_SPEC`（多一列 `metrics`：读到就算成 `reward` 后 pop 掉，
    所以不在这里）；`reward` / `stage` 是加载端现算的两列。

    ⚠ BC 语料（`worker/data/npyio.py::load_shard`）是**另一套**形状（`a_hold` 在那里必存在、
    并被改名成 `hold`）—— 两套不共用本契约，否则「有 a_hold 就是 v6」这条判据会被搅浑。
    """

    obs: np.ndarray
    scalars: np.ndarray
    extra: np.ndarray
    a_move: np.ndarray
    a_fire: np.ndarray
    lp_move: np.ndarray
    lp_fire: np.ndarray
    value: np.ndarray
    done: np.ndarray
    mask: np.ndarray
    reward: np.ndarray
    stage: np.ndarray


class ShardV6(Shard):
    """v6 shard = `Shard` + 两列 hold（`a_hold.npy` 在盘上才带，见 `load_shard_fields`）。"""

    a_hold: np.ndarray
    lp_hold: np.ndarray


class Episode(TypedDict):
    """`load_episode_from_shard` 产出的键**与序**。

    序是契约的一部分：`_episode.episode_from_shard` 按 `fields` 元组组装，而那个元组由本类的
    声明序派生（`np_core.EPISODE_FIELDS`）—— 历史 shard 的 episode 字典键序因此逐字不变。
    `adv` / `ret` 不在 shard 里，是 GAE 现算的占位；它们排在 `mask` **之前**，别为了好看挪走。
    """

    obs: np.ndarray
    scalars: np.ndarray
    a_move: np.ndarray
    a_fire: np.ndarray
    lp_move: np.ndarray
    lp_fire: np.ndarray
    value: np.ndarray
    adv: np.ndarray
    ret: np.ndarray
    mask: np.ndarray


class EpisodeV6(Episode):
    """v6 episode = `Episode` + 两列 hold（多出来的两键**追加在末尾**，与历史键序一致）。"""

    a_hold: np.ndarray
    lp_hold: np.ndarray
