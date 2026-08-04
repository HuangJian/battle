import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

// ============================================================
// God AI Hard/Chaos Gate (plan/God-AI-Redesign-v2 §6, M0)
//
// The classic gate (god-ai-regression-gate.test.ts) guards the shipped
// classic behavior. This gate guards the OTHER two difficulties — the ones
// the v2 redesign exists to fix (hard 38.6% → target 80%, chaos 34.6% →
// target 50% at 35×20, measured 2026-08-03, DECISIONS §99).
//
// Floors are derived from the §105 baselines (35 stages × 20 seeds, 18000
// ticks, telemetry off — byte-identical to the classic gate's run shape).
// §104 (M6) shipped playerStartLevel 0→1; §105 (M7, 2026-08-03) fixed the
// simulation runner (playerLevel + lives + telemetry-isPlayer — DECISIONS
// §105). §109 (M11, 2026-08-03) shipped playerStartLevel 1→2 (60-seed hard
// +9.4pp / chaos +7.5pp), then §110 (2026-08-03) REVERTED it back to 1★ by
// user decision ("2★ 起步有点儿欺负敌人" — it affects human hard/chaos
// play, not just God AI). §111 (2026-08-04) extended the 3★ star shield to
// ALL difficulties (engine change, SimulationCombat — classic-only before;
// measured impact noise-level since the player rarely reaches 3★, HP probe
// 35×20: lvl3+ alive-time 1.3-1.6%). §113 (M13, 2026-08-04) SHIPPED the
// field-wide outnumbered positioning retreat (outnumberedFieldRetreat=1 /
// 3 enemies / 15 cells, pool-model only — classic byte-identical): 20-seed
// hard +2.7pp / chaos +2.6pp, 60-seed hard +2.3pp / chaos +0.6pp — the FIRST
// mechanism without a chaos downside (base losses + deaths down in BOTH
// difficulties). Current truth (20-seed, playerStartLevel=1, ALL difficulties
// startLives=3 — §130 全难度命数统一, hard 2→3): hard 61.6% / chaos 58.3%
// → aggregate floor 405/382 (DECISIONS §115, M4 round-2 shipped
// DEFAULT — pool-model search tuning, classic restored byte-identical;
// §130 2026-08-05 全难度 3 命后 hard 门禁真值重测).
// (DECISIONS §111).
//
// These are 20-seed SCREENING floors (same convention as the classic gate's
// per-stage floors). They catch regressions, not improvements: any shipped
// God-AI change that drops hard/chaos below these floors must be reverted or
// re-measured. Upgrade to 60-seed truth when the v2 milestones reach them.
//
// Run: bun test tests/god-ai-hard-chaos-gate.test.ts
// (plays 1400 full games — expect several minutes)
// ============================================================

const GATE_SEEDS = Array.from({ length: 20 }, (_, i) => i + 1) // 1..20
const MARGIN_WINS = 4

// Per-stage wins measured at 35×20 on the §130 baseline (2026-08-05,
// playerStartLevel=1, ALL difficulties startLives=3 — §130 全难度命数统一,
// hard 2→3), star shield on all difficulties, M13 field-wide retreat ON, M4
// round-2 search defaults — replan=1, threatRange 23, campTimeout 20, etc.
// — for pool difficulties. §110 reverted §109's 2★ back to 1★ by user
// decision. Values are WIN COUNTS out of GATE_SEEDS (20). hard = 3 lives (§130).
const HARD_TRUTH_WINS: number[] = [
  11, // S0  Outpost
  14, // S1  Waterways
  12, // S2  Steel Fortress
  13, // S3  Crossfire
  12, // S4  Maze
  14, // S5  Brickworks
  14, // S6  Iron Curtain
  8, // S7  Riverbed
  15, // S8  Twin Towers
  15, // S9  Gauntlet
  14, // S10  Fortress
  10, // S11  Lattice
  14, // S12  Bunker Hill
  15, // S13  Steel Web
  10, // S14  Citadel
  10, // S15  Crossroads
  14, // S16  Twin Spires
  14, // S17  Gridlock
  14, // S18  Frozen Field
  7, // S19  Bastion
  13, // S20  Checkers
  12, // S21  Oasis
  20, // S22  Ramparts
  9, // S23  Labyrinth
  14, // S24  Quarry
  10, // S25  Ice Palace
  9, // S26  Brick Maze
  8, // S27  Thicket
  10, // S28  Spider
  16, // S29  Concentric
  11, // S30  Eagle Nest
  16, // S31  Star Fort
  16, // S32  Diamond
  1, // S33  Battlement
  16, // S34  Final Redoubt
]

// §130 同次测量（gate-context，2026-08-05）：chaos 命数未变，但跨进程
// genId 上下文噪声致 7 关各 ±1（总量 408/700 vs §115 的 407/700）——以本次
// 门禁自身口径为准。
const CHAOS_TRUTH_WINS: number[] = [
  13, // S0  Outpost
  18, // S1  Waterways
  8, // S2  Steel Fortress
  12, // S3  Crossfire
  8, // S4  Maze
  9, // S5  Brickworks
  12, // S6  Iron Curtain
  9, // S7  Riverbed
  15, // S8  Twin Towers
  16, // S9  Gauntlet
  12, // S10  Fortress
  8, // S11  Lattice
  12, // S12  Bunker Hill
  16, // S13  Steel Web
  9, // S14  Citadel
  16, // S15  Crossroads
  12, // S16  Twin Spires
  18, // S17  Gridlock
  10, // S18  Frozen Field
  11, // S19  Bastion
  9, // S20  Checkers
  7, // S21  Oasis
  18, // S22  Ramparts
  4, // S23  Labyrinth
  16, // S24  Quarry
  10, // S25  Ice Palace
  7, // S26  Brick Maze
  11, // S27  Thicket
  13, // S28  Spider
  16, // S29  Concentric
  8, // S30  Eagle Nest
  14, // S31  Star Fort
  16, // S32  Diamond
  1, // S33  Battlement
  14, // S34  Final Redoubt
]

// Aggregate floors: truth mean − 3.7pp (3 binomial sd at n=700).
// §130 (2026-08-05): 全难度命数统一 3 后重测 → hard 61.6% / chaos 58.3%.
const HARD_AGGREGATE_FLOOR = Math.floor(((61.6 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 405/700
const CHAOS_AGGREGATE_FLOOR = Math.floor(((58.3 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 382/700

function stageFloor(truth: number[]): (idx: number) => number {
  // truth holds per-stage WIN COUNTS out of GATE_SEEDS (not percentages).
  return (idx: number) => Math.max(0, truth[idx] - MARGIN_WINS)
}

function runDifficulty(
  difficulty: string,
  truth: number[],
  label: string,
  aggregateFloor: number,
): void {
  const floor = stageFloor(truth)
  let totalWins = 0
  const failures: string[] = []
  for (let idx = 0; idx < STAGES.length; idx++) {
    let wins = 0
    for (const seed of GATE_SEEDS) {
      // Pass a fresh params clone per run: the gate measures the SHIPPED
      // defaults and must be immune to any singleton pollution from other
      // test files (cross-file module state is shared in bun test — a test
      // mutating DEFAULT_GOD_AI_PARAMS flipped S25 1/20 -> 0/20, DECISIONS §98).
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
    totalWins += wins
    const f = floor(idx)
    const pct = ((wins / GATE_SEEDS.length) * 100).toFixed(0)
    console.log(
      `[gate:${label}] S${idx} ${STAGES[idx].name}: ${wins}/${GATE_SEEDS.length} (${pct}%) floor=${f}${wins < f ? '  <-- BELOW FLOOR' : ''}`,
    )
    if (wins < f) failures.push(`S${idx} ${STAGES[idx].name}: ${wins} < ${f}`)
  }
  const meanPct = ((totalWins / (35 * GATE_SEEDS.length)) * 100).toFixed(1)
  console.log(`[gate:${label}] aggregate: ${totalWins}/700 (${meanPct}%) floor=${aggregateFloor}`)
  expect(failures).toEqual([])
  expect(totalWins).toBeGreaterThanOrEqual(aggregateFloor)
}

describe('god-ai-hard-chaos-gate', () => {
  it('all 35 hard stages meet the M0 screening floors', () => {
    runDifficulty('hard', HARD_TRUTH_WINS, 'hard', HARD_AGGREGATE_FLOOR)
  }, 900000)

  it('all 35 chaos stages meet the M0 screening floors (lives=3)', () => {
    runDifficulty('chaos', CHAOS_TRUTH_WINS, 'chaos', CHAOS_AGGREGATE_FLOOR)
  }, 900000)
})
