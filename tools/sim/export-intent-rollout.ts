/**
 * export-intent-rollout.ts — M8 意图 RL on-policy rollout collector（意图步 semi-MDP）。
 *
 * 与 export-rl-rollout.ts（per-tick move/fire）相对，本采集器按 **意图步** 记录：
 *   决策只在 replan tick（默认 replanEvery=30）发生；动作 = 采样的意图（8 类，
 *   ESCAPE 依死类掩码剔除）；窗口内意图冻结（IntentExecutor rlPick → God-AI 白名单
 *   子链共享委托）；奖励按窗口累计（intent-rl-reward.ts：击杀/清砖/拾取/阵亡/基地墙损
 *   + potential shaping + 无产出切换成本 + 终局）。
 *
 * 输出 shards（每局一个目录，npy + manifest）：
 *   obs (N,14,26,26) u1 | scalars (N,19) f4 | inject (N,9) f4（prev one-hot 8 + duration）
 *   a_intent (N,) u1 | lp_intent (N,) f4 | value (N,) f4 | reward (N,) f4
 *   done (N,) u1 | mask (N,8) u1（死类掩码）| dt (N,) u2（窗口时长 tick，GAE 变步长 γ）
 * ——ppo_intent.py 消费（γ_step = γ_tick^Δt）。
 *
 * 确定性：采样 RNG = mulberry32(seed ^ 0x85ebca6b)（与 export-rl-rollout 同族，独立于
 * world.rng / godRng）；零 world.rng 消费；同 (stage,seed) 双跑逐字节一致。
 *
 * Usage:
 *   bun tools/sim/export-intent-rollout.ts --weights tmp/intent-rl/weights.json \
 *       --out tmp/intent-rl-traj/it1 --stages 0-3 --seeds 0-3 --max-ticks 12000
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, BASE_POS } from '../../src/constants'
import { OBS_SCHEMA_MAJOR } from '../../src/nn/obs-encoder'
import { buildIntentModelFromText, type IntentModelLike } from '../../src/nn/infer'
import { IntentExecutor } from '../../src/nn/intent-executor'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs'
import { RNG } from '../../src/utils/RNG'
import { buildPack } from './pack-container'
import { INTENT_IDS } from '../../src/ai/intent/vocab'
import {
  INTENT_REWARD,
  SWITCH_COST,
  potential,
  shapingStep,
  shapingMult,
  settleWindow,
} from '../../src/nn/intent-rl-reward'
import { GRID, CELL, ENEMIES_PER_STAGE } from '../../src/constants'
import {
  scoreRun,
  V7_SCORE_CONFIG,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const MAX_TICKS = 36000
const DEFAULT_REPLAN = 30
const INTENT_DIM = INTENT_IDS.length // 8

// 死类掩码：ESCAPE（idx 7）reflex-only，不参与采样/训练（与 IntentExecutor MASKED_INTENTS 一致）。
const INTENT_MASK = [1, 1, 1, 1, 1, 1, 1, 0] as const

// shard 文件名清单（--pack 打容器时按此顺序）。
const INTENT_SHARD_FILES = [
  'obs.npy',
  'scalars.npy',
  'inject.npy',
  'a_intent.npy',
  'lp_intent.npy',
  'value.npy',
  'reward.npy',
  'done.npy',
  'mask.npy',
  'dt.npy',
] as const

// ---- 轻量可复现 PRNG（mulberry32）——采样 RNG，独立于 world.rng/godRng ----
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

/** 掩码 softmax 采样（ESCAPE 恒 -inf；返回 idx + logp）。导出供单测直测采样器。 */
export function sampleCat(
  logits: Float32Array,
  mask: readonly number[],
  rng: () => number,
): { idx: number; logp: number } {
  const n = logits.length
  let max = -Infinity
  for (let i = 0; i < n; i++) {
    const v = mask[i] !== 1 ? -1e9 : logits[i]
    if (v > max) max = v
  }
  const ps = new Float32Array(n)
  let sum = 0
  for (let i = 0; i < n; i++) {
    const v = mask[i] !== 1 ? -1e9 : logits[i]
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

/** 基地保护环格（countBaseWall 口径）——环内清砖计入基地墙损、不计 BRICK_CLEAR。 */
function isBaseRingCell(col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  if (row === br - 1 && col >= bc - 1 && col <= bc + 2) return true
  if (row >= br && row <= br + 1 && (col === bc - 1 || col === bc + 2)) return true
  return false
}

interface Step {
  obs: Uint8Array
  scalars: Float32Array
  inject: Float32Array
  a: number
  lp: number
  value: number
  reward: number
  done: number
  dt: number
}

interface ShardData {
  steps: Step[]
  n: number
}

function newShard(): ShardData {
  return { steps: [], n: 0 }
}

interface RunResult {
  shard: ShardData
  outcome: string
  ticks: number
  win: boolean
  kills: number
  intentCounts: number[]
  /** v7 godai-score 诊断（HTML 报告 score_mean/baseIntegrity/击杀数；reward 另用意图窗口）。 */
  score: number
  dims: Record<string, { value: number | null; raw: number }>
}

// ---- v7 诊断打分配置（与 export-rl-rollout 同款守家优先 RL 败局带）----
// 意图 RL 的 reward 是意图窗口奖励（intent-rl-reward.ts），v7 dims 仅作 HTML 报告/
// 评估诊断（score_mean/baseIntegrity/击杀数），与训练奖励解耦。
const RL_LOSS_WEIGHTS: Weights = {
  progress: 0.3,
  baseIntegrity: 0.25,
  baseSafety: 0.25,
  tempo: 0.08,
  accuracy: 0.06,
  openingTempo: 0.03,
  loot: 0.03,
}
const RL_SCORE_CONFIG: ScoreConfig = { ...V7_SCORE_CONFIG, lossWeights: RL_LOSS_WEIGHTS }

export function runOne(
  stageIdx: number,
  stage: unknown,
  seed: number,
  difficulty: string,
  maxTicks: number,
  weightsText: string,
  replanEvery: number,
): RunResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const model: IntentModelLike = buildIntentModelFromText(weightsText)
  const shard = newShard()
  const rng = mulberry32((seed ^ 0x85ebca6b) >>> 0)
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)

  // 意图步半 MDP 记账状态（rlPick 闭包 + 主循环共享）。
  let pending: Step | null = null
  let windowStart = 0
  let windowReward = 0
  let windowShaping = 0 // potential shaping 单独累计（结算按意图 shapingMult 加权）
  let windowOutput = false // 本窗口是否有击杀/清砖/拾取（无产出切换成本判定）
  let prevIntent = -1

  const settle = (currentIntent: number, tick: number, terminal: boolean): void => {
    if (!pending) return
    const dt = tick - windowStart
    const switched = !terminal && currentIntent !== prevIntent
    // shaping 按本窗口意图加权（INTENT_SHAPING_MULT）：防守类窗口的守家梯度放大，
    // 反制 HUNT 单点坍缩；dense 分量不加权（语义无区分是坍缩根因，见 reward 注释）。
    pending.reward = settleWindow(
      windowReward + windowShaping * shapingMult(prevIntent),
      switched,
      windowOutput,
    )
    pending.done = terminal ? 1 : 0
    pending.dt = dt
    shard.steps.push(pending)
    shard.n++
    pending = null
  }

  const rlPick = (
    obs: Uint8Array,
    scalars: Float32Array,
    inject: Float32Array,
    tick: number,
  ): number => {
    model.intentForward(obs, scalars, inject)
    const { idx, logp } = sampleCat(model.intentLogits, INTENT_MASK, rng)
    // 结算上一窗口（切换判定用新采样意图）。
    settle(idx, tick, false)
    // 记录新意图步。
    pending = {
      obs: obs.slice(),
      scalars: scalars.slice(),
      inject: inject.slice(),
      a: idx,
      lp: logp,
      value: model.valueOut[0],
      reward: 0,
      done: 0,
      dt: 0,
    }
    windowStart = tick
    windowReward = 0
    windowShaping = 0
    windowOutput = false
    prevIntent = idx
    return idx
  }

  const executor = new IntentExecutor(world, {
    rng: godRng,
    rlPick,
    replanEvery,
    riskGated: false,
  })
  const sim = new Simulation(world, executor as never)
  world.loadStageData(stage as never, stageIdx)

  const intentCounts = new Array<number>(INTENT_DIM).fill(0)
  let kills = 0
  let prevWalls = countBaseWall(world)
  const baseWallTotalInitial = prevWalls // 初始保护环格数（baseIntegrity 分母）
  let t = 0
  let outcome: string = 'timeout'

  // ---- v7 诊断 telemetry（scoreRun 消费；与 export-rl-rollout 同口径）----
  let playerShots = 0
  let playerDeaths = 0
  let powerUpsSpawned = 0
  let powerUpsCollected = 0
  let firstKillTick: number | undefined
  let basePressureSum = 0
  let basePressureSamples = 0
  const cellsVisited = new Set<number>()
  const seenPuIds = new Set<number>()
  let prevLivePuIds = new Set<number>()

  while (t < maxTicks) {
    const phiBefore = potential(world)
    sim.tick()
    executor.endFrame() // 每 tick 重置 thought（InputLike 契约），否则 decide 只跑一次
    const phiAfter = potential(world)
    const shaping = shapingStep(phiBefore, phiAfter)
    windowShaping += shaping

    // 逐 tick 密集分量（窗口累计；击杀/清砖/拾取记产出）。
    let dense = 0
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if (e.by === 'player') {
          dense += INTENT_REWARD.KILL
          kills++
          if (firstKillTick === undefined) firstKillTick = t - 1
          windowOutput = true
        }
        if (e.tank?.isPlayer) {
          dense += INTENT_REWARD.LIFE_LOSS
          playerDeaths++
        }
      } else if (e.type === 'terrain_destroyed') {
        if (e.by === 'player' && !isBaseRingCell(e.col, e.row)) {
          dense += INTENT_REWARD.BRICK_CLEAR
          windowOutput = true
        }
      } else if (e.type === 'powerup_collected') {
        dense += INTENT_REWARD.PICKUP
        powerUpsCollected++
        collectedThisTick++
        windowOutput = true
      } else if (e.type === 'bullet_fired' && (e.bullet as { isPlayer?: boolean })?.isPlayer) {
        playerShots++
      }
    }
    // power-up census（seen-ids + same-tick pickup 对账，镜像 export-rl-rollout）。
    {
      const live = new Set<number>()
      for (const pu of world.powerUps) {
        live.add(pu.id)
        if (!seenPuIds.has(pu.id)) {
          seenPuIds.add(pu.id)
          powerUpsSpawned++
        }
      }
      let vanished = 0
      for (const id of prevLivePuIds) if (!live.has(id)) vanished++
      powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePuIds = live
    }
    const wallsNow = countBaseWall(world)
    if (wallsNow < prevWalls) dense += (wallsNow - prevWalls) * INTENT_REWARD.BASE_WALL_LOSS
    prevWalls = wallsNow
    windowReward += dense
    if (world.player?.alive) {
      const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
      const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
      cellsVisited.add(row * GRID + col)
    }
    // baseSafety 诊断：每 tick 采样 base pressure（= -potential，复用已算值）。
    basePressureSum += -phiAfter
    basePressureSamples++

    t++
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

  // 终局结算：终局奖励入末窗 + 结算最后 pending（done=1，无切换）。
  const terminalReward =
    outcome === 'stage_clear'
      ? INTENT_REWARD.CLEAR_STAGE
      : outcome === 'base_destroyed'
        ? INTENT_REWARD.BASE_DESTROYED
        : outcome === 'lives_exhausted'
          ? INTENT_REWARD.LIVES_EXHAUSTED
          : INTENT_REWARD.TIMEOUT
  windowReward += terminalReward
  settle(prevIntent, t, true)

  // 意图动作分布（诊断；切换判定在结算时已入 reward）。
  for (const s of shard.steps) intentCounts[s.a]++

  // ---- v7 诊断打分（HTML 报告 score_mean/baseIntegrity/击杀数；与 reward 解耦）----
  const baseAlive = !world.tileMap.isBaseDestroyed()
  const baseWallIntact = countBaseWall(world)
  const baseWallTotal = baseWallTotalInitial
  const scorable = {
    outcome,
    ticks: t,
    finalState: {
      killCount: kills,
      lives: world.lives,
      baseAlive,
    },
    firstKillTick,
    telemetry: {
      enemyTotal: (stage as { enemyCount?: number })?.enemyCount ?? ENEMIES_PER_STAGE,
      startLives: world.difficulty?.startLives ?? START_LIVES,
      playerDeaths,
      playerShots,
      powerUpsSpawned,
      powerUpsCollected,
      starsCollected: 0,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact,
      baseWallTotal,
      basePressureMean: basePressureSamples > 0 ? basePressureSum / basePressureSamples : 0,
      basePressureSamples,
      cellsVisited: cellsVisited.size,
      deaths: [],
    } satisfies Omit<RunTelemetry, 'deaths'> & { deaths: never[] },
  } as never
  const scored = scoreRun(scorable, RL_SCORE_CONFIG)
  const dims: Record<string, { value: number | null; raw: number }> = {}
  for (const k of Object.keys(scored.dims) as DimensionKey[]) {
    dims[k] = { value: scored.dims[k].value, raw: scored.dims[k].raw }
  }

  return {
    shard,
    outcome,
    ticks: t,
    win: outcome === 'stage_clear',
    kills,
    intentCounts,
    score: scored.score,
    dims,
  }
}

function writeIntentShard(dir: string, d: ShardData, manifest: unknown): void {
  const N = d.n
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 19)
  const inject = new Float32Array(N * 9)
  const a = new Uint8Array(N)
  const lp = new Float32Array(N)
  const value = new Float32Array(N)
  const reward = new Float32Array(N)
  const done = new Uint8Array(N)
  const mask = new Uint8Array(N * INTENT_DIM)
  const dt = new Uint16Array(N)
  for (let i = 0; i < N; i++) {
    const s = d.steps[i]
    obs.set(s.obs, i * 14 * 26 * 26)
    scalars.set(s.scalars, i * 19)
    inject.set(s.inject, i * 9)
    a[i] = s.a
    lp[i] = s.lp
    value[i] = s.value
    reward[i] = s.reward
    done[i] = s.done
    for (let j = 0; j < INTENT_DIM; j++) mask[i * INTENT_DIM + j] = INTENT_MASK[j]
    dt[i] = s.dt
  }
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, 19], 'f4')
  writeNpy(`${dir}/inject.npy`, inject, [N, 9], 'f4')
  writeNpy(`${dir}/a_intent.npy`, a, [N], 'u1')
  writeNpy(`${dir}/lp_intent.npy`, lp, [N], 'f4')
  writeNpy(`${dir}/value.npy`, value, [N], 'f4')
  writeNpy(`${dir}/reward.npy`, reward, [N], 'f4')
  writeNpy(`${dir}/done.npy`, done, [N], 'u1')
  writeNpy(`${dir}/mask.npy`, mask, [N, INTENT_DIM], 'u1')
  writeNpy(`${dir}/dt.npy`, dt, [N], 'u2')
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
  let outDir = 'tmp/intent-rl-traj'
  let difficulty = 'hard'
  let stagesStr = '0-3'
  let seedsStr = '0-3'
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/intent-rl/weights.json'
  let replan = DEFAULT_REPLAN
  let wver = ''
  let nodeLabel = ''
  let packPath = ''
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights') weightsPath = args[++i]
    else if (args[i] === '--replan') replan = parseInt(args[++i], 10)
    else if (args[i] === '--wver') wver = args[++i]
    else if (args[i] === '--node-label') nodeLabel = args[++i]
    else if (args[i] === '--pack') packPath = args[++i]
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')

  const outcomes: Record<string, number> = {}
  let totalSamples = 0
  let totalTicks = 0
  let wins = 0
  let totalKills = 0
  const intentAcc = new Array<number>(INTENT_DIM).fill(0)
  const perGame: string[] = []
  const scoreList: number[] = []
  const dimAcc: Record<string, number[]> = {}

  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) {
      perGame.push(`[SKIP] stage ${si}: not found`)
      continue
    }
    for (const seed of seeds) {
      const res = runOne(si, stage, seed, difficulty, maxTicks, weightsText, replan)
      outcomes[res.outcome] = (outcomes[res.outcome] ?? 0) + 1
      if (res.win) wins++
      totalKills += res.kills
      for (let j = 0; j < INTENT_DIM; j++) intentAcc[j] += res.intentCounts[j]
      scoreList.push(res.score)
      for (const [k, v] of Object.entries(res.dims)) {
        if (v.value !== null) (dimAcc[k] ??= []).push(v.value)
      }
      const shardName = `rl_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        collector: 'INTENT-RL',
        policy: 'intent-exec-rl',
        rewardScheme: 'intent-window-v1',
        difficulty,
        stage: si,
        seed,
        outcome: res.outcome,
        ticks: res.ticks,
        nSamples: res.shard.n,
        replan,
        kills: res.kills,
        intentCounts: res.intentCounts,
        switchCost: SWITCH_COST,
        // v7 诊断（HTML 报告 baseIntegrity 等读取）。
        score: res.score,
        dims: res.dims,
        ...(wver ? { wver, node: nodeLabel } : {}),
      }
      if (res.shard.n > 0) writeIntentShard(`${outDir}/${shardName}`, res.shard, manifest)
      totalSamples += res.shard.n
      totalTicks += res.ticks
      perGame.push(
        `[OK] s${si} seed${seed} samples=${res.shard.n} outcome=${res.outcome} ticks=${res.ticks} win=${res.win} kills=${res.kills}`,
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
    collector: 'INTENT-RL',
    rewardScheme: 'intent-window-v1',
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
    totalKills,
    intentCounts: intentAcc,
    replan,
    switchCost: SWITCH_COST,
    // v7 诊断：scoreList/dimLists 供 run_rl_intent 聚合 score_mean/baseIntegrity 与
    // HTML 巡检「本段各关表现」读取。
    scoreStats: stat(scoreList),
    dimMeans,
    scoreList: scoreList.map((x) => +x.toFixed(5)),
    dimLists: Object.fromEntries(
      Object.entries(dimAcc).map(([k, xs]) => [k, xs.map((x) => +x.toFixed(5))]),
    ),
    ...(wver ? { wver, node: nodeLabel } : {}),
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== INTENT-RL on-policy rollout (intent-window-v1) ===`)
  console.log(`games=${total} winRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`)
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks} totalKills=${totalKills}`)
  console.log(`intentDist=${INTENT_IDS.map((id, i) => `${id}=${intentAcc[i]}`).join(' ')}`)
  console.log(`shards under: ${outDir}  (consume with ppo_intent.py)`)
  writeFileSync(`${outDir}/_rl_report.json`, JSON.stringify(summary, null, 2))

  if (packPath) {
    if (stages.length !== 1 || seeds.length !== 1) {
      console.error('[export-intent-rollout] --pack requires exactly one stage and one seed')
      process.exit(2)
    }
    const shardDir = `${outDir}/rl_s${stages[0]}_seed${seeds[0]}`
    if (!existsSync(shardDir)) {
      console.error(
        `[export-intent-rollout] --pack: no shards written for s${stages[0]}/seed${seeds[0]} ` +
          `(0 samples — check maxTicks/stage validity)`,
      )
      process.exit(3)
    }
    const entries = INTENT_SHARD_FILES.map((name) => ({
      name,
      data: readFileSync(`${shardDir}/${name}`),
    }))
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
