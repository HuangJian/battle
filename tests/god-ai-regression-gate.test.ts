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
// Floors are derived from the 2026-07-30 truth-scale measurement taken
// after the §47 base protection ring collision fix (35 stages × 60 seeds,
// classic, 18000 ticks, per-stage override table active — see
// src/ai/godai-stage-overrides.ts and DECISIONS.md §47):
//   Mean win rate 87.7%; every stage >= 60%. S32 Diamond reached 90.0% @60
//   (85.0% @120) once the ring collision exploit was fixed.
//
// Per-stage floor = round(truthWinRate * 20) - 4 wins of margin
// (binomial sd at n=20, p=0.85 is ~1.6; 4 wins ≈ 2.5 sd).
// Aggregate floor = 83% of 700 runs (truth 87.7%, ~3 sd margin).
//
// When params are intentionally re-tuned (a new CMA-ES round or a new
// stage override), re-measure at 60 seeds via tools/validate-p4.ts and
// regenerate the floors below (truth*20 - 4).
//
// Run: bun test tests/god-ai-regression-gate.test.ts
// (takes a few minutes — it plays 700 full games)
// ============================================================

const GATE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20

// Truth win rates (%) from the 35×60 validation after the §47 base
// protection ring collision fix (2026-07-30, mean 87.7%). Previous truths
// (P4 R7, 2026-07-29, mean 81.9%) are obsolete because §47 changed
// simulation-layer bullet/base collision semantics.
// NOTE: These values are before the §49 God AI RNG split, which may
// cause minor drift in win rates.
const TRUTH_WIN_PCT: number[] = [
  98.3, // S0  Outpost
  96.7, // S1  Waterways
  98.3, // S2  Steel Fortress
  93.3, // S3  Crossfire
  95.0, // S4  Maze
  90.0, // S5  Brickworks
  72.0, // S6  Iron Curtain (override REMOVED §54: stale conservative leash harmed S6)
  91.7, // S7  Riverbed
  95.0, // S8  Twin Towers
  98.3, // S9  Gauntlet
  86.7, // S10 Fortress
  83.3, // S11 Lattice
  86.7, // S12 Bunker Hill
  96.7, // S13 Steel Web
  80.0, // S14 Citadel
  85.0, // S15 Crossroads
  93.3, // S16 Twin Spires
  98.3, // S17 Gridlock
  60.8, // S18 Frozen Field (override REMOVED §55: radius14 caused -5.8pp, aimError:0 neutral)
  85.0, // S19 Bastion
  80.0, // S20 Checkers
  90.0, // S21 Oasis
  91.7, // S22 Ramparts
  85.0, // S23 Labyrinth
  85.0, // S24 Quarry
  77.5, // S25 Ice Palace (override REMOVED §55: aimError:0 vs default 0.03 = identical behavior)
  66.7, // S26 Brick Maze (override: fast replan + path noise)
  90.0, // S27 Thicket
  86.7, // S28 Spider
  85.0, // S29 Concentric
  85.0, // S30 Eagle Nest
  88.3, // S31 Star Fort
  90.0, // S32 Diamond (override: t2aMaxRange=2 close-combat; §47 ring fix 72.5→85.0 @120, 90.0 @60)
  88.3, // S33 Battlement
  91.7, // S34 Final Redoubt
]

const MARGIN_WINS = 4
// Truth mean 87.7% @60 seeds; binomial 3 sd at n=700 is ~3.7pp → 83% floor.
const AGGREGATE_FLOOR = Math.floor(0.83 * 35 * GATE_SEEDS.length) // 581/700

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
