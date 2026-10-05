#!/usr/bin/env bun
/**
 * spatial-probe-bench.ts — S0′-4：新塔/新列会不会拖慢 rollout（plan §4 Step 1）。
 *
 * 在**真实决策点**上分别测（hu150 权重的贪心局，ladder-c20-lives1 四关）：
 *   · encoder.encode        现有观测编码（不含 extra）
 *   · computePolicyExtra    新增 9 维扫描（一趟子弹 + 一趟坦克 + 每敌 4 向射线）
 *   · model.forward         主干+头（native 路径；含 bufA）
 *   · spatialTowerForward   空间塔（随机权重；C=8 生产档 h=64/d=8）
 * 报每决策点绝对毫秒（mean/median/p95）+ 每局决策数 + 新增算力占比推算吞吐下降 %。
 *
 * Usage:
 *   bun tools/sim/spatial-probe-bench.ts --weights <json> --seeds 2
 */
import { readFileSync } from 'fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { decodeStageGrid, type StageJson } from '../../src/nn/config-stage'
import { buildModelFromText } from '../../src/nn/infer'
import { ObsEncoder, computeMasks } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM, computePolicyExtra } from '../../src/nn/policy-extra'
import {
  SPATIAL_TOWER_FC_OUT,
  SPATIAL_TOWER_FEAT,
  SPATIAL_TOWER_C,
  spatialTowerForward,
} from '../../src/nn/spatial-tower'
import { decodeMove } from '../../src/nn/action-space'
import {
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
} from '../../src/nn/decision-gate'
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
    `[bench] ${name.padEnd(26)} mean=${out.mean_us.toFixed(1)}µs median=${out.median_us.toFixed(1)}µs p95=${out.p95_us.toFixed(1)}µs (n=${out.samples})\n`,
  )
  return out
}

function main(): void {
  const weightsPath = arg('weights')
  if (!weightsPath) throw new Error('--weights <json> is required')
  const levelPath = arg('level', 'nn-training/levels/ladder-c20-lives1.jsonc')!
  const seed0 = parseInt(arg('seed0', '850000')!, 10)
  const seeds = parseInt(arg('seeds', '2')!, 10)
  const level = parseJsonc(readFileSync(levelPath, 'utf8')) as {
    stages: Array<Record<string, unknown>>
    difficulty?: string
    max_ticks?: number
    player?: { lives?: number; level?: number }
  }
  const difficulty = level.difficulty ?? 'hard'
  const maxTicks = level.max_ticks ?? 12900
  const model = buildModelFromText(readFileSync(weightsPath, 'utf8'))
  const bufA = (model as unknown as { bufA: Float32Array }).bufA
  if (!bufA) throw new Error('bench: 模型没有 bufA（架构不符）')

  const tEnc: Stats = { samples: 0, sum: 0, list: [] }
  const tExtra: Stats = { samples: 0, sum: 0, list: [] }
  const tFwd: Stats = { samples: 0, sum: 0, list: [] }
  const tTower: Stats = { samples: 0, sum: 0, list: [] }
  let decisions = 0
  let games = 0
  let ticksTotal = 0

  // 塔的随机权重（只测耗时，不测数值）
  const projW = new Float32Array(SPATIAL_TOWER_C * 64)
  const projB = new Float32Array(SPATIAL_TOWER_C)
  const fcW = new Float32Array(SPATIAL_TOWER_FC_OUT * SPATIAL_TOWER_FEAT)
  const fcB = new Float32Array(SPATIAL_TOWER_FC_OUT)
  for (let i = 0; i < projW.length; i++) projW[i] = (i % 7) * 0.01 - 0.03
  for (let i = 0; i < fcW.length; i++) fcW[i] = (i % 5) * 0.002 - 0.004
  const zBuf = new Float32Array(SPATIAL_TOWER_C * 26 * 26)
  const vecBuf = new Float32Array(SPATIAL_TOWER_FEAT)
  const towerOut = new Float32Array(SPATIAL_TOWER_FC_OUT)

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
      const extra = new Float32Array(POLICY_EXTRA_DIM)
      let t = 0
      while (t < maxTicks) {
        if (decisionDue(t, world, gate, gateCfg)) {
          let t0 = performance.now()
          enc.encode(world)
          let t1 = performance.now()
          computePolicyExtra(world, extra)
          let t2 = performance.now()
          model.forward(enc.obs, enc.scalars)
          let t3 = performance.now()
          spatialTowerForward(bufA, projW, projB, fcW, fcB, zBuf, vecBuf, towerOut)
          let t4 = performance.now()
          add(tEnc, (t1 - t0) * 1000)
          add(tExtra, (t2 - t1) * 1000)
          add(tFwd, (t3 - t2) * 1000)
          add(tTower, (t4 - t3) * 1000)
          decisions++
          const masks = computeMasks(world)
          const mv = argmaxCat(model.moveLogits, masks.move)
          const fr = model.fireLogits[1] > model.fireLogits[0] && masks.fire[1] === 1 ? 1 : 0
          held.setAction(decodeMove(mv), fr === 1)
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
  const rExtra = report('computePolicyExtra', tExtra)
  const rFwd = report('model.forward (native)', tFwd)
  const rTower = report('spatialTowerForward', tTower)
  const oldPerDecision = rEnc.mean_us + rFwd.mean_us
  const addPerDecision = rExtra.mean_us + rTower.mean_us
  const dropPct = (addPerDecision / oldPerDecision) * 100
  process.stderr.write(
    `[bench] decisions=${decisions} games=${games} ticks=${ticksTotal} ` +
      `old/decision=${(oldPerDecision / 1000).toFixed(3)}ms new-extra=${(addPerDecision / 1000).toFixed(3)}ms ` +
      `⇒ 吞吐下降 ≈ ${dropPct.toFixed(2)}%（MAdds 口径 +0.36M/38.19M ≈ 0.95%）\n`,
  )
  process.stdout.write(
    JSON.stringify(
      {
        weights: weightsPath,
        games,
        decisions,
        per_decision: {
          encode_us: rEnc,
          extra_us: rExtra,
          forward_us: rFwd,
          tower_us: rTower,
          old_total_us: oldPerDecision,
          added_us: addPerDecision,
          throughput_drop_pct: dropPct,
        },
        madds_note: '+0.36M / 38.19M ≈ 0.95%（算术下界；实测量含常数开销）',
      },
      null,
      2,
    ) + '\n',
  )
}

main()
