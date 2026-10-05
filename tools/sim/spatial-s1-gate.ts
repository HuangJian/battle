#!/usr/bin/env bun
/**
 * spatial-s1-gate.ts — S1 性能与传输门（plan/policy-spatial-head.plan.md §4 Step 3）。
 *
 * 在**真实决策点**上量三件事（同一批 ladder-c20-lives1 对局，h=64/d=8 生产档）：
 *  对局由 hu150（生产策略，已训练）驱动——legB 的新头是 warm-start 随机初始化，
 *  自己走会秒死（~56 决策/局）；用生产策略驱动才能把决策点分布拉到真 rollout 的形态。
 *  ① native 全前向耗时（旧/腿A/腿B 三档同点对比）：
 *     · 新头耗时 = legA − old（137 输入 vs 128，含 extra 装配）
 *     · 塔耗时   = legB − legA（C=8 塔在 forward 内）
 *     · 新增占全前向比例 = (legB − old) / legB —— S1 表「推理开销占比（native）」。
 *  ② conv_ts 兜底上限：关 native 且关 wasm 后 legB 全前向的每决策耗时（上界读数）。
 *  ③ 三后端对账（真实决策点抽样）：采样点的 (obs, scalars, extra) 在 native / wasm / TS
 *     各跑一遍，比 pooled / moveLogits / fireLogits / valueOut：
 *     · native ↔ wasm 契约 = **逐字节相等**（同内核源码两编译目标）；
 *     · TS ↔ wasm 报绝对 + 相对漂移（相对 = max|Δ|/(1+max|ref|)，量级归一）。
 *
 * 与 spatial-s0a-golden.ts 的关系：那条用 LCG 随机构造 obs（稀疏/稠密两档）；
 * 本工具用**真对局**的决策点复核同一漂移口径——S0 冻结 fixture，S1 在 rollout 语境下复测。
 *
 * Usage:
 *   bun tools/sim/spatial-s1-gate.ts \
 *     --old  nn-training/weights/x20-adv-hurt/x20-adv-hurt.it150.*.json \
 *     --legA nn-training/weights/spatial-legA/spatial-legA.it0.json \
 *     --legB nn-training/weights/spatial-legB/spatial-legB.it0.json
 */
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'
import { createHash } from 'crypto'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { decodeStageGrid, type StageJson } from '../../src/nn/config-stage'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { decodeMove } from '../../src/nn/action-space'
import {
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
} from '../../src/nn/decision-gate'
import {
  featuresEngine,
  nativeStatus,
  resetNativeConvForTest,
  setNativeConvEnabled,
} from '../../src/nn/conv/conv_native_adapter'
import { setStudentConvWasmEnabled } from '../../src/nn/conv/conv_wasm_adapter'
import { parseJsonc } from '../probe/jsonc'
import type { Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

class HeldInput implements InputLike {
  private dir: Direction | null = null
  private firing = false
  setAction(dir: Direction | null, firing: boolean): void {
    this.dir = dir
    this.firing = firing
  }
  getMoveDirection(): Direction | null {
    return this.dir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {
    this.dir = null
    this.firing = false
  }
}

function argmaxCat(logits: Float32Array, mask: number[]): number {
  let best = -Infinity
  let bi = logits.length - 1
  for (let i = 0; i < logits.length; i++) {
    const v = mask[i] !== 1 ? -1e9 : logits[i]
    if (v > best) {
      best = v
      bi = i
    }
  }
  return bi
}

interface Stats {
  samples: number
  sum: number
  list: number[]
}
function add(s: Stats, us: number): void {
  s.samples++
  s.sum += us
  s.list.push(us)
}
function report(name: string, s: Stats): Record<string, number> {
  const sorted = [...s.list].sort((a, b) => a - b)
  const q = (p: number): number =>
    sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] ?? 0
  const out = {
    mean_us: s.sum / Math.max(1, s.samples),
    median_us: q(0.5),
    p95_us: q(0.95),
    samples: s.samples,
  }
  process.stderr.write(
    `[s1] ${name.padEnd(30)} mean=${out.mean_us.toFixed(1)}µs median=${out.median_us.toFixed(1)}µs p95=${out.p95_us.toFixed(1)}µs (n=${out.samples})\n`,
  )
  return out
}

type Backend = 'native' | 'wasm' | 'ts'

function forceBackend(b: Backend): void {
  // ⚠ 顺序：先 reset（清模块级缓存），再设开关——反了的话 reset 会把
  // setNativeConvEnabled(false) 的强制关闭状态清掉（_status=null ⇒ 重新 attest ⇒ 又走 native）。
  resetNativeConvForTest()
  setNativeConvEnabled(b === 'native')
  setStudentConvWasmEnabled(b !== 'ts')
}

interface Capture {
  obs: Uint8Array
  scalars: Float32Array
  extra: Float32Array
  stage: number
  seed: number
}

interface ReconcileOut {
  pooled: Float32Array
  moveLogits: Float32Array
  fireLogits: Float32Array
  valueOut: Float32Array
  engine: string
}

/** 起一个 fresh 模型跑一次 forward（与 s0a golden 同规：每臂新建，避免跨臂缓存）。 */
function runBackend(weightsText: string, cap: Capture, b: Backend): ReconcileOut {
  forceBackend(b)
  const model = buildModelFromText(weightsText) as unknown as ModelLike & {
    pooled: Float32Array
    valueOut: Float32Array
  }
  model.forward(cap.obs, cap.scalars, cap.extra)
  return {
    pooled: Float32Array.from(model.pooled),
    moveLogits: Float32Array.from(model.moveLogits),
    fireLogits: Float32Array.from(model.fireLogits),
    valueOut: Float32Array.from(model.valueOut),
    engine: featuresEngine(),
  }
}

function bytesOf(a: Float32Array): Buffer {
  return Buffer.from(a.buffer, a.byteOffset, a.byteLength)
}

function maxAbs(a: Float32Array, b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

/** 相对漂移：max|Δ| / (1 + max|ref|)（logits 量级随输入分布变化，绝对容差没有可比性）。 */
function relDelta(a: Float32Array, b: Float32Array): number {
  let m = 0
  let ref = 0
  for (let i = 0; i < a.length; i++) {
    m = Math.max(m, Math.abs(a[i] - b[i]))
    ref = Math.max(ref, Math.abs(b[i]))
  }
  return m / (1 + ref)
}

function sha16(text: string): string {
  return createHash('sha256').update(text).digest('hex').slice(0, 16)
}

function main(): void {
  const oldPath = arg('old')
  const legAPath = arg('legA')
  const legBPath = arg('legB')
  if (!oldPath || !legAPath || !legBPath) {
    throw new Error('--old/--legA/--legB 三个权重路径都是必填')
  }
  const levelPath = arg('level', 'nn-training/levels/ladder-c20-lives1.jsonc')!
  const seed0 = parseInt(arg('seed0', '850000')!, 10)
  const seeds = parseInt(arg('seeds', '2')!, 10)
  const reconcileWant = parseInt(arg('reconcile', '80')!, 10)
  const reconcileStride = parseInt(arg('reconcile-stride', '32')!, 10)
  const tsPoints = parseInt(arg('ts-points', '150')!, 10)
  const outPath = arg('out', 'tmp/spatial-s1/readings.json')!

  const oldText = readFileSync(oldPath, 'utf8')
  const legAText = readFileSync(legAPath, 'utf8')
  const legBText = readFileSync(legBPath, 'utf8')

  const level = parseJsonc(readFileSync(levelPath, 'utf8')) as {
    stages: Array<Record<string, unknown>>
    difficulty?: string
    max_ticks?: number
    player?: { lives?: number; level?: number }
  }
  const difficulty = level.difficulty ?? 'hard'
  const maxTicks = level.max_ticks ?? 12900

  // native 优先链（与生产一致）；先建三档模型（native 首次调用会 attest）。
  forceBackend('native')
  const oldModel = buildModelFromText(oldText)
  const legAModel = buildModelFromText(legAText)
  const legBModel = buildModelFromText(legBText)
  const natStatus = nativeStatus()
  process.stderr.write(
    `[s1] native status (pre-run): available=${natStatus.available} reason=${natStatus.reason} lib=${natStatus.libPath ?? '-'}\n`,
  )

  const tEnc: Stats = { samples: 0, sum: 0, list: [] }
  const tOld: Stats = { samples: 0, sum: 0, list: [] }
  const tA: Stats = { samples: 0, sum: 0, list: [] }
  const tB: Stats = { samples: 0, sum: 0, list: [] }
  const captures: Capture[] = []
  let decisions = 0
  let games = 0
  let ticksTotal = 0
  let engineSeen = 'unknown'

  const models = [legBModel, legAModel, oldModel]
  for (let si = 0; si < 4; si++) {
    for (let k = 0; k < seeds; k++) {
      const seed = seed0 + k
      const stageId = 2000 + si
      const stage = decodeStageGrid(level.stages[si] as unknown as StageJson, stageId, seed)
      const world = new World()
      world.rng.reseed(seed)
      world.difficultyKey = difficulty
      world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
      world.rules = RULES[difficulty] ?? DEFAULT_RULES
      world.playerLevel = level.player?.level ?? 0
      world.lives = level.player?.lives ?? 1
      const held = new HeldInput()
      const sim = new Simulation(world, held)
      world.loadStageData(stage, si)
      held.reset()
      const gate = createDecisionGateState()
      const gateCfg = createDecisionGateConfig(false)
      const enc = new ObsEncoder()
      let t = 0
      while (t < maxTicks) {
        if (decisionDue(t, world, gate, gateCfg)) {
          // 编码只量一次（三档 forward 共用同一份输入）
          const e0 = performance.now()
          enc.encode(world)
          const e1 = performance.now()
          add(tEnc, (e1 - e0) * 1000)

          // 三档同点 forward，轮转顺序抵消系统性顺序偏差；legB 的结果用于驱动本局。
          const order = [(decisions + 1) % 3, (decisions + 2) % 3, decisions % 3]
          const times = [0, 0, 0]
          for (const mi of order) {
            const f0 = performance.now()
            if (mi === 2) oldModel.forward(enc.obs, enc.scalars)
            else models[mi].forward(enc.obs, enc.scalars, enc.extra)
            const f1 = performance.now()
            times[mi] = (f1 - f0) * 1000
          }
          add(tB, times[0])
          add(tA, times[1])
          add(tOld, times[2])
          engineSeen = featuresEngine()

          if (captures.length < reconcileWant && decisions % reconcileStride === 0) {
            captures.push({
              obs: Uint8Array.from(enc.obs),
              scalars: Float32Array.from(enc.scalars),
              extra: Float32Array.from(enc.extra),
              stage: si,
              seed,
            })
          }

          const masks = computeMasks(world)
          const mv = argmaxCat(oldModel.moveLogits, masks.move)
          const fr = oldModel.fireLogits[1] > oldModel.fireLogits[0] && masks.fire[1] === 1 ? 1 : 0
          held.setAction(decodeMove(mv), fr === 1)
          decisions++
        }
        sim.tick()
        held.endFrame()
        t++
        if (world.state !== 'playing') break
      }
      ticksTotal += t
      games++
    }
  }

  const rEnc = report('encoder.encode', tEnc)
  const rOld = report('forward old(hu150)', tOld)
  const rA = report('forward legA(137)', tA)
  const rB = report('forward legB(151+tower)', tB)
  const natStatusAfter = nativeStatus()
  process.stderr.write(`[s1] native engines seen: ${engineSeen}\n`)

  // ---- ② conv_ts 兜底上限：同批对局重打，只量 legB 全前向（前 tsPoints 个决策）----
  forceBackend('ts')
  const tsModel = buildModelFromText(legBText)
  const tTs: Stats = { samples: 0, sum: 0, list: [] }
  outer: for (let si = 0; si < 4; si++) {
    for (let k = 0; k < seeds; k++) {
      const seed = seed0 + k
      const stageId = 2000 + si
      const stage = decodeStageGrid(level.stages[si] as unknown as StageJson, stageId, seed)
      const world = new World()
      world.rng.reseed(seed)
      world.difficultyKey = difficulty
      world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
      world.rules = RULES[difficulty] ?? DEFAULT_RULES
      world.playerLevel = level.player?.level ?? 0
      world.lives = level.player?.lives ?? 1
      const held = new HeldInput()
      const sim = new Simulation(world, held)
      world.loadStageData(stage, si)
      held.reset()
      const gate = createDecisionGateState()
      const gateCfg = createDecisionGateConfig(false)
      const enc = new ObsEncoder()
      let t = 0
      while (t < maxTicks) {
        if (decisionDue(t, world, gate, gateCfg)) {
          enc.encode(world)
          const f0 = performance.now()
          tsModel.forward(enc.obs, enc.scalars, enc.extra)
          const f1 = performance.now()
          add(tTs, (f1 - f0) * 1000)
          const masks = computeMasks(world)
          const mv = argmaxCat(tsModel.moveLogits, masks.move)
          const fr = tsModel.fireLogits[1] > tsModel.fireLogits[0] && masks.fire[1] === 1 ? 1 : 0
          held.setAction(decodeMove(mv), fr === 1)
          if (tTs.samples >= tsPoints) break outer
        }
        sim.tick()
        held.endFrame()
        t++
        if (world.state !== 'playing') break
      }
    }
  }
  const rTs = report('forward legB (conv_ts 兜底)', tTs)
  const tsEngine = featuresEngine()

  // ---- ③ 三后端对账（真实决策点抽样）----
  let nativeWasmBitwise = true
  const tsDelta: Record<string, { abs: number; rel: number }> = {}
  const engines: string[] = []
  for (const cap of captures) {
    const nat = runBackend(legBText, cap, 'native')
    const was = runBackend(legBText, cap, 'wasm')
    const ts = runBackend(legBText, cap, 'ts')
    engines.push(`${nat.engine}/${was.engine}/${ts.engine}`)
    const fields = ['pooled', 'moveLogits', 'fireLogits', 'valueOut'] as const
    const bitwise =
      bytesOf(nat.pooled).equals(bytesOf(was.pooled)) &&
      bytesOf(nat.moveLogits).equals(bytesOf(was.moveLogits)) &&
      bytesOf(nat.fireLogits).equals(bytesOf(was.fireLogits)) &&
      bytesOf(nat.valueOut).equals(bytesOf(was.valueOut))
    nativeWasmBitwise = nativeWasmBitwise && bitwise
    for (const f of fields) {
      const abs = maxAbs(ts[f], was[f])
      const rel = relDelta(ts[f], was[f])
      const cur = tsDelta[f] ?? { abs: 0, rel: 0 }
      tsDelta[f] = { abs: Math.max(cur.abs, abs), rel: Math.max(cur.rel, rel) }
    }
  }

  // 恢复默认（native 优先）——礼貌收尾。
  forceBackend('native')

  const newHeadUs = rA.mean_us - rOld.mean_us
  const towerUs = rB.mean_us - rA.mean_us
  const totalNewUs = rB.mean_us - rOld.mean_us
  const sharePct = (totalNewUs / rB.mean_us) * 100
  process.stderr.write(
    `[s1] 新头=${newHeadUs.toFixed(1)}µs 塔=${towerUs.toFixed(1)}µs 合计新增=${totalNewUs.toFixed(1)}µs ` +
      `占 legB 全前向 ${sharePct.toFixed(2)}%（native；TS 兜底 legB=${rTs.mean_us.toFixed(1)}µs）\n`,
  )
  process.stderr.write(
    `[s1] 三后端对账：points=${captures.length} native↔wasm 逐字节=${nativeWasmBitwise} ts↔wasm ${JSON.stringify(tsDelta)}\n`,
  )

  const out = {
    weights: { old: oldPath, legA: legAPath, legB: legBPath },
    weightsSha16: { old: sha16(oldText), legA: sha16(legAText), legB: sha16(legBText) },
    games,
    decisions,
    ticks: ticksTotal,
    native_status: {
      available: natStatusAfter.available,
      reason: natStatusAfter.reason,
      engine: engineSeen,
    },
    native_us: { encode: rEnc, old_fwd: rOld, legA_fwd: rA, legB_fwd: rB },
    cost_split: {
      new_head_us: newHeadUs,
      tower_us: towerUs,
      total_new_us: totalNewUs,
      total_new_share_pct: sharePct,
      old_total_us: rB.mean_us,
    },
    ts_fallback: { legB_fwd: rTs, engine: tsEngine, note: 'native 与 wasm 全关 = 纯 TS 上限' },
    reconcile: {
      points: captures.length,
      native_vs_wasm_bitwise_all: nativeWasmBitwise,
      ts_vs_wasm: tsDelta,
      engines: [...new Set(engines)],
    },
  }
  mkdirSync(dirname(outPath), { recursive: true })
  writeFileSync(outPath, JSON.stringify(out, null, 2))
  console.log(JSON.stringify(out, null, 2))
}

main()
