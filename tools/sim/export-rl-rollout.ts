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
 * F3 基地失守门控（2026-08-22）：base_destroyed 局 gatedScore = v7 score × M(0.25)。
 * R3 长跑实证 v7 败局带存在 Goodhart 倒挂——秒投降局（lives/baseSafety 满值、
 * 其余维度归零）score=0.1211 高于认真打仗的 ~0.110，PPO 理性收敛到投降。
 * 门控同时落两点：① 终局锚点（改变总回报，翻转倒挂：投降上限 0.121×0.25≈0.03）；
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
import { buildModelFromText } from '../../src/nn/infer'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync, readFileSync } from 'fs'
import {
  scoreRun,
  V7_SCORE_CONFIG,
  DEFAULT_LOSS_WEIGHTS,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const MAX_TICKS = 36000
const K = 10
const MOVE_DIM = 5
const FIRE_DIM = 2
const ITEM_DIM = 3
const MASK_DIM = MOVE_DIM + FIRE_DIM + ITEM_DIM

// ---- R3 reward constants ----
const REWARD_SCALE = 10 // 奖励尺度：每局总回报 ≡ REWARD_SCALE × gatedScore
// F3：基地失守局终局 score ×= 0.25（投降上限 ≈0.03 < 打仗实测 ~0.10+，翻转倒挂）。
const BASE_LOSS_MULT = 0.25
// F3b：RL 奖励的败局带剔除 lives。败局里的「剩余生命」只可能出现在 base_destroyed
// 局（lives_exhausted 局 lives=0）——它支付的正是「基地死时自己没死」的投降画像，
// 且与打仗行为负相关（交战才有阵亡风险），是坍缩的主要收入源（0.256×1.0/0.991）。
// 评估口径 godai-score.ts 的 DEFAULT_LOSS_WEIGHTS 保持原值不动（God-AI 基线可比性）。
const RL_LOSS_WEIGHTS: Weights = { ...DEFAULT_LOSS_WEIGHTS, lives: 0 }
// RL 专用打分配置：v7 带几何 + 剔除 lives 的败局带。weightedQuality 对 w<=0 维度
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
  guardPulse = false
  frenzyPulse = false
  private curItem = 0

  setAction(move: number, fire: number, item: number): void {
    this.curItem = item
    if (move === 0) this.moveDir = this.lastDir
    else {
      this.lastDir = MOVE_DECODE[move - 1]
      this.moveDir = this.lastDir
    }
    this.firing = fire === 1
    // 道具脉冲只在持有窗口首帧有效（endFrame 后清零），避免每帧重复激活。
    this.guardPulse = this.curItem === 1
    this.frenzyPulse = this.curItem === 2
  }

  getMoveDirection(): Direction | null {
    return this.moveDir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    if (kind === 'guard') return this.guardPulse
    if (kind === 'frenzy') return this.frenzyPulse
    return false
  }
  endFrame(): void {
    this.guardPulse = false
    this.frenzyPulse = false
  }
  reset(): void {
    this.moveDir = null
    this.lastDir = 'up'
    this.firing = false
    this.guardPulse = false
    this.frenzyPulse = false
    this.curItem = 0
  }
}

interface RolloutModel {
  forward(obs: Uint8Array, scalars: Float32Array): void
  readonly moveLogits: Float32Array
  readonly fireLogits: Float32Array
  readonly itemLogits: Float32Array
  readonly valueOut: Float32Array
}

// ---- per-game telemetry（语义逐字段对齐 simulation-runner）----
interface Telemetry {
  enemyTotal: number
  startLives: number
  playerDeaths: number
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
  add(
    'tempo',
    (w.tempo ?? 0) > 0 ? clamp01(minutes > 0 ? kills / minutes / (w.tempo as number) : 0) : null,
  )
  add(
    'accuracy',
    t.playerShots > 0 && (w.accuracy ?? 0) > 0
      ? clamp01(kills / t.playerShots / (w.accuracy ?? 0.3))
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

interface ShardData {
  obs: Uint8Array[]
  scalars: Float32Array[]
  aMove: number[]
  aFire: number[]
  aItem: number[]
  lpMove: number[]
  lpFire: number[]
  lpItem: number[]
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
    aItem: [],
    lpMove: [],
    lpFire: [],
    lpItem: [],
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
}

function runOne(
  stageIdx: number,
  stage: any,
  seed: number,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
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
  world.loadStageData(stage, stageIdx)
  scripted.reset()

  const encoder = new ObsEncoder()
  const shard = newShard()
  const rng = mulberry32((seed ^ 0x85ebca6b) >>> 0)

  const tel: Telemetry = {
    enemyTotal: (stage as any)?.enemyCount ?? ENEMIES_PER_STAGE,
    startLives: world.difficulty?.startLives ?? START_LIVES,
    playerDeaths: 0,
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
    aItem: number
    lpMove: number
    lpFire: number
    lpItem: number
    value: number
    mask: number[]
  } | null = null
  let phiPrev = 0 // 势基准：Φ_0 全额计入首窗，终局对账保证 Σr ≡ SCALE×score
  let paidTotal = 0 // Σ 已支付势差
  let t = 0
  let outcome = 'timeout'

  const countersPhi = (): number => {
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
    shard.aItem.push(pending.aItem)
    shard.lpMove.push(pending.lpMove)
    shard.lpFire.push(pending.lpFire)
    shard.lpItem.push(pending.lpItem)
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
      const it = sampleCat(model.itemLogits, masks.item, rng)
      const value = model.valueOut[0]

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
        aMove: mv.idx,
        aFire: fr.idx,
        aItem: it.idx,
        lpMove: mv.logp,
        lpFire: fr.logp,
        lpItem: it.logp,
        value,
        mask: [...masks.move, ...masks.fire, ...masks.item],
      }
      scripted.setAction(mv.idx, fr.idx, it.idx)
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
    shard.reward[shard.n - 1] += REWARD_SCALE * gatedScore - paidTotal
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
  }
}

function visitedCellsAdd(set: Set<number>, col: number, row: number): void {
  set.add(row * GRID + col)
}

function writeRlShard(dir: string, d: ShardData, manifest: unknown): void {
  const N = d.n
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 24)
  const aMove = new Uint8Array(N)
  const aFire = new Uint8Array(N)
  const aItem = new Uint8Array(N)
  const lpMove = new Float32Array(N)
  const lpFire = new Float32Array(N)
  const lpItem = new Float32Array(N)
  const value = new Float32Array(N)
  const reward = new Float32Array(N)
  const done = new Uint8Array(N)
  const mask = new Uint8Array(N * MASK_DIM)
  for (let i = 0; i < N; i++) {
    obs.set(d.obs[i], i * 14 * 26 * 26)
    scalars.set(d.scalars[i], i * 24)
    aMove[i] = d.aMove[i]
    aFire[i] = d.aFire[i]
    aItem[i] = d.aItem[i]
    lpMove[i] = d.lpMove[i]
    lpFire[i] = d.lpFire[i]
    lpItem[i] = d.lpItem[i]
    value[i] = d.value[i]
    reward[i] = d.reward[i]
    done[i] = d.done[i]
    for (let j = 0; j < MASK_DIM; j++) mask[i * MASK_DIM + j] = d.mask[i * MASK_DIM + j]
  }
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, 24], 'f4')
  writeNpy(`${dir}/a_move.npy`, aMove, [N], 'u1')
  writeNpy(`${dir}/a_fire.npy`, aFire, [N], 'u1')
  writeNpy(`${dir}/a_item.npy`, aItem, [N], 'u1')
  writeNpy(`${dir}/lp_move.npy`, lpMove, [N], 'f4')
  writeNpy(`${dir}/lp_fire.npy`, lpFire, [N], 'f4')
  writeNpy(`${dir}/lp_item.npy`, lpItem, [N], 'f4')
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
  const args = process.argv.slice(2)
  let outDir = 'tmp/rl-traj'
  let difficulty = 'hard'
  let stagesStr = '0-3'
  let seedsStr = '0-3'
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/rl-weights/weights.json'
  let wver = ''
  let nodeLabel = ''
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights') weightsPath = args[++i]
    // 分布式溯源字段（plan/distributed-rollout.md v3.3）：仅在显式传入时写入，
    // 保证本机既有调用的 manifest/_rl_report 逐字节不变。
    else if (args[i] === '--wver') wver = args[++i]
    else if (args[i] === '--node-label') nodeLabel = args[++i]
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
  const perGame: string[] = []

  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    for (const seed of seeds) {
      const res = runOne(si, stage, seed, difficulty, maxTicks, weightsText)
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
        rewardScheme: 'v7-aligned-f3',
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
        ...(wver ? { wver, node: nodeLabel } : {}),
      }
      if (res.shard.n > 0) writeRlShard(`${outDir}/${shardName}`, res.shard, manifest)
      totalSamples += res.shard.n
      totalTicks += res.ticks
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
    rewardScheme: 'v7-aligned-f3',
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
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
}

if (import.meta.main) main()
