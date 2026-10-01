/**
 * export-rl-rollout.ts — RL on-policy rollout collector (R3: v7-aligned reward).
 *
 * ⚠️ 本脚本内部是串行的（多 stage × 多 seed 逐局跑）。调用方必须并行执行以提高效率：
 *   每局耗时 0.5-5s，30 局串行 15-150s，并行可压缩到数秒。
 *   推荐做法：拆成 N 个单局进程（--stages N --seeds M --out 独立目录），
 *   各自写 manifest.json，事后聚合。不要给本文件加 Worker 池——它在 codeHash
 *   覆盖集内，改动触发全节点重编译，且训练侧 queue.py 的分发模式已正确。
 *
 * 复用 ObsEncoder + StudentModel(+value head) 驱动 headless 仿真，按决策 tick
 * (K=10) 跑随机策略采样 → 写出 trajectory shards 供 Python PPO 消费。
 *
 * 奖励（R3，2026-08-21）：与评估口径 `tools/eval/godai-score.ts` v7 严格对齐——
 *   Φ(s)   = SCALE × V7_LOSS_BAND_MAX × Q_partial(s)
 *            （Q_partial：当前计数器下的 losses 带加权质量，权重重分配规则与
 *             godai-score.weightedQuality 一致；无生存免费收益——苟活不产出）
 *   r_t    = Φ(t) − Φ(t−1)                       （窗口势差，稠密信用分配）
 *   终局项 = SCALE × gatedScore − (Φ_end − Φ_0)
 *   ⇒ 每局总回报 ≡ SCALE × gatedScore（恒等式），胜局经带切换自然放大
 *     （V7: clear ≥ 0.70 vs loss ≤ 0.40）。
 *
 * F3 基地失守门控（2026-08-22，R6 收紧）：base_destroyed 局 gatedScore = v7 score × M。
 *   M 原 0.25，R6（2026-08-25 训练质量审计：it1–it68 未收敛、eval base_destroyed
 *   占 ~80%、baseIntegrity 恒 0）降至 0.1——投降更昂贵，守家成为第一顺位目标。
 *   R3 长跑实证 v7 败局带存在 Goodhart 倒挂——秒投降局（lives/baseSafety 满值、
 *   其余维度归零）score=0.1211 高于认真打仗的 ~0.110，PPO 理性收敛到投降。
 * 门控同时落两点：① 终局锚点（改变总回报，翻转倒挂：投降上限 0.121×M）；
 * ② Φ 本身（基地被拆后势 ×M，塌陷记入死亡所在窗而非堆在末个样本）。
 * 只改 ② 不改 ① 时终局对账会精确抵消门控（Ng et al. 势塑形不变性），总回报不变。
 * 评估口径 godai-score.ts 保持纯 v7 不动——那是 God-AI 全部基线的可比性基准。
 *
 * Telemetry 采集语义与 `simulation-runner.ts` 逐字段一致（采样节拍 6 tick、
 * BASE_PRESSURE_RADIUS=12、powerup census 的 same-tick 对账、事件判据
 * tank_destroyed/bullet_fired/powerup_collected），因此每局的维度指标与
 * godai-score 对同一局打分完全可互换。
 *
 * 输出 shards（每局一个目录，npy + manifest；行宽 SSOT = 编码器常量
 * OBS_CHANNELS/BOARD/SCALAR_DIM，加/减通道只改编码器，禁止回字面量）：
 *   obs (N,C,H,W) u1 | scalars (N,S) f4 | a_x / lp_x / value (N,) | done | mask (N,7)
 * manifest 记录 outcome/ticks + score/quality + 全部 11 维 dims{value,raw}
 * ——训练侧与事后分析可直接用同一套数字。
 *
 * Usage:
 *   bun tools/sim/export-rl-rollout.ts --weights tmp/rl-weights/weights.json \
 *       --out tmp/rl-traj/it1 --stages 0-3 --seeds 0-3 --max-ticks 12000
 *
 * 起始分布（plan/x20-state-init.plan.md P1）：`--init-snapshot <file>` 把起始世界换成人类 demo
 * 在 tick T 的快照（单局专用：一个快照对应一局）。缺省不给 = 行为逐字节不变。
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { allEnemiesCleared } from '../../src/game/SimulationEffects'
import { isStuckTick, playerCenterCell } from '../../src/game/stuck-detect'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, ENEMIES_PER_STAGE, BASE_POS, CELL, GRID, TANK } from '../../src/constants'
import { type Direction } from '../../src/constants'
import {
  ObsEncoder,
  computeMasks,
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  BOARD,
  SCALAR_DIM,
} from '../../src/nn/obs-encoder'
import {
  isArenaId,
  resolveArenaStage,
  arenaLevelOfId,
  stageLayoutHash,
} from '../../src/nn/arena-ladder'
import { decodeStageGrid } from '../../src/nn/config-stage'
import { buildModelFromText } from '../../src/nn/infer'
import { MOVE_DIM, decodeMove, encodeMove } from '../../src/nn/action-space'
import { featuresEngine } from '../../src/nn/conv/conv'
import { dodgeL0 } from '../../src/nn/dodge-l0'
import {
  DANGER_HP_THRESHOLD,
  DMG_FIRST_WINDOW_TICKS,
  NEAR_ENEMY_BAND_PX,
  alignedEnemyCount,
  cornerWeightTick,
  damageClusterStats,
  enclWeightTick,
  enemyBulletLaneWeightTick,
  exposureExempt,
  inThreatLane,
  nearestEnemyDistPx,
  playerHpRatio,
  threatLaneExempt,
} from '../../src/nn/danger-metrics'
import { createShieldWindow } from '../../src/nn/shield-window'
import {
  HIT_BACK,
  HIT_FAR,
  HIT_SIDE,
  centerCellKey,
  classifyHit,
  fireOriginCellKey,
  keyCol,
  keyRow,
} from '../../src/nn/hit-geometry'
import {
  registerEnemyAimShot,
  registerPlayerAimShot,
  takeAimShot,
  type AimShotRec,
  type AimShotTable,
} from './aim-shot-registry'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { npyBytes } from '../../src/nn/npy'
import {
  DEFAULT_DECISION_K,
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
  decisionReadout,
  poolDecisionReadouts,
  type DecisionReadout,
} from '../../src/nn/decision-gate'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import type { WorldSnapshot } from '../../src/snapshot/types'
import { worldTickHash } from '../../src/replay/tickHash'
import { basename } from 'path'
import { buildPack } from './pack-container'
import { runServe } from './serve-loop'
import {
  scoreRun,
  V7_SCORE_CONFIG,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const MAX_TICKS = 36000
// K 的单一来源（plan/new-era-stop.plan.md §6 R2）：旧字面量 10 保留为别名，语义不变。
const K = DEFAULT_DECISION_K
const FIRE_DIM = 2
export const MASK_DIM = MOVE_DIM + FIRE_DIM // 7 (v2: item head removed)

// shard 文件名清单（与 writeRlShard 的 writeNpy 调用一一对应；--pack 打容器时按此顺序）。
/** shard 落盘的文件清单（顺序 == manifest 里 files 的顺序；两条 pack 路径共用，测试也拿它当真相源）。 */
export const RL_SHARD_FILES = [
  'obs.npy',
  'scalars.npy',
  'a_move.npy',
  'a_fire.npy',
  'lp_move.npy',
  'lp_fire.npy',
  'value.npy',
  // plan/rl-training-config.md §4.2：奖励不再在 TS 算。落 [N+1,21] f8 指标向量
  // （前 N 行=每决策步计数器快照，第 N+1 行=终局快照），Python 公式引擎算奖励。
  'metrics.npy',
  'done.npy',
  'mask.npy',
] as const

// ---- 41 维指标列序（MUST mirror `nn-training/rl/reward_library.py::METRICS`）----
// 改任一侧必须同步另一侧 + manifest metrics_version 不变则任何 shape[0] 下游
// 会静默错读。idx10=starsCollected 补 plan §4.1 表的空槽（连续编号 0..20）。
// idx21–28=道具流分类型计数（§9，metric v3：spawn/got × bomb/tank/freeze/shield，
// 追加在尾部，老列号不动）。idx29=puSpawnStar（v4：star 供给列，拾取列 idx10 已有）。
// idx30=clearTick（v5：敌人首次全灭的 tick，哨兵 -1 = 本局未清场）。
// idx31–38=分敌种击杀/命中（metrics v6，plan/t5-metrics-v6：kills/hits ×
// basic/fast/power/armor，追加在尾部，0–30 列号永久不动）。列序 = ENEMY_KIND_ORDER。
// idx39=puGotOther（metrics v7，plan/pickup-shaping.plan.md Phase 0 顺手项）：
//   `puGot*` 四桶只认 bomb/tank/freeze/shield，而 fence/boat/repair/emp/decoy/mine/
//   guard/frenzy/sacrifice/rewind 等类型拾取后四桶全零（x5⑩「puGot* 全零输出 bug」，
//   根因 = 未分桶的类型走输入侧却无输出侧桶）；残差桶让
//   `powerUpsCollected ≡ starsCollected + puGot{4} + puGotOther` 恒成立（可测守恒）。
//   不进任何公式（"不进训练变量"）。
// idx40=pickupDist（metrics v7 头牌列，plan/pickup-shaping.plan.md §3）：每决策步玩家
//   到最近**存活**拾取（powerUp.alive）中心格的曼哈顿距离；无存活拾取或玩家不在场时
//   填哨兵 `PICKUP_DIST_SENTINEL`（-1，公式侧用 where 归零）。势能法趋近塑形项的量纲。
// idx41–44=危险暴露四列（metrics v8，plan/x20-dodge-avoidance.plan.md §2）：
//   playerHpRatio（hp/maxHp，clamp01，与 obs s19 同源）· dangerTicks（累计 hpRatio<0.4 的
//   tick）· threatTicks（累计「在敌方弹道/炮口线上」的 tick，口径**冻结**在
//   `src/nn/danger-metrics.ts`）· dmgFirst600（tick<600 的累计承伤；`player_damage`
//   事件本就不含致死一击，见 `SimulationCombat.ts:604-609`）。与 v7 同规：**不进任何
//   现存公式**（只加观测、不改公式），但 bump METRICS_VERSION ⇒ 旧 v7 语料不兼容
//   （加载期按行宽/版本响亮报错，不静默错读）。
// idx45–49=命中方位计数（metrics v9，plan/geo-threat-instrumentation.plan.md §1.1）：
//   backHits / sideHits / frontHitsExempt / farHits / geoFallback。分类器唯一实现 =
//   `src/nn/hit-geometry.ts::classifyHit`（纯逻辑、真值表单测）。
//   · 几何取**开火时刻**的 shooter 格 + **命中时刻**的受害车快照（`enemy_hit`
//     新增 targetId/targetDir/targetX/targetY/bulletId，加法只读字段）；
//   · `farHits` = 轴向开火**物理不可能命中**的份额（开火后目标自己走进弹道），只作监视、
//     不定价。⚠ 它**不是**「非瞄准份额」的全量：地雷/牺牲等爆炸类击杀不推 `enemy_hit`
//     （全仓唯一推点 = `SimulationCombat.bulletHitsTank`）⇒ 那些击杀不落任何方位桶；
//   · **普通 front 不单列**：frontHits ≡ enemyHits − back − side − frontHitsExempt − far
//     （可测守恒；跨局对账用它，不要另立第 10 列）；
//   · `geoFallback` = bulletId 反查不到开火记录的命中数（state-init 交棒时已在飞的弹），
//     不进 front 推导，跨局对账时单独列示。
//   · 击杀**不单列**：击中即按同几何计同样激励，wKill 照旧在终点结算（plan §1.1）。
// idx50–53=穿越税观测族（metrics v9，plan §1.3）：onLaneTicks（raw 永真）·
//   onLaneExemptTicks（lane ∧ 冻/盾，**加法列**，不是从 raw 里扣）· onLaneMoveTicks
//   （在线且本 tick 中心格变化）· onLaneHoldFireTicks（在线且静止且本 tick 开火输出）。
//   公式侧净价 = `-w*d(onLaneTicks) + w*d(onLaneExemptTicks)`；四个都是累计 tick
//   计数器（Φ 逐行差分 ⇒ 直接入公式 = 每 tick 罚款且 Φ 无界，须封顶或改深度型势）。
// idx54–68=「差距四族」15 列（metrics v10，plan/metrics-v10-gap-columns.plan.md §1）：
//   54 stopTicks（本 tick **无移动输出**的累计；动作头 idx0 = STOP；评估侧同义 `getMoveDirection() === null`）·
//   55 fireHeldTicks（本 tick **开火输出为真**的累计；= `fireHeld` 的 tick 累计，不是实弹）·
//   56 idleTicks（累计 `player.moving === false`；想动被挡也计，与 export-eval-game 既有 `idleTicks` 同义）·
//   57 enemyDist（**每行采样**：最近存活**已激活**敌车中心距（px）；无车/玩家不在场 = 哨兵 -1）·
//   58 nearEnemy4Ticks（累计最近敌距 ≤ `NEAR_ENEMY_BAND_PX`
//      （4 格 = 64px，pin 值）的 tick）·
//   59 damageBursts（相邻两笔扣血间隔 ≤ 120t 的次数；`damageClusterStats`）·
//   60 maxDamage120（任意以某笔为起点的 120t 滑窗内最大累积承伤；同上，单调）·
//   61 damageWhileLow（扣血那一刻 `playerHpRatio < 0.4` 的那部分伤害累计；
//      份额 = 本列 ÷ playerDamageTaken，离线可算）·
//   62–64 encl1Ticks/encl2Ticks/encl3pTicks（同拍与玩家**同行/同列**的敌车数
//      = 1 / = 2 / ≥ 3 的 tick 累计；谓词 = `src/nn/danger-metrics.ts::alignedEnemyCount`，
//      19px 物理带 + 轴无遮挡 + 已激活；互斥划分）·
//   65 enclMax（本局同拍包围敌数**峰值**；每行同值，单调不减；v10 批次内顺位自 idx68）。
// v10 批次内整理（未签入，允许）：移除 enclExempt1/2/3pTicks 三列；enclMax 顺位 68→65。
// idx66–73 ＝ aim-dodge-levers 8 列（plan/aim-dodge-levers.plan.md §4；v10 批次内追加）：
//   66 aimHits（玩家弹命中，含致死；开火结果族；**回写列**——信用落开火决策步）·
//   67 aimHitDistSum（Σ 命中距离（格）：开火拍玩家中心格 → 命中拍受害车中心格切比雪夫）·
//   68 aimBricks（真拆砖/破钢；`terrain_destroyed.bulletId`；一弹多格只落一次）·
//   69 aimIgnited（弹弹对消，`bullet_cancelled`；只观测）·
//   70 aimMisses（打钢未破/出界/基地/局末在飞；≡ hit 镜像，勿同腿）·
//   71 hurtWeight（敌弹火线承伤 Σ(6−x)，x≤5；结算于 player_damage/player_hit；**回写列**）·
//   72 enclWeightTicks（n≥2 ⇒ Σ max(0,5−d)；**拌入**豁免 A）·
//   73 cornerWeightTicks（四角锚点 Σ max(0,4−d)；**拌入**豁免 A）。
//   恒等式：playerShots = aimHits+aimBricks+aimIgnited+aimMisses（局末在飞归 miss）。
//   量纲：54–56/58/62–64/72–73 是累计 tick 计数器（Φ 逐行差分 ⇒ 直接入公式 = 每 tick
//   罚款且 Φ 无界）；59/60/65 单调增（差分 ≥ 0）；57 带 -1 哨兵，公式侧必须 `where` 归零；
//   66–71 是**回写列**（开火拍信用；不得当逐窗口速率用）。
// **本常量必须与 `buildMetricsRow` 的行宽一致** —— 2026-09-12 的 P0：只改了行、没改
// 这里，`metrics.set(row, i * METRICS_DIM)` 每局越界抛 RangeError，整条采集腿零产出。
// 导出仅供测试断言行宽（tests/export-rl-rollout-metrics.test.ts）。
export const METRICS_DIM = 74
/** metrics v10：差距四族 15 列 + aim-dodge 批次 8 列（idx54–73）。与 Python METRICS_VERSION 同步。 */
export const METRICS_VERSION = 10
/**
 * 列名（顺序 = `buildMetricsRow` 的实现顺序，SSOT）。Python `reward_library.METRICS`
 * 必须与本数组**逐位相等**（跨语言列序 oracle，sb P2-D；tests/export-rl-rollout-metrics.test.ts）。
 * 只做顺序/命名的对账 —— 数值语义仍以 `buildMetricsRow` 为准。
 */
/**
 * ⚠ **raw-only 名单**（plan/aim-dodge-levers.plan.md §3.6，**仅审计，不得定价**）：
 * `encl1Ticks`/`encl2Ticks`/`encl3pTicks`、`nearEnemy4Ticks`、`onLaneMoveTicks`、
 * `onLaneHoldFireTicks` —— 它们是未豁免的 raw 累计（含冻/盾拍），与拌入列
 * （`enclWeightTicks`/`cornerWeightTicks`/`hurtWeight`，豁免 A）**不得直接对照**；
 * 定价请用拌入列。v9 已签入列的 raw/豁免对（`onLaneTicks`/`onLaneExemptTicks`）不受影响。
 */
export const METRICS_COLUMN_NAMES: readonly string[] = [
  'ticks',
  'kills',
  'lives',
  'playerHits',
  'playerDamageTaken',
  'playerShots',
  'enemyHits',
  'powerUpsCollected',
  'powerUpsSpawned',
  'stuckTicks',
  'starsCollected',
  'baseAlive',
  'baseWallTotal',
  'baseWallIntact',
  'basePressureSum',
  'basePressureSamples',
  'firstKillTick',
  'playerDeaths',
  'cellsVisited',
  'playerLevel',
  'enemyTotal',
  'puSpawnBomb',
  'puSpawnTank',
  'puSpawnFreeze',
  'puSpawnShield',
  'puGotBomb',
  'puGotTank',
  'puGotFreeze',
  'puGotShield',
  'puSpawnStar',
  'clearTick',
  'killsBasic',
  'killsFast',
  'killsPower',
  'killsArmor',
  'hitsBasic',
  'hitsFast',
  'hitsPower',
  'hitsArmor',
  'puGotOther',
  'pickupDist',
  'playerHpRatio',
  'dangerTicks',
  'threatTicks',
  'dmgFirst600',
  'backHits',
  'sideHits',
  'frontHitsExempt',
  'farHits',
  'geoFallback',
  'onLaneTicks',
  'onLaneExemptTicks',
  'onLaneMoveTicks',
  'onLaneHoldFireTicks',
  'stopTicks',
  'fireHeldTicks',
  'idleTicks',
  'enemyDist',
  'nearEnemy4Ticks',
  'damageBursts',
  'maxDamage120',
  'damageWhileLow',
  'encl1Ticks',
  'encl2Ticks',
  'encl3pTicks',
  'enclMax',
  'aimHits',
  'aimHitDistSum',
  'aimBricks',
  'aimIgnited',
  'aimMisses',
  'hurtWeight',
  'enclWeightTicks',
  'cornerWeightTicks',
]
/**
 * pickupDist 哨兵：无存活拾取（或玩家不在场）时填此值 —— 与 firstKillTick/clearTick
 * 的 -1 哨兵同构。真实曼哈顿距离恒 ≥0（同格 = 0），-1 不可能与真实值混淆。
 * 势能侧 Φ 不直接消费哨兵：公式统一 `where(pickupDist < 0, 0, pickupDist)` 归零。
 */
export const PICKUP_DIST_SENTINEL = -1
/** 分敌种计数的固定顺序（与 export-eval-game.ENEMY_KIND_ORDER 同契约）。 */
export const ENEMY_KIND_ORDER = ['basic', 'fast', 'power', 'armor'] as const

/** kind → ENEMY_KIND_ORDER 下标；非敌车 kind 返回 -1（零分配热路径，AGENTS §14）。 */
export function enemyKindIndex(kind: string): number {
  switch (kind) {
    case 'basic':
      return 0
    case 'fast':
      return 1
    case 'power':
      return 2
    case 'armor':
      return 3
    default:
      return -1
  }
}

// F3：基地失守局终局 score ×= BASE_LOSS_MULT。旧值 0.25 让「投降」太便宜——
// it1–it68 审计发现 agent 卡在「会动不会守家」局部最优（eval base_destroyed 占
// ~80%、baseIntegrity 恒 0），根因之一就是守家梯度太弱 + 失守代价太低。降至 0.1：
// 基地失守时势 Φ ×0.1（→ 失守瞬间负势差放大），把「守住基地」变成第一顺位目标。
// F3b 的「投降坍缩」诱因（lives 高权重 + base_destroyed 保留 lives）已由
// RL_LOSS_WEIGHTS.lives=0 消除，故此处可放心收紧而不复发。
const BASE_LOSS_MULT = 0.1
// F3b：RL 奖励的败局带剔除 lives。败局里的「剩余生命」只可能出现在 base_destroyed
// 局（lives_exhausted 局 lives=0）——它支付的正是「基地死时自己没死」的投降画像，
// 且与打仗行为负相关（交战才有阵亡风险），是坍缩的主要收入源（0.256×1.0/0.991）。
// 评估口径 godai-score.ts 的 DEFAULT_LOSS_WEIGHTS 保持原值不动（God-AI 基线可比性）。
//
// R6（2026-08-25 训练质量审计）：重做 RL 败局带权重——原值 progress 0.477 占 64% 有效
// 权重、基地防守（baseIntegrity 0.17 + baseSafety 0.044）仅 29%，奖励势几乎只追击杀数，
// 对「守家/拦截」给不出梯度。现改为基地防守 50%（baseIntegrity 0.25 + baseSafety 0.25，
// 其中 baseSafety=1−mean(basePressure) 是每 6 tick 采样的密集信号）+ progress 30% +
// 补回 accuracy（原 DEFAULT_LOSS_WEIGHTS 无 accuracy 键 → lossPartialQ 静默丢弃）。
// 显式列出而非展开 DEFAULT_LOSS_WEIGHTS：权重是「守家优先」的人类先验，与 God-AI
// 评估口径解耦，后续调权不必惊动评估基线。
const RL_LOSS_WEIGHTS: Weights = {
  progress: 0.3,
  baseIntegrity: 0.25,
  baseSafety: 0.25,
  tempo: 0.08,
  accuracy: 0.06,
  openingTempo: 0.03,
  loot: 0.03,
}
// RL 专用打分配置：v7 带几何 + 守家优先的败局带。weightedQuality 对 w<=0 维度
// 整体剔除（分子分母都不计），与 null-剔除语义一致。
const RL_SCORE_CONFIG: ScoreConfig = { ...V7_SCORE_CONFIG, lossWeights: RL_LOSS_WEIGHTS }
// 与 simulation-runner 相同的 telemetry 节拍/半径（对齐的前提）
const TELEMETRY_SAMPLE_TICKS = 6
const BASE_PRESSURE_RADIUS = 12

/** 受控输入：实现 InputLike，把采样动作施加到 World（B案：0 = STOP，1..4 = 方向）。 */
class ScriptedInput {
  moveDir: Direction | null = null
  firing = false

  setAction(move: number, fire: number): void {
    this.moveDir = decodeMove(move)
    this.firing = fire === 1
  }

  getMoveDirection(): Direction | null {
    return this.moveDir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {
    // no per-tick pulses in v2
  }
  reset(): void {
    this.moveDir = null
    this.firing = false
  }
}

interface RolloutModel {
  forward(obs: Uint8Array, scalars: Float32Array): void
  readonly moveLogits: Float32Array
  readonly fireLogits: Float32Array
  readonly valueOut: Float32Array
}

// ---- per-game telemetry（语义逐字段对齐 simulation-runner）----
export interface Telemetry {
  enemyTotal: number
  startLives: number
  playerDeaths: number
  /** 玩家被命中次数（player_hit 事件：死亡 + 星盾消耗）。玩具奖励的 w_dmg 项。 */
  playerHits: number
  /** 非致命扣血累计（player_damage 事件 damage 累计，§2.4）。 */
  playerDamageTaken: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  starsCollected: number
  /** 分类型掉落/拾取（道具流遥测，§9）：spawn-side 经 census 按 pu.type 计数，
   * pickup-side 经 powerup_collected 事件按 e.powerUp 计数（census 对账余量
   * 归不清类型的那笔，沿用既有保守口径）。列序见 metricsRow 尾部 idx 21–28。 */
  puSpawnBomb: number
  puSpawnTank: number
  puSpawnFreeze: number
  puSpawnShield: number
  puGotBomb: number
  puGotTank: number
  puGotFreeze: number
  puGotShield: number
  /** 残差桶（metrics v7）：拾取类型不在 {star,bomb,tank,freeze,shield} 的收集数
   * （fence/boat/repair/emp/decoy/mine/guard/frenzy/sacrifice/rewind）——
   * 让 `powerUpsCollected ≡ stars + 四桶 + 本桶` 恒成立（x5⑩ 全零 bug 的补桶）。 */
  puGotOther: number
  /** star 掉落数（c4 基线发现 star 供给不可测：拾取有列、掉落无列） */
  puSpawnStar: number
  baseWallTotal: number
  baseWallIntact: number
  basePressureSum: number
  basePressureSamples: number
  cellsVisited: Set<number>
  firstKillTick: number | undefined
  /** 敌人**首次全灭**的 tick（`allEnemiesCleared` 首次为真）；undefined = 本局未清场。
   *  2026-09-12 新增：敌人全灭后若场上还有道具会进 BONUS TIME 窗口（≈600 tick）才
   *  stage_clear，而 max_ticks 可能在窗口结束前截断。reward 侧需要「清场 tick」才能
   *  只豁免该窗口的 tick 惩罚（此前只能用 `min(ticks, wTickFree)` 近似）。
   *  与 firstKillTick 同构：标量、每行 metrics 写同值、未成立时哨兵 -1。 */
  clearTick: number | undefined
  /** 命中敌方累计（enemy_hit 事件数，含致死命中）。 */
  enemyHits: number
  /** 分敌种玩家击杀（tank_destroyed by=player，kind ∈ ENEMY_KIND_ORDER）。metrics v6。 */
  killsByKind: [number, number, number, number]
  /** 分敌种命中（enemy_hit.targetKind）。metrics v6；含致死命中。 */
  hitsByKind: [number, number, number, number]
  /** 连续「原地 + 未命中」tick 数。 */
  stuckTicks: number
  // ---- metrics v8：危险暴露（plan/x20-dodge-avoidance.plan.md §2）----
  /** 累计 `hpRatio < DANGER_HP_THRESHOLD` 的 tick（仅玩家存活时计）。 */
  dangerTicks: number
  /** 累计「在敌方弹道/炮口线上」的 tick（仅玩家存活时计）。
   *  ⚠ v9 起**谓词已改定义**（19px 带 + 无半径上限 + 判墙体遮挡）⇒ 与 v8 读数
   *  不可比（plan §1.2 祖父条款）。 */
  threatTicks: number
  /** tick < `DMG_FIRST_WINDOW_TICKS` 的累计承伤（致死一击本就不在 player_damage 里）。 */
  dmgFirst600: number
  // ---- metrics v9：命中方位计入（plan/geo-threat-instrumentation.plan.md §1.1）----
  /** 背刺命中（受害者身后开火）。 */
  backHits: number
  /** 侧击命中（含同格贴身 + 近身对角 scrum）。 */
  sideHits: number
  /** 正面命中**但冻/盾豁免**（aimed 域非 far 一律进此桶；公式侧按 back 价给）。 */
  frontHitsExempt: number
  /** 轴向开火物理不可能命中的份额（开火后目标自己走进弹道）；只观测、不定价。
   *  ⚠ 不含爆炸类击杀（地雷/牺牲不走 `enemy_hit`）⇒ 不是「非瞄准份额」的全量。 */
  farHits: number
  /** bulletId 反查不到开火记录的命中数（state-init 交棒时已在飞的弹）。 */
  geoFallback: number
  // ---- metrics v9：穿越税观测族（plan §1.3）----
  /** 在线 tick 累计（raw，永真；含豁免拍）。 */
  onLaneTicks: number
  /** 在线 ∧ 冻/盾 的 tick 累计（raw 的**子集**，加法列）。 */
  onLaneExemptTicks: number
  /** 在线 ∧ 本 tick 中心格变化。 */
  onLaneMoveTicks: number
  /** 在线 ∧ 静止 ∧ 本 tick 开火输出（架枪直读，反证门用）。 */
  onLaneHoldFireTicks: number
  // ---- metrics v10：差距四族（plan/metrics-v10-gap-columns.plan.md §1）----
  /** 累计「本 tick 无移动输出」（动作头 idx0 = STOP，held 到下一决策步）。 */
  stopTicks: number
  /** 累计「本 tick 开火输出为真」（fire head held；非 `bullet_fired` 实弹）。 */
  fireHeldTicks: number
  /** 累计 `player.moving === false`（想动被地形挡也计）。 */
  idleTicks: number
  /** 累计「最近敌距 ≤ `NEAR_ENEMY_BAND_PX`」的 tick（玩家存活 ∧ 有已激活敌车）。 */
  nearEnemy4Ticks: number
  /** 扣血那一刻 `playerHpRatio < DANGER_HP_THRESHOLD` 的伤害累计（份额分母 = playerDamageTaken）。 */
  damageWhileLow: number
  /** 扣血事件 tick（升序，事件属 t-1；`damageClusterStats` 用它算 bursts/max120）。 */
  damageTicks: number[]
  /** 扣血事件伤害量（与 `damageTicks` 同长同序）。 */
  damageAmounts: number[]
  /** 同拍包围敌车数 = 1 / = 2 / ≥ 3 的 tick 累计（互斥划分）。 */
  encl1Ticks: number
  encl2Ticks: number
  encl3pTicks: number
  /** 本局同拍包围敌数峰值（每行同值，单调不减；v10 批次内顺位 idx65）。 */
  enclMax: number
  // ---- aim-dodge-levers：开火结果族 + 承伤族 + 暴露杠杆（plan/aim-dodge-levers.plan.md §4）----
  /** 玩家弹命中（含致死）；**回写列**（信用落开火决策步）。恒等式左端 = playerShots。 */
  aimHits: number
  /** Σ 命中距离（格；开火拍玩家中心格→命中拍受害车中心格，切比雪夫）。 */
  aimHitDistSum: number
  /** 真拆砖/破钢数（`terrain_destroyed.bulletId`；一弹多格只结算一次）。rung 空场恒 0。 */
  aimBricks: number
  /** 弹弹对消数（`bullet_cancelled`；只观测）。 */
  aimIgnited: number
  /** 未命中且未破坏数（打钢未破/出界/基地/局末在飞；≡ hit 镜像）。 */
  aimMisses: number
  /** 敌弹火线承伤 Σ max(0,6−x)（x≤5；结算于击伤拍；**回写列**；A 拌入）。 */
  hurtWeight: number
  /** 被包围距离加权 tick 累计（n≥2 ⇒ Σ max(0,5−d)；**拌入**豁免 A）。 */
  enclWeightTicks: number
  /** 角落距离加权 tick 累计（Σ max(0,4−d)，d≤3；**拌入**豁免 A）。 */
  cornerWeightTicks: number
}

function countBaseWall(world: World): number {
  // 与 runner 的 countBaseWall 同一环格定义（8 格保护圈，brick/steel 算完整）。
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let n = 0
  for (let col = bc - 1; col <= bc + 2; col++) {
    if (isSolid(world, col, br - 1)) n++
  }
  for (let row = br; row <= br + 1; row++) {
    if (isSolid(world, bc - 1, row)) n++
    if (isSolid(world, bc + 2, row)) n++
  }
  return n
}

function isSolid(world: World, col: number, row: number): boolean {
  const t = world.tileMap.get(col, row)
  return t === 'brick' || t === 'steel'
}

/** aim-dodge-levers 新列下标（与 `buildMetricsRow` / `METRICS_COLUMN_NAMES` 同序）。 */
export const AIM_COL = {
  hits: 66,
  dist: 67,
  bricks: 68,
  ignited: 69,
  misses: 70,
  hurt: 71,
  encl: 72,
  corner: 73,
} as const

/** 回写补丁（内部/测试共用形状）：`[from, to)` 行区间内 `rows[i][col] += delta`。 */
export interface SuffixPatch {
  col: number
  from: number
  to: number
  delta: number
}

/**
 * 回写补丁应用（纯函数；plan/aim-dodge-levers.plan.md §3.4/§7.1）。
 *
 * 区间补丁 = 「后缀累加」的等价位实现：`[from, to)` 是结算时**已经推出去**的行
 * （开火行之后 … 结算行），这些行补 delta；其后的行由 `buildMetricsRow` 读 tel
 * （结算时已 +delta）自然携带。打包前调用一次 ⇒ 开火行之后的所有行都含该结算，
 * `r[fireRow] = Φ[fireRow+1] − Φ[fireRow]` 拿到信用（§3.4）。
 *
 * `to` 允许超出 `rows.length`（结算晚于终局行边界的历史补丁）⇒ 内部 clamp，不抛不越界。
 * 纯函数：只改 `rows[i][col]`，不读 world / RNG（回写不进 `tickHash`，§3.4）。
 */
export function applySuffixPatches(rows: number[][], patches: ReadonlyArray<SuffixPatch>): void {
  const n = rows.length
  for (let pi = 0; pi < patches.length; pi++) {
    const p = patches[pi]
    const end = p.to < n ? p.to : n
    for (let i = p.from; i < end; i++) rows[i][p.col] += p.delta
  }
}

/**
 * 当步玩家到最近**存活**拾取的曼哈顿距离（格）。metrics v7（pickup-shaping §3）：
 * - 距离口径 = 中心格曼哈顿（`floor((x+w/2)/CELL)`），与 census/telemetry 同构；
 * - 玩家不在场（`?alive` 为假/无 player）⇒ 哨兵 `PICKUP_DIST_SENTINEL`；
 * - 场上有存活拾取但玩家活着 ⇒ 真距离（同格 = 0）；
 * - 无存活拾取（含阵亡玩家）⇒ 哨兵（公式侧 `where` 归零，见课程公式）。
 * 热路径（AGENTS §14.1）：零分配 —— 只读迭代 `world.powerUps`，无 filter/map/Set。
 * 防御性 `pu.alive` 检查：dead 拾取可能在 _cleanup 前残留于数组（与 census 同口径）。
 */
export function nearestPickupDist(world: World): number {
  const p = world.player
  if (!p || !p.alive) return PICKUP_DIST_SENTINEL
  const pcol = Math.floor((p.x + p.w / 2) / CELL)
  const prow = Math.floor((p.y + p.h / 2) / CELL)
  let best = PICKUP_DIST_SENTINEL // -1 兼作「无存活拾取」记号
  const pus = world.powerUps
  for (let i = 0; i < pus.length; i++) {
    const pu = pus[i]
    if (!pu.alive) continue
    const col = Math.floor((pu.x + pu.w / 2) / CELL)
    const row = Math.floor((pu.y + pu.h / 2) / CELL)
    const d = Math.abs(col - pcol) + Math.abs(row - prow)
    if (best < 0 || d < best) best = d
  }
  return best
}

/**
 * 单行 metrics 快照（列序 = 文件头 `METRICS_DIM` 的 SSOT 实现）。
 *
 * 语义 = 旧 countersPhi 的同刻状态（tick t 决策前）；行宽必须恒为 `METRICS_DIM`。
 * 提为模块级导出函数（而非 `runOne` 内的闭包）只为让测试能直接断言行宽 —— 这条
 * 断言正是 2026-09-12 那次「加列忘改 `METRICS_DIM`」P0 的护栏。
 */
export function buildMetricsRow(t: number, world: World, tel: Telemetry): number[] {
  tel.baseWallIntact = countBaseWall(world) // 与旧 countersPhi 同侧效应
  // 伤害成簇（v10）：两类读数都由事件序列一次算出（单调增，零分配；见 danger-metrics）。
  const rowDamage = damageClusterStats(tel.damageTicks, tel.damageAmounts)
  return [
    t, // 0 ticks
    world.killCount, // 1 kills
    world.lives, // 2 lives
    tel.playerHits, // 3 playerHits
    tel.playerDamageTaken, // 4 playerDamageTaken
    tel.playerShots, // 5 playerShots
    tel.enemyHits, // 6 enemyHits
    tel.powerUpsCollected, // 7 powerUpsCollected
    tel.powerUpsSpawned, // 8 powerUpsSpawned
    tel.stuckTicks, // 9 stuckTicks
    tel.starsCollected, // 10 starsCollected
    world.tileMap.isBaseDestroyed() ? 0 : 1, // 11 baseAlive
    tel.baseWallTotal, // 12 baseWallTotal
    tel.baseWallIntact, // 13 baseWallIntact
    tel.basePressureSum, // 14 basePressureSum
    tel.basePressureSamples, // 15 basePressureSamples
    tel.firstKillTick === undefined ? -1 : tel.firstKillTick, // 16 firstKillTick（哨兵 -1）
    tel.playerDeaths, // 17 playerDeaths
    tel.cellsVisited.size, // 18 cellsVisited
    world.playerLevel, // 19 playerLevel
    tel.enemyTotal, // 20 enemyTotal
    tel.puSpawnBomb, // 21 puSpawnBomb
    tel.puSpawnTank, // 22 puSpawnTank
    tel.puSpawnFreeze, // 23 puSpawnFreeze
    tel.puSpawnShield, // 24 puSpawnShield
    tel.puGotBomb, // 25 puGotBomb
    tel.puGotTank, // 26 puGotTank
    tel.puGotFreeze, // 27 puGotFreeze
    tel.puGotShield, // 28 puGotShield
    tel.puSpawnStar, // 29 puSpawnStar
    tel.clearTick === undefined ? -1 : tel.clearTick, // 30 clearTick（v5；-1 = 未清场）
    tel.killsByKind[0], // 31 killsBasic
    tel.killsByKind[1], // 32 killsFast
    tel.killsByKind[2], // 33 killsPower
    tel.killsByKind[3], // 34 killsArmor
    tel.hitsByKind[0], // 35 hitsBasic
    tel.hitsByKind[1], // 36 hitsFast
    tel.hitsByKind[2], // 37 hitsPower
    tel.hitsByKind[3], // 38 hitsArmor
    tel.puGotOther, // 39 puGotOther（v7：四桶外拾取残差，x5⑩ 全零 bug 补桶）
    nearestPickupDist(world), // 40 pickupDist（v7：最近存活拾取中心格曼哈顿距离，哨兵 -1）
    playerHpRatio(world), // 41 playerHpRatio（v8：hp/maxHp，clamp01；玩家不在场 = 0）
    tel.dangerTicks, // 42 dangerTicks（v8：累计 hpRatio<0.4 的 tick）
    tel.threatTicks, // 43 threatTicks（v9 改定义：19px 带 + 无半径 + 判遮挡）
    tel.dmgFirst600, // 44 dmgFirst600（v8：tick<600 的累计承伤，不含致死一击）
    tel.backHits, // 45 backHits（v9：背刺命中）
    tel.sideHits, // 46 sideHits（v9：侧击命中，含同格 + scrum）
    tel.frontHitsExempt, // 47 frontHitsExempt（v9：正面的冻/盾豁免桶）
    tel.farHits, // 48 farHits（v9：轴向打不到的炸弹/流弹份额，不定价）
    tel.geoFallback, // 49 geoFallback（v9：开火记录反查失败数，不进 front 推导）
    tel.onLaneTicks, // 50 onLaneTicks（v9：在线 tick raw）
    tel.onLaneExemptTicks, // 51 onLaneExemptTicks（v9：raw 的冻/盾子集）
    tel.onLaneMoveTicks, // 52 onLaneMoveTicks（v9：在线且中心格变化）
    tel.onLaneHoldFireTicks, // 53 onLaneHoldFireTicks（v9：在线且静止且开火）
    tel.stopTicks, // 54 stopTicks（v10：本 tick 无移动输出的累计）
    tel.fireHeldTicks, // 55 fireHeldTicks（v10：本 tick 开火输出为真）
    tel.idleTicks, // 56 idleTicks（v10：player.moving === false）
    nearestEnemyDistPx(world), // 57 enemyDist（v10：最近已激活敌车距离 px；哨兵 -1）
    tel.nearEnemy4Ticks, // 58 nearEnemy4Ticks（v10：最近敌距 ≤ 4 格）
    rowDamage.bursts, // 59 damageBursts（v10：相邻扣血 ≤120t 的次数）
    rowDamage.max120, // 60 maxDamage120（v10：120t 滑窗最大承伤）
    tel.damageWhileLow, // 61 damageWhileLow（v10：低血期承伤；份额分母 = idx4）
    tel.encl1Ticks, // 62 encl1Ticks（v10：同拍同行/同列敌车 = 1）
    tel.encl2Ticks, // 63 encl2Ticks（v10：= 2）
    tel.encl3pTicks, // 64 encl3pTicks（v10：≥ 3）
    tel.enclMax, // 65 enclMax（v10 批次内顺位自 68）
    tel.aimHits, // 66 aimHits（aim-dodge：命中，含致死；回写列——补丁见 §3.4）
    tel.aimHitDistSum, // 67 aimHitDistSum（aim-dodge：Σ 命中距离（格））
    tel.aimBricks, // 68 aimBricks（aim-dodge：真拆砖/破钢）
    tel.aimIgnited, // 69 aimIgnited（aim-dodge：对消；只观测）
    tel.aimMisses, // 70 aimMisses（aim-dodge：未命中一律 miss，含局末在飞）
    tel.hurtWeight, // 71 hurtWeight（aim-dodge：敌弹火线承伤；回写列）
    tel.enclWeightTicks, // 72 enclWeightTicks（aim-dodge：n≥2 ⇒ Σ max(0,5−d)；拌入 A）
    tel.cornerWeightTicks, // 73 cornerWeightTicks（aim-dodge：Σ max(0,4−d)；拌入 A）
  ]
}

function sampleBasePressure(world: World): number {
  if (!world.tileMap.hasBase()) return 0
  let worst = 0
  for (const t of world.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const p = 1 - dist / BASE_PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

// 轻量可复现 PRNG（mulberry32），避免依赖外部 RNG API 差异。
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function sampleCat(
  logits: Float32Array,
  mask: number[] | null,
  rng: () => number,
): { idx: number; logp: number } {
  const n = logits.length
  let max = -Infinity
  for (let i = 0; i < n; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    if (v > max) max = v
  }
  const ps = new Float32Array(n)
  let sum = 0
  for (let i = 0; i < n; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    ps[i] = Math.exp(v - max)
    sum += ps[i]
  }
  for (let i = 0; i < n; i++) ps[i] /= sum
  const u = rng()
  let c = 0
  for (let i = 0; i < n; i++) {
    c += ps[i]
    if (u <= c) return { idx: i, logp: Math.log(ps[i] + 1e-8) }
  }
  return { idx: n - 1, logp: Math.log(ps[n - 1] + 1e-8) }
}

const _logpBuf = new Float32Array(8) // 复用缓冲（§14.2）：logProbAt 与 sampleCat 同 softmax 口径

/** 任意下标在采集策略分布下的 logp（保底层覆盖步记账，§3.5 F3）。 */
function logProbAt(logits: Float32Array, mask: number[] | null, idx: number): number {
  const n = logits.length
  let max = -Infinity
  for (let i = 0; i < n; i++) {
    const v = mask && mask[i] !== 1 ? -1e9 : logits[i]
    if (v > max) max = v
    _logpBuf[i] = v
  }
  let sum = 0
  for (let i = 0; i < n; i++) {
    _logpBuf[i] = Math.exp(_logpBuf[i] - max)
    sum += _logpBuf[i]
  }
  return Math.log(_logpBuf[idx] / sum + 1e-8)
}

export interface ShardData {
  obs: Uint8Array[]
  scalars: Float32Array[]
  aMove: number[]
  aFire: number[]
  lpMove: number[]
  lpFire: number[]
  value: number[]
  /** 每决策步的 41 维指标快照（[N+1][41]：决策行 + 终局行）——reward 的唯一定义源。 */
  metrics: number[][]
  done: number[]
  mask: number[]
  n: number
}

function newShard(): ShardData {
  return {
    obs: [],
    scalars: [],
    aMove: [],
    aFire: [],
    lpMove: [],
    lpFire: [],
    value: [],
    metrics: [],
    done: [],
    mask: [],
    n: 0,
  }
}

// ────────────────────────────────────────────────────────────────────────────
// 起始分布注入（plan/x20-state-init.plan.md P1；银行 = tools/sim/build-state-init-bank.ts）
//
// 一局仍是标准 `(stage, seed)` 游戏，但**起始世界**换成人类 demo 在 tick T 的快照（人类真到达过
// 的状态），T 之后交棒给策略。为什么是快照而不是「人类磁带快进」（plan §2 B1/B2/B5/B6）：输入
// 重建出来的状态不是人类的状态（idle 语义、RNG 分叉、FF 段可能直接死亡），而快照把它变成
// 一个纯注入问题。奖励不受影响：Φ 差分（Python 侧）对「继承的进度」天然不付钱。
// ────────────────────────────────────────────────────────────────────────────

/** 银行快照文件（`build-state-init-bank.ts` 产物；`snapshot` = `cloneWorld` 原样）。 */
export interface InitSnapshotFile {
  version: number
  id: string
  stage: number
  demoSeed: number
  tick: number
  /** 录像记录链上该 tick 的 tickHash（银行构建期逐点核过 ⇒ 这是外部证据）。 */
  tickHash: string
  /** 人类在该切点的行为侧面（分析用；不参与玩法）。 */
  human?: Record<string, number>
  snapshot: WorldSnapshot
  /** 来自磁盘的路径（诊断用，不存在于文件里）。 */
  path: string
}

/**
 * 读 + 校验快照文件。**任一条不符就抛错**（调用方响亮失败，绝不静默退回标准开局——
 * 「日志说中段起跑、实际跑的是标准开局」是这门课最贵的错）。
 */
export function loadInitSnapshot(
  path: string,
  opts: { stageIdx: number; maxTicks: number; k?: number },
): InitSnapshotFile {
  if (!existsSync(path)) throw new Error(`[init] 快照文件不存在：${path}`)
  let raw: any
  try {
    raw = JSON.parse(readFileSync(path, 'utf8'))
  } catch (e) {
    throw new Error(`[init] 快照不是合法 JSON：${path}（${(e as Error).message}）`)
  }
  if (!raw || typeof raw !== 'object' || !raw.snapshot) {
    throw new Error(`[init] 快照缺 snapshot 字段：${path}`)
  }
  // 跨关借状态是另一个实验（plan §2 B1）：银行局的关必须就是本局的关。
  if (raw.stage !== opts.stageIdx) {
    throw new Error(
      `[init] 快照 stage=${raw.stage} != 本局 stage=${opts.stageIdx}（跨关借状态被拒）`,
    )
  }
  const k = opts.k ?? K
  const tick = raw.tick
  if (!Number.isInteger(tick) || tick <= 0) throw new Error(`[init] 快照 tick 非法：${raw.tick}`)
  if (tick % k !== 0) {
    throw new Error(
      `[init] 交棒 tick ${tick} 不在决策边界上（%${k}）——首个决策前会有 ${tick % k} 个 tick ` +
        '由残留动作驱动，静默污染（plan §2 B6）',
    )
  }
  if (tick >= opts.maxTicks) {
    throw new Error(`[init] 交棒 tick ${tick} ≥ maxTicks ${opts.maxTicks}（交棒后一局都没有）`)
  }
  if (typeof raw.tickHash !== 'string' || !raw.tickHash) {
    throw new Error(`[init] 快照缺 tickHash：${path}（没有它就没有外部保真证据）`)
  }
  return { ...raw, path }
}

/**
 * restore + 交棒前校验。**顺序是有原因的**（每一条都对应一种误用）：
 *   ① `restoreWorld` 原样注入人类到达过的世界（tile/敌位/道具/计时器/基地 HP/地雷/时钟；
 *      `state` 由序列化器恒置 `playing`——快照里不存它，银行也只收局中态）；
 *   ② **先**核 `worldTickHash` 与录像记录链上的那一点（银行构建期逐点对过）——⚠ 必须在重开
 *      RNG **之前**：tickHash 含 `rngState`，先 reseed 会把外部证据变成必然失败/必然放过；
 *   ③ 再 `reseed(seed)`：状态来自人类，**未来**由本局抽到的种子决定。不重开的话，不同 seed
 *      会产出内容完全相同的局（旋转种子名不副实，§15.1 记账失真）；
 *   ④ 最后重新施加 CLI/关卡权威值（restore 会带回 demo 的 difficulty/lives/level）。
 */
export function applyInitSnapshot(
  world: World,
  snap: InitSnapshotFile,
  opts: {
    seed: number
    difficultyKey: string
    livesOverride: number | null
    playerLevelOverride: number | null
  },
): void {
  const { seed, difficultyKey, livesOverride, playerLevelOverride } = opts
  restoreWorld(world, snap.snapshot)
  if (world.frame !== snap.tick) {
    throw new Error(`[init] 世界时钟 ${world.frame} != 快照 tick ${snap.tick}（${snap.path}）`)
  }
  if (worldTickHash(world) !== snap.tickHash) {
    throw new Error(
      `[init] tickHash 自检失败（${snap.path}）：期望 ${snap.tickHash}，实得 ${worldTickHash(world)}`,
    )
  }
  if (!(world.player?.alive ?? false)) {
    throw new Error(`[init] 快照里玩家不存活（${snap.path}）——银行只收活状态`)
  }
  world.rng.reseed(seed)
  world.difficultyKey = difficultyKey
  world.difficulty = (DIFFICULTIES as any)[difficultyKey] ?? (DIFFICULTIES as any)['classic']
  world.rules = (RULES as any)[difficultyKey] ?? DEFAULT_RULES
  if (livesOverride !== null) world.lives = livesOverride
  if (playerLevelOverride !== null) world.playerLevel = playerLevelOverride
}

interface RunResult {
  shard: ShardData
  /** 起始分布注入的事实（未注入 = 空串 / 0 / null；manifest 只在注入时追加这三个字段）。 */
  initSnapshot: string
  initTick: number
  initCounters: Record<string, number> | null
  outcome: string
  ticks: number
  win: boolean
  score: number
  scoreUngated: number
  quality: number
  dims: Record<string, { value: number | null; raw: number }>
  decisionTicks: number
  /** R2.3 决策门读数（步数 / 事件步 / Δt 分布）；均匀模式下 = `minDt==maxDt==K`。 */
  decisionReadout: DecisionReadout
  dodgeTicks: number
  playerDeaths: number
  playerHits: number
  playerShots: number
  kills: number
  enemyHits: number
  powerUpsCollected: number
  playerDamageTaken: number
  stuckTicks: number
  /** metrics v8 危险暴露（每局标量读数；逐决策步分布见 metrics 行）。 */
  dmgFirst600: number
  dangerTicks: number
  threatTicks: number
  /** metrics v9 命中方位 + 穿越税（每局标量读数；逐决策步分布见 metrics 行）。 */
  backHits: number
  sideHits: number
  frontHitsExempt: number
  farHits: number
  geoFallback: number
  onLaneTicks: number
  onLaneExemptTicks: number
  onLaneMoveTicks: number
  onLaneHoldFireTicks: number
  /** metrics v10 差距四族（每局标量读数；逐决策步分布见 metrics 行）。 */
  stopTicks: number
  fireHeldTicks: number
  idleTicks: number
  /** 终局快照值（与 `playerHpRatio` 同约）：末行采样的最近已激活敌车距（哨兵 -1）。 */
  enemyDist: number
  nearEnemy4Ticks: number
  damageBursts: number
  maxDamage120: number
  damageWhileLow: number
  encl1Ticks: number
  encl2Ticks: number
  encl3pTicks: number
  enclMax: number
  /** aim-dodge-levers 8 列（每局标量读数；逐决策步分布见 metrics 行）。 */
  aimHits: number
  aimHitDistSum: number
  aimBricks: number
  aimIgnited: number
  aimMisses: number
  hurtWeight: number
  enclWeightTicks: number
  cornerWeightTicks: number
  enemyTotal: number
  startLives: number
  puGotTank: number
  puGotOther: number
}

/** dodge 模式解析（卡 A3）：arena → l0；真实关 → off（既有 rollout 逐字节不变）。 */
function resolveDodge(dodgeArg: string, stageIdx: number): 'off' | 'l0' | 'god' {
  if (dodgeArg === 'off' || dodgeArg === 'l0' || dodgeArg === 'god') return dodgeArg
  return isArenaId(stageIdx) ? 'l0' : 'off'
}

function runOne(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
  dodgeMode: 'off' | 'l0' | 'god',
  customStage = false,
  livesOverride: number | null = null,
  playerLevelOverride: number | null = null,
  init: { path: string } | null = null,
  /**
   * x2 事件 rung（plan/new-era-stop.plan.md §6 R2）：给决策门加 threat-ONSET
   * （均匀 K ∪ 事件 + Δt≥3）。缺省 false = 均匀 K 旧行为（既有调用逐字节不变）。
   */
  decisionEvents = false,
): RunResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  // plan/rl-training-config.md §6：命数/星级覆盖走 CLI（课程配置单一来源）。
  // S-Dodge 的 `lives=1` 硬编码已删除——课程显式写 player.lives（评审 P1-11）。
  if (livesOverride !== null) world.lives = livesOverride
  if (playerLevelOverride !== null) world.playerLevel = playerLevelOverride

  const model = buildModelFromText(weightsText) as unknown as RolloutModel
  const scripted = new ScriptedInput()
  const sim = new Simulation(world, scripted as any)
  // arena 编号不得进入 loadStageData 的 stageIndex：index 进 killScore 的
  // 1.05^index 关卡缩放，而分数经 dropOnScoreMilestone 反哺玩法——index=1000
  // 时单杀得分 ~4e22，里程碑掉落循环 push ~1e18 个掉落物 → 内存耗尽段错误
  //（2026-08-30 实测，tmp/memprobe.ts 逐语句定位）。arena 一律用 index 0
  // （与 World.loadStageData 文档"generated stages use index 0"同口径）。
  // 守卫②（plan §5.2）：自定义关显式 index 0——与 isArenaId 巧合解耦
  //（关卡号取值无关，1.05^index 缩放事故对自定义关同样成立）。
  world.loadStageData(stage, customStage || isArenaId(stageIdx) ? 0 : stageIdx)
  // 起始分布（plan P1）：中段快照注入。null = 现状逐字节不变。
  let initTick = 0
  let initCounters: Record<string, number> | null = null
  let initSnapshotName = ''
  if (init) {
    const snap = loadInitSnapshot(init.path, { stageIdx, maxTicks })
    applyInitSnapshot(world, snap, {
      seed,
      difficultyKey: difficulty,
      livesOverride,
      playerLevelOverride,
    })
    initTick = snap.tick
    initSnapshotName = basename(init.path)
    // 交棒点的**世界事实**（继承的进度）：分析侧要 rebase 就靠它。注意快照里没有逐事件计数器
    // （hits/shots/damage 那些是「这一局发生的事」，人类那一段的没有随状态带过来）。
    initCounters = {
      kills: world.killCount,
      enemiesSpawned: world.enemiesSpawned,
      enemiesRemaining: world.enemiesRemaining,
      lives: world.lives,
      playerLevel: world.playerLevel,
      score: world.score,
      baseHp: world.baseHp,
      frame: world.frame,
    }
  }
  scripted.reset()
  // R2 决策门（唯一实现）：逐 tick 喂（事件模式要沿检测 + 最小间隔闸）。
  const gateCfg = createDecisionGateConfig(decisionEvents)
  const gate = createDecisionGateState()
  if (init) {
    // R2.4 共存裁决（方案 i，DECISIONS §2026-09-27-…）：交棒后首段禁 threat 事件，
    // 保留均匀 K。理由：注入世界的 `prevThreat` 历史是未知的（快照只存当前态）——
    // 在交棒瞬间“首次入带”会变成一次伪造的 onset（不是真实的“从安全到危险”转移）。
    // 抑制窗 = 第一个 K 段（至下一个均匀边界），均匀决策照常；窗内 prevThreat 照常更新，
    // 所以窗内已开始的威胁不会被憋到窗后追发。
    gate.suppressEventsUntilTick = initTick + K
  }
  // god 链臂（A3 A/B 对照专用）：God-AI 探针只读 World（自身独立 RNG，§47），
  // 不参与驱动仿真——每决策 tick 跑一次 think 判 _lastBranch==='dodge'。
  const godProbe =
    dodgeMode === 'god'
      ? new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS }, new RNG((seed ^ 0x5bd1e995) >>> 0))
      : null
  godProbe?.reset()

  const encoder = new ObsEncoder()
  const shard = newShard()
  const rng = mulberry32((seed ^ 0x85ebca6b) >>> 0)

  const tel: Telemetry = {
    enemyTotal: (stage as any)?.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.lives,
    playerDeaths: 0,
    playerHits: 0,
    playerDamageTaken: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    puSpawnBomb: 0,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puGotBomb: 0,
    puGotTank: 0,
    puGotFreeze: 0,
    puGotShield: 0,
    puGotOther: 0,
    puSpawnStar: 0,
    baseWallTotal: countBaseWall(world),
    baseWallIntact: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
    clearTick: undefined,
    enemyHits: 0,
    killsByKind: [0, 0, 0, 0],
    hitsByKind: [0, 0, 0, 0],
    stuckTicks: 0,
    dangerTicks: 0,
    threatTicks: 0,
    dmgFirst600: 0,
    backHits: 0,
    sideHits: 0,
    frontHitsExempt: 0,
    farHits: 0,
    geoFallback: 0,
    onLaneTicks: 0,
    onLaneExemptTicks: 0,
    onLaneMoveTicks: 0,
    onLaneHoldFireTicks: 0,
    stopTicks: 0,
    fireHeldTicks: 0,
    idleTicks: 0,
    nearEnemy4Ticks: 0,
    damageWhileLow: 0,
    damageTicks: [],
    damageAmounts: [],
    encl1Ticks: 0,
    encl2Ticks: 0,
    encl3pTicks: 0,
    enclMax: 0,
    aimHits: 0,
    aimHitDistSum: 0,
    aimBricks: 0,
    aimIgnited: 0,
    aimMisses: 0,
    hurtWeight: 0,
    enclWeightTicks: 0,
    cornerWeightTicks: 0,
  }
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()
  let prevCell = { col: -1, row: -1 }
  /** metrics v9：bulletId → **开火时刻** shooter 中心格键（`bullet_fired` 事件登记）。
   *  为什么必须有这张表：弹命中时已位移，「命中时刻弹坐标」推不出开火点；而
   *  `bullet_fired` 事件里的弹坐标就是开火那一拍的生成位置（生成式见
   *  `hit-geometry.ts::fireOriginCellKey`），故事件侧登记即真值 —— 不需要
   *  「新生弹首见」那类采样启发式（评审 M3）。上界 = 本局开火数（数百条）。 */
  const fireOriginByBullet = new Map<number, number>()
  /**
   * 本批豁免 A：盾道具窗 tracker（每 tick 在 `sim.tick()` 之后喂一拍；§1.2）。
   * 消费侧（encl/corner/敌弹权重）只经 `exposureExempt` 问它，不直接读分类。
   */
  const shieldWin = createShieldWindow()
  /**
   * 本批 shot registry（§3.1/§3.2/§3.4）：`bullet_fired` 登记、结算即删（settle-once）。
   * 实现与评估侧共用（`aim-shot-registry.ts`）；与 `fireOriginByBullet`（v9 的 shooter
   * 格表）同源不同用：那是「已发生命中」的几何反查。
   */
  const shotRegistry: AimShotTable = new Map()
  /**
   * 本批回写补丁（§3.4：「后缀累加」的等价位实现）：结算时给**已经推出去**的
   * `[patchFrom, 当前行数)` 补增量；当前行数之后的行由 `buildMetricsRow` 读 tel
   * （结算时已 +delta）自然携带。打包前一次应用 ⇒ 开火行之后的所有行都含该结算，
   * `r[fireRow] = Φ[fireRow+1] − Φ[fireRow]` 拿到信用。
   */
  const patches: Array<{ col: number; from: number; to: number; delta: number }> = []
  const pushPatch = (col: number, from: number, delta: number): void => {
    patches.push({ col, from, to: shard.metrics.length, delta })
  }
  /**
   * 删除 + 补丁（settle-once 本体）：查不到 / 已结算 / 敌弹 = no-op（返回 null）。
   * 各列 tel 计数由调用方同步加（显式书写，避免 col→字段的隐式映射）。
   */
  const settleAimShot = (id: number, col: number, delta: number): AimShotRec | null => {
    const rec = takeAimShot(shotRegistry, id, true)
    if (!rec) return null
    pushPatch(col, rec.patchFrom, delta)
    return rec
  }
  /** 结算一颗**敌**登记弹的承伤（击伤拍；权重进 hurtWeight —— 只读累计，不写 miss）。 */
  const settleHurtShot = (id: number): void => {
    const rec = takeAimShot(shotRegistry, id, false)
    if (!rec) return
    if (rec.laneWeight > 0) {
      tel.hurtWeight += rec.laneWeight
      pushPatch(AIM_COL.hurt, rec.patchFrom, rec.laneWeight)
    }
  }
  /** 本 tick 的开火输出（fire head，held 到下一个决策步；非冷却态）。 */
  let fireHeld = false
  /** 本 tick 的移动输出是否为 STOP（动作头 idx0；held 到下一个决策步）。
   *  初始 true = `ScriptedInput.reset()` 后 moveDir 为 null（STOP），与首拍一致。 */
  let stopHeld = true
  if (init) {
    // 交棒基线（plan §2 M2/M3）：metrics 只描述**交棒之后**的窗口，但不得让场上既有的东西被
    // 当成「刚发生」。seenPuIds 预置 ⇒ `powerUpsSpawned` 不把交棒时已存在的道具算成新 spawn；
    // prevCell 预置 ⇒ 交棒那一刻不产生假 stuckTicks。
    for (const pu of world.powerUps) {
      seenPuIds.add(pu.id)
      prevLivePuIds.add(pu.id)
    }
    prevCell = playerCenterCell(world.player) ?? { col: -1, row: -1 }
  }

  let pending: {
    obs: Uint8Array
    sc: Float32Array
    aMove: number
    aFire: number
    lpMove: number
    lpFire: number
    value: number
    mask: number[]
  } | null = null
  let t = initTick // 游戏时钟继续走：cut 吃掉 T（max_ticks 仍对游戏时钟算）
  let outcome = 'timeout'
  let decisionTicks = 0 // 决策步数（K 间隔；事件模式下含事件步——分布看 decisionReadout）
  let dodgeTicks = 0 // L0/保底层覆盖采样动作的决策 tick 数（§3.5 覆盖率口径）

  // 每决策步推一行 + 终局再推一行 ⇒ shard.metrics 恒为 [N+1] 行、每行 METRICS_DIM 列
  // （行宽 SSOT 在模块级 `buildMetricsRow`，见文件头 METRICS_DIM 注释）。
  const metricsRow = (): number[] => buildMetricsRow(t, world, tel)

  const flushPending = (term: boolean): void => {
    if (!pending) return
    shard.obs.push(pending.obs)
    shard.scalars.push(pending.sc)
    shard.aMove.push(pending.aMove)
    shard.aFire.push(pending.aFire)
    shard.lpMove.push(pending.lpMove)
    shard.lpFire.push(pending.lpFire)
    shard.value.push(pending.value)
    shard.done.push(term ? 1 : 0)
    for (let j = 0; j < MASK_DIM; j++) shard.mask.push(pending.mask[j])
    shard.n++
    pending = null
  }

  while (t < maxTicks) {
    if (decisionDue(t, world, gate, gateCfg)) {
      // §368 提速③：obs 只在决策 tick 编码（原本每 tick 都编码，占整局 1.8–2.3%；
      // 非决策 tick 的编码结果无人消费——obs 只在下面 forward 与 pending 快照里用）。
      encoder.encode(world)
      model.forward(encoder.obs, encoder.scalars)
      const masks = computeMasks(world)
      const mv = sampleCat(model.moveLogits, masks.move, rng)
      const fr = sampleCat(model.fireLogits, masks.fire, rng)
      const value = model.valueOut[0]
      decisionTicks++

      // ---- L0 保底层覆盖（§3.5 / 卡 A3）----
      // 覆盖步记账（F3）：落盘 executed 动作 + executed 动作在采集策略下的 logp
      // （logProbAt 与采样同 softmax 口径）⇒ PPO ratio 对覆盖步良定义。
      let aMove = mv.idx
      let lpMove = mv.logp
      if (dodgeMode === 'l0') {
        const sampledDir = decodeMove(mv.idx)
        const d = dodgeL0(world, sampledDir)
        if (d.triggered && d.dir) {
          aMove = encodeMove(d.dir)
          lpMove = logProbAt(model.moveLogits, masks.move, aMove)
          dodgeTicks++
        }
      } else if (dodgeMode === 'god' && godProbe) {
        godProbe.getMoveDirection()
        godProbe.isFiring()
        if (godProbe._lastBranch === 'dodge' && godProbe._moveDir) {
          aMove = encodeMove(godProbe._moveDir)
          lpMove = logProbAt(model.moveLogits, masks.move, aMove)
          dodgeTicks++
        }
        godProbe.endFrame()
      }

      // 记录本决策步的指标快照（与 obs 行同刻状态——reward 由 Python 公式引擎
      // 对 metrics 行求 Φ 后 diff 派生，见 plan §4.3.3）。首个决策点无 pending
      // 可 flush，只推行 + 建 pending。
      shard.metrics.push(metricsRow())
      if (pending) flushPending(false)

      pending = {
        obs: encoder.obs.slice(),
        sc: encoder.scalars.slice(),
        aMove,
        aFire: fr.idx,
        lpMove,
        lpFire: fr.logp,
        value,
        mask: [...masks.move, ...masks.fire],
      }
      scripted.setAction(aMove, fr.idx)
      // 本决策步选定的 fire head（held 到下一个决策步；`onLaneHoldFireTicks` 用它，
      // 不用 `bullet_fired` —— 后者被冷却/弹量上限门掉，是「实弹」不是「开火输出」）。
      // ≡ export-eval-game 的 `ai.isFiring()`（nn 策略下同 ScriptedInput 持有实现，
      // endFrame 无脉冲，两边读的是同一个 held head；god 策略只走 eval 侧，不跨源对账）。
      fireHeld = fr.idx === 1
      // metrics v10：移动输出为 STOP（`decodeMove(idx <= 0) === null`）⇒ `stopTicks` 计本拍。
      stopHeld = aMove === 0
    }
    sim.tick()
    scripted.endFrame()
    t++

    // 清场 tick（2026-09-12）：敌人**首次全灭**的 tick。`allEnemiesCleared` 首行即
    // `enemiesRemaining <= 0` 短路，配合 `clearTick === undefined` 守卫 ⇒ 一旦记录
    // 就不再调用，每 tick 开销可忽略。
    if (tel.clearTick === undefined && allEnemiesCleared(world)) tel.clearTick = t

    // 本批豁免 A（§1.2）：盾窗 tracker 每 tick 喂一拍（在任何 exposureExempt 消费之前）。
    shieldWin.update(world)

    // ---- telemetry（语义对齐 simulation-runner）----
    let collectedThisTick = 0
    let hitThisTick = false
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && tel.firstKillTick === undefined) tel.firstKillTick = t - 1
        if ((e as any).tank?.isPlayer) tel.playerDeaths++
        else if ((e as any).by === 'player' && (e as any).tank?.allegiance === 'enemy') {
          // 分敌种击杀（metrics v6）：只记玩家击杀敌车。AOE 走 SimulationPlayer /
          // SimulationEnemies 两条路径，都推 tank_destroyed 且带 kind。
          // ⚠ 已知例外（评审 P0，2026-09-16）：bomb 道具「清屏」
          // （`src/game/SimulationPowerUps.ts:391`）调 `recordEnemyKill`（`world.killCount++`）
          // 但**不推本事件** ⇒ 这里的四桶之和**恒 ≤** 指标 `kills` 列
          //（实测 God AI 长局缺 11.6%、NN/ladder-c03 缺 ~1.4%；不守恒局 100% 拾取过 bomb）。
          // 奖励侧由 `x3-credit-p6` 公式末尾的残差桶按 basic 价补回；**不要**在 metrics
          // 侧试图"补齐"成相等——那会让口径与 `recordEnemyKill` 的真实计数分叉。
          const ki = enemyKindIndex((e as any).tank.kind)
          if (ki >= 0) tel.killsByKind[ki]++
        }
      } else if (e.type === 'player_hit') {
        tel.playerHits++
        // 本批：承伤结算（致死 + 星盾消耗两条路径；P0-A 签名修复后 bulletId 必在）。
        if (e.bulletId !== undefined) settleHurtShot(e.bulletId)
      } else if (e.type === 'player_damage') {
        tel.playerDamageTaken += e.damage
        // metrics v8：开局窗累计（事件属 tick t-1；tick < 600 即局内前 600 tick）。
        if (t - 1 < DMG_FIRST_WINDOW_TICKS) tel.dmgFirst600 += e.damage
        // metrics v10：伤害成簇（事件序列）+ 低血期承伤。
        // 「低血」以**消费时刻**（本 tick 终态）的 hp 比判（与 §71 面板同口径）：
        // 把「被打到残血的那笔」本身计入，之后残血期的每笔都计入。
        tel.damageTicks.push(t - 1)
        tel.damageAmounts.push(e.damage)
        if (world.player?.alive && playerHpRatio(world) < DANGER_HP_THRESHOLD)
          tel.damageWhileLow += e.damage
        // 本批：承伤结算（非致死；权重 Σ(6−x) 由逐 tick 累计而来）。
        if (e.bulletId !== undefined) settleHurtShot(e.bulletId)
      } else if (e.type === 'bullet_fired') {
        const eb = e.bullet
        if (eb.isPlayer) {
          tel.playerShots++
          // metrics v9：登记**开火时刻** shooter 中心格（弹的生成位置是真值，
          // 命中侧以 bulletId 反查；晚一步采样只会更差，见评审 M3）。
          const fk = fireOriginCellKey(eb) // = 开火拍**玩家中心格**（弹几何重建；§3.1 端点）
          fireOriginByBullet.set(eb.id, fk)
          // 本批：开火结果族登记（patchFrom = 下一 metrics 行下标 ⇒ 信用落开火决策步）。
          registerPlayerAimShot(
            shotRegistry,
            eb.id,
            keyCol(fk),
            keyRow(fk),
            shard.metrics.length,
            eb,
          )
        } else if (eb.allegiance === 'enemy') {
          // 本批：承伤族登记（玩家侧盟军弹不入表；权重逐 tick 累计见主循环）。
          registerEnemyAimShot(shotRegistry, eb.id, shard.metrics.length, eb)
        }
      } else if (e.type === 'terrain_destroyed') {
        // 本批：brick 桶（只计真破坏；一弹 2×2 多格可推多条事件 ⇒ settle-once 去重）。
        if (e.bulletId !== undefined) {
          const rec = settleAimShot(e.bulletId, AIM_COL.bricks, 1)
          if (rec) tel.aimBricks++
        }
      } else if (e.type === 'bullet_cancelled') {
        // 本批：对消桶（真事件归因；a/b 至多一边是玩家弹）。
        const ra = settleAimShot(e.aId, AIM_COL.ignited, 1)
        if (ra) tel.aimIgnited++
        const rb = settleAimShot(e.bId, AIM_COL.ignited, 1)
        if (rb) tel.aimIgnited++
      } else if (e.type === 'powerup_collected') {
        collectedThisTick++
        tel.powerUpsCollected++
        const put = (e as any).powerUp
        if (put === 'star') tel.starsCollected++
        else if (put === 'bomb') tel.puGotBomb++
        else if (put === 'tank') tel.puGotTank++
        else if (put === 'freeze') tel.puGotFreeze++
        else if (put === 'shield') tel.puGotShield++
        else tel.puGotOther++
      } else if (e.type === 'enemy_hit') {
        tel.enemyHits++
        hitThisTick = true
        const ki = enemyKindIndex((e as any).targetKind)
        if (ki >= 0) tel.hitsByKind[ki]++
        // ---- metrics v9：方位计数（plan/geo-threat-instrumentation.plan.md §1.1）----
        // V = 玩家格 − 受害敌格：受害人取**命中时刻快照**（事件字段；致死命中同 tick
        // 被 removeDeadEntities 压实，回查必落空）；玩家取**开火时刻** shooter 格。
        const vKey = centerCellKey(e.targetX, e.targetY, TANK, TANK)
        const pKey = fireOriginByBullet.get(e.bulletId)
        let pCol: number
        let pRow: number
        if (pKey === undefined) {
          // state-init 交棒时已在飞的弹（本局没有它的 bullet_fired）⇒ 回退命中时刻
          // 玩家格 + 记 geoFallback。双记是刻意的：该命中仍按 proxy 落某一个桶
          // （front ≡ 总数 − back − side − exempt − far 守恒），geoFallback 列本身
          // 不参与相减，只供跨局对账时标出"桶里有几发是 proxy 定位"。
          tel.geoFallback++
          const p = world.player
          const fbKey = p ? centerCellKey(p.x, p.y, p.w, p.h) : vKey
          pCol = keyCol(fbKey)
          pRow = keyRow(fbKey)
        } else {
          pCol = keyCol(pKey)
          pRow = keyRow(pKey)
        }
        const geo = classifyHit(pCol, pRow, keyCol(vKey), keyRow(vKey), e.targetDir)
        if (geo === HIT_FAR) tel.farHits++
        // 冻/盾豁免（用户口径）：raw 几何先算出（含 far 判定），**非 far 的 aimed 域**
        // 不论 raw 是什么一律进豁免桶；far 不受豁免影响。普通 front 由总数减出。
        else if (threatLaneExempt(world)) tel.frontHitsExempt++
        else if (geo === HIT_BACK) tel.backHits++
        else if (geo === HIT_SIDE) tel.sideHits++
        // geo === HIT_FRONT ⇒ 不落桶（frontHits = enemyHits − back − side − exempt − far）
        // ---- 本批开火结果族：命中结算（含致死；d = 开火拍玩家中心格 → 命中拍受害车中心格）----
        const rec = settleAimShot(e.bulletId, AIM_COL.hits, 1)
        if (rec) {
          tel.aimHits++
          const vCol = Math.floor((e.targetX + TANK / 2) / CELL)
          const vRow = Math.floor((e.targetY + TANK / 2) / CELL)
          const d = Math.max(Math.abs(vCol - rec.fireCol), Math.abs(vRow - rec.fireRow))
          tel.aimHitDistSum += d
          pushPatch(AIM_COL.dist, rec.patchFrom, d)
        }
      }
    }
    // ---- 本批承伤族：已登记敌弹的逐 tick 火线权重（A 拌入；只算登记表内的弹）----
    if (world.player?.alive) {
      const bullets = world.bullets
      for (let bi = 0; bi < bullets.length; bi++) {
        const b = bullets[bi]
        if (b.allegiance !== 'enemy') continue
        const rec = shotRegistry.get(b.id)
        if (rec && !rec.isPlayer) rec.laneWeight += enemyBulletLaneWeightTick(world, b, shieldWin)
      }
    }
    // power-up census（seen-ids + same-tick pickup 对账，镜像 runner）
    {
      const live = new Set<number>()
      for (const pu of world.powerUps) {
        live.add(pu.id)
        if (!seenPuIds.has(pu.id)) {
          seenPuIds.add(pu.id)
          tel.powerUpsSpawned++
          if (pu.type === 'bomb') tel.puSpawnBomb++
          else if (pu.type === 'tank') tel.puSpawnTank++
          else if (pu.type === 'freeze') tel.puSpawnFreeze++
          else if (pu.type === 'shield') tel.puSpawnShield++
          else if (pu.type === 'star') tel.puSpawnStar++
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      tel.powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
    }
    // 停滞判定（obs v3 sN4 / reward 同源，dsf A4）：中心 cell 不变 且 本 tick 未
    // 命中敌车 → stuckTicks++；否则清零。实现 = src/game/stuck-detect.ts 共享纯
    // 函数（与 Simulation.updatePlaying 末尾的 World.stuckTicks 维护同一实现）。
    const cur = playerCenterCell(world.player)
    // metrics v9：「本 tick 位移≠0」= 中心格变化（`stuck-detect` 同口径，不另发明）。
    const movedThisTick = cur !== null && (cur.col !== prevCell.col || cur.row !== prevCell.row)
    if (isStuckTick(world.player?.alive ?? false, cur, prevCell, hitThisTick)) {
      tel.stuckTicks++
    } else {
      tel.stuckTicks = 0
    }
    prevCell = cur ?? { col: -1, row: -1 }

    // 危险暴露累加（metrics v8）：与 stuckTicks 同刻（`sim.tick()` 之后，代表本 tick 的
    // 终态）。两条都是零分配纯判定（AGENTS §14.1–14.2）；玩家阵亡期间不计
    // （「残血」以活着为前提，死亡帧不算暴露）。
    if (world.player?.alive) {
      // ---- metrics v10：输入级 3 列 + 距离 2 列 + 被包围族 7 列 ----
      // 输入级：held head（与决策步同频，不经时钟脉冲）；引擎静止：`player.moving`。
      if (stopHeld) tel.stopTicks++
      if (fireHeld) tel.fireHeldTicks++
      if (!world.player.moving) tel.idleTicks++
      const enemyDist = nearestEnemyDistPx(world)
      if (enemyDist >= 0 && enemyDist <= NEAR_ENEMY_BAND_PX) tel.nearEnemy4Ticks++
      // 被包围：互斥分档（v10 批次内整理：`enclExempt*` 三列已移除，§0.1 #9；
      // 加权维由本批的 `enclWeightTicks` 承接）。
      const encl = alignedEnemyCount(world)
      if (encl > 0) {
        if (encl >= 3) tel.encl3pTicks++
        else if (encl === 2) tel.encl2Ticks++
        else tel.encl1Ticks++
        if (encl > tel.enclMax) tel.enclMax = encl
      }
      // 本批暴露杠杆（A 拌入，§3.6）：非豁免拍才累计（两列分开 gate；冻期/盾道具窗跳过）。
      if (!exposureExempt(world, shieldWin)) {
        tel.enclWeightTicks += enclWeightTick(world)
        tel.cornerWeightTicks += cornerWeightTick(world)
      }
      if (playerHpRatio(world) < DANGER_HP_THRESHOLD) tel.dangerTicks++
      if (inThreatLane(world)) {
        tel.threatTicks++
        // ---- metrics v9：穿越税观测族（plan §1.3）----
        // raw 永真：冻/盾拍**照记** onLaneTicks，豁免以**加法列**另记（不是从 raw 里扣）。
        // 两个累计量的差分各自干净 —— 逐行 flag 相乘会在翻转拍打出幻影 ± 尖峰，
        // 且公式引擎禁跨步归约（reward_library.py 白名单无 sum/cumsum），交集必须在
        // 共位处一次算好（plan §1.3 机制注）。
        tel.onLaneTicks++
        if (threatLaneExempt(world)) tel.onLaneExemptTicks++
        if (movedThisTick) tel.onLaneMoveTicks++
        else if (fireHeld) tel.onLaneHoldFireTicks++
      }
    }

    if (t % TELEMETRY_SAMPLE_TICKS === 0) {
      tel.basePressureSum += sampleBasePressure(world)
      tel.basePressureSamples++
      if (world.player?.alive) {
        const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
        const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
        visitedCellsAdd(tel.cellsVisited, col, row)
      }
    }

    if (world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover') {
      outcome =
        world.state === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
  }

  // ---- 终局统一处理（stageclear/gameover break 与 timeout 出口共用）----
  // 本批 settle-once ④：仍未结算的**玩家**登记弹 = 出界 / 打钢未破 / 打基地 / 局末在飞
  // ⇒ 一律折入 aimMisses（§3.1）；敌弹未结算的权重直接丢弃（不写 miss）。
  for (const rec of shotRegistry.values()) {
    if (rec.isPlayer) {
      tel.aimMisses++
      pushPatch(AIM_COL.misses, rec.patchFrom, 1)
    }
  }
  shotRegistry.clear()
  // 终局指标快照（与旧 phiEnd 同刻）；flush 最后一个 pending（done=1），
  // 修复 §3.6(b) 样本丢失。reward 本身不再由 TS 结算（Python 公式引擎负责）。
  shard.metrics.push(metricsRow())
  if (pending) flushPending(true)
  // ---- 本批回写应用（打包前一次；§3.4）----
  // 区间补丁 = 「后缀」的等价位实现（纯函数，可单测）：[patchFrom, 当前行数) 覆盖已推的行，
  // 其后由 buildMetricsRow 读 tel（结算时已 +delta）承担。应用后开火行之后的所有行都含该结算。
  applySuffixPatches(shard.metrics, patches)

  // ---- 精确 v7 打分（score/gatedScore 落 manifest，Python 的 reconcile 输入）----
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: world.killCount,
      lives: world.lives,
      baseAlive: !world.tileMap.isBaseDestroyed(),
    },
    firstKillTick: tel.firstKillTick,
    clearTick: tel.clearTick,
    telemetry: {
      enemyTotal: tel.enemyTotal,
      startLives: tel.startLives,
      playerDeaths: tel.playerDeaths,
      playerShots: tel.playerShots,
      powerUpsSpawned: tel.powerUpsSpawned,
      powerUpsCollected: tel.powerUpsCollected,
      starsCollected: tel.starsCollected,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact: tel.baseWallIntact,
      baseWallTotal: tel.baseWallTotal,
      basePressureMean:
        tel.basePressureSamples > 0 ? tel.basePressureSum / tel.basePressureSamples : 0,
      basePressureSamples: tel.basePressureSamples,
      cellsVisited: tel.cellsVisited.size,
      deaths: [],
    } satisfies Omit<RunTelemetry, 'deaths'> & { deaths: never[] },
  } as any
  const scored = scoreRun(scorable, RL_SCORE_CONFIG)
  // F3 终局锚点：base_destroyed 局 gatedScore ×= M（manifest.score 即 gated 值）。
  const gatedScore = outcome === 'base_destroyed' ? scored.score * BASE_LOSS_MULT : scored.score

  // 过关口径（2026-09-12 用户裁定）：**敌人全灭即算过关**，不要求拿到 `stage_clear` 事件。
  // 成因：全灭后若场上还有道具会进 BONUS TIME 窗口（`POWERUP_PICKUP_WINDOW_MS = 10000ms
  // ≈ 600 tick`），窗口走完才 stage_clear；而 `max_ticks` 可能在窗口结束前截断 ⇒
  // `outcome='max_ticks'` 却已歼灭。单关训练场景下二者等价（道具跨关累积的增益只在多关
  // 训练时才存在），故 `win` 统一为 win ∪ cleared。`outcome` 字段保留原始值不受影响
  // （reward 仍按 `terminal[outcome]` 结算，见 c6-bonus.jsonc 的补偿项）。
  const win = outcome === 'stage_clear' || allEnemiesCleared(world)
  const dims: Record<string, { value: number | null; raw: number }> = {}
  for (const k of Object.keys(scored.dims) as DimensionKey[]) {
    dims[k] = { value: scored.dims[k].value, raw: scored.dims[k].raw }
  }
  return {
    shard,
    initSnapshot: initSnapshotName,
    initTick,
    initCounters,
    outcome,
    ticks: t,
    win,
    score: gatedScore,
    scoreUngated: scored.score,
    quality: scored.quality,
    dims,
    decisionTicks,
    decisionReadout: decisionReadout(gate),
    dodgeTicks,
    playerDeaths: tel.playerDeaths,
    playerHits: tel.playerHits,
    playerShots: tel.playerShots,
    kills: world.killCount,
    enemyHits: tel.enemyHits,
    powerUpsCollected: tel.powerUpsCollected,
    playerDamageTaken: tel.playerDamageTaken,
    stuckTicks: tel.stuckTicks,
    dmgFirst600: tel.dmgFirst600,
    dangerTicks: tel.dangerTicks,
    threatTicks: tel.threatTicks,
    backHits: tel.backHits,
    sideHits: tel.sideHits,
    frontHitsExempt: tel.frontHitsExempt,
    farHits: tel.farHits,
    geoFallback: tel.geoFallback,
    onLaneTicks: tel.onLaneTicks,
    onLaneExemptTicks: tel.onLaneExemptTicks,
    onLaneMoveTicks: tel.onLaneMoveTicks,
    onLaneHoldFireTicks: tel.onLaneHoldFireTicks,
    stopTicks: tel.stopTicks,
    fireHeldTicks: tel.fireHeldTicks,
    idleTicks: tel.idleTicks,
    enemyDist: nearestEnemyDistPx(world), // 终局快照（同 metrics 行口径；玩家已死 = 哨兵 -1）
    nearEnemy4Ticks: tel.nearEnemy4Ticks,
    // 两个成簇读数由事件序列一次算出（与 metrics 行同函数；单调增）。
    damageBursts: damageClusterStats(tel.damageTicks, tel.damageAmounts).bursts,
    maxDamage120: damageClusterStats(tel.damageTicks, tel.damageAmounts).max120,
    damageWhileLow: tel.damageWhileLow,
    encl1Ticks: tel.encl1Ticks,
    encl2Ticks: tel.encl2Ticks,
    encl3pTicks: tel.encl3pTicks,
    enclMax: tel.enclMax,
    aimHits: tel.aimHits,
    aimHitDistSum: tel.aimHitDistSum,
    aimBricks: tel.aimBricks,
    aimIgnited: tel.aimIgnited,
    aimMisses: tel.aimMisses,
    hurtWeight: tel.hurtWeight,
    enclWeightTicks: tel.enclWeightTicks,
    cornerWeightTicks: tel.cornerWeightTicks,
    enemyTotal: tel.enemyTotal,
    startLives: tel.startLives,
    puGotTank: tel.puGotTank,
    puGotOther: tel.puGotOther,
  }
}

function visitedCellsAdd(set: Set<number>, col: number, row: number): void {
  set.add(row * GRID + col)
}

export function writeRlShard(dir: string, d: ShardData, manifest: unknown): void {
  mkdirSync(dir, { recursive: true })
  for (const { name, data } of shardNpyEntries(d)) {
    writeFileSync(`${dir}/${name}`, data)
  }
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
}

/** 局末 shard → 内存 npy 字节（与 writeRlShard 同布局；sampler 热路径 / perf 对比用）。 */
export function shardNpyEntries(d: ShardData): Array<{ name: string; data: Buffer }> {
  const N = d.n
  if (N === 0) return []
  if (d.metrics.length !== N + 1) {
    throw new Error(`metrics row count ${d.metrics.length} != n+1=${N + 1} —— 指标行失配，拒绝打包`)
  }
  const obs = new Uint8Array(N * OBS_CHANNELS * BOARD * BOARD)
  const scalars = new Float32Array(N * SCALAR_DIM)
  const aMove = new Uint8Array(N)
  const aFire = new Uint8Array(N)
  const lpMove = new Float32Array(N)
  const lpFire = new Float32Array(N)
  const value = new Float32Array(N)
  const metrics = new Float64Array((N + 1) * METRICS_DIM)
  const done = new Uint8Array(N)
  const mask = new Uint8Array(N * MASK_DIM)
  for (let i = 0; i < N; i++) {
    obs.set(d.obs[i], i * OBS_CHANNELS * BOARD * BOARD)
    scalars.set(d.scalars[i], i * SCALAR_DIM)
    aMove[i] = d.aMove[i]
    aFire[i] = d.aFire[i]
    lpMove[i] = d.lpMove[i]
    lpFire[i] = d.lpFire[i]
    value[i] = d.value[i]
    done[i] = d.done[i]
    for (let j = 0; j < MASK_DIM; j++) mask[i * MASK_DIM + j] = d.mask[i * MASK_DIM + j]
  }
  for (let i = 0; i <= N; i++) {
    metrics.set(d.metrics[i], i * METRICS_DIM)
  }
  return [
    { name: 'obs.npy', data: npyBytes(obs, [N, OBS_CHANNELS, BOARD, BOARD], 'u1') },
    { name: 'scalars.npy', data: npyBytes(scalars, [N, SCALAR_DIM], 'f4') },
    { name: 'a_move.npy', data: npyBytes(aMove, [N], 'u1') },
    { name: 'a_fire.npy', data: npyBytes(aFire, [N], 'u1') },
    { name: 'lp_move.npy', data: npyBytes(lpMove, [N], 'f4') },
    { name: 'lp_fire.npy', data: npyBytes(lpFire, [N], 'f4') },
    { name: 'value.npy', data: npyBytes(value, [N], 'f4') },
    { name: 'metrics.npy', data: npyBytes(metrics, [N + 1, METRICS_DIM], 'f8') },
    { name: 'done.npy', data: npyBytes(done, [N], 'u1') },
    { name: 'mask.npy', data: npyBytes(mask, [N, MASK_DIM], 'u1') },
  ]
}

/** bench / 进程内调用：与 CLI 同一 `runOne`（生产 sampler 仍 spawn 子进程）。 */
export function runOneBench(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
  dodgeMode: 'off' | 'l0' | 'god' = 'off',
  customStage = false,
  livesOverride: number | null = null,
  playerLevelOverride: number | null = null,
  init: { path: string } | null = null,
  /** x2 事件 rung（R2）：同 `runOne`。 */
  decisionEvents = false,
): RunResult {
  return runOne(
    stageIdx,
    stage,
    seed,
    difficulty,
    maxTicks,
    weightsText,
    dodgeMode,
    customStage,
    livesOverride,
    playerLevelOverride,
    init,
    decisionEvents,
  )
}

/**
 * 解析 `--lives-override`（必填，无默认值）。
 *
 * 2026-09-19 根因修复：此前缺席时静默回落到难度默认值（全难度 startLives=3），
 * 而课程/关卡语义（`CourseConfig.player.lives` ← 关卡 `player.lives` 合并）走的
 * 正是这个 flag —— 裸调 CLI 忘传 = 静默 3 命，烧掉整批 it0 标定语料（x2–x7 全中招，
 * 训练侧因必经课程合并从未中招）。从此缺席/非法 = 响亮失败，不设默认值。
 * 训练调用链（`rl/cmd.py` ← `apply_course` ← 关卡合并）恒传此 flag，不受影响。
 */
export function resolveLivesFlag(raw: string): number {
  const v = parseInt(raw, 10)
  if (!raw || !Number.isInteger(v) || v < 1) {
    throw new Error(
      `[export-rl-rollout] missing/invalid --lives-override ${JSON.stringify(raw)} —— ` +
        '命数无默认值（难度默认 3 命曾静默烧掉标定语料）。显式传 --lives-override N（N≥1），' +
        '数值取关卡文件 player.lives（训练侧由课程自动合并透传，无需手填）。',
    )
  }
  return v
}

function parseRange(s: string): number[] {
  const out: number[] = []
  for (const part of s.split(',')) {
    if (part.includes('-')) {
      const [a, b] = part.split('-').map((x) => parseInt(x, 10))
      for (let i = a; i <= b; i++) out.push(i)
    } else out.push(parseInt(part, 10))
  }
  return out
}

// export：同质长驻入口 `serve-any.ts` 要按 mode 分派到本导出器（其余三个导出器早已导出）。
export function main(argv: string[] = process.argv.slice(2)): void {
  const t0 = Date.now()
  const args = argv
  let outDir = 'tmp/rl-traj'
  let difficulty = 'hard'
  let stagesStr = '0-3'
  let seedsStr = '0-3'
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/rl-weights/weights.json'
  let wver = ''
  let nodeLabel = ''
  // D14（plan/remote-ppo-architecture.md）：语料血缘 course_fp = 课程文件 sha256。
  // hub 侧在发布 rollout 时透传——写进每局 shard manifest，远程 PPO 装载校验
  // job.course_fp == shard.course_fp（跨课程语料绝不混训）。空 = 非课程路径，不写。
  let courseFp = ''
  // D14 语义版：corpus_fp = 语料身份（env+reward 解析值哈希，Python config.corpus_identity_fp）。
  // 与 course_fp 并存；worker 装载校验优先比 corpus_fp（预算/路径编辑不动它 → 不误拒）。
  let corpusFp = ''
  // plan/rl-training-config.md §5：自定义关 stageJson（课程配置 grid，13×13 瓦格）
  let stageJson = ''
  let livesOverride = ''
  let playerLevelOverride = ''
  // --dodge <mode>（goal-nn 卡 A3）：'' = 按 stage 解析（arena → 'l0'，真实关 →
  // 'off'，既有真实关 rollout 逐字节不变）；'off'|'l0'|'god' 强制（'god' 仅 A/B 报告用）。
  // 自定义关（--stage-json）恒为 'off'（守卫④：arena 默认 l0 会覆盖动作）。
  let dodgeArg = ''
  // --pack <path>（v3.6）：把单局结果打成 BCV2 容器写到指定路径——sampler-agent 用它把
  // base64+gzip+JSON 拼装从主线程下沉到本子进程并行执行（tools/sim/pack-container.ts）。
  let packPath = ''
  /** sampler HTTP：pack 直接用内存 entries，跳过 writeRlShard→read 回环（iter 仍写盘）。 */
  let packMemory = false
  // 起始分布注入（plan/x20-state-init.plan.md P1）：快照文件路径，'' = 标准开局（旧行为）。
  let initSnapshotPath = ''
  // x2 事件 rung（plan/new-era-stop.plan.md §6 R2）：显式开启才加 threat-ONSET；
  // 缺省 = 均匀 K（旧 manifest/_rl_report 逐字节不变）。
  let decisionEvents = false
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights') weightsPath = args[++i]
    else if (args[i] === '--stage-json') stageJson = args[++i]
    else if (args[i] === '--lives-override') livesOverride = args[++i]
    else if (args[i] === '--player-level') playerLevelOverride = args[++i]
    else if (args[i] === '--dodge') dodgeArg = args[++i]
    // 分布式溯源字段（plan/distributed-rollout.md v3.3）：仅在显式传入时写入，
    // 保证本机既有调用的 manifest/_rl_report 逐字节不变。
    else if (args[i] === '--wver') wver = args[++i]
    else if (args[i] === '--node-label') nodeLabel = args[++i]
    else if (args[i] === '--course-fp') courseFp = args[++i]
    else if (args[i] === '--corpus-fp') corpusFp = args[++i]
    else if (args[i] === '--pack') packPath = args[++i]
    else if (args[i] === '--pack-memory') packMemory = true
    else if (args[i] === '--init-snapshot') initSnapshotPath = args[++i]
    else if (args[i] === '--decision-events') decisionEvents = true
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  if (initSnapshotPath && (stages.length !== 1 || seeds.length !== 1)) {
    throw new Error(
      '[export-rl-rollout] --init-snapshot 只支持单局（--stages 与 --seeds 各一个）：' +
        '一个快照对应一局，多局共用同一个起始世界没有任何语义',
    )
  }
  mkdirSync(outDir, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')

  // 最近一局的单局 shard manifest（--pack 单局模式用：BCV2 manifest 必须是
  // **单局**（outcome/nSamples/metrics_version 等），不能是聚合 summary——
  // 2026-09-03 修正：此前 pack 用聚合 summary → dist/self 落盘缺 outcome →
  // engine 加载器把分布式局错标 timeout，奖励错算）。
  let lastShardManifest: Record<string, unknown> | null = null
  let lastPackEntries: Array<{ name: string; data: Buffer }> | null = null
  const outcomes: Record<string, number> = {}
  const scores: number[] = []
  const scoresUngated: number[] = []
  const dimAcc: Record<string, number[]> = {}
  let totalSamples = 0
  let totalTicks = 0
  let wins = 0
  let totalDecisionTicks = 0
  let totalDodgeTicks = 0
  let totalDeaths = 0
  let totalHits = 0
  let totalShots = 0
  let totalEnemyHits = 0
  let totalPowerUps = 0
  const decisionReadouts: DecisionReadout[] = []
  const perGame: string[] = []

  for (const si of stages) {
    // arena 编号命名空间（goal-nn 卡 A1）：si >= 1000 经 ARENA_LADDER 解析为
    // 玩具场；真实关走 STAGES。同一整数贯穿 course.py → run_rl.py → queue.py →
    // sampler-agent → 本解析层 → shard 命名，六环节零改动（agent 原样透传）。
    //
    // 守卫①（plan §5.2）：--stage-json 存在时**先**解码自定义关，短路
    // arena/真实关解析——否则 2000+i 走到 STAGES[2000] 未定义 → 静默 SKIP 漏跑。
    // spawn_variants（seed 哈希选点）存在时，同一 stage 每 seed 出生点不同 →
    // 解码必须发生在 seed loop 内。真实关/arena 舞台与 seed 无关，stage loop 解析一次。
    const arenaStage = stageJson ? null : isArenaId(si) ? resolveArenaStage(si) : null
    const baseStage = arenaStage ?? STAGES[si]
    if (!stageJson && !baseStage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    for (const seed of seeds) {
      const custom = stageJson ? decodeStageGrid(stageJson, si, seed) : null
      const stage = custom ?? baseStage!
      // 守卫④：自定义关强制 off（isArenaId(2000+i)=true → resolveDodge 默认 l0
      // 会覆盖动作——自定义关没有保底层语义，口径偏移不可接受）。
      const dodgeMode = custom ? 'off' : resolveDodge(dodgeArg, si)
      const res = runOne(
        si,
        stage,
        seed,
        difficulty,
        maxTicks,
        weightsText,
        dodgeMode,
        !!custom,
        resolveLivesFlag(livesOverride),
        playerLevelOverride ? parseInt(playerLevelOverride, 10) : null,
        initSnapshotPath ? { path: initSnapshotPath } : null,
        decisionEvents,
      )
      decisionReadouts.push(res.decisionReadout)
      outcomes[res.outcome] = (outcomes[res.outcome] ?? 0) + 1
      if (res.win) wins++
      scores.push(res.score)
      scoresUngated.push(res.scoreUngated)
      for (const [k, v] of Object.entries(res.dims)) {
        if (v.value !== null) (dimAcc[k] ??= []).push(v.value)
      }
      const shardName = `rl_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        collector: 'RL',
        policy: 'nn-student-rl',
        metrics_version: METRICS_VERSION, // [N+1,41] f8（idx0–40）—— v7 追加 puGotOther/pickupDist；shape[0] 下游据此分版本
        difficulty,
        stage: si,
        seed,
        outcome: res.outcome,
        ticks: res.ticks,
        nSamples: res.shard.n,
        k: K,
        // R2.3 决策门读数（`--decision-events` 时才有意义；缺省不写，旧 manifest 逐字节不变）。
        ...(decisionEvents ? { decisionReadout: res.decisionReadout } : {}),
        score: res.score, // 已 gated（base_destroyed ×BASE_LOSS_MULT）；Python reconcile 输入
        scoreUngated: res.scoreUngated,
        quality: res.quality,
        dims: res.dims,
        // arena 身份（卡 A1 验收③：布局散列与 reports/arena-layout-hashes.json 对账）
        ...(arenaStage
          ? {
              arena: {
                level: arenaLevelOfId(si),
                layoutHash: stageLayoutHash(arenaStage),
              },
            }
          : {}),
        // L0 保底层覆盖率（卡 A3 验收①：<2% 可辩护；≥2% 升级 P0 决策）
        dodge: {
          mode: dodgeMode,
          coverage: res.decisionTicks > 0 ? +(res.dodgeTicks / res.decisionTicks).toFixed(5) : 0,
          dodgeTicks: res.dodgeTicks,
          decisionTicks: res.decisionTicks,
        },
        kills: res.kills,
        enemyHits: res.enemyHits,
        hitRate: res.playerShots > 0 ? +(res.enemyHits / res.playerShots).toFixed(4) : 0,
        powerUpsCollected: res.powerUpsCollected,
        playerHits: res.playerHits,
        playerShots: res.playerShots,
        playerDamageTaken: res.playerDamageTaken,
        stuckTicks: res.stuckTicks,
        // 百分比指标数据源（控制台 iters 聚合，2026-09-16）：敌数/命数/死亡/加命
        enemyTotal: res.enemyTotal,
        startLives: res.startLives,
        playerDeaths: res.playerDeaths,
        puGotTank: res.puGotTank,
        puGotOther: res.puGotOther,
        // feat：本 shard 由哪条 features 后端产出（native / wasm / ts）。
        // 加它是为了「以为开了 native 其实回落了」能被事后看见（评审 B5③）；
        // **与 wver 解耦**：本机直跑（无 --wver）也必须记，否则「本机看到的读数走哪条后端」
        // 无从查证；只有真正依赖下发链路的 wver/node 才跟着 wver 走。
        // 不进 data_fp（那个只对 dir/wver/stage/seed 求 sha，见 protocol.py::data_fp）。
        feat: featuresEngine(),
        ...(wver ? { wver, node: nodeLabel } : {}),
        ...(courseFp ? { course_fp: courseFp } : {}),
        ...(corpusFp ? { corpus_fp: corpusFp } : {}),
        // 起始分布（additive-only）：只在注入了才写。分析侧据此把「继承的进度」从读数里剔掉。
        ...(res.initCounters
          ? {
              initSnapshot: res.initSnapshot,
              initTick: res.initTick,
              initCounters: res.initCounters,
            }
          : {}),
      }
      if (res.shard.n > 0) {
        lastShardManifest = manifest
        if (packMemory && packPath) {
          lastPackEntries = shardNpyEntries(res.shard)
        } else {
          writeRlShard(`${outDir}/${shardName}`, res.shard, manifest)
        }
      }
      totalSamples += res.shard.n
      totalTicks += res.ticks
      totalDecisionTicks += res.decisionTicks
      totalDodgeTicks += res.dodgeTicks
      totalDeaths += res.playerDeaths
      totalHits += res.playerHits
      totalShots += res.playerShots
      totalEnemyHits += res.enemyHits
      totalPowerUps += res.powerUpsCollected
      perGame.push(
        `[OK] s${si} seed${seed} samples=${res.shard.n} outcome=${res.outcome} ticks=${res.ticks} win=${res.win} score=${res.score.toFixed(3)} kills=${res.kills} enemyHits=${res.enemyHits} hitRate=${res.playerShots > 0 ? (res.enemyHits / res.playerShots).toFixed(3) : 0}` +
          (res.initCounters ? ` init=${res.initSnapshot}@${res.initTick}` : ''),
      )
    }
  }

  const total = seeds.length * stages.length
  const winRate = total > 0 ? wins / total : 0
  const stat = (xs: number[]): { mean: number; std: number; min: number; max: number } | null => {
    if (xs.length === 0) return null
    const mean = xs.reduce((a, b) => a + b, 0) / xs.length
    const std =
      xs.length > 1 ? Math.sqrt(xs.reduce((a, x) => a + (x - mean) ** 2, 0) / (xs.length - 1)) : 0
    return { mean, std, min: Math.min(...xs), max: Math.max(...xs) }
  }
  const dimMeans: Record<string, number> = {}
  for (const [k, xs] of Object.entries(dimAcc))
    dimMeans[k] = +(xs.reduce((a, b) => a + b, 0) / xs.length).toFixed(4)
  const summary = {
    collector: 'RL',
    metrics_version: METRICS_VERSION, // [N+1,41] f8（idx0–40）—— 与 manifest 同版（下游分版本读取）
    customStages: stageJson ? '1' : '0', // 自定义关（Python 课程）标记
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
    dodge: {
      mode: stageJson
        ? 'off'
        : [...new Set(stages.map((si) => resolveDodge(dodgeArg, si)))].join(','),
      coverage: totalDecisionTicks > 0 ? +(totalDodgeTicks / totalDecisionTicks).toFixed(5) : 0,
      dodgeTicks: totalDodgeTicks,
      decisionTicks: totalDecisionTicks,
    },
    // R2.3 avg-Δt 读数（`--decision-events` 时才有意义；缺省不写 = 旧报告逐字节不变）。
    ...(decisionEvents ? { decision: poolDecisionReadouts(decisionReadouts) } : {}),
    behavior: {
      deathsPerGame: +(totalDeaths / Math.max(1, total)).toFixed(3),
      playerHitsPerGame: +(totalHits / Math.max(1, total)).toFixed(3),
      shotsPerGame: +(totalShots / Math.max(1, total)).toFixed(2),
      enemyHitsPerGame: +(totalEnemyHits / Math.max(1, total)).toFixed(3),
      hitRateOverall: totalShots > 0 ? +(totalEnemyHits / totalShots).toFixed(4) : 0,
      powerUpsPerGame: +(totalPowerUps / Math.max(1, total)).toFixed(3),
    },
    scoreStats: stat(scores),
    // 未门控的纯 v7 分：与 God-AI 基线口径可比，用于诊断门控前后的行为分化
    scoreStatsUngated: stat(scoresUngated),
    dimMeans,
    // 原始值列表：供 run_rl.py 跨 worker 精确重聚合
    scoreList: scores.map((x) => +x.toFixed(5)),
    dimLists: Object.fromEntries(
      Object.entries(dimAcc).map(([k, xs]) => [k, xs.map((x) => +x.toFixed(5))]),
    ),
    ...(wver ? { wver, node: nodeLabel } : {}),
    ...(courseFp ? { course_fp: courseFp } : {}),
    ...(corpusFp ? { corpus_fp: corpusFp } : {}),
    ...(initSnapshotPath
      ? { initSnapshot: basename(initSnapshotPath), initTick: lastShardManifest?.['initTick'] }
      : {}),
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== RL on-policy rollout (R3 v7-aligned-f3) ===`)
  console.log(`games=${total} winRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`)
  console.log(`score=${JSON.stringify(summary.scoreStats)}`)
  console.log(`dims=${JSON.stringify(dimMeans)}`)
  if (decisionEvents) {
    console.log(`decision=${JSON.stringify(summary.decision)}`)
  }
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks}`)
  // --pack-memory：npy 只在内存里组装后进容器，盘上**没有** shard 目录 —— 再打
  // 「shards under: …」会把排障的人往空目录引（sampler-agent 路径的实际消费方是 pack）。
  console.log(
    packMemory
      ? 'pack-memory: shards 未落盘（内存直进容器，消费方 = sampler-agent 的 --pack）'
      : `shards under: ${outDir}  (consume with ppo.py)`,
  )
  writeFileSync(`${outDir}/_rl_report.json`, JSON.stringify(summary, null, 2))

  // ---- BCV2 结果容器（v3.6，sampler-agent 专用；本机直跑不带 --pack 时完全无感）----
  if (packPath) {
    if (stages.length !== 1 || seeds.length !== 1) {
      throw new Error('[export-rl-rollout] --pack requires exactly one stage and one seed')
    }
    let entries: Array<{ name: string; data: Buffer }>
    if (packMemory && lastPackEntries) {
      entries = lastPackEntries
    } else {
      const shardDir = `${outDir}/rl_s${stages[0]}_seed${seeds[0]}`
      if (!existsSync(shardDir)) {
        console.error(
          `[export-rl-rollout] --pack: no shards written for s${stages[0]}/seed${seeds[0]} ` +
            `(0 samples — check maxTicks/stage validity)`,
        )
      }
      entries = RL_SHARD_FILES.map((name) => ({
        name,
        data: readFileSync(`${shardDir}/${name}`),
      }))
    }
    const packManifest = {
      ...(lastShardManifest ?? summary),
      mode: 'rollout',
      elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    }
    writeFileSync(packPath, buildPack(packManifest, entries))
  }
}

if (import.meta.main) {
  if (process.argv.includes('--serve')) runServe(main)
  else main()
}
