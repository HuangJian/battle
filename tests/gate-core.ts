// ============================================================
// Shared harness for the God-AI hard/chaos regression gate.
//
// This is NOT a test file (no `.test` infix) — it is imported by the split
// gate part files and the aggregate reducer. It holds the truth arrays,
// the per-stage simulation runner, and a run-scoped temp-dir protocol that
// lets the parallel part files hand their partial win counts to the single
// aggregate reducer.
//
// Why split at all? The original gate ran 1400 synchronous, CPU-bound
// simulations inside ONE file on a SINGLE core (~62s). `test.concurrent`
// does NOT parallelize synchronous work in bun (it is cooperative), so the
// only way to use all cores is file-level parallelism: bun's `--parallel`
// runs each `*.test.ts` in its own worker PROCESS. We therefore split the
// 35 stages × 2 difficulties across 14 part files (7 stage-chunks × hard/
// chaos) and let `--parallel` spread them across cores.
//
// Cross-file communication uses a temp dir keyed by the parent PID of the
// `bun test` process. Every `--parallel` worker is a child of that same
// parent, so they all share `process.ppid` and therefore the same dir; a
// fresh `bun test` invocation gets a fresh PID → no cross-run staleness.
// Each part writes atomically (tmp + rename) so the reducer never reads a
// half-written file.
// ============================================================

import {
  writeFileSync,
  renameSync,
  existsSync,
  readFileSync,
  readdirSync,
  mkdirSync,
  rmSync,
} from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

export const GATE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20
export const MARGIN_WINS = 4

// Per-stage wins measured at 35×20 on the §134 baseline (2026-08-05,
// playerStartLevel=1, ALL difficulties startLives=3 — §130 全难度命数统一,
// hard 2→3), star shield on all difficulties, M13 field-wide retreat ON, M4
// round-2 search defaults, plus §134 defenseInterceptMode=1 SHIPPED.
// §2026-08-09 re-baseline (35×60, drop-range re-tune 1/2/3 -> 2/4/6 + spawn
// avoidance): measured hard mean 70.6% / chaos mean 67.6%. Values below are
// the 60-seed winrate converted to a 20-seed-equivalent count
// (round(pct*20)) so they drop straight into the 20-seed floor math.
export const HARD_TRUTH_WINS: number[] = [
  16, // S1  Outpost
  16, // S2  Waterways
  11, // S3  Steel Fortress
  13, // S4  Crossfire
  12, // S5  Maze
  12, // S6  Brickworks
  16, // S7  Iron Curtain
  8, // S8  Riverbed
  16, // S9  Twin Towers
  14, // S10 Gauntlet
  15, // S11 Fortress
  15, // S12 Lattice
  16, // S13 Bunker Hill
  15, // S14 Steel Web
  13, // S15 Citadel
  13, // S16 Crossroads
  16, // S17 Twin Spires
  19, // S18 Gridlock
  16, // S19 Frozen Field
  7, // S20 Bastion (navStuckZone=1 regression; §186 powerup-stuck 10→8, §187 T2a-skipStuck 8→7)
  13, // S21 Checkers
  12, // S22 Oasis
  20, // S23 Ramparts
  10, // S24 Labyrinth
  19, // S25 Quarry
  11, // S26 Ice Palace
  15, // S27 Brick Maze
  10, // S28 Thicket
  16, // S29 Spider
  18, // S30 Concentric
  14, // S31 Eagle Nest
  15, // S32 Star Fort
  19, // S33 Diamond
  3, // S34 Battlement
  16, // S35 Final Redoubt
]

export const CHAOS_TRUTH_WINS: number[] = [
  15, // S1  Outpost
  17, // S2  Waterways
  10, // S3  Steel Fortress
  14, // S4  Crossfire
  9, // S5  Maze
  12, // S6  Brickworks
  13, // S7  Iron Curtain
  10, // S8  Riverbed
  17, // S9  Twin Towers
  15, // S10 Gauntlet
  17, // S11 Fortress
  12, // S12 Lattice
  12, // S13 Bunker Hill
  15, // S14 Steel Web
  12, // S15 Citadel
  13, // S16 Crossroads
  15, // S17 Twin Spires
  19, // S18 Gridlock
  13, // S19 Frozen Field
  10, // S20 Bastion
  16, // S21 Checkers
  15, // S22 Oasis
  20, // S23 Ramparts
  7, // S24 Labyrinth
  16, // S25 Quarry
  9, // S26 Ice Palace
  15, // S27 Brick Maze
  12, // S28 Thicket
  14, // S29 Spider
  18, // S30 Concentric
  13, // S31 Eagle Nest
  15, // S32 Star Fort
  16, // S33 Diamond
  1, // S34 Battlement
  18, // S35 Final Redoubt
]

// Aggregate floors: truth mean − 3.7pp (3 binomial sd at n=700).
export const HARD_AGGREGATE_FLOOR = Math.floor(((70.6 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 468/700
export const CHAOS_AGGREGATE_FLOOR = Math.floor(((67.6 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 447/700

export function stageFloor(truth: number[]): (idx: number) => number {
  // truth holds per-stage WIN COUNTS out of GATE_SEEDS (not percentages).
  return (idx: number) => Math.max(0, truth[idx] - MARGIN_WINS)
}

/** Run one stage for a difficulty across all GATE_SEEDS; return win count. */
export function runStage(difficulty: string, idx: number): number {
  let wins = 0
  for (const seed of GATE_SEEDS) {
    // Fresh params clone per run: the gate measures the SHIPPED defaults and
    // must be immune to any singleton pollution from other test files
    // (cross-file module state is shared in bun test — DECISIONS §98).
    const r = runSimulation({
      seed,
      stage: STAGES[idx],
      difficulty,
      maxTicks: 18000,
      sampleInterval: 18000,
      godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
    })
    if (r.outcome === 'stage_clear') wins++
  }
  return wins
}

// ---- cross-file result sharing (ppid-scoped temp dir) ----

const RUN_DIR = join(tmpdir(), `battle-gate-${process.ppid}`)

function ensureDir(): void {
  if (!existsSync(RUN_DIR)) mkdirSync(RUN_DIR, { recursive: true })
}

/** Atomically publish a part's partial win counts for the reducer to read. */
export function writePart(id: string, payload: { difficulty: string; wins: number[] }): void {
  ensureDir()
  const tmp = join(RUN_DIR, `${id}.tmp`)
  const final = join(RUN_DIR, `${id}.json`)
  writeFileSync(tmp, JSON.stringify(payload))
  renameSync(tmp, final)
}

export function hasAllParts(expectedIds: string[]): boolean {
  return expectedIds.every((id) => existsSync(join(RUN_DIR, `${id}.json`)))
}

export function readAllParts(
  expectedIds: string[],
): Map<string, { difficulty: string; wins: number[] }> {
  const out = new Map<string, { difficulty: string; wins: number[] }>()
  for (const id of expectedIds) {
    const p = join(RUN_DIR, `${id}.json`)
    if (!existsSync(p)) continue
    out.set(id, JSON.parse(readFileSync(p, 'utf8')))
  }
  return out
}

export function cleanupRunDir(): void {
  if (existsSync(RUN_DIR)) rmSync(RUN_DIR, { recursive: true, force: true })
}

/** List any leftover part JSONs (debug aid). */
export function listParts(): string[] {
  if (!existsSync(RUN_DIR)) return []
  return readdirSync(RUN_DIR).filter((f) => f.endsWith('.json'))
}

export { STAGES }
