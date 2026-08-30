/**
 * export-rl-rollout.ts — RL on-policy rollout collector (R3: v7-aligned reward).
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
 * 输出 shards（每局一个目录，npy + manifest）：
 *   obs (N,14,26,26) u1 | scalars (N,24) f4 | a_x / lp_x / value/reward (N,) | done | mask (N,10)
 * manifest 记录 outcome/ticks + score/quality + 全部 11 维 dims{value,raw}
 * ——训练侧与事后分析可直接用同一套数字。
 *
 * Usage:
 *   bun tools/sim/export-rl-rollout.ts --weights tmp/rl-weights/weights.json \
 *       --out tmp/rl-traj/it1 --stages 0-3 --seeds 0-3 --max-ticks 12000
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, ENEMIES_PER_STAGE, BASE_POS, CELL, GRID } from '../../src/constants'
import { type Direction } from '../../src/constants'
import { ObsEncoder, computeMasks, OBS_SCHEMA_MAJOR } from '../../src/nn/obs-encoder'
import {
  isArenaId,
  resolveArenaStage,
  arenaLevelOfId,
  stageLayoutHash,
} from '../../src/nn/arena-ladder'
import {
  TOY_REWARD_ARMS,
  TOY_REWARD_DEFAULT_ARM,
  toyPotential,
  toyTerminal,
  type ToyRewardArm,
} from '../../src/nn/rl-reward-toy'
import { buildModelFromText } from '../../src/nn/infer'
import { dodgeL0 } from '../../src/nn/dodge-l0'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { RNG } from '../../src/utils/RNG'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs'
import { buildPack } from './pack-container'
import {
  scoreRun,
  V7_SCORE_CONFIG,
  DEFAULT_STAGE_REFS,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const MAX_TICKS = 36000
const K = 10
const MOVE_DIM = 5
const FIRE_DIM = 2
const MASK_DIM = MOVE_DIM + FIRE_DIM // 7 (v2: item head removed)

// shard 文件名清单（与 writeRlShard 的 writeNpy 调用一一对应；--pack 打容器时按此顺序）。
const RL_SHARD_FILES = [
  'obs.npy',
  'scalars.npy',
  'a_move.npy',
  'a_fire.npy',
  'lp_move.npy',
  'lp_fire.npy',
  'value.npy',
  'reward.npy',
  'done.npy',
  'mask.npy',
] as const

// ---- R3 reward constants ----
const REWARD_SCALE = 10 // 奖励尺度：每局总回报 ≡ REWARD_SCALE × gatedScore
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

const MOVE_DECODE: Direction[] = ['up', 'down', 'left', 'right']

/** 受控输入：实现 InputLike，把采样动作施加到 World（持有门控，None=hold lastDir）。 */
class ScriptedInput {
  moveDir: Direction | null = null
  lastDir: Direction = 'up'
  firing = false

  setAction(move: number, fire: number): void {
    if (move === 0) this.moveDir = this.lastDir
    else {
      this.lastDir = MOVE_DECODE[move - 1]
      this.moveDir = this.lastDir
    }
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
    this.lastDir = 'up'
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
interface Telemetry {
  enemyTotal: number
  startLives: number
  playerDeaths: number
  /** 玩家被命中次数（player_hit 事件：死亡 + 星盾消耗）。玩具奖励的 w_dmg 项。 */
  playerHits: number
  playerShots: number
  powerUpsSpawned: number
  powerUpsCollected: number
  starsCollected: number
  baseWallTotal: number
  baseWallIntact: number
  basePressureSum: number
  basePressureSamples: number
  cellsVisited: Set<number>
  firstKillTick: number | undefined
}

function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0
  return x < 0 ? 0 : Math.min(x, 1)
}

function ramp(x: number, lo: number, hi: number): number {
  if (hi === lo) return x >= hi ? 1 : 0
  return clamp01((x - lo) / (hi - lo))
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

/** 当前计数器下的 losses 带部分质量（权重重分配规则镜像 weightedQuality）。 */
function lossPartialQ(
  t: Telemetry,
  kills: number,
  lives: number,
  ticks: number,
  baseAlive: boolean,
  w: Weights,
): number {
  let acc = 0
  let wsum = 0
  const add = (key: DimensionKey, v: number | null): void => {
    const weight = w[key]
    if (v === null || weight === undefined || weight <= 0) return
    acc += weight * v
    wsum += weight
  }
  const minutes = ticks / 3600
  add('progress', t.enemyTotal > 0 ? clamp01(kills / t.enemyTotal) : null)
  add('lives', t.startLives > 0 ? clamp01(lives / t.startLives) : null)
  add(
    'baseIntegrity',
    !baseAlive
      ? 0
      : t.baseWallTotal > 0
        ? 0.55 + 0.45 * clamp01(t.baseWallIntact / t.baseWallTotal)
        : null,
  )
  // R6 修复：tempo / accuracy 的归一化参考值必须是 StageRefs（kpmRef=8、
  // accuracyRef=0.3），不能拿「权重」当参考——原实现用 w.tempo(=0.026)/w.accuracy
  // 作除数，导致 tempo 恒饱和到 1.0（无梯度）、accuracy 因 DEFAULT_LOSS_WEIGHTS
  // 无 accuracy 键而恒为 null（死维度）。与 computeDimensions 对齐。
  add(
    'tempo',
    DEFAULT_STAGE_REFS.kpmRef > 0
      ? clamp01(minutes > 0 ? kills / minutes / DEFAULT_STAGE_REFS.kpmRef : 0)
      : null,
  )
  add(
    'accuracy',
    t.playerShots > 0 && DEFAULT_STAGE_REFS.accuracyRef > 0
      ? clamp01(kills / t.playerShots / DEFAULT_STAGE_REFS.accuracyRef)
      : null,
  )
  add('loot', t.powerUpsSpawned > 0 ? clamp01(t.powerUpsCollected / t.powerUpsSpawned) : null)
  add(
    'baseSafety',
    t.basePressureSamples > 0 ? clamp01(1 - t.basePressureSum / t.basePressureSamples) : null,
  )
  add('openingTempo', t.firstKillTick === undefined ? 0 : 1 - ramp(t.firstKillTick, 0, 1800))
  return wsum > 0 ? acc / wsum : 0
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

interface ShardData {
  obs: Uint8Array[]
  scalars: Float32Array[]
  aMove: number[]
  aFire: number[]
  lpMove: number[]
  lpFire: number[]
  value: number[]
  reward: number[]
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
    reward: [],
    done: [],
    mask: [],
    n: 0,
  }
}

interface RunResult {
  shard: ShardData
  outcome: string
  ticks: number
  win: boolean
  score: number
  scoreUngated: number
  quality: number
  dims: Record<string, { value: number | null; raw: number }>
  decisionTicks: number
  dodgeTicks: number
  playerDeaths: number
  playerHits: number
  playerShots: number
}

/** 奖励 scheme 解析结果（§3.4：arena → 玩具臂；真实关 → v7；--reward 可显式覆盖）。 */
type ResolvedReward =
  | { scheme: 'v7'; label: string }
  | { scheme: 'toy'; arm: ToyRewardArm; label: string }

function resolveReward(rewardArg: string, stageIdx: number): ResolvedReward {
  if (rewardArg === 'v7') return { scheme: 'v7', label: 'v7-aligned-f3' }
  if (rewardArg.startsWith('toy:')) {
    const arm = TOY_REWARD_ARMS[rewardArg.slice(4)]
    if (!arm)
      throw new Error(
        `unknown toy reward arm: ${rewardArg} (known: ${Object.keys(TOY_REWARD_ARMS).join('|')})`,
      )
    return { scheme: 'toy', arm, label: `toy-${arm.name}` }
  }
  // 缺省：arena 编号 → 级默认臂（TOY_REWARD_DEFAULT_ARM，A2 扫描选定后改写）；
  // 真实关 → v7（既有口径，逐字节不变）。
  const level = arenaLevelOfId(stageIdx)
  if (level) {
    const armName = TOY_REWARD_DEFAULT_ARM[level]
    return { scheme: 'toy', arm: TOY_REWARD_ARMS[armName], label: `toy-${armName}` }
  }
  return { scheme: 'v7', label: 'v7-aligned-f3' }
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
  reward: ResolvedReward,
  dodgeMode: 'off' | 'l0' | 'god',
): RunResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const model = buildModelFromText(weightsText) as unknown as RolloutModel
  const scripted = new ScriptedInput()
  const sim = new Simulation(world, scripted as any)
  // arena 编号不得进入 loadStageData 的 stageIndex：index 进 killScore 的
  // 1.05^index 关卡缩放，而分数经 dropOnScoreMilestone 反哺玩法——index=1000
  // 时单杀得分 ~4e22，里程碑掉落循环 push ~1e18 个掉落物 → 内存耗尽段错误
  //（2026-08-30 实测，tmp/memprobe.ts 逐语句定位）。arena 一律用 index 0
  // （与 World.loadStageData 文档"generated stages use index 0"同口径）。
  world.loadStageData(stage, isArenaId(stageIdx) ? 0 : stageIdx)
  scripted.reset()
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
    startLives: world.difficulty?.startLives ?? START_LIVES,
    playerDeaths: 0,
    playerHits: 0,
    playerShots: 0,
    powerUpsSpawned: 0,
    powerUpsCollected: 0,
    starsCollected: 0,
    baseWallTotal: countBaseWall(world),
    baseWallIntact: countBaseWall(world),
    basePressureSum: 0,
    basePressureSamples: 0,
    cellsVisited: new Set<number>(),
    firstKillTick: undefined,
  }
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()

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
  let phiPrev = 0 // 势基准：Φ_0 全额计入首窗，终局对账保证 Σr ≡ SCALE×score
  let paidTotal = 0 // Σ 已支付势差
  let t = 0
  let outcome = 'timeout'
  let decisionTicks = 0 // 决策 tick 数（K 间隔）
  let dodgeTicks = 0 // L0/保底层覆盖采样动作的决策 tick 数（§3.5 覆盖率口径）

  const countersPhi = (): number => {
    if (reward.scheme === 'toy') {
      // 玩具场势（§3.4 / 卡 A2）：击杀 − 被命中 + 存活；与 v7 势互斥。
      return toyPotential({ kills: world.killCount, playerHits: tel.playerHits }, t, reward.arm)
    }
    tel.baseWallIntact = countBaseWall(world)
    const baseAlive = !world.tileMap.isBaseDestroyed()
    // F3：M 进入 Φ——基地被拆后势 ×M，塌陷负势差精确记入死亡所在窗。
    return (
      REWARD_SCALE *
      RL_SCORE_CONFIG.lossBandMax *
      lossPartialQ(tel, world.killCount, world.lives, t, baseAlive, RL_LOSS_WEIGHTS) *
      (baseAlive ? 1 : BASE_LOSS_MULT)
    )
  }

  const flushPending = (reward: number, term: boolean): void => {
    if (!pending) return
    shard.obs.push(pending.obs)
    shard.scalars.push(pending.sc)
    shard.aMove.push(pending.aMove)
    shard.aFire.push(pending.aFire)
    shard.lpMove.push(pending.lpMove)
    shard.lpFire.push(pending.lpFire)
    shard.value.push(pending.value)
    shard.reward.push(reward)
    shard.done.push(term ? 1 : 0)
    for (let j = 0; j < MASK_DIM; j++) shard.mask.push(pending.mask[j])
    shard.n++
    pending = null
  }

  while (t < maxTicks) {
    encoder.encode(world)
    if (t % K === 0) {
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
        const sampledDir = mv.idx === 0 ? scripted.lastDir : MOVE_DECODE[mv.idx - 1]
        const d = dodgeL0(world, sampledDir)
        if (d.triggered && d.dir) {
          aMove = MOVE_DECODE.indexOf(d.dir) + 1
          lpMove = logProbAt(model.moveLogits, masks.move, aMove)
          dodgeTicks++
        }
      } else if (dodgeMode === 'god' && godProbe) {
        godProbe.getMoveDirection()
        godProbe.isFiring()
        if (godProbe._lastBranch === 'dodge' && godProbe._moveDir) {
          aMove = MOVE_DECODE.indexOf(godProbe._moveDir) + 1
          lpMove = logProbAt(model.moveLogits, masks.move, aMove)
          dodgeTicks++
        }
        godProbe.endFrame()
      }

      // 窗口势差结算：上一窗口的 Φ 变化记入其 reward。
      // 注意：首个决策点无 pending 可收，只建立势基准，不入账。
      const phiNow = countersPhi()
      if (pending) {
        const dq = phiNow - phiPrev
        paidTotal += dq
        flushPending(dq, false)
      }
      phiPrev = phiNow

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
    }
    sim.tick()
    scripted.endFrame()
    t++

    // ---- telemetry（语义对齐 simulation-runner）----
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).by === 'player' && tel.firstKillTick === undefined) tel.firstKillTick = t - 1
        if ((e as any).tank?.isPlayer) tel.playerDeaths++
      } else if (e.type === 'player_hit') {
        tel.playerHits++
      } else if (e.type === 'bullet_fired' && (e as any).bullet?.isPlayer) {
        tel.playerShots++
      } else if (e.type === 'powerup_collected') {
        collectedThisTick++
        tel.powerUpsCollected++
        if ((e as any).powerUp === 'star') tel.starsCollected++
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
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      tel.powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
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
  // flush 最后一个 pending（含部分窗口势差，done=1），修复 §3.6(b) 样本丢失。
  const phiEnd = countersPhi()
  if (pending) {
    const dqEnd = phiEnd - phiPrev
    paidTotal += dqEnd
    flushPending(dqEnd, true)
  }

  // ---- 精确 v7 打分 + 对账：Σr ≡ SCALE × score（恒等式）----
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: world.killCount,
      lives: world.lives,
      baseAlive: !world.tileMap.isBaseDestroyed(),
    },
    firstKillTick: tel.firstKillTick,
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
  // F3 终局锚点：base_destroyed 局对账目标 ×= M。恒等式变为 Σr ≡ SCALE × gatedScore
  // （manifest.score 即 gated 值，训练侧无需感知）。
  const gatedScore = outcome === 'base_destroyed' ? scored.score * BASE_LOSS_MULT : scored.score
  if (shard.n > 0) {
    if (reward.scheme === 'toy') {
      // 玩具场（卡 A2）：终局奖励 = 全歼 +w_clear / 阵亡终局 −w_death；势差已在窗口付讫。
      shard.reward[shard.n - 1] += toyTerminal(outcome, reward.arm)
    } else {
      shard.reward[shard.n - 1] += REWARD_SCALE * gatedScore - paidTotal
    }
  }

  const win = outcome === 'stage_clear'
  const dims: Record<string, { value: number | null; raw: number }> = {}
  for (const k of Object.keys(scored.dims) as DimensionKey[]) {
    dims[k] = { value: scored.dims[k].value, raw: scored.dims[k].raw }
  }
  return {
    shard,
    outcome,
    ticks: t,
    win,
    score: gatedScore,
    scoreUngated: scored.score,
    quality: scored.quality,
    dims,
    decisionTicks,
    dodgeTicks,
    playerDeaths: tel.playerDeaths,
    playerHits: tel.playerHits,
    playerShots: tel.playerShots,
  }
}

function visitedCellsAdd(set: Set<number>, col: number, row: number): void {
  set.add(row * GRID + col)
}

function writeRlShard(dir: string, d: ShardData, manifest: unknown): void {
  const N = d.n
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 19)
  const aMove = new Uint8Array(N)
  const aFire = new Uint8Array(N)
  const lpMove = new Float32Array(N)
  const lpFire = new Float32Array(N)
  const value = new Float32Array(N)
  const reward = new Float32Array(N)
  const done = new Uint8Array(N)
  const mask = new Uint8Array(N * MASK_DIM)
  for (let i = 0; i < N; i++) {
    obs.set(d.obs[i], i * 14 * 26 * 26)
    scalars.set(d.scalars[i], i * 19)
    aMove[i] = d.aMove[i]
    aFire[i] = d.aFire[i]
    lpMove[i] = d.lpMove[i]
    lpFire[i] = d.lpFire[i]
    value[i] = d.value[i]
    reward[i] = d.reward[i]
    done[i] = d.done[i]
    for (let j = 0; j < MASK_DIM; j++) mask[i * MASK_DIM + j] = d.mask[i * MASK_DIM + j]
  }
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, 19], 'f4')
  writeNpy(`${dir}/a_move.npy`, aMove, [N], 'u1')
  writeNpy(`${dir}/a_fire.npy`, aFire, [N], 'u1')
  writeNpy(`${dir}/lp_move.npy`, lpMove, [N], 'f4')
  writeNpy(`${dir}/lp_fire.npy`, lpFire, [N], 'f4')
  writeNpy(`${dir}/value.npy`, value, [N], 'f4')
  writeNpy(`${dir}/reward.npy`, reward, [N], 'f4')
  writeNpy(`${dir}/done.npy`, done, [N], 'u1')
  writeNpy(`${dir}/mask.npy`, mask, [N, MASK_DIM], 'u1')
  writeFileSync(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2))
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

function main(): void {
  const t0 = Date.now()
  const args = process.argv.slice(2)
  let outDir = 'tmp/rl-traj'
  let difficulty = 'hard'
  let stagesStr = '0-3'
  let seedsStr = '0-3'
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/rl-weights/weights.json'
  let wver = ''
  let nodeLabel = ''
  // --reward <spec>（goal-nn 卡 A2）：'' = 按 stage 解析（arena → 玩具默认臂 /
  // 真实关 → v7）；'v7' 强制 v7；'toy:<arm>' 强制玩具臂（扫参用）。
  let rewardArg = ''
  // --dodge <mode>（goal-nn 卡 A3）：'' = 按 stage 解析（arena → 'l0'，真实关 →
  // 'off'，既有真实关 rollout 逐字节不变）；'off'|'l0'|'god' 强制（'god' 仅 A/B 报告用）。
  let dodgeArg = ''
  // --pack <path>（v3.6）：把单局结果打成 BCV2 容器写到指定路径——sampler-agent 用它把
  // base64+gzip+JSON 拼装从主线程下沉到本子进程并行执行（tools/sim/pack-container.ts）。
  let packPath = ''
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights') weightsPath = args[++i]
    else if (args[i] === '--reward') rewardArg = args[++i]
    else if (args[i] === '--dodge') dodgeArg = args[++i]
    // 分布式溯源字段（plan/distributed-rollout.md v3.3）：仅在显式传入时写入，
    // 保证本机既有调用的 manifest/_rl_report 逐字节不变。
    else if (args[i] === '--wver') wver = args[++i]
    else if (args[i] === '--node-label') nodeLabel = args[++i]
    else if (args[i] === '--pack') packPath = args[++i]
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')

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
  const perGame: string[] = []

  for (const si of stages) {
    // arena 编号命名空间（goal-nn 卡 A1）：si >= 1000 经 ARENA_LADDER 解析为
    // 玩具场；真实关走 STAGES。同一整数贯穿 course.py → run_rl.py → queue.py →
    // sampler-agent → 本解析层 → shard 命名，六环节零改动（agent 原样透传）。
    const arenaStage = isArenaId(si) ? resolveArenaStage(si) : null
    const stage = arenaStage ?? STAGES[si]
    if (!stage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    const reward = resolveReward(rewardArg, si)
    const dodgeMode = resolveDodge(dodgeArg, si)
    for (const seed of seeds) {
      const res = runOne(si, stage, seed, difficulty, maxTicks, weightsText, reward, dodgeMode)
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
        rewardScheme: reward.label,
        difficulty,
        stage: si,
        seed,
        outcome: res.outcome,
        ticks: res.ticks,
        nSamples: res.shard.n,
        k: K,
        score: res.score,
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
        ...(wver ? { wver, node: nodeLabel } : {}),
      }
      if (res.shard.n > 0) writeRlShard(`${outDir}/${shardName}`, res.shard, manifest)
      totalSamples += res.shard.n
      totalTicks += res.ticks
      totalDecisionTicks += res.decisionTicks
      totalDodgeTicks += res.dodgeTicks
      totalDeaths += res.playerDeaths
      totalHits += res.playerHits
      totalShots += res.playerShots
      perGame.push(
        `[OK] s${si} seed${seed} samples=${res.shard.n} outcome=${res.outcome} ticks=${res.ticks} win=${res.win} score=${res.score.toFixed(3)} kills=${res.dims.progress.raw}`,
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
    // 混合 stage 集可能多 scheme（实践中单 stage/局）——逐局 label 集合。
    rewardScheme: [...new Set(stages.map((si) => resolveReward(rewardArg, si).label))].join(','),
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
    dodge: {
      mode: [...new Set(stages.map((si) => resolveDodge(dodgeArg, si)))].join(','),
      coverage: totalDecisionTicks > 0 ? +(totalDodgeTicks / totalDecisionTicks).toFixed(5) : 0,
      dodgeTicks: totalDodgeTicks,
      decisionTicks: totalDecisionTicks,
    },
    behavior: {
      deathsPerGame: +(totalDeaths / Math.max(1, total)).toFixed(3),
      hitsPerGame: +(totalHits / Math.max(1, total)).toFixed(3),
      shotsPerGame: +(totalShots / Math.max(1, total)).toFixed(2),
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
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== RL on-policy rollout (R3 v7-aligned-f3) ===`)
  console.log(`games=${total} winRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`)
  console.log(`score=${JSON.stringify(summary.scoreStats)}`)
  console.log(`dims=${JSON.stringify(dimMeans)}`)
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks}`)
  console.log(`shards under: ${outDir}  (consume with ppo.py)`)
  writeFileSync(`${outDir}/_rl_report.json`, JSON.stringify(summary, null, 2))

  // ---- BCV2 结果容器（v3.6，sampler-agent 专用；本机直跑不带 --pack 时完全无感）----
  if (packPath) {
    if (stages.length !== 1 || seeds.length !== 1) {
      console.error('[export-rl-rollout] --pack requires exactly one stage and one seed')
      process.exit(2)
    }
    const shardDir = `${outDir}/rl_s${stages[0]}_seed${seeds[0]}`
    // 0 样本局（maxTicks<K 等异常参数）不会写 shard 目录——显式报错而非 ENOENT 堆栈。
    if (!existsSync(shardDir)) {
      console.error(
        `[export-rl-rollout] --pack: no shards written for s${stages[0]}/seed${seeds[0]} ` +
          `(0 samples — check maxTicks/stage validity)`,
      )
      process.exit(3)
    }
    const entries = RL_SHARD_FILES.map((name) => ({
      name,
      data: readFileSync(`${shardDir}/${name}`),
    }))
    // 溯源戳与 v1 agent 主线程所盖戳逐字段一致：validate_result 按
    // manifest.stage/seed/wver 对账（标量），elapsedSec 语义改为子进程内耗时。
    const packManifest = {
      ...summary,
      stage: stages[0],
      seed: seeds[0],
      mode: 'rollout',
      elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
    }
    writeFileSync(packPath, buildPack(packManifest, entries))
  }
}

if (import.meta.main) main()
