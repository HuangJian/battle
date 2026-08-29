/**
 * export-goal-rollout.ts — T7.2 goal RL on-policy rollout collector（承诺步 semi-MDP）。
 *
 * 与 export-intent-rollout.ts（意图步 8 路）相对，本采集器按 **goal 承诺步** 记录：
 *   决策只在心跳 tick（GoalExecutor promiseTicks=T）发生；动作 = 采样的目标格
 *   （fine 676 路 / coarse 169 路块级 —— --coarse，T9a §T9a.1b）；
 *   执行 = executor 契约（L2 路径跟随 + dodge 硬约束）；
 *   奖励按窗口累计（R_event 继承 INTENT_REWARD 量级 §12.3.1 + R_shaping §12.3）。
 *
 * 采样分布（T7.2 解释性决策，记录于 docs/goal-nn.progress.md）：
 *   softmax(网络热图) 限可达格（mask ≠ −Infinity 作有效性过滤，λ·k 代价不进采样分布）。
 *   理由：(a) 与 T9a.1b 块级规格一致（块 logit = 块内网络 logits logsumexp，被 mask 的
 *   块整块剔除）；(b) λ·k 是部署 argmax 的 tie-break，PPO 探索不应被启发式扭曲。
 *   coarse 执行：块内按 (heat+mask) argmax 得精细格（§T9a.1b）。
 *
 * 奖励（窗口结算，§12.3/§12.3.1）：
 *   R_event   继承 INTENT_REWARD（KILL 4.0 / CLEAR_STAGE 50.0 / BASE_DESTROYED −50.0 /
 *             LIVES_EXHAUSTED −30.0 / LIFE_LOSS −5.0 / BASE_WALL_LOSS −3.0 /
 *             TIMEOUT −1.0 / BRICK_CLEAR 0.5 / PICKUP 2.0）—— 不重调已验证量级。
 *   R_shaping 到达 +1.0（窗口末在目标格 Chebyshev ≤1）
 *             守家 0.5 × (γ^dt·Φ(窗末) − Φ(窗初))（potential telescoping，§12.2 变步长式）
 *             交战效率 0.3 × (窗口命中/开火)（自伤否决率项 T9 全量期补）
 *
 * 输出 shards（每局一个目录，npy + manifest）：
 *   obs (N,14,26,26) u1 | scalars (N,19) f4 | inject (N,9) f4
 *   a_goal (N,) i64 | lp_goal (N,) f4 | value (N,) f4 | reward (N,) f4
 *   done (N,) u8→i64 | goal_mask (N,676|169) u1 | dt (N,) u2 | engage (N,) i64
 *
 * 确定性：采样 RNG = mulberry32(seed ^ 0x5bf03635)（与 intent 采集器同族、独立常数）；
 * 零 world.rng 消费；同 (stage,seed) 双跑逐字节一致。
 *
 * Usage:
 *   bun tools/sim/export-goal-rollout.ts --weights tmp/goal-rl/weights.json \
 *       --out tmp/goal-rl-traj/it1 --stages 0-3 --seeds 0-3 --heartbeat 240 [--coarse]
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, GRID, CELL, ENEMIES_PER_STAGE, BASE_POS } from '../../src/constants'
import { OBS_SCHEMA_MAJOR } from '../../src/nn/obs-encoder'
import { buildGoalModelFromText, type GoalModelLike } from '../../src/nn/infer'
import { GOAL_INJECT_DIM } from '../../src/nn/goal-inject'
import { GoalExecutor } from '../../src/nn/goal-executor'
import { ReachMasker, selectGoal } from '../../src/ai/goal/reach-mask'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs'
import { RNG } from '../../src/utils/RNG'
import { buildPack } from './pack-container'
import { INTENT_REWARD, potential } from '../../src/nn/intent-rl-reward'
import {
  scoreRun,
  V7_SCORE_CONFIG,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from '../eval/godai-score'
import type { RunTelemetry } from './simulation-runner'

const MAX_TICKS = 36000
const DEFAULT_HEARTBEAT = 240
const GAMMA_TICK = 0.995
const SHAPING_ARRIVE = 1.0
const SHAPING_HOME = 0.5
const SHAPING_ENGAGE = 0.3
const FINE_DIM = 676
const COARSE_SIDE = 13
const COARSE_DIM = 169

const GOAL_SHARD_FILES = [
  'obs.npy',
  'scalars.npy',
  'inject.npy',
  'a_goal.npy',
  'lp_goal.npy',
  'value.npy',
  'reward.npy',
  'done.npy',
  'goal_mask.npy',
  'dt.npy',
  'engage.npy',
] as const

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

/** 掩码 categorical 采样（mask[i]===1 可选；与 export-intent-rollout.sampleCat 同族）。 */
export function sampleCat(
  logits: Float32Array,
  mask: readonly number[] | Uint8Array,
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
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let n = 0
  for (let col = bc - 1; col <= bc + 2; col++) {
    const t = world.tileMap.get(col, br - 1)
    if (t === 'brick' || t === 'steel') n++
  }
  for (let row = br; row <= br + 1; row++) {
    for (const col of [bc - 1, bc + 2]) {
      const t = world.tileMap.get(col, row)
      if (t === 'brick' || t === 'steel') n++
    }
  }
  return n
}

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
  mask: Uint8Array
  dt: number
  engage: number
}

interface RunResult {
  shard: { steps: Step[]; n: number }
  outcome: string
  ticks: number
  win: boolean
  kills: number
  actionDim: number
  score: number
  dims: Record<string, { value: number | null; raw: number }>
}

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
  heartbeat: number,
  coarse: boolean,
): RunResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const model: GoalModelLike = buildGoalModelFromText(weightsText)
  const steps: Step[] = []
  const rng = mulberry32((seed ^ 0x5bf03635) >>> 0)
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const masker = new ReachMasker()
  const actionDim = coarse ? COARSE_DIM : FINE_DIM

  // 窗口记账状态（goalPick 闭包 + 主循环共享）。
  let pending: Step | null = null
  let windowStart = 0
  let windowPhiStart = 0
  let windowReward = 0 // R_event（dense 部分）
  let windowShots = 0
  let windowHits = 0
  let windowGoalCell = -1

  const settle = (tick: number, terminal: boolean): void => {
    if (!pending) return
    const dt = tick - windowStart
    const phiEnd = potential(world)
    // §12.2 变步长 telescoping：F_k = γ^{dt_k}·Φ(窗末) − Φ(窗初)（× 守家权重）。
    const shaping = SHAPING_HOME * (Math.pow(GAMMA_TICK, dt) * phiEnd - windowPhiStart)
    // 到达：窗口末玩家中心格 Chebyshev ≤ 1 于目标格。
    let arrived = 0
    const p = world.player
    if (p && windowGoalCell >= 0) {
      const gc = windowGoalCell % GRID
      const gr = (windowGoalCell - gc) / GRID
      const pc = Math.floor((p.x + p.w / 2) / CELL)
      const pr = Math.floor((p.y + p.h / 2) / CELL)
      if (Math.max(Math.abs(pc - gc), Math.abs(pr - gr)) <= 1) arrived = 1
    }
    const hitRatio = windowShots > 0 ? windowHits / windowShots : 0
    pending.reward = windowReward + shaping + SHAPING_ARRIVE * arrived + SHAPING_ENGAGE * hitRatio
    pending.done = terminal ? 1 : 0
    pending.dt = dt
    steps.push(pending)
    pending = null
  }

  const goalPick = (
    obs: Uint8Array,
    scalars: Float32Array,
    inject: Float32Array,
    tick: number,
  ): number => {
    // 可达性掩码（与执行器同一实现/同一世界状态 ⇒ 同一 k-field）。
    const p0 = world.player
    const pc = p0 ? Math.max(0, Math.min(GRID - 1, Math.round(p0.x / CELL))) : 0
    const pr = p0 ? Math.max(0, Math.min(GRID - 1, Math.round(p0.y / CELL))) : 0
    masker.compute(world.tileMap, pc, pr)
    const maskF = masker.mask(0.5) // λ 只影响执行 argmax，不进采样分布
    const k = masker.k

    model.goalForward(obs, scalars, inject)

    // 结算上一窗口（以本 tick 为界）。
    settle(tick, false)

    // 有效性掩码 + 采样（fine：cell 级；coarse：块级 logsumexp，块内执行 argmax）。
    let a = -1
    let lp = -Infinity
    let chosenCell = -1
    if (coarse) {
      const blockLogits = new Float32Array(COARSE_DIM)
      const blockValid = new Uint8Array(COARSE_DIM)
      for (let b = 0; b < COARSE_DIM; b++) {
        const bc = b % COARSE_SIDE
        const br = (b - bc) / COARSE_SIDE
        let mx = -Infinity
        for (let dr = 0; dr < 2; dr++) {
          for (let dc = 0; dc < 2; dc++) {
            const cell = (br * 2 + dr) * GRID + (bc * 2 + dc)
            if (k[cell] !== 65535 && model.goalHeatmap[cell] > mx) mx = model.goalHeatmap[cell]
          }
        }
        let lse = 0
        if (mx > -Infinity) {
          // 稳定 logsumexp（仅可达格参与；全不可达块 logit = -1e9 由 valid=0 屏蔽）
          for (let dr = 0; dr < 2; dr++) {
            for (let dc = 0; dc < 2; dc++) {
              const cell = (br * 2 + dr) * GRID + (bc * 2 + dc)
              if (k[cell] !== 65535) lse += Math.exp(model.goalHeatmap[cell] - mx)
            }
          }
          lse = mx + Math.log(lse)
        }
        blockLogits[b] = lse
        blockValid[b] = mx > -Infinity ? 1 : 0
      }
      const s = sampleCat(blockLogits, blockValid, rng)
      a = s.idx
      lp = s.logp
      // 块内执行 argmax（heat + mask 代价；§T9a.1b）。
      const bc = a % COARSE_SIDE
      const br = (a - bc) / COARSE_SIDE
      const blockHeat = new Float32Array(4)
      const blockMask = new Float32Array(4)
      for (let dr = 0; dr < 2; dr++) {
        for (let dc = 0; dc < 2; dc++) {
          const cell = (br * 2 + dr) * GRID + (bc * 2 + dc)
          blockHeat[dr * 2 + dc] = model.goalHeatmap[cell]
          blockMask[dr * 2 + dc] = maskF[cell]
        }
      }
      const cellInBlock = selectGoal(blockHeat, blockMask)
      if (cellInBlock >= 0) {
        const dc = cellInBlock % 2
        const dr = (cellInBlock - dc) / 2
        chosenCell = (br * 2 + dr) * GRID + (bc * 2 + dc)
      }
    } else {
      const valid = new Uint8Array(FINE_DIM)
      for (let i = 0; i < FINE_DIM; i++) valid[i] = k[i] !== 65535 ? 1 : 0
      const s = sampleCat(model.goalHeatmap, valid, rng)
      a = s.idx
      lp = s.logp
      chosenCell = a
    }

    // shard mask（u1）：fine = cell 有效性；coarse = 块有效性。
    const maskOut = new Uint8Array(actionDim)
    if (coarse) {
      for (let b = 0; b < COARSE_DIM; b++) {
        const bc = b % COARSE_SIDE
        const br = (b - bc) / COARSE_SIDE
        let valid = 0
        for (let dr = 0; dr < 2 && !valid; dr++)
          for (let dc = 0; dc < 2 && !valid; dc++)
            if (k[(br * 2 + dr) * GRID + (bc * 2 + dc)] !== 65535) valid = 1
        maskOut[b] = valid
      }
    } else {
      for (let i = 0; i < FINE_DIM; i++) maskOut[i] = k[i] !== 65535 ? 1 : 0
    }

    // engage argmax（诊断记录；非 PPO 动作，k1）。
    const engage = model.engageLogits[1] > model.engageLogits[0] ? 1 : 0

    pending = {
      obs: obs.slice(),
      scalars: scalars.slice(),
      inject: inject.slice(),
      a,
      lp,
      value: model.valueOut[0],
      reward: 0,
      done: 0,
      mask: maskOut,
      dt: 0,
      engage,
    }
    windowStart = tick
    windowPhiStart = potential(world)
    windowReward = 0
    windowShots = 0
    windowHits = 0
    windowGoalCell = chosenCell
    return chosenCell
  }

  const executor = new GoalExecutor(world, {
    rng: godRng,
    goalPick,
    promiseTicks: heartbeat,
  })
  const sim = new Simulation(world, executor as never)
  world.loadStageData(stage as never, stageIdx)
  executor.reset()

  let kills = 0
  let prevWalls = countBaseWall(world)
  const baseWallTotalInitial = prevWalls
  let t = 0
  let outcome: string = 'timeout'

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
    sim.tick()
    executor.endFrame()

    let dense = 0
    let collectedThisTick = 0
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if (e.by === 'player') {
          dense += INTENT_REWARD.KILL
          kills++
          windowHits++
          if (firstKillTick === undefined) firstKillTick = t - 1
        }
        if (e.tank?.isPlayer) {
          dense += INTENT_REWARD.LIFE_LOSS
          playerDeaths++
        }
      } else if (e.type === 'terrain_destroyed') {
        if (e.by === 'player' && !isBaseRingCell(e.col, e.row)) dense += INTENT_REWARD.BRICK_CLEAR
      } else if (e.type === 'powerup_collected') {
        dense += INTENT_REWARD.PICKUP
        powerUpsCollected++
        collectedThisTick++
      } else if (e.type === 'bullet_fired' && (e.bullet as { isPlayer?: boolean })?.isPlayer) {
        playerShots++
        windowShots++
      }
    }
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
    basePressureSum += -potential(world)
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

  const terminalReward =
    outcome === 'stage_clear'
      ? INTENT_REWARD.CLEAR_STAGE
      : outcome === 'base_destroyed'
        ? INTENT_REWARD.BASE_DESTROYED
        : outcome === 'lives_exhausted'
          ? INTENT_REWARD.LIVES_EXHAUSTED
          : INTENT_REWARD.TIMEOUT
  windowReward += terminalReward
  settle(t, true)

  // v7 诊断打分（HTML 报告；与 reward 解耦，同 export-intent-rollout 口径）。
  const baseAlive = !world.tileMap.isBaseDestroyed()
  const scorable = {
    outcome,
    ticks: t,
    finalState: { killCount: kills, lives: world.lives, baseAlive },
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
      baseWallIntact: countBaseWall(world),
      baseWallTotal: baseWallTotalInitial,
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
    shard: { steps, n: steps.length },
    outcome,
    ticks: t,
    win: outcome === 'stage_clear',
    kills,
    actionDim,
    score: scored.score,
    dims,
  }
}

function writeGoalShard(dir: string, d: { steps: Step[]; n: number }, manifest: unknown): void {
  const N = d.n
  if (N === 0) return
  const actionDim = d.steps[0].mask.length
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 19)
  const inject = new Float32Array(N * GOAL_INJECT_DIM)
  const a = new Uint16Array(N)
  const lp = new Float32Array(N)
  const value = new Float32Array(N)
  const reward = new Float32Array(N)
  const done = new Uint8Array(N)
  const mask = new Uint8Array(N * actionDim)
  const dt = new Uint16Array(N)
  const engage = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    const s = d.steps[i]
    obs.set(s.obs, i * 14 * 26 * 26)
    scalars.set(s.scalars, i * 19)
    inject.set(s.inject, i * GOAL_INJECT_DIM)
    a[i] = s.a
    lp[i] = s.lp
    value[i] = s.value
    reward[i] = s.reward
    done[i] = s.done
    mask.set(s.mask, i * actionDim)
    dt[i] = s.dt
    engage[i] = s.engage
  }
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, 19], 'f4')
  writeNpy(`${dir}/inject.npy`, inject, [N, GOAL_INJECT_DIM], 'f4')
  writeNpy(`${dir}/a_goal.npy`, a, [N], 'u2')
  writeNpy(`${dir}/lp_goal.npy`, lp, [N], 'f4')
  writeNpy(`${dir}/value.npy`, value, [N], 'f4')
  writeNpy(`${dir}/reward.npy`, reward, [N], 'f4')
  writeNpy(`${dir}/done.npy`, done, [N], 'u1')
  writeNpy(`${dir}/goal_mask.npy`, mask, [N, actionDim], 'u1')
  writeNpy(`${dir}/dt.npy`, dt, [N], 'u2')
  writeNpy(`${dir}/engage.npy`, engage, [N], 'u1')
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
  let outDir = 'tmp/goal-rl-traj'
  let difficulty = 'hard'
  let stagesStr = '0-3'
  let seedsStr = '0-3'
  let maxTicks = MAX_TICKS
  let weightsPath = 'tmp/goal-rl/weights.json'
  let heartbeat = DEFAULT_HEARTBEAT
  let coarse = false
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
    else if (args[i] === '--heartbeat') heartbeat = parseInt(args[++i], 10)
    else if (args[i] === '--coarse') coarse = true
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
  const scoreList: number[] = []
  const dimAcc: Record<string, number[]> = {}

  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) continue
    for (const seed of seeds) {
      const res = runOne(si, stage, seed, difficulty, maxTicks, weightsText, heartbeat, coarse)
      outcomes[res.outcome] = (outcomes[res.outcome] ?? 0) + 1
      if (res.win) wins++
      totalKills += res.kills
      scoreList.push(res.score)
      for (const [k, v] of Object.entries(res.dims)) {
        if (v.value !== null) (dimAcc[k] ??= []).push(v.value)
      }
      const shardName = `rl_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        collector: 'GOAL-RL',
        policy: 'goal-rl',
        rewardScheme: 'goal-window-v1',
        firePolicy: 'firecontrol-l3-min', // §11.3.1：标注/采样所用开火策略版本
        actionSpace: res.actionDim === COARSE_DIM ? 'coarse-169' : 'fine-676',
        difficulty,
        stage: si,
        seed,
        outcome: res.outcome,
        ticks: res.ticks,
        nSamples: res.shard.n,
        heartbeat,
        kills: res.kills,
        score: res.score,
        dims: res.dims,
        ...(wver ? { wver, node: nodeLabel } : {}),
      }
      if (res.shard.n > 0) writeGoalShard(`${outDir}/${shardName}`, res.shard, manifest)
      totalSamples += res.shard.n
      totalTicks += res.ticks
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
    collector: 'GOAL-RL',
    rewardScheme: 'goal-window-v1',
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
    totalKills,
    heartbeat,
    actionSpace: coarse ? 'coarse-169' : 'fine-676',
    scoreStats: stat(scoreList),
    dimMeans,
    scoreList: scoreList.map((x) => +x.toFixed(5)),
    dimLists: Object.fromEntries(
      Object.entries(dimAcc).map(([k, xs]) => [k, xs.map((x) => +x.toFixed(5))]),
    ),
    ...(wver ? { wver, node: nodeLabel } : {}),
  }
  console.log(`\n=== GOAL-RL on-policy rollout (goal-window-v1) ===`)
  console.log(`games=${total} winRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`)
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks} totalKills=${totalKills}`)
  console.log(`shards under: ${outDir}  (consume with ppo_goal.py)`)
  writeFileSync(`${outDir}/_rl_report.json`, JSON.stringify(summary, null, 2))

  if (packPath) {
    if (stages.length !== 1 || seeds.length !== 1) {
      console.error('[export-goal-rollout] --pack requires exactly one stage and one seed')
      process.exit(2)
    }
    const shardDir = `${outDir}/rl_s${stages[0]}_seed${seeds[0]}`
    if (!existsSync(shardDir)) {
      console.error('[export-goal-rollout] --pack: no shards written')
      process.exit(3)
    }
    const entries = GOAL_SHARD_FILES.map((name) => ({
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
