#!/usr/bin/env bun
/**
 * export-godai-labels.ts — God-AI teacher corpus exporter (plan/RL-Net-Selection.md P1.5).
 *
 * Runs the live God-AI (GodAIInput) through real engine games and captures, at
 * each decision tick (the same event-type predicate as the BC exporter,
 * plan §1.3: turn / fire-edge / item / subsample every K=10), the pair
 *   (obs-encoder(world), God-AI action label + invalid-action mask + condition)
 * written as npy shards in the SAME format as `tools/replay/export-observations.ts`
 * (obs/scalars/actions/masks/conditions + manifest), so the existing
 * `train_bc.py` can consume the corpus directly. This is the "offline"
 * (hard-label) stage of the P1.5 distillation pipeline — the teacher is the
 * shipped God-AI, before the RL teacher (rl_model.py) exists.
 *
 * Determinism: same (stage, seed, difficulty) → identical shard bytes. The
 * God-AI driver consumes its own independent RNG (seeded `seed ^ 0x9e3779b9`,
 * same as `tools/sim/simulation-runner.ts`), so the run is fully reproducible.
 *
 * Modes / flags:
 *   --stages all|N|N-M   stage range (1-based, default all)
 *   --seeds  N|N-M       seed range (default 1-10)
 *   --difficulty <key>   difficulty (default hard)
 *   --out <dir>          output root (default tmp/godai-labels)
 *   --max-ticks <N>      per-game tick cap (default 36000)
 *   --verify-determinism re-run the FIRST seed of each stage twice and
 *                        byte-compare the shards (gate ②). No training shards.
 *
 * Usage:
 *   bun tools/sim/export-godai-labels.ts --stages 1-5 --seeds 1-3 --out tmp/godai-labels
 *   bun tools/sim/export-godai-labels.ts --stages 1 --seeds 1 --verify-determinism
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { GodAIInput } from '../../src/ai/GodAIInput'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/god/params'
import {
  ObsEncoder,
  decisionTick,
  actionFromFrame,
  computeMasks,
  OBS_SCHEMA_MAJOR,
} from '../../src/nn/obs-encoder'
import { writeShard } from '../../src/nn/npy'
import { mkdirSync, writeFileSync, readdirSync, readFileSync, rmSync } from 'fs'
import { platform, arch, cpus } from 'os'

const EXPORTER_VERSION = '1.0.0'
const K = 10

const ENV = {
  runtime: `bun ${Bun.version}`,
  platform: platform(),
  arch: arch(),
  cpuCores: cpus().length,
}

interface Acc {
  obs: Uint8Array[]
  scalars: Float32Array[]
  actions: number[]
  masks: number[]
  conditions: number[]
  nSamples: number
}

function newAcc(): Acc {
  return { obs: [], scalars: [], actions: [], masks: [], conditions: [], nSamples: 0 }
}

function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [a, b] = spec.split('-').map(Number)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [Number(spec)]
}

/**
 * Run one God-AI game and capture decision-tick (state, label) samples.
 * World setup mirrors `tools/sim/rl-bridge.ts` reset() / simulation-runner.ts.
 */
export function exportGame(seed: number, stageIndex: number, difficulty: string, maxTicks: number): Acc {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const god = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
  const sim = new Simulation(world, god)
  world.loadStageData(STAGES[stageIndex], stageIndex)
  god.reset()

  const encoder = new ObsEncoder()
  const acc = newAcc()

  let prevDir: ReturnType<GodAIInput['getMoveDirection']> = null
  let prevGuard = false
  let prevFrenzy = false
  let t = 0
  while (t < maxTicks) {
    // obs(t): world state BEFORE this tick's input is consumed (plan §1.3).
    encoder.encode(world)
    const dir = god.getMoveDirection()
    const firing = god.isFiring()
    const guard = god.wasItemPressed('guard')
    const frenzy = god.wasItemPressed('frenzy')

    const { isDecision, condition } = decisionTick(
      t, world, prevDir, dir, prevGuard, guard, prevFrenzy, frenzy, K,
    )
    if (isDecision) {
      const label = actionFromFrame({ direction: dir, firing, guard, frenzy })
      const masks = computeMasks(world)
      acc.obs.push(encoder.obs.slice())
      acc.scalars.push(encoder.scalars.slice())
      acc.actions.push(label.move, label.fire, label.item)
      acc.masks.push(...masks.move, ...masks.fire, ...masks.item)
      acc.conditions.push(condition)
      acc.nSamples++
    }

    sim.tick()
    god.endFrame()
    prevDir = dir
    prevGuard = guard
    prevFrenzy = frenzy
    t++
    const st: string = world.state
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
  }
  return acc
}

function flushShard(acc: Acc, dir: string, name: string, baseManifest: Record<string, unknown>): void {
  const N = acc.nSamples
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 24)
  const actions = new Uint8Array(N * 3)
  const masks = new Uint8Array(N * 10)
  const conditions = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    obs.set(acc.obs[i], i * 14 * 26 * 26)
    scalars.set(acc.scalars[i], i * 24)
    actions[i * 3] = acc.actions[i * 3]
    actions[i * 3 + 1] = acc.actions[i * 3 + 1]
    actions[i * 3 + 2] = acc.actions[i * 3 + 2]
    for (let j = 0; j < 10; j++) masks[i * 10 + j] = acc.masks[i * 10 + j]
    conditions[i] = acc.conditions[i]
  }
  const manifest = {
    schemaMajor: OBS_SCHEMA_MAJOR,
    obsSchemaMajor: OBS_SCHEMA_MAJOR,
    exporterVersion: EXPORTER_VERSION,
    shard: name,
    teacher: 'god-ai',
    nSamples: N,
    ...baseManifest,
  }
  writeShard(dir, { obs, scalars, actions, masks, conditions }, manifest)
}

function compareShards(a: string, b: string): { ok: boolean; detail: string } {
  const files = readdirSync(a).filter((f) => f.endsWith('.npy'))
  for (const f of files) {
    const ba = readFileSync(`${a}/${f}`)
    const bb = readFileSync(`${b}/${f}`)
    if (ba.length !== bb.length) return { ok: false, detail: `${f} size ${ba.length} vs ${bb.length}` }
    if (Buffer.compare(ba, bb) !== 0) return { ok: false, detail: `${f} bytes differ` }
  }
  return { ok: true, detail: `${files.length} npy files byte-identical` }
}

function main(): void {
  const arg = (name: string, fallback?: string): string | undefined => {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }
  const has = (name: string): boolean => process.argv.includes(`--${name}`)

  const difficulty = arg('difficulty', 'hard')!
  const stageSpec = arg('stages', 'all')!
  const seedSpec = arg('seeds', '1-10')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const outDir = arg('out', 'tmp/godai-labels')!
  const determinism = has('verify-determinism')

  let stages: number[]
  if (stageSpec === 'all') stages = STAGES.map((_, i) => i)
  else stages = parseRange(stageSpec).map((s) => s - 1)
  const seeds = parseRange(seedSpec)
  if (seeds.length === 0 || stages.length === 0) {
    console.error('usage: bun tools/sim/export-godai-labels.ts --stages all|N|N-M --seeds N|N-M [--out dir] [--verify-determinism]')
    process.exit(2)
  }

  if (determinism) {
    // Gate ②: re-run each (stage, first seed) twice and byte-compare.
    const detA = `${outDir}/__det_a`
    const detB = `${outDir}/__det_b`
    rmSync(detA, { recursive: true, force: true })
    rmSync(detB, { recursive: true, force: true })
    let fails = 0
    const results: string[] = []
    for (const si of stages) {
      const seed = seeds[0]
      const label = `s${si + 1}_seed${seed}`
      const shardName = `shard_${label}`
      flushShard(exportGame(seed, si, difficulty, maxTicks), `${detA}/${shardName}`, shardName, { stage: si, seed, difficulty, totalTicks: 0, outcome: 'det' })
      flushShard(exportGame(seed, si, difficulty, maxTicks), `${detB}/${shardName}`, shardName, { stage: si, seed, difficulty, totalTicks: 0, outcome: 'det' })
      const cmp = compareShards(`${detA}/${shardName}`, `${detB}/${shardName}`)
      if (!cmp.ok) fails++
      results.push(`[${cmp.ok ? 'DET OK' : 'DET FAIL'}] ${label}: ${cmp.detail}`)
    }
    console.log(results.join('\n'))
    console.log(`\n=== determinism check ===`)
    console.log(`games=${stages.length} byteIdentical=${stages.length - fails} fails=${fails}`)
    writeFileSync(`${outDir}/_determinism_report.json`, JSON.stringify({ games: stages.length, fails, env: ENV, results }, null, 2))
    process.exit(fails > 0 ? 1 : 0)
  }

  mkdirSync(outDir, { recursive: true })
  let totalSamples = 0
  let totalTicks = 0
  const perFile: string[] = []
  const t0 = Date.now()
  for (const si of stages) {
    for (const seed of seeds) {
      const acc = exportGame(seed, si, difficulty, maxTicks)
      totalSamples += acc.nSamples
      totalTicks += 0 // tick count not tracked per-game here; nSamples is the unit
      const name = `shard_s${String(si + 1).padStart(2, '0')}_seed${String(seed).padStart(3, '0')}`
      flushShard(acc, `${outDir}/${name}`, name, { stage: si, seed, difficulty, teacher: 'god-ai' })
      perFile.push(`[OK] stage=${si + 1} seed=${seed} samples=${acc.nSamples}`)
    }
  }
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1)

  console.log(perFile.join('\n'))
  console.log(`\n=== export summary ===`)
  console.log(`games=${stages.length * seeds.length} samples=${totalSamples} elapsed=${elapsed}s`)
  console.log(`shards written under: ${outDir}`)
  writeFileSync(`${outDir}/_export_report.json`, JSON.stringify({
    games: stages.length * seeds.length,
    totalSamples,
    elapsedSec: Number(elapsed),
    stages: stages.map((s) => s + 1),
    seeds,
    difficulty,
    outDir,
    env: ENV,
  }, null, 2))
}

if (import.meta.main) {
  main()
}