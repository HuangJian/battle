#!/usr/bin/env bun
/**
 * idle-rate.ts — human/NN "stop" measurement tool (plan/new-era-stop.plan.md Phase -1).
 *
 * The whole new-era plan rests on one measurable claim: "stop" is a real,
 * frequently-correct action (three-ish of every ten ticks), yet the action space
 * has no word for it. This tool measures it on real recordings BEFORE any schema
 * or training change — it is the plan's cheapest possible falsification shot.
 *
 * Two readouts, per tick, from a `.replay`:
 *
 *   stopPickRate  — *intent*: fraction of recorded input frames with
 *                   `direction === null` (pack.ts packs null as 0). This is what
 *                   a human/NN "chose to stop"; it ignores physics.
 *   physicalIdle% — *physics*: fraction of alive ticks where the player tank has
 *                   `moving === false` (SimulationPlayer sets it from the input's
 *                   `getMoveDirection()`; a wall/cooldown does not change it, but
 *                   ice glide keeps the tank sliding after a stop intent).
 *
 * Because intent ≠ physics on ice, the report is split three ways, matching the
 * plan §5.2 decomposition:
 *   threat  — player is on an enemy bullet/barrel lane (`inThreatLane`)
 *   ice     — player tank center is over an ice tile (`world.isTankOnIce`)
 *   clean   — neither
 * Layers are classified per tick from the replayed World; `ice` takes priority
 * over `threat` (a stop on ice is a different physical event — see glm/hy P1-8).
 *
 * Also emitted: input-null run lengths (stop 持有长度) and physical-idle run
 * lengths, so the plan's K/2 compatibility judgement (hy P1-4) can be made.
 *
 * Determinism: reuses the exact wiring of tools/replay/verify-replay.ts
 * (restoreWorld → ReplayInput → Simulation), read-only w.r.t. the World. A
 * replay that desyncs (hash chain present but mismatched) is reported, not
 * silently averaged in.
 *
 * Usage:
 *   bun tools/eval/idle-rate.ts tmp/human-x20-replays            # walk dirs
 *   bun tools/eval/idle-rate.ts a.replay b.replay --json out.json
 *   bun tools/eval/idle-rate.ts tmp/... --no-replay              # intent-only (fast)
 */
import { readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { restoreWorld } from '../../src/snapshot/WorldSerializer'
import { unpackFrames } from '../../src/replay/pack'
import { REPLAY_HASH_INTERVAL } from '../../src/replay/config'
import { worldTickHash } from '../../src/replay/tickHash'
import { inThreatLane } from '../../src/nn/danger-metrics'
import type { InputFrame } from '../../src/replay/types'
import type { GameState } from '../../src/types'
import { arg } from '../lib/cli'

const stateOf = (w: World): GameState => w.state

// ================================================================
// Pure helpers (unit-tested; no sim, no DOM)
// ================================================================

/** Lengths of maximal runs of `true` in `flags`, in order. */
export function runLengths(flags: boolean[]): number[] {
  const runs: number[] = []
  let cur = 0
  for (const f of flags) {
    if (f) cur++
    else if (cur > 0) {
      runs.push(cur)
      cur = 0
    }
  }
  if (cur > 0) runs.push(cur)
  return runs
}

/** Median of a numeric list (0 for empty). Sorts a copy; callers pass small arrays. */
export function median(xs: number[]): number {
  if (xs.length === 0) return 0
  const s = [...xs].sort((a, b) => a - b)
  const mid = s.length >> 1
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

export interface FrameStats {
  /** Recorded frames (== ticks in a well-formed replay). */
  totalTicks: number
  /** Frames with `direction === null` (stop intent). */
  nullTicks: number
  /** Stop-intent run-length distribution. */
  nullRunLens: number[]
}

/** Intent-only stats straight from the packed input stream. */
export function frameStats(frames: InputFrame[]): FrameStats {
  const nullFlags = frames.map((f) => f.direction === null)
  return {
    totalTicks: frames.length,
    nullTicks: nullFlags.reduce((a, b) => a + (b ? 1 : 0), 0),
    nullRunLens: runLengths(nullFlags),
  }
}

// ================================================================
// Replay analysis
// ================================================================

export type Layer = 'threat' | 'ice' | 'clean'

export interface LayerStat {
  ticks: number
  /** Ticks in this layer with a stop intent. */
  stopTicks: number
  /** Ticks in this layer with `moving === false`. */
  idleTicks: number
}

export interface ReplayIdleResult {
  file: string
  totalTicks: number
  /** Recorded input frames (stopPickRate denominator) — usually == totalTicks. */
  intentFrames: number
  /** Ticks where the player was alive and spawned. */
  aliveTicks: number
  /** Stop intent (frame null) over ALL recorded frames. */
  stopPickRate: number
  /** Per-1000-tick normalisation of the same quantity (plan §3 unit). */
  stopPickPer1k: number
  /** Physical idle over alive ticks, as a fraction. */
  physicalIdle: number
  /** Stop-intent ticks that were in the threat layer / all stop-intent ticks. */
  stopInThreatPct: number
  layers: Record<Layer, LayerStat>
  nullRunLens: number[]
  idleRunLens: number[]
  nullRunMedian: number
  idleRunMedian: number
  /** Hash-chain verdict when present (null = legacy, no chain). */
  hashVerified: boolean | null
  desynced: boolean
  parseError?: string
}

function emptyLayer(): LayerStat {
  return { ticks: 0, stopTicks: 0, idleTicks: 0 }
}

/** Full readout: parse → restore → replay, classifying every tick. */
export function analyzeReplayText(text: string, file: string): ReplayIdleResult {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) {
    return {
      file,
      totalTicks: 0,
      intentFrames: 0,
      aliveTicks: 0,
      stopPickRate: 0,
      stopPickPer1k: 0,
      physicalIdle: 0,
      stopInThreatPct: 0,
      layers: { threat: emptyLayer(), ice: emptyLayer(), clean: emptyLayer() },
      nullRunLens: [],
      idleRunLens: [],
      nullRunMedian: 0,
      idleRunMedian: 0,
      hashVerified: null,
      desynced: false,
      parseError: parsed.error,
    }
  }
  const replay = parsed.replay
  const meta = replay.metadata

  // Intent readout straight from the packed stream (authoritative, no sim).
  const unpacked = unpackFrames(replay.frames)
  const p1 = unpacked?.p1 ?? []
  const intent = frameStats(p1)

  const world = new World()
  world.rng.reseed(replay.seed)
  const dkey = meta.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = DIFFICULTIES[dkey] ?? DIFFICULTIES['classic']
  world.rules = RULES[dkey] ?? DEFAULT_RULES
  const stage = STAGES[meta.stage] ?? STAGES[0]
  world.loadStageData(stage, 0)
  restoreWorld(world, replay.initialSnapshot)
  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null
  world.state = 'playing'

  const layers: Record<Layer, LayerStat> = {
    threat: emptyLayer(),
    ice: emptyLayer(),
    clean: emptyLayer(),
  }
  const stopInThreat = { stop: 0, inThreat: 0 }
  const idleFlags: boolean[] = []

  const recordedHashes = replay.tickHashes ?? []
  const interval = replay.hashInterval ?? REPLAY_HASH_INTERVAL
  let hashVerified: boolean | null = null
  let hashIdx = 0

  let tick = 0
  let aliveTicks = 0
  let idleTicks = 0

  while (!input.isFinished && tick < replay.totalTicks + 10) {
    // Classify the state the action is about to act on (pre-tick), so an index
    // i classification lines up with p1[i]'s intent.
    const p0 = world.player
    const alive0 = !!p0 && p0.alive && p0.spawnTimer <= 0
    const onIce = p0 ? world.isTankOnIce(p0) : false
    const threat = !onIce && inThreatLane(world)
    const layer: Layer = onIce ? 'ice' : threat ? 'threat' : 'clean'
    const stopIntent = p1[tick]?.direction === null

    sim.tick()
    input.advance()
    tick++
    world.consumeEvents?.()

    if (hashIdx < recordedHashes.length && tick % interval === 0) {
      if (worldTickHash(world) === recordedHashes[hashIdx]) {
        if (hashVerified !== false) hashVerified = true
      } else {
        hashVerified = false
      }
      hashIdx++
    }

    if (alive0) {
      aliveTicks++
      const st = layers[layer]
      st.ticks++
      if (stopIntent) {
        st.stopTicks++
        stopInThreat.stop++
        if (threat) stopInThreat.inThreat++
      }
      const p = world.player
      const idle = !!p && !p.moving
      if (idle) {
        st.idleTicks++
        idleTicks++
      }
      idleFlags.push(idle)
    }

    const endState = stateOf(world)
    if (endState === 'stageclear' || endState === 'gameover' || endState === 'victory') break
  }

  const intentTicks = intent.totalTicks || p1.length
  return {
    file,
    totalTicks: tick,
    intentFrames: intentTicks,
    aliveTicks,
    stopPickRate: intentTicks > 0 ? intent.nullTicks / intentTicks : 0,
    stopPickPer1k: intentTicks > 0 ? (intent.nullTicks / intentTicks) * 1000 : 0,
    physicalIdle: aliveTicks > 0 ? idleTicks / aliveTicks : 0,
    stopInThreatPct: stopInThreat.stop > 0 ? stopInThreat.inThreat / stopInThreat.stop : 0,
    layers,
    nullRunLens: intent.nullRunLens,
    idleRunLens: runLengths(idleFlags),
    nullRunMedian: median(intent.nullRunLens),
    idleRunMedian: median(runLengths(idleFlags)),
    hashVerified,
    desynced: hashVerified === false,
  }
}

// ================================================================
// Aggregation
// ================================================================

export interface Pooled {
  replays: number
  frames: number
  stopPickRate: number
  stopPickPer1k: number
  physicalIdle: number
  stopInThreatPct: number
  layers: Record<Layer, { ticks: number; stopPickPer1k: number; physicalIdle: number }>
  nullRunMedian: number
  idleRunMedian: number
}

export function pool(results: ReplayIdleResult[]): Pooled {
  let frames = 0
  let nullTicks = 0
  let aliveTicks = 0
  let idleTicks = 0
  let stopTotal = 0
  let stopInThreat = 0
  const layerTicks: Record<Layer, number> = { threat: 0, ice: 0, clean: 0 }
  const layerStop: Record<Layer, number> = { threat: 0, ice: 0, clean: 0 }
  const layerIdle: Record<Layer, number> = { threat: 0, ice: 0, clean: 0 }
  const nullRuns: number[] = []
  const idleRuns: number[] = []

  for (const r of results) {
    if (r.parseError) continue
    frames += r.intentFrames
    nullTicks += Math.round(r.stopPickRate * r.intentFrames)
    aliveTicks += r.aliveTicks
    idleTicks += r.physicalIdle * r.aliveTicks
    for (const l of ['threat', 'ice', 'clean'] as Layer[]) {
      layerTicks[l] += r.layers[l].ticks
      layerStop[l] += r.layers[l].stopTicks
      layerIdle[l] += r.layers[l].idleTicks
    }
    nullRuns.push(...r.nullRunLens)
    idleRuns.push(...r.idleRunLens)
  }
  // stopInThreat pooled from layer stop counts (threat layer only).
  stopTotal = layerStop.threat + layerStop.ice + layerStop.clean
  stopInThreat = layerStop.threat

  const layersOut = {} as Pooled['layers']
  for (const l of ['threat', 'ice', 'clean'] as Layer[]) {
    layersOut[l] = {
      ticks: layerTicks[l],
      stopPickPer1k: layerTicks[l] > 0 ? (layerStop[l] / layerTicks[l]) * 1000 : 0,
      physicalIdle: layerTicks[l] > 0 ? layerIdle[l] / layerTicks[l] : 0,
    }
  }

  return {
    replays: results.filter((r) => !r.parseError).length,
    frames,
    stopPickRate: frames > 0 ? nullTicks / frames : 0,
    stopPickPer1k: frames > 0 ? (nullTicks / frames) * 1000 : 0,
    physicalIdle: aliveTicks > 0 ? idleTicks / aliveTicks : 0,
    stopInThreatPct: stopTotal > 0 ? stopInThreat / stopTotal : 0,
    layers: layersOut,
    nullRunMedian: median(nullRuns),
    idleRunMedian: median(idleRuns),
  }
}

// ================================================================
// CLI
// ================================================================

function collectReplayFiles(paths: string[]): string[] {
  const out: string[] = []
  const walk = (p: string): void => {
    let st
    try {
      st = statSync(p)
    } catch {
      return
    }
    if (st.isDirectory()) {
      for (const name of readdirSync(p)) walk(join(p, name))
    } else if (p.endsWith('.replay')) {
      out.push(p)
    }
  }
  for (const p of paths) walk(p)
  return out.sort()
}

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`
}

async function main(): Promise<void> {
  const jsonOut = arg('json')
  const noReplay = process.argv.includes('--no-replay')
  const verbose = process.argv.includes('--verbose')
  const paths = process.argv.slice(2).filter((a) => !a.startsWith('--') && a !== jsonOut)

  if (paths.length === 0) {
    console.error('usage: bun tools/eval/idle-rate.ts <dir|file.replay...> [--json out.json]')
    process.exit(2)
  }

  const files = collectReplayFiles(paths)
  console.log(`[idle-rate] ${files.length} replays | mode=${noReplay ? 'intent-only' : 'full'}`)

  const results: ReplayIdleResult[] = []
  const t0 = performance.now()
  for (const f of files) {
    const text = await Bun.file(f).text()
    const r = noReplay ? { ...analyzeIntentOnly(text, f) } : analyzeReplayText(text, f)
    results.push(r)
    if (verbose || r.desynced) {
      const tag = r.parseError ? 'PARSE-ERR' : r.desynced ? 'DESYNC' : 'ok'
      console.log(
        `  [${tag}] ${r.file} stop/1k=${r.stopPickPer1k.toFixed(1)} ` +
          `idle=${pct(r.physicalIdle)} stops@threat=${pct(r.stopInThreatPct)} ` +
          `nullRunMed=${r.nullRunMedian} idleRunMed=${r.idleRunMedian}`,
      )
    }
  }
  const elapsed = ((performance.now() - t0) / 1000).toFixed(1)

  const parsed = results.filter((r) => !r.parseError)
  const bad = results.filter((r) => r.parseError || r.desynced)
  const agg = pool(results)

  console.log(`\n[idle-rate] ${parsed.length} replays in ${elapsed}s (${bad.length} bad)`)
  console.log(
    `  stopPickRate=${pct(agg.stopPickRate)}  (${agg.stopPickPer1k.toFixed(1)}/千tick over ${agg.frames} frames)`,
  )
  console.log(`  physicalIdle=${pct(agg.physicalIdle)}`)
  console.log(`  stop decisions in threat lane=${pct(agg.stopInThreatPct)}`)
  console.log(
    `  stop-run median=${agg.nullRunMedian} ticks | physical-idle-run median=${agg.idleRunMedian} ticks`,
  )
  console.log('  by layer:')
  for (const l of ['threat', 'ice', 'clean'] as Layer[]) {
    const s = agg.layers[l]
    console.log(
      `    ${l.padEnd(6)} ticks=${String(s.ticks).padStart(7)} ` +
        `stop=${s.stopPickPer1k.toFixed(1)}/1k  idle=${pct(s.physicalIdle)}`,
    )
  }

  if (jsonOut) {
    await Bun.write(jsonOut, JSON.stringify({ pooled: agg, results }, null, 2))
    console.log(`\n  written to ${jsonOut}`)
  }
}

/** Fast path: intent-only, no World replay (for quick corpora surveys). */
function analyzeIntentOnly(text: string, file: string): ReplayIdleResult {
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`${file}: ${parsed.error}`)
  const unpacked = unpackFrames(parsed.replay.frames)
  const intent = frameStats(unpacked?.p1 ?? [])
  const denom = intent.totalTicks
  return {
    file,
    totalTicks: denom,
    intentFrames: denom,
    aliveTicks: 0,
    stopPickRate: denom > 0 ? intent.nullTicks / denom : 0,
    stopPickPer1k: denom > 0 ? (intent.nullTicks / denom) * 1000 : 0,
    physicalIdle: 0,
    stopInThreatPct: 0,
    layers: { threat: emptyLayer(), ice: emptyLayer(), clean: emptyLayer() },
    nullRunLens: intent.nullRunLens,
    idleRunLens: [],
    nullRunMedian: median(intent.nullRunLens),
    idleRunMedian: 0,
    hashVerified: null,
    desynced: false,
  }
}

if (import.meta.main) main()
