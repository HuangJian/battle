import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

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
// §95 (2026-08-03): regenerated from the 35×60 measurement of the SHIPPED
// §95 defaults (turn cooldown 100ms + halt-during-cooldown — DECISIONS §95).
// Mean 91.19% — the best of the 50/100/160 sweep (50ms drift 91.0% baseline,
// 100ms+halt 91.2% net +5, 160ms+halt 89.8% net −24). S28 keeps its
// conservative gate-context floor (same order-dependent noise as §94).
const TRUTH_WIN_PCT: number[] = [
  98.3, // S0  Outpost
  98.3, // S1  Waterways
  96.7, // S2  Steel Fortress
  95.0, // S3  Crossfire
  100.0, // S4  Maze (§95 +8.3pp)
  88.3, // S5  Brickworks
  83.3, // S6  Iron Curtain (§95 +8.3pp)
  86.7, // S7  Riverbed
  96.7, // S8  Twin Towers
  100.0, // S9  Gauntlet (§95 +1.7pp)
  88.3, // S10 Fortress
  88.3, // S11 Lattice
  98.3, // S12 Bunker Hill (§95 +8.3pp)
  98.3, // S13 Steel Web
  88.3, // S14 Citadel (§95 +6.6pp)
  90.0, // S15 Crossroads
  98.3, // S16 Twin Spires
  95.0, // S17 Gridlock
  80.0, // S18 Frozen Field
  90.0, // S19 Bastion
  90.0, // S20 Checkers (§95 +10pp)
  93.3, // S21 Oasis
  95.0, // S22 Ramparts
  90.0, // S23 Labyrinth
  85.0, // S24 Quarry
  83.3, // S25 Ice Palace
  81.7, // S26 Brick Maze
  91.7, // S27 Thicket
  91.7, // S28 Spider — gate-context floor kept at pre-§87 level: the full-suite
  //   context is order-dependent (module-level genId counter, World.ts) and
  //   Spider swings 13-20/20 between contexts; §95 eval shows 91.7% @60 (+5),
  //   but a floor of 14 fails on pre-existing context noise, not on §95.
  90.0, // S29 Concentric
  90.0, // S30 Eagle Nest (§95 +13.3pp)
  86.7, // S31 Star Fort
  80.0, // S32 Diamond (§95 +5pp)
  96.7, // S33 Battlement (§95 +5pp)
  88.3, // S34 Final Redoubt
]

const MARGIN_WINS = 4
// Truth mean 91.19% @60 seeds (§95 — DECISIONS §95); binomial 3 sd at n=700
// is ~3.7pp → 87.5% floor.
const AGGREGATE_FLOOR = Math.floor(0.875 * 35 * GATE_SEEDS.length) // 612/700

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
          // Fresh clone per run — immune to singleton pollution from other
          // test files in the shared bun test process (DECISIONS §98).
          godAIParams: { ...DEFAULT_GOD_AI_PARAMS },
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
