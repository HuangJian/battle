"""
Canonical NN training schema — SINGLE SOURCE OF TRUTH shared with the TS side.

The TS exporter (`src/nn/obs-encoder.ts`) MUST produce tensors that obey this
exact layout, and the TS runtime inference (`src/nn/infer.ts`) MUST consume
weights exported by `weights_io.save_weights_json` in this exact format.

Keep this file and `src/nn/obs-encoder.ts` in lock-step. Any change here is a
schema MAJOR bump (see `OBS_SCHEMA_MAJOR` below) and forces a full re-export of
all npy shards (plan NN-M0 exit rule, nn2 P0-2).
"""

# ---- Observation spatial tensor ----
OBS_CHANNELS = 14          # plan §1.1: channels 0..13
BOARD = 26                 # GRID — 26x26 sub-block grid
OBS_SHAPE = (OBS_CHANNELS, BOARD, BOARD)

# ---- Scalar feature vector ----
# v2 (OBS_SCHEMA_MAJOR=2): item-inventory scalars removed (guard/frenzy/
# rewind stock, frenzyActive, frenzyShotsLeft) — 24 → 19 (plan AI-No-Items
# Warmstart M2 ②). SCALAR_X_INDICES renumbered [20,23] → [15,18].
SCALAR_DIM = 19

# ---- Action heads (v2: item head REMOVED — AI 不使用主动道具) ----
MOVE_DIM = 5               # none/up/down/left/right
FIRE_DIM = 2               # hold-state: 0=release, 1=hold
MASK_DIM = MOVE_DIM + FIRE_DIM  # 7

# Schema major version. Written into every npy shard manifest and into the
# exported weights file. Bump +1 on ANY channel/scalar/action layout change.
OBS_SCHEMA_MAJOR = 2

# ---- Channel index map (plan §1.1) ----
CH = {
    "terrain_brick": 0,
    "terrain_steel": 1,
    "terrain_water": 2,
    "terrain_forest": 3,
    "terrain_ice": 4,
    "base": 5,            # eagle (=2) + ring cells (=1)
    "self": 6,            # player tank
    "enemy_basic": 7,
    "enemy_fast": 8,
    "enemy_power": 9,
    "enemy_armor": 10,
    "bullet": 11,         # enemy bullet 1-4, player bullet 5-8
    "powerup": 12,        # on-field power-up, value = 1+enumIndex
    "wave_heat": 13,      # projected spawns in next K ticks per spawn point
}

# ---- PowerUpType declaration order (src/types.ts:20-38, plan §1.1 ch12) ----
# MUST match the `export type PowerUpType` literal union order EXACTLY.
POWERUP_ORDER = [
    "star", "bomb", "shield", "freeze", "tank", "fence", "boat",
    "guard", "frenzy", "sacrifice", "rewind", "repair", "emp", "decoy", "mine",
]
POWERUP_ENUM = {name: i for i, name in enumerate(POWERUP_ORDER)}  # 0..14
assert len(POWERUP_ORDER) == 15, "PowerUpType has exactly 15 members"

# ---- Enemy kind -> channel offset (relative to CH.enemy_basic) ----
KIND_INDEX = {"basic": 0, "fast": 1, "power": 2, "armor": 3}

# ---- Intelligence tier -> tierCode (plan §1.1 ch7-10) ----
TIER_INDEX = {"none": 0, "rookie": 1, "soldier": 2, "veteran": 3, "commander": 4}

# ---- Direction -> dirIdx (constants.ts DIR_DX/DY order) ----
DIR_INDEX = {"up": 0, "down": 1, "left": 2, "right": 3}
DIR_FROM_INDEX = ["up", "down", "left", "right"]

# ---- Scalar layout (plan §1.2, v2: 19 floats; item inventory scalars removed). ----
# Indices that flip sign under mirrorX (relative-direction x-components).
SCALAR_X_INDICES = [15, 18]

SCALAR_LAYOUT = [
    (0,  "slack"),                 # min enemy killSlack, normalized 0..1
    (1,  "baseDeadline"),          # min enemyDamageDeadline, normalized 0..1
    (2,  "lives"),                 # lives / START_LIVES
    (3,  "level"),                 # player star level, clamped /3
    (4,  "fireProgress"),          # 0..1 cooldown progress (nextFireInterval)
    (5,  "turnCooldownRemaining"), # 0..1 (turnCooldownMs - elapsed)/cd
    (6,  "ringCompleteness"),      # intact ring cells / 8
    (7,  "enemiesOnField"),        # alive enemies / MAX_ENEMIES_ALIVE
    (8,  "spawnQueueRemaining"),   # remaining queue / enemiesTotal
    (9,  "tier_none"),             # fraction of enemies at tier none
    (10, "tier_rookie"),
    (11, "tier_soldier"),
    (12, "tier_veteran"),
    (13, "tier_commander"),
    (14, "nearestEnemyDist"),      # normalized 0..1 by field diagonal
    (15, "nearestEnemyRelX"),      # dx/dist, -1..1  (FLIPS on mirrorX)
    (16, "nearestEnemyRelY"),      # dy/dist, -1..1
    (17, "nearestBaseDist"),       # normalized 0..1
    (18, "nearestBaseRelX"),       # dx/dist, -1..1  (FLIPS on mirrorX)
]
assert len(SCALAR_LAYOUT) == SCALAR_DIM
assert sorted(i for i, _ in SCALAR_LAYOUT) == list(range(SCALAR_DIM))

# ---- Decision-tick condition category (plan §1.3) ----
COND_TURN = 0
COND_FIRE = 1
COND_ITEM = 2
COND_SUBSAMPLE = 3

# ---- mirrorX direction-channel set (channels whose value encodes a dirIdx) ----
DIRECTION_CHANNELS = {CH["self"], CH["enemy_basic"], CH["enemy_fast"],
                      CH["enemy_power"], CH["enemy_armor"], CH["bullet"]}
