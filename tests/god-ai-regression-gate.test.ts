import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/sim/simulation-runner'
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
// Floors are derived from truth-scale measurements (35 stages × 60 seeds,
// classic, 18000 ticks). Baseline: after the §47 base protection ring
// collision fix (2026-07-30, mean 87.7% — DECISIONS.md §47).
// §87 (2026-08-02): regenerated for the shipped §87 defaults (urgent
// power-up pickup priority, DECISIONS §87) — mean 90.9% (1908/2100).
//
// Per-stage floor = round(truthWinRate * 20) - 4 wins of margin
// (binomial sd at n=20, p=0.85 is ~1.6; 4 wins ≈ 2.5 sd).
// Aggregate floor = truth − 3.7pp (3 binomial sd at n=700): 90.9% → 87.2%
// → 610/700.
//
// When params are intentionally re-tuned (a new CMA-ES round or a new
// stage-adaptation threshold), re-measure at 60 seeds via tools/validate-p4.ts
// and regenerate the floors below (truth*20 - 4).
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
// §87 (2026-08-02): regenerated from the 35×60 measurement of the SHIPPED
// §87 defaults (urgent power-up pickup ON: 8/4/2 + danger 0 + minEnemyDist 5
// + spawnRowMax 3). Mean 90.9% (1908/2100) — the §87 A/B's B arm
// (tmp/pickup-on-8-4-2-g6.json), see DECISIONS §87.
// §88/§94 (2026-08-03): regenerated from the 35×60 measurement of the SHIPPED
// §88 defaults (chokepoint holding ON — DECISIONS §94). Mean 90.9% — identical
// to §87, with gains S6 +1.7pp (73.3→75.0), S16 +1.7pp (95.0→96.7),
// S28 +5.0pp (86.7→91.7) and NO stage below its §87 truth. S28 keeps its
// conservative pre-§87 truth: in the full-suite gate context Spider swings
// 13-20/20, so the 91.7% @60 truth (floor 14) fails on context noise.
const TRUTH_WIN_PCT: number[] = [
  98.3, // S0  Outpost
  96.7, // S1  Waterways
  98.3, // S2  Steel Fortress
  96.7, // S3  Crossfire
  91.7, // S4  Maze
  83.3, // S5  Brickworks
  75.0, // S6  Iron Curtain (§94 +1.7pp: chokepoint holding covers the western lane)
  93.3, // S7  Riverbed
  98.3, // S8  Twin Towers
  98.3, // S9  Gauntlet
  93.3, // S10 Fortress
  88.3, // S11 Lattice
  90.0, // S12 Bunker Hill
  100.0, // S13 Steel Web
  81.7, // S14 Citadel
  96.7, // S15 Crossroads
  96.7, // S16 Twin Spires (§94 +1.7pp)
  98.3, // S17 Gridlock
  85.0, // S18 Frozen Field (override REMOVED §55; §87 pickup priority +4 wins @60)
  98.3, // S19 Bastion
  80.0, // S20 Checkers
  91.7, // S21 Oasis
  96.7, // S22 Ramparts
  95.0, // S23 Labyrinth
  90.0, // S24 Quarry
  85.0, // S25 Ice Palace (override REMOVED §55)
  85.0, // S26 Brick Maze (§58 data-driven: brickDenseAdaptRatio → fast replan + path noise)
  90.0, // S27 Thicket
  86.7, // S28 Spider — gate-context floor kept at pre-§87 level: the full-suite
  //   context is order-dependent (module-level genId counter, World.ts) and
  //   Spider swings 13-20/20 between contexts; §94 eval shows 91.7% @60 (+5),
  //   but a floor of 14 fails on pre-existing context noise, not on §94.
  90.0, // S29 Concentric
  76.7, // S30 Eagle Nest
  93.3, // S31 Star Fort
  75.0, // S32 Diamond (§56 close-combat → t2aHighHpMaxRange; §58 armorAdaptRatio → camp/nav timing; §87 +5 wins @60)
  91.7, // S33 Battlement
  93.3, // S34 Final Redoubt
]

const MARGIN_WINS = 4
// Truth mean 90.9% @60 seeds (DECISIONS §87); binomial 3 sd at n=700 is
// ~3.7pp → 87.2% floor.
const AGGREGATE_FLOOR = Math.floor(0.872 * 35 * GATE_SEEDS.length) // 610/700

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
