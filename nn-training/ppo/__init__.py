"""PPO on-policy policy gradient backend.

Sub-modules:
  engine  - build_ppo / ppo_update / load_episodes / discover_rl_shards / ...
  common  - masked_logsoftmax / compute_gae / discover_shards / load_shard_fields / ...
  np_core - ppo/common 的**纯 numpy/stdlib 核心**（torch-free）
  goal    - GoalNet RL adapter (goal-step PPO)
  intent  - IntentNet RL adapter (intent-step semi-MDP)
  bench   - PPO benchmark script

根级便捷名走 **PEP 562 惰性再导出**（2026-09-26）：`ppo.common` / `ppo.engine` 顶层
都 `import torch`，此前 eager `from ppo.common import ...` 使任何 `import ppo.<子模块>`
（含免 torch 的 `ppo.np_core`）都被顺手拖进 torch。现在只有真的访问 `ppo.ppo_update`
这类根级名字时才 import 对应模块。
"""

from __future__ import annotations

import importlib
from typing import Any

#: 根级便捷名 → 源模块。
_EXPORTS: dict[str, str] = {
    "chunk_episodes": "ppo.common",
    "compute_gae": "ppo.common",
    "discover_shards": "ppo.common",
    "load_shard_fields": "ppo.common",
    "masked_logsoftmax": "ppo.common",
    "build_ppo": "ppo.engine",
    "discover_rl_shards": "ppo.engine",
    "load_episodes": "ppo.engine",
    "load_shard": "ppo.engine",
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
