/**
 * export-dagger-beta.ts — β-混合执行的 DAgger 采集器（2026-11-12）。
 *
 * 与 `export-dagger-labels.ts` 的区别：学生和 God 每决策拍都算动作，但**执行**按
 * β 概率选 God（β=0 = 纯学生，β=1 = 纯 God）。这样轨迹里会出现「God 接管几拍后
 * 回到好状态」的片段，而不是「错状态 + 孤立 God 动作」。
 *
 * 用法：
 *   bun tools/sim/export-dagger-beta.ts --weights nn-training/weights/g1c-4s2e/weights.json \
 *     --stage-json-file tmp/god-o2-four-stages.json --stages 2000-2003 --seeds 860001-860200 \
 *     --out tmp/g1g-dagger-beta --beta 0.5 --keep-forced
 *
 * 标签始终是 God 动作（标准 DAgger）；`--keep-forced` 只决定落盘哪些状态。
 */
import { mkdirSync, readFileSync, writeFileSync } from 'fs'

import { GodAIInput } from '../../src/ai/GodAIInput'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/god/params'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import type { Direction } from '../../src/constants'
import { decodeMove4, encodeMove4 } from '../../src/nn/action-space-v6'
import { corridorCells } from '../../src/nn/danger-metrics'
import { decodeStageGrid, CUSTOM_STAGE_BASE } from '../../src/nn/config-stage'
import {
  ObsEncoder,
  computeMasks,
  restrictAdvanceMask,
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  BOARD,
  SCALAR_DIM,
  SCHEMA_FINGERPRINT,
} from '../../src/nn/obs-encoder'
import { buildModelFromText } from '../../src/nn/infer'
import { writeNpy } from '../../src/nn/npy'
import { POLICY_EXTRA_DIM } from '../../src/nn/policy-extra'
import { Simulation } from '../../src/game/Simulation'
import { World } from '../../src/game/World'
import { RNG } from '../../src/utils/RNG'

class ScriptedInput {
  moveDir: Direction | null = null
  firing = false
  hold = false
  fireBefore = false
  setAction(move: number, fire: number, hold = 0): void {
    this.moveDir = decodeMove4(move)
    this.hold = hold === 1
    this.fireBefore = fire === 1
    this.firing = fire !== 0
  }
  getMoveDirection(): Direction | null {
    return this.moveDir
  }
  isFiring(): boolean {
    return this.firing
  }
  isHolding(): boolean {
    return this.hold
  }
  isFireBeforeTurn(): boolean {
    return this.fireBefore
  }
  usesHoldFire3(): boolean {
    return true
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {
    this.moveDir = null
    this.firing = false
    this.hold = false
    this.fireBefore = false
  }
}

function argmaxCat(logits: Float32Array, mask: number[]): number {
  let best = -1
  let bestV = -Infinity
  for (let i = 0; i < logits.length; i++) {
    if ((mask[i] ?? 0) === 0) continue
    if (logits[i] > bestV) {
      bestV = logits[i]
      best = i
    }
  }
  return best >= 0 ? best : 0
}

interface Sample {
  obs: Uint8Array
  scalars: Float32Array
  extra: Float32Array
  move: number
  fire: number
  hold: number
}

function flushShard(
  samples: Sample[],
  dir: string,
  name: string,
  meta: Record<string, unknown>,
): void {
  const N = samples.length
  if (N === 0) return
  const obs = new Uint8Array(N * OBS_CHANNELS * BOARD * BOARD)
  const scalars = new Float32Array(N * SCALAR_DIM)
  const extra = new Float32Array(N * POLICY_EXTRA_DIM)
  const aMove = new Uint8Array(N)
  const aFire = new Uint8Array(N)
  const aHold = new Uint8Array(N)
  const mask = new Uint8Array(N * 9)
  const conditions = new Uint8Array(N)
  const returns = new Float32Array(N)
  for (let i = 0; i < N; i++) {
    const s = samples[i]
    obs.set(s.obs, i * OBS_CHANNELS * BOARD * BOARD)
    scalars.set(s.scalars, i * SCALAR_DIM)
    extra.set(s.extra, i * POLICY_EXTRA_DIM)
    aMove[i] = s.move
    aFire[i] = s.fire
    aHold[i] = s.hold
    for (let j = 0; j < 9; j++) mask[i * 9 + j] = 1
  }
  mkdirSync(dir, { recursive: true })
  writeNpy(`${dir}/obs.npy`, obs, [N, OBS_CHANNELS, BOARD, BOARD], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, SCALAR_DIM], 'f4')
  writeNpy(`${dir}/extra.npy`, extra, [N, POLICY_EXTRA_DIM], 'f4')
  writeNpy(`${dir}/a_move.npy`, aMove, [N], 'u1')
  writeNpy(`${dir}/a_fire.npy`, aFire, [N], 'u1')
  writeNpy(`${dir}/a_hold.npy`, aHold, [N], 'u1')
  writeNpy(`${dir}/mask.npy`, mask, [N, 9], 'u1')
  writeNpy(`${dir}/conditions.npy`, conditions, [N], 'u1')
  writeNpy(`${dir}/returns.npy`, returns, [N], 'f4')
  writeFileSync(
    `${dir}/manifest.json`,
    JSON.stringify(
      {
        schemaMajor: OBS_SCHEMA_MAJOR,
        obsSchemaMajor: OBS_SCHEMA_MAJOR,
        schemaFingerprint: SCHEMA_FINGERPRINT,
        exporterVersion: 'dagger-beta-v1',
        shard: name,
        teacher: 'god-ai-dagger-beta',
        nSamples: N,
        actionSpace: 'v6',
        ...meta,
      },
      null,
      2,
    ),
  )
}

const arg = (name: string, fallback: string): string => {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : fallback
}
const has = (name: string): boolean => process.argv.includes(`--${name}`)
const parseRange = (spec: string): number[] => {
  const out: number[] = []
  for (const part of spec.replace(/\s/g, '').split(',')) {
    if (!part) continue
    if (part.includes('-')) {
      const [a, b] = part.split('-').map(Number)
      for (let i = a; i <= b; i++) out.push(i)
    } else out.push(Number(part))
  }
  return out
}

const weightsPath = arg('weights', '')
const stageJsonFile = arg('stage-json-file', '')
const stageSpec = arg('stages', '2000-2003')
const seedSpec = arg('seeds', '1-10')
const outDir = arg('out', 'tmp/dagger-beta')
const maxTicks = Number(arg('max-ticks', '3000'))
const lives = Number(arg('lives-override', '1'))
const level = Number(arg('player-level', '0'))
const K = Number(arg('decision-k', '10'))
const BETA = Number(arg('beta', '0.5'))
const keepForced = has('keep-forced')
if (!weightsPath || !stageJsonFile) {
  console.error(
    'usage: --weights <student.json> --stage-json-file <json> --stages 2000-2003 --seeds a-b --out dir [--beta 0.5] [--keep-forced]',
  )
  process.exit(2)
}
const weightsText = readFileSync(weightsPath, 'utf8')
const weightsSha = new Bun.CryptoHasher('sha256').update(weightsText).digest('hex').slice(0, 12)
const stages = JSON.parse(readFileSync(stageJsonFile, 'utf8')) as Array<Record<string, unknown>>
const stageIds = parseRange(stageSpec)
const seeds = parseRange(seedSpec)

let games = 0
let kept = 0
let samplesTotal = 0
let godTookOver = 0
let decisions = 0
let studentWins = 0
let forcedDecisions = 0
for (const sid of stageIds) {
  const i = sid - CUSTOM_STAGE_BASE
  if (!(i >= 0 && i < stages.length)) {
    console.error(`[dagger-beta] stage ${sid} 越界`)
    process.exit(2)
  }
  for (const seed of seeds) {
    const world = new World()
    world.rng.reseed(seed)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    world.playerLevel = level
    world.lives = lives
    const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG((seed ^ 0x9e3779b9) >>> 0))
    const model = buildModelFromText(weightsText)
    const betRng = new RNG((seed ^ 0x51ed270b) >>> 0)
    const scripted = new ScriptedInput()
    const sim = new Simulation(world, scripted)
    world.loadStageData(decodeStageGrid(JSON.stringify(stages[i]), sid, seed), 0)
    god.reset()
    scripted.reset()
    const encoder = new ObsEncoder()
    const samples: Sample[] = []
    /** F4：教师最近一次非 null 方向；STOP 标签不能用学生朝向（可能正朝墙）。 */
    let lastTeacherDir: Direction | null = null
    let t = 0
    while (t < maxTicks) {
      if (t % K === 0) {
        encoder.encode(world)
        const masks = computeMasks(world, true)
        model.forward(encoder.obs, encoder.scalars, encoder.extra)
        const smv = argmaxCat(model.moveLogits, masks.move)
        restrictAdvanceMask(world, smv, masks)
        const sfr = argmaxCat(model.fireLogits, masks.fire)
        const shd = model.holdLogits ? argmaxCat(model.holdLogits, masks.hold) : 0
        const p = world.player
        const studentForced = p !== null && corridorCells(world, p, decodeMove4(smv), 1) === 0
        const tDir = god.getMoveDirection()
        if (tDir !== null) lastTeacherDir = tDir
        const tFiring = god.isFiring()
        god.endFrame()
        const gmv =
          tDir === null
            ? encodeMove4(lastTeacherDir ?? p?.prevMoveDir ?? p?.dir ?? 'up')
            : encodeMove4(tDir)
        const ghd = tDir === null ? 1 : 0
        const gfr = tFiring ? 2 : 0
        decisions++
        if (studentForced) forcedDecisions++
        if (!keepForced || studentForced) {
          samples.push({
            obs: encoder.obs.slice(),
            scalars: encoder.scalars.slice(),
            extra: encoder.extra.slice(),
            move: gmv,
            fire: gfr,
            hold: ghd,
          })
        }
        const useGod = betRng.next() < BETA
        if (useGod) godTookOver++
        scripted.setAction(useGod ? gmv : smv, useGod ? gfr : sfr, useGod ? ghd : shd)
      }
      sim.tick()
      scripted.endFrame()
      t++
      const st = world.state
      if (st === 'stageclear' || st === 'victory' || st === 'gameover') break
    }
    const won = world.state === 'stageclear' || world.state === 'victory'
    if (won) studentWins++
    games++
    if (samples.length > 0) {
      kept++
      const name = `beta_s${sid}_seed${seed}`
      flushShard(samples, `${outDir}/${name}`, name, {
        stage: sid,
        seed,
        student_weights_sha: weightsSha,
        beta: BETA,
        keep: keepForced ? 'forced' : 'all',
      })
      samplesTotal += samples.length
    }
  }
}
console.log(
  JSON.stringify(
    {
      games,
      shards: kept,
      samples: samplesTotal,
      decisions,
      forced_decisions: forcedDecisions,
      forced_pct: Math.round((1000 * forcedDecisions) / Math.max(1, decisions)) / 10,
      god_took_over_pct: Math.round((1000 * godTookOver) / Math.max(1, decisions)) / 10,
      student_win_pct: Math.round((1000 * studentWins) / Math.max(1, games)) / 10,
      beta: BETA,
      keep: keepForced ? 'forced' : 'all',
      out: outDir,
    },
    null,
    2,
  ),
)
