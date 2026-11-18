"""shard / episode 数据面契约（P0-4 / B5 第二块）。

本文件钉**契约本身**（公开事实），字段表 ↔ 契约的同源判据住在被测代码里：`np_core` 的 import
哨兵（字段表改一边、契约没改 ⇒ import 当场炸，同 `common/contracts.py` 里 HeadLayout↔schema
那一条）。这样守卫不必读私有表（G5 的「私有断言」），也少一次运行期扫描。

钉住三件事：

1. **键集**：`Shard` / `ShardV6` 的键就是 load 完的 shard 键（v6 恰好多两列 hold）。
2. **episode 的键与序**：`EPISODE_FIELDS*` 由 `Episode` / `EpisodeV6` 的声明序派生，这里把
   **历史两串元组**逐字钉回去 —— 键序是契约的一部分（`episode_from_shard` 按此序组装，
   训练侧按 dict 序拼接），谁为了好看挪了 `adv`/`ret` 的位置就红。
3. **零运行时成本**：运行期 `TypedDict` 就是 `dict`（没有包装层）；且 `common/contracts` 仍不
   import numpy（L0 要能解到没装 numpy 的云机上）—— 后一条用子进程查 `sys.modules`。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.contracts import Episode, EpisodeV6, Shard, ShardV6
from worker.ppo import np_core

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

#: episode 的历史键序（v6 的两列**追加在末尾**）。这两串是基线，不是可推导物。
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


def test_shard_contract_keys() -> None:
    assert tuple(Shard.__annotations__) == SHARD_KEYS
    assert tuple(ShardV6.__annotations__) == SHARD_KEYS_V6


def test_episode_key_order_is_unchanged() -> None:
    assert np_core.EPISODE_FIELDS == EPISODE_KEYS
    assert np_core.EPISODE_FIELDS_V6 == EPISODE_KEYS_V6
    # 派生点就在这两行上：契约声明序即键序，别处不许再手写一份
    assert tuple(Episode.__annotations__) == EPISODE_KEYS
    assert tuple(EpisodeV6.__annotations__) == EPISODE_KEYS_V6


def test_contracts_are_plain_dicts_at_runtime() -> None:
    """零运行时成本：没有包装类、没有构造开销 —— `TypedDict` 类就是 `dict`。"""
    for cls in (Shard, ShardV6, Episode, EpisodeV6):
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
