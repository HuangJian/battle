"""PPO on-policy policy gradient backend.

Sub-modules:
  engine  - build_ppo / ppo_update / load_episodes / discover_rl_shards / ...
  common  - masked_logsoftmax / compute_gae / discover_shards / load_shard_fields / ...
  goal    - GoalNet RL adapter (goal-step PPO)
  intent  - IntentNet RL adapter (intent-step semi-MDP)
  bench   - PPO benchmark script
"""
from ppo.common import (  # noqa: F401
    chunk_episodes,
    compute_gae,
    discover_shards,
    load_shard_fields,
    masked_logsoftmax,
)
from ppo.engine import (  # noqa: F401
    build_ppo,
    discover_rl_shards,
    load_episodes,
    load_shard,
    ppo_update,
)
