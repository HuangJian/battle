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
OBS_CHANNELS = 16  # plan §1.1 (0..13) + v3: 14 hit_to_kill, 15 spawning
BOARD = 26  # GRID — 26x26 sub-block grid
OBS_SHAPE = (OBS_CHANNELS, BOARD, BOARD)

# ---- Scalar feature vector ----
# v2 (OBS_SCHEMA_MAJOR=2): item-inventory scalars removed (guard/frenzy/
# rewind stock, frenzyActive, frenzyShotsLeft) — 24 → 19 (plan AI-No-Items
# Warmstart M2 ②). SCALAR_X_INDICES renumbered [20,23] → [15,18].
# v3 (OBS_SCHEMA_MAJOR=3): 19 → 30 (s19..s29，obs-schema-v3.plan.md v4.0 §3.3 定稿)。
SCALAR_DIM = 30

# ---- Action heads (v2: item head REMOVED — AI 不使用主动道具) ----
MOVE_DIM = 5  # stop/up/down/left/right (B案: index 0 = STOP)
FIRE_DIM = 2  # hold-state: 0=release, 1=hold
MASK_DIM = MOVE_DIM + FIRE_DIM  # 7

# Move-label semantics version (plan/new-era-stop.plan.md #7).
# Index 0 used to mean "keep current heading"; B案 redefines it as **STOP** — the
# same byte `a_move==0` is now physically opposite. This tag enters
# `rl.config.corpus_identity_fp`, so every policy rollout shard produced under the
# old mapping is a *foreign lineage* (rejected by D14 in every funnel) while
# BC/demo shards stay valid — their null→0 labels were always physically correct,
# and `bc_corpus_identity_fp` deliberately does not include this tag.
# MUST match `src/nn/action-space.ts::MOVE_LABEL_SEMANTICS` (twin-anchored tests).
MOVE_LABEL_SEMANTICS = "stop0"

# Schema major version. Written into every npy shard manifest and into the
# exported weights file. Bump +1 on ANY channel/scalar/action layout change.
OBS_SCHEMA_MAJOR = 3

# ---- Channel index map (plan §1.1 + v3: 14/15) ----
# 全局规则（hy E1 / dsf A2，实施者必读）：
#   · 所有 grid 通道是 uint8——浮点幅值必须 ×255 取整（或量化成档）后写入；
#   · bullet 通道是**混合基记法** (speedBucket<<3) + (owner<<2) + (d+1)——
#     **全程加法**（d+1=4 占 bit2 与 owner 位重叠，字面 OR 会令 player-right
#     ≡ enemy-right），禁止照抄位域直觉用 OR。
CH = {
    "terrain_brick": 0,
    "terrain_steel": 1,
    "terrain_water": 2,
    "terrain_forest": 3,
    "terrain_ice": 4,
    "base": 5,  # eagle (=2) + ring cells (=1)
    "self": 6,  # player tank
    "enemy_basic": 7,  # (bonus<<6) + (tier<<3) + (d+1)，1..100——bonus 位在 bit6（A1）
    "enemy_fast": 8,
    "enemy_power": 9,
    "enemy_armor": 10,
    "bullet": 11,  # 混合基：1..32（speedBucket 见 BULLET_SPEED_BUCKETS_PX）
    "powerup": 12,  # (lifeBucket<<4) | (1+enumIndex)，1..63
    "wave_heat": 13,  # projected spawns in next K ticks per spawn point（N 点通用）
    "hit_to_kill": 14,  # min(9, ceil(hp/damage))——随玩家星级重算（live damage，禁缓存）
    "spawning": 15,  # 生成中敌倒计时 0..255
}

# ---- PowerUpType declaration order (src/types.ts:20-38, plan §1.1 ch12) ----
# MUST match the `export type PowerUpType` literal union order EXACTLY.
POWERUP_ORDER = [
    "star",
    "bomb",
    "shield",
    "freeze",
    "tank",
    "fence",
    "boat",
    "guard",
    "frenzy",
    "sacrifice",
    "rewind",
    "repair",
    "emp",
    "decoy",
    "mine",
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

# ---- Scalar layout (plan §1.2 + v3: 30 floats，定稿 obs-schema-v3.plan.md §3.3) ----
# Indices that flip sign under mirrorX (relative-direction x-components).
# v3：15/18 保留，29 = vx 冰面横向速度（s28 vy 不翻）。
SCALAR_X_INDICES = [15, 18, 29]

SCALAR_LAYOUT = [
    (0, "slack"),  # min enemy killSlack, normalized 0..1
    (1, "baseDeadline"),  # min enemyDamageDeadline, normalized 0..1
    (2, "lives"),  # lives / 3（跨 tier OOD 注记：1 命 tier 恒 0.333、2 命起手 0.667）
    (3, "level"),  # player star level, clamped /3
    (4, "fireProgress"),  # 0..1 cooldown progress (nextFireInterval)
    (5, "turnCooldownRemaining"),  # 0..1 (turnCooldownMs - elapsed)/cd
    (6, "ringCompleteness"),  # intact ring cells / 8
    (7, "enemiesOnField"),  # alive enemies / MAX_ENEMIES_ALIVE（生成中敌不算）
    (8, "spawnQueueRemaining"),  # remaining queue / enemiesTotal
    (9, "tier_none"),  # fraction of enemies at tier none（生成中敌不算——定案）
    (10, "tier_rookie"),
    (11, "tier_soldier"),
    (12, "tier_veteran"),
    (13, "tier_commander"),
    (14, "nearestEnemyDist"),  # normalized 0..1 by field diagonal
    (15, "nearestEnemyRelX"),  # dx/dist, -1..1  (FLIPS on mirrorX)
    (16, "nearestEnemyRelY"),  # dy/dist, -1..1
    (17, "nearestBaseDist"),  # normalized 0..1
    (18, "nearestBaseRelX"),  # dx/dist, -1..1  (FLIPS on mirrorX)
    # ---- v3 新增（obs-schema-v3.plan.md v4.0 §3.3 定稿）----
    (19, "playerHp"),  # clamp01(hp/maxHp)——ratio 定案（raw 量纲离群）
    (20, "playerShield"),  # clamp01(min(shieldTimer, RESPAWN_SHIELD_MS)/RESPAWN_SHIELD_MS)
    (21, "freeze"),  # clamp01(freezeTimer / POWERUP_DURATION_MS)
    (22, "stuck"),  # clamp01(stuckTicks / 900)——World 字段，共享判定 stuck-detect.ts
    (23, "boat"),  # boatTimer > 0 ? 1 : 0
    (24, "baseHp"),  # clamp01(baseHp/baseMaxHp) 无条件——无基地关恒 1.0 不变化（v3.1）
    (25, "fence"),  # fenceExpireFrame !== undefined ? 1 : 0
    (26, "score"),  # clamp01(score / 20000)（全难度计分非恒 0）
    (27, "emp"),  # clamp01(empTimer / EMP_DURATION_MS)
    (28, "iceVy"),  # vy/speed，[-1,1]（不 clamp01，同 s15/16 惯例）
    (29, "iceVx"),  # vx/speed，[-1,1]（x 分量 ⇒ SCALAR_X_INDICES）
]
assert len(SCALAR_LAYOUT) == SCALAR_DIM
assert sorted(i for i, _ in SCALAR_LAYOUT) == list(range(SCALAR_DIM))

# ---- Decision-tick condition category (plan §1.3) ----
COND_TURN = 0
COND_FIRE = 1
COND_ITEM = 2
COND_SUBSAMPLE = 3

# ---- mirrorX direction-channel set (channels whose value encodes a dirIdx) ----
DIRECTION_CHANNELS = {
    CH["self"],
    CH["enemy_basic"],
    CH["enemy_fast"],
    CH["enemy_power"],
    CH["enemy_armor"],
    CH["bullet"],
}

# ---- v3 弹速档（ch11 混合基高位，C10）----
# 真弹速源 = bulletSpeedCps 表（config/speed.ts baseBulletSpeedPxPerTick），
# **非** profile.projectileSpeed——规格初稿 {40,45,50,70} 前提勘误（那是能力维度，
# 与实际弹速脱钩）。桶边界按 px/tick「慢→快」序数划分：
#   modern 实测：armor 3.60 / power 3.80 / basic 4.00 / fast+player 4.20-4.60 px/tick；
#   classic power 重弹 8.0 px/tick 自然落最高档（快弹语义，方向正确）。
BULLET_SPEED_BUCKETS_PX = [3.7, 3.9, 4.1]
SPAWN_COUNTDOWN_MS = 1000


def _fnv1a(s: str) -> str:
    h = 0x811C9DC5
    for ch in s:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")


# SCHEMA_FINGERPRINT（hy X4）：双端一致性机器锚——TS 侧 obs-encoder.ts 逐字对齐
# 同一 payload（字段序/连接符一致）；任何常量变动指纹必变 ⇒ 双端单测红 +
# 写进 npy shard manifest。派生公式的正确性由单测另锁，指纹只钉「常量身份」。
_FINGERPRINT_PARTS = [
    "v3",
    str(OBS_SCHEMA_MAJOR),
    str(OBS_CHANNELS),
    str(SCALAR_DIM),
    str(BOARD),
    ",".join(str(i) for i in SCALAR_X_INDICES),
    # 标量**语义**序列（hy X4 / obs spec §3.4-7）：只钉维度会让「交换 s19/s20 含义」
    # 这类漏同步指纹不变——30 个名字进 payload，TS 侧 SCALAR_NAMES 逐字对齐。
    ",".join(name for _, name in SCALAR_LAYOUT),
    "|".join(str(v) for v in CH.values()),  # TS 侧逐元素 push → '|' 连接（勿改 ','）
    ",".join(POWERUP_ORDER),
    ",".join(str(b) for b in BULLET_SPEED_BUCKETS_PX),
    str(SPAWN_COUNTDOWN_MS),
    str(600),  # WAVE_HEAT_TICKS（TS const，plan §1.1 ch13）
]
SCHEMA_FINGERPRINT = _fnv1a("|".join(_FINGERPRINT_PARTS))
