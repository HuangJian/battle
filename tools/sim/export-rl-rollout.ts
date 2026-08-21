/**
 * export-rl-rollout.ts — RL on-policy rollout collector (承接 P1.5 蒸馏 → RL).
 *
 * 复用 ObsEncoder + StudentModel(+value head) 驱动 headless 仿真，按决策 tick
 * (K=10) 跑随机策略采样 → 写出 trajectory shards 供 Python PPO 消费。
 *
 * 与 export-dagger-labels.ts 的区别：
 *   - 动作由「模型采样」(stochastic, 记录 logprob) 而非「教师标号」决定；
 *   - 额外记录 value(来自 value head)、reward(由 World 状态差分计算)、done；
 *   - 自定义 ScriptedInput 实现 InputLike，把采样动作施加到 World（持有门控
 *     与 BC 同节拍），None 动作语义沿用 policy-input 的「hold lastDir」。
 *
 * 输出 shards（每局一个，npy + manifest），字段：
 *   obs (N,14,26,26) u1 | scalars (N,24) f4 | a_move/a_fire/a_item (N,) u1
 *   lp_move/lp_fire/lp_item (N,) f4 | value (N,) f4 | reward (N,) f4
 *   done (N,) u1 | mask (N,10) u1
 * 每个 transition 对应一个决策 tick 的 (s_t, a_t, logp_t, V_t, r_{t+1}, done_{t+1})。
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
import { START_LIVES } from '../../src/constants'
import { type Direction } from '../../src/constants'
import {
  ObsEncoder,
  computeMasks,
  OBS_SCHEMA_MAJOR,
} from '../../src/nn/obs-encoder'
import { buildModelFromText } from '../../src/nn/infer'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync, readFileSync } from 'fs'

const MAX_TICKS = 36000
const K = 10
const MOVE_DIM = 5
const FIRE_DIM = 2
const ITEM_DIM = 3
const MASK_DIM = MOVE_DIM + FIRE_DIM + ITEM_DIM

// ---- reward weights (CLI 可覆盖) ----
let W_WIN = 5.0
let W_KILL = 0.2
let W_BASE = 1.0
let W_SURV = 0.01
let W_LOSE_BASE = -2.0
let W_LOSE = -1.0

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

interface Metrics {
  enemies: number
  baseHp: number
  baseMax: number
  state: string
}

function snapshot(w: World): Metrics {
  return {
    enemies: w.enemiesRemaining,
    baseHp: w.baseHp,
    baseMax: w.baseMaxHp,
    state: w.state,
  }
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

function rewardStep(prev: Metrics, cur: Metrics): number {
  let r = W_SURV
  r += W_KILL * Math.max(0, prev.enemies - cur.enemies)
  const bPrev = prev.baseMax > 0 ? prev.baseHp / prev.baseMax : 1
  const bCur = cur.baseMax > 0 ? cur.baseHp / cur.baseMax : 1
  r += W_BASE * (bCur - bPrev)
  return r
}

function terminalBonus(w: World): number {
  if (w.state === 'stageclear' || w.state === 'victory') return W_WIN
  if (w.state === 'gameover') return w.tileMap.isBaseDestroyed() ? W_LOSE_BASE : W_LOSE
  return 0
}

interface Pending {
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
  prevMetrics: Metrics
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
  let pending: Pending | null = null
  let t = 0
  let outcome = 'timeout'

  const finalizePending = (cur: Metrics, term: boolean): void => {
    if (!pending) return
    let r = rewardStep(pending.prevMetrics, cur) + terminalBonus(world)
    shard.obs.push(pending.obs)
    shard.scalars.push(pending.sc)
    shard.aMove.push(pending.aMove)
    shard.aFire.push(pending.aFire)
    shard.aItem.push(pending.aItem)
    shard.lpMove.push(pending.lpMove)
    shard.lpFire.push(pending.lpFire)
    shard.lpItem.push(pending.lpItem)
    shard.value.push(pending.value)
    shard.reward.push(r)
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
      const cur = snapshot(world)
      if (pending) finalizePending(cur, world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover')
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
        prevMetrics: cur,
      }
      scripted.setAction(mv.idx, fr.idx, it.idx)
    }
    sim.tick()
    scripted.endFrame()
    t++
    if (world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover') {
      const cur = snapshot(world)
      finalizePending(cur, true)
      outcome =
        world.state === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
  }

  const win = outcome === 'stage_clear'
  return { shard, outcome, ticks: t, win }
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
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
    else if (args[i] === '--weights') weightsPath = args[++i]
    else if (args[i] === '--w-win') W_WIN = parseFloat(args[++i])
    else if (args[i] === '--w-kill') W_KILL = parseFloat(args[++i])
    else if (args[i] === '--w-base') W_BASE = parseFloat(args[++i])
    else if (args[i] === '--w-surv') W_SURV = parseFloat(args[++i])
    else if (args[i] === '--w-lose-base') W_LOSE_BASE = parseFloat(args[++i])
    else if (args[i] === '--w-lose') W_LOSE = parseFloat(args[++i])
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })
  const weightsText = readFileSync(weightsPath, 'utf8')

  const outcomes: Record<string, number> = {}
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
      const { shard, outcome, ticks, win } = runOne(si, stage, seed, difficulty, maxTicks, weightsText)
      outcomes[outcome] = (outcomes[outcome] ?? 0) + 1
      if (win) wins++
      const shardName = `rl_s${si}_seed${seed}`
      const manifest = {
        schemaMajor: OBS_SCHEMA_MAJOR,
        collector: 'RL',
        policy: 'nn-student-rl',
        difficulty,
        stage: si,
        seed,
        outcome,
        ticks,
        nSamples: shard.n,
        k: K,
        rewardWeights: { W_WIN, W_KILL, W_BASE, W_SURV, W_LOSE_BASE, W_LOSE },
      }
      if (shard.n > 0) writeRlShard(`${outDir}/${shardName}`, shard, manifest)
      totalSamples += shard.n
      totalTicks += ticks
      perGame.push(`[OK] s${si} seed${seed} samples=${shard.n} outcome=${outcome} ticks=${ticks} win=${win}`)
    }
  }

  const total = seeds.length * stages.length
  const winRate = total > 0 ? wins / total : 0
  const summary = {
    collector: 'RL',
    difficulty,
    stages,
    seeds,
    games: total,
    winRate: +winRate.toFixed(4),
    outcomes,
    totalSamples,
    totalTicks,
  }
  console.log(perGame.join('\n'))
  console.log(`\n=== RL on-policy rollout (P1.5→RL) ===`)
  console.log(`games=${total} winRate=${winRate.toFixed(4)} outcomes=${JSON.stringify(outcomes)}`)
  console.log(`totalSamples=${totalSamples} totalTicks=${totalTicks}`)
  console.log(`shards under: ${outDir}  (consume with ppo.py)`)
  writeFileSync(`${outDir}/_rl_report.json`, JSON.stringify(summary, null, 2))
}

if (import.meta.main) main()
