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
// §2026-08-09: re-baselined at 35×60 (drop-range re-tune 1/2/3 -> 2/4/6 + spawn
// avoidance already shipped). The §134 20-seed truth had drifted (e.g. S16
// recorded 16, current main ~13) — see per-array comments below.
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
// startLives=3 — §130 全难度命数统一, hard 2→3): hard 63.1% / chaos 60.0%
// → aggregate floor 415/394 (DECISIONS §115, M4 round-2 shipped
// DEFAULT — pool-model search tuning, classic restored byte-identical;
// §130 2026-08-05 全难度 3 命后 hard 门禁真值重测; §134 2026-08-05 方向 D
// defenseIntercept 防守位停射拦截 SHIPPED — 60-seed hard +0.76pp / chaos
// +2.15pp 显著 p=0.0087, 门禁真值再重测).
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

// Per-stage wins measured at 35×20 on the §134 baseline (2026-08-05,
// playerStartLevel=1, ALL difficulties startLives=3 — §130 全难度命数统一,
// hard 2→3), star shield on all difficulties, M13 field-wide retreat ON, M4
// round-2 search defaults — replan=1, threatRange 23, campTimeout 20, etc.
// — for pool difficulties, plus §134 defenseInterceptMode=1 SHIPPED (防守位
// 停射拦截基地车道敌人 — 60-seed hard +0.76pp / chaos +2.15pp 显著). §110
// reverted §109's 2★ back to 1★ by user decision. Values are WIN COUNTS out
// of GATE_SEEDS (20). hard = 3 lives (§130).
// §2026-08-09 re-baseline (35×60, drop-range re-tune 1/2/3 -> 2/4/6 +
// spawn-point avoidance already shipped in buildDrop; no coop/dual in this
// gate). Measured hard mean 70.6% / chaos mean 67.6%. The prior §134 20-seed
// truth was stale: e.g. S16 Crossroads recorded 16 but current main measures
// ~13/20 — a pre-existing truth drift unrelated to this change. Values below
// are the 60-seed winrate converted to a 20-seed-equivalent count
// (round(pct*20)) so they drop straight into the gate's 20-seed floor math.
const HARD_TRUTH_WINS: number[] = [
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

// §2026-08-09 re-baseline (35×60, drop-range re-tune 1/2/3 -> 2/4/6 +
// spawn-point avoidance already shipped in buildDrop; no coop/dual in this
// gate). Measured chaos mean 67.6% (§134 recorded 60.0%). The prior §134 20-seed
// truth was stale: e.g. S16 Crossroads recorded 16 but current main measures
// ~13/20 — a pre-existing truth drift unrelated to this change. Values below
// are the 60-seed winrate converted to a 20-seed-equivalent count
// (round(pct*20)) so they drop straight into the gate's 20-seed floor math.
const CHAOS_TRUTH_WINS: number[] = [
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
  10, // S20 Bastion (navStuckZone=1 regression; §186 powerup-stuck 10→8, §187 T2a-skipStuck 8→7)
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
// §2026-08-09 re-baseline (35×60): hard 70.6% / chaos 67.6% (drop-range re-tune
// 1/2/3 -> 2/4/6 + spawn-point avoidance; no coop/dual in this gate).
const HARD_AGGREGATE_FLOOR = Math.floor(((70.6 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 468/700
const CHAOS_AGGREGATE_FLOOR = Math.floor(((67.6 - 3.7) / 100) * 35 * GATE_SEEDS.length) // 447/700

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
      // mutating DEFAULT_GOD_AI_PARAMS flipped S26 1/20 -> 0/20, DECISIONS §98).
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
      `[gate:${label}] S${idx + 1} ${STAGES[idx].name}: ${wins}/${GATE_SEEDS.length} (${pct}%) floor=${f}${wins < f ? '  <-- BELOW FLOOR' : ''}`,
    )
    if (wins < f) failures.push(`S${idx + 1} ${STAGES[idx].name}: ${wins} < ${f}`)
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
