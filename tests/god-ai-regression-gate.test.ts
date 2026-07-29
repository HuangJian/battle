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

// Truth win rates (%) from the 35×60 R7 validation, 2026-07-29.
const TRUTH_WIN_PCT: number[] = [
  95.0, // S0  Outpost
  98.3, // S1  Waterways
  95.0, // S2  Steel Fortress
  91.7, // S3  Crossfire
  85.0, // S4  Maze
  83.3, // S5  Brickworks
  63.3, // S6  Iron Curtain (override: retreat off + tight threat range)
  96.7, // S7  Riverbed
  93.3, // S8  Twin Towers
  96.7, // S9  Gauntlet
  81.7, // S10 Fortress
  66.7, // S11 Lattice
  71.7, // S12 Bunker Hill
  95.0, // S13 Steel Web
  66.7, // S14 Citadel
  76.7, // S15 Crossroads
  83.3, // S16 Twin Spires
  95.0, // S17 Gridlock
  61.7, // S18 Frozen Field (override: wide retreat + perfect aim)
  78.3, // S19 Bastion
  80.0, // S20 Checkers
  83.3, // S21 Oasis
  95.0, // S22 Ramparts
  80.0, // S23 Labyrinth
  80.0, // S24 Quarry
  73.3, // S25 Ice Palace (override: perfect aim)
  65.0, // S26 Brick Maze (override: fast replan + path noise)
  86.7, // S27 Thicket
  81.7, // S28 Spider
  86.7, // S29 Concentric
  83.3, // S30 Eagle Nest
  75.0, // S31 Star Fort
  51.7, // S32 Diamond (known structural hard case — no override works)
  83.3, // S33 Battlement
  86.7, // S34 Final Redoubt
]

const MARGIN_WINS = 4
const AGGREGATE_FLOOR = Math.floor(0.77 * 35 * GATE_SEEDS.length) // 539/700

function stageFloor(idx: number): number {
  return Math.max(0, Math.round((TRUTH_WIN_PCT[idx] / 100) * GATE_SEEDS.length) - MARGIN_WINS)
}

describe('god-ai-regression-gate', () => {
  it(
    'all 35 classic stages meet the P4 R7 tuning floors',
    () => {
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
    },
    600000,
  )
})
