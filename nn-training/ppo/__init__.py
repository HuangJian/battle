"""PPO on-policy policy gradient backend.

Sub-modules:
  engine  - build_ppo / ppo_update / PPO 更新循环（torch）
  common  - masked_logsoftmax / cat_logprob / sync_scalars / ckpt（torch）
  np_core - **纯 numpy/stdlib 核心**（torch-free）：GAE / shard 发现装载 / episode
            捆绑（load_episodes、discover_rl_shards、load_shard）/ XLA 助手
  goal    - GoalNet RL adapter (goal-step PPO)
  intent  - IntentNet RL adapter (intent-step semi-MDP)
  bench   - PPO benchmark script

根级便捷名走 **PEP 562 惰性再导出**（2026-09-26）：`ppo.common` / `ppo.engine` 顶层
都 `import torch`，此前 eager `from ppo.common import ...` 使任何 `import ppo.<子模块>`
（含免 torch 的 `ppo.np_core`）都被顺手拖进 torch。现在只有真的访问 `ppo.ppo_update`
这类根级名字时才 import 对应模块。

台阶还得踩到底（同日修正）：**惰性还不够，指向还得对**。首版把 `compute_gae` /
`chunk_episodes` / `discover_shards` / `load_shard_fields` 都指向 `ppo.common`——那是再
导出的旧家 ⇒ `ppo.compute_gae` 依旧拖 torch。见 `_EXPORTS` 的注释。

第三次踩（同日）：`discover_rl_shards` / `load_episodes` / `load_shard` 随 per-tick
shard 装载从 `ppo/engine` 搬进 `ppo/np_core` ⇒ 便捷名同步改指 `ppo.np_core`。
（`ppo.load_episodes` 此前必拖 torch，`e2e/test_run_rl` 的无 torch 路径因此收集失败。）
"""

from __future__ import annotations

import importlib
from typing import Any

#: 根级便捷名 → 源模块。
#:
#: **指向可以是免 torch 的 `ppo.np_core`**（2026-09-26 修正）：`np_core` 那边才是
#: `compute_gae` / `chunk_episodes` / `discover_shards` / `load_shard_fields` 的**家**，
#: `ppo.common` 只是再导出。此前这五个都指向 `ppo.common` ⇒ 访问 `ppo.compute_gae`
#: 会把 torch 拖进运行期（同样踩了「导入点没随函数搬家」——e2e/test_run_rl 的两条
#: 手算用例因此在无 torch 机上必红）。名字指向哪家，就看谁**定义**它。
_EXPORTS: dict[str, str] = {
    "chunk_episodes": "ppo.np_core",
    "compute_gae": "ppo.np_core",
    "discover_shards": "ppo.np_core",
    "discover_rl_shards": "ppo.np_core",
    "load_episodes": "ppo.np_core",
    "load_shard": "ppo.np_core",
    "load_shard_fields": "ppo.np_core",
    "masked_logsoftmax": "ppo.common",
    "build_ppo": "ppo.engine",
    "ppo_update": "ppo.engine",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module 'ppo' has no attribute {name!r}")
    return getattr(importlib.import_module(mod), name)


def __dir__() -> list[str]:
    return list(__all__)
