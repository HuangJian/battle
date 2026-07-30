import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Wide-Seed Regression Gate (plan/God-AI-Curriculum §6)
//
// The split-parity test locks exact per-seed output on Stage 0 (8 seeds).
// This gate is the REAL tuning regression check: it runs ALL 35 classic
// stages on a fixed seed set and asserts per-stage win floors plus an
// aggregate mean floor. The simulation is deterministic, so a fixed seed
// set yields fixed aggregates — these floors catch any regression in the
// God AI's actual playing strength across every stage family, not just
// refactor drift on two easy stages.
//
// History: the old gate only covered S0/S1 and silently masked a large
// S1 regression during the P4 campaign. Never again — the gate now covers
// every stage.
//
// Floors are derived from the P4 R7 truth-scale measurement
// (35 stages × 60 seeds, classic, 18000 ticks, per-stage override table
// active — see .workbuddy/optimization-p4-r7/ and
// src/ai/godai-stage-overrides.ts):
//   Mean win rate 81.9%; every stage >= 60% except S32 Diamond (52%),
//   the known structural hard case (armor-heavy force on a fragmented
//   steel+forest map; verified not param-tunable at 60 seeds).
//
// Per-stage floor = round(truthWinRate * 20) - 4 wins of margin
// (binomial sd at n=20, p=0.85 is ~1.6; 4 wins ≈ 2.5 sd).
// Aggregate floor = 77% of 700 runs (truth 81.9%, ~3 sd margin).
//
// When params are intentionally re-tuned (a new CMA-ES round or a new
// stage override), re-measure at 60 seeds via tools/validate-p4.ts and
// regenerate the floors below (truth*20 - 4).
//
// Run: bun test tests/god-ai-regression-gate.test.ts
// (takes a few minutes — it plays 700 full games)
// ============================================================

const GATE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20

// Truth win rates (%) from 35×60 validation, re-measured 2026-07-30
// after God AI RNG split (DECISIONS §47) + S6 CMA-ES R8 re-tuning.
// S6 went 33% -> 37% with new override strategy (enabled outnumbered
// retreat + tighter threat + wider player range). S32 Diamond unchanged.
const TRUTH_WIN_PCT: number[] = [
  90.0, // S0  Outpost
  96.7, // S1  Waterways
  95.0, // S2  Steel Fortress
  96.7, // S3  Crossfire
  93.3, // S4  Maze
  80.0, // S5  Brickworks
  36.7, // S6  Iron Curtain (R8 override: enabled outnumbered retreat, tighter threat, wider player range)
  88.3, // S7  Riverbed
  91.7, // S8  Twin Towers
  91.7, // S9  Gauntlet
  83.3, // S10 Fortress
  63.3, // S11 Lattice
  73.3, // S12 Bunker Hill
  96.7, // S13 Steel Web
  65.0, // S14 Citadel
  81.7, // S15 Crossroads
  88.3, // S16 Twin Spires
  95.0, // S17 Gridlock
  63.3, // S18 Frozen Field (override: wide retreat + perfect aim)
  66.7, // S19 Bastion
  71.7, // S20 Checkers
  76.7, // S21 Oasis
  93.3, // S22 Ramparts
  78.3, // S23 Labyrinth
  75.0, // S24 Quarry
  66.7, // S25 Ice Palace (override: perfect aim)
  66.7, // S26 Brick Maze (override: fast replan + path noise)
  80.0, // S27 Thicket
  80.0, // S28 Spider
  76.7, // S29 Concentric
  81.7, // S30 Eagle Nest
  73.3, // S31 Star Fort
  56.7, // S32 Diamond (override: t2aMaxRange=2 close-combat) — dropped 16pp after RNG split
  90.0, // S33 Battlement
  85.0, // S34 Final Redoubt
]

const MARGIN_WINS = 4
const AGGREGATE_FLOOR = Math.floor(0.77 * 35 * GATE_SEEDS.length) // 539/700

function stageFloor(idx: number): number {
  return Math.max(0, Math.round((TRUTH_WIN_PCT[idx] / 100) * GATE_SEEDS.length) - MARGIN_WINS)
}

describe('god-ai-regression-gate', () => {
  it('all 35 classic stages meet the P4 R7 tuning floors', () => {
    let totalWins = 0
    const failures: string[] = []
    for (let idx = 0; idx < STAGES.length; idx++) {
      let wins = 0
      for (const seed of GATE_SEEDS) {
        const r = runSimulation({
          seed,
          stage: STAGES[idx],
          difficulty: 'classic',
          maxTicks: 18000,
          sampleInterval: 18000,
        })
        if (r.outcome === 'stage_clear') wins++
      }
      totalWins += wins
      const floor = stageFloor(idx)
      const pct = ((wins / GATE_SEEDS.length) * 100).toFixed(0)
      console.log(
        `[gate] S${idx} ${STAGES[idx].name}: ${wins}/${GATE_SEEDS.length} (${pct}%) floor=${floor}${wins < floor ? '  <-- BELOW FLOOR' : ''}`,
      )
      if (wins < floor) failures.push(`S${idx} ${STAGES[idx].name}: ${wins} < ${floor}`)
    }
    const meanPct = ((totalWins / (35 * GATE_SEEDS.length)) * 100).toFixed(1)
    console.log(`[gate] aggregate: ${totalWins}/700 (${meanPct}%) floor=${AGGREGATE_FLOOR}`)
    expect(failures).toEqual([])
    expect(totalWins).toBeGreaterThanOrEqual(AGGREGATE_FLOOR)
  }, 600000)
})
