"""shard / episode 数据面契约（P0-4 / B5 第二至三块）。

本文件钉**契约本身**（公开事实），字段表 ↔ 契约的同源判据住在被测代码里：`np_core` 的 import
哨兵（字段表改一边、契约没改 ⇒ import 当场炸，同 `common/contracts.py` 里 HeadLayout↔schema
那一条）。这样守卫不必读私有表（G5 的「私有断言」），也少一次运行期扫描。

钉住三件事：

1. **shard 键集**：`Shard` / `ShardV6` 的键就是 load 完的 shard 键（v6 恰好多两列 hold）。
2. **episode 的字段与序**：三个后端（ppo / goal / intent）的 `fields` 元组**全部**从
   `Episode` / `EpisodeV6` / `GoalEpisode` / `IntentEpisode` 的声明序派生 —— 这里把历史四串
   元组逐字钉回去。键序是契约的一部分（`episode_from_shard` 按此序组装，训练侧按 dict 序拼接），
   谁为了好看挪了 `adv`/`ret` 的位置就红。`engage_label` 是**存在才带**的那一列（`optional=`），
   它必须恰好是 `GoalEpisodeLabeled` 比 `GoalEpisode` 多出来的键。
3. **零运行时成本**：运行期 `TypedDict` 就是 `dict`（没有包装层）；且 `common/contracts` 仍不
   import numpy（L0 要能解到没装 numpy 的云机上）—— 后一条用子进程查 `sys.modules`。

为什么要这层守卫：三个 `load_episode_from_shard` 的返回现在是 `Episode` / `GoalEpisode` /
`IntentEpisode`（`RolloutBackend` 上是 `EpisodeLike`）。契约与装配序一旦分家，最坏形态是
「键都在、序变了」——类型系统看不见，只有这里看得见。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.contracts import (
    EPISODE_FIELDS,
    EPISODE_FIELDS_V6,
    GOAL_EPISODE_FIELDS,
    GOAL_EPISODE_OPTIONAL,
    INTENT_EPISODE_FIELDS,
    Episode,
    EpisodeLike,
    EpisodeV6,
    GoalEpisode,
    GoalEpisodeLabeled,
    IntentEpisode,
    Shard,
    ShardV6,
)

#: legacy shard 加载完的键（盘上字段减去 `metrics`，加上现算的 `reward`/`stage`）。
SHARD_KEYS = (
    "obs",
    "scalars",
    "extra",
    "a_move",
    "a_fire",
    "lp_move",
    "lp_fire",
    "value",
    "done",
    "mask",
    "reward",
    "stage",
)
SHARD_KEYS_V6 = (*SHARD_KEYS, "a_hold", "lp_hold")

#: 三个后端 episode 的历史键序（v6 / 带标注的两族把多出的键**追加在末尾**）。
#: 这些是基线，不是可推导物 —— 改序必须是有人故意的。
EPISODE_KEYS = (
    "obs",
    "scalars",
    "a_move",
    "a_fire",
    "lp_move",
    "lp_fire",
    "value",
    "adv",
    "ret",
    "mask",
)
EPISODE_KEYS_V6 = (*EPISODE_KEYS, "a_hold", "lp_hold")
GOAL_EPISODE_KEYS = (
    "obs",
    "scalars",
    "inject",
    "a_goal",
    "lp_goal",
    "value",
    "adv",
    "ret",
    "goal_mask",
    "dt",
    "engage",
)
INTENT_EPISODE_KEYS = (
    "obs",
    "scalars",
    "inject",
    "a_intent",
    "lp_intent",
    "value",
    "adv",
    "ret",
    "mask",
    "dt",
)


def test_shard_contract_keys() -> None:
    assert tuple(Shard.__annotations__) == SHARD_KEYS
    assert tuple(ShardV6.__annotations__) == SHARD_KEYS_V6


def test_episode_key_order_is_unchanged() -> None:
    assert EPISODE_FIELDS == EPISODE_KEYS
    assert EPISODE_FIELDS_V6 == EPISODE_KEYS_V6
    assert GOAL_EPISODE_FIELDS == GOAL_EPISODE_KEYS
    assert INTENT_EPISODE_FIELDS == INTENT_EPISODE_KEYS
    # 派生点就在这两行上：契约声明序即键序，别处不许再手写一份
    assert tuple(Episode.__annotations__) == EPISODE_KEYS
    assert tuple(EpisodeV6.__annotations__) == EPISODE_KEYS_V6


def test_goal_optional_column_is_the_labeled_variant() -> None:
    """`optional=` 的那一列必须正好是 `GoalEpisodeLabeled` 多出来的键（末尾追加）。"""
    assert GOAL_EPISODE_OPTIONAL == ("engage_label",)
    assert tuple(GoalEpisodeLabeled.__annotations__) == (*GOAL_EPISODE_KEYS, "engage_label")
    assert set(GOAL_EPISODE_FIELDS) | set(GOAL_EPISODE_OPTIONAL) == set(
        GoalEpisodeLabeled.__annotations__
    )


def test_episode_like_covers_the_three_backends() -> None:
    """`RolloutBackend` 只承诺「这四族之一」—— 三个后端各自的返回类型都要在里面。

    这里用 `typing.get_args` 而不是 `isinstance`（运行期 `TypedDict` 不能用于 isinstance）。
    """
    from typing import get_args

    members = get_args(EpisodeLike)
    for family in (Episode, EpisodeV6, GoalEpisode, GoalEpisodeLabeled, IntentEpisode):
        assert family in members, f"{family.__name__} 不在 EpisodeLike 里"


def test_contracts_are_plain_dicts_at_runtime() -> None:
    """零运行时成本：没有包装类、没有构造开销 —— `TypedDict` 类就是 `dict`。"""
    for cls in (Shard, ShardV6, Episode, EpisodeV6, GoalEpisode, GoalEpisodeLabeled, IntentEpisode):
        assert cls.__mro__ == (cls, dict, object), cls.__name__


def test_importing_the_contract_does_not_pull_numpy() -> None:
    """L0 的运行时依赖仍是 stdlib（numpy 只应出现在 `TYPE_CHECKING` 下）。"""
    code = (
        "import sys, common.contracts; "
        "assert 'numpy' not in sys.modules, 'common.contracts 把 numpy 拖进来了'"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
