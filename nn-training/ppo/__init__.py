"""PPO on-policy policy gradient backend.

Sub-modules:
  engine  - build_ppo / ppo_update / load_episodes / discover_rl_shards / ...
  common  - masked_logsoftmax / compute_gae / discover_shards / load_shard_fields / ...
  goal    - GoalNet RL adapter (goal-step PPO)
  intent  - IntentNet RL adapter (intent-step semi-MDP)
  bench   - PPO benchmark script
"""
from ppo.common import (
    chunk_episodes as chunk_episodes,
)
from ppo.common import (
    compute_gae as compute_gae,
)
from ppo.common import (
    discover_shards as discover_shards,
)
from ppo.common import (
    load_shard_fields as load_shard_fields,
)
from ppo.common import (
    masked_logsoftmax as masked_logsoftmax,
)
from ppo.engine import (
    build_ppo as build_ppo,
)
from ppo.engine import (
    discover_rl_shards as discover_rl_shards,
)
from ppo.engine import (
    load_episodes as load_episodes,
)
from ppo.engine import (
    load_shard as load_shard,
)
from ppo.engine import (
    ppo_update as ppo_update,
)
