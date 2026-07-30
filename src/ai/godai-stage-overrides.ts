import type { GodAIParams } from './GodAIInput'

/**
 * Per-stage GOD AI parameter overrides (P4, plan/God-AI-Next-Round).
 *
 * Why this exists: CMA-ES rounds 1-6 proved that a single global parameter
 * set CANNOT satisfy all 35 classic stages — some stages demand opposite
 * behaviors from the global default.
 *
 * This is data over code (MANIFEST §2.4): a human player also adapts
 * tactics per map. The table is keyed by stage NAME (stable across index
 * remaps and available to headless runners that load stages standalone).
 *
 * Validation protocol: every override here must be validated at >= 60
 * seeds against the same base params WITHOUT the override (see
 * tools/probe-params.ts). 20-seed probes are noise (binomial +/-11pp) —
 * they were shown to select mirage gains that vanish on fresh seeds.
 *
 * STALE OVERRIDE AUDIT (2026-07-30, DECISIONS.md §54-§55):
 * All overrides were re-probed at 120 seeds against the current global
 * default (post-RNG-split, post-§47 base protection ring). Results:
 *
 *   S6  Iron Curtain  — REMOVED (§54): override 59.2% < naked 62.5%.
 *     The conservative leash (maxPlayerDistFromBase:16) restricted mid-game
 *     map control and paradoxically increased base destructions (43 vs 30).
 *   S18 Frozen Field  — REMOVED (§55): override 56.7% < naked 60.8%.
 *     The wider retreat radius (outnumberedRadiusCells:14) caused the player
 *     to fall back too early, losing map control. aimError:0 was also neutral
 *     (59.2% vs 60.8%, within noise). Original validation (52→67%) is stale.
 *   S25 Ice Palace    — REMOVED (§55): override 77.5% = naked 77.5% (identical).
 *     The default aimError (0.0303) is already so small that setting it to 0
 *     produces zero behavioral difference on this stage.
 *
 *   S26 Brick Maze    — KEPT: override 68.3% ≈ naked 67.5% (+0.8pp, within
 *     noise) but prevents base destruction (0 vs 2). The faster replanning +
 *     path randomness synergy breaks deadlock patrol loops. Individual
 *     params are each slightly worse (65.8%), but the combination works.
 *   S32 Diamond       — KEPT: override 72.5% >> naked 48.3% (+24.2pp).
 *     Close-combat strategy (t2aMaxRange:2) is essential for armor grinding.
 *
 * LESSON: overrides are data, and data goes stale. After RNG split, §47
 * collision fix, and v7 evaluation changes, the global default improved
 * dramatically. Every override must be re-validated after major baseline
 * shifts — otherwise stale overrides silently harm the stages they were
 * meant to help (as happened with S6 and S18).
 */
export const GOD_AI_STAGE_OVERRIDES: Record<string, Partial<GodAIParams>> = {
  'Brick Maze': {
    // Failure mode is pure lives_exhausted (base almost never falls).
    // Faster replanning + a dash of path randomness break the deadlock
    // patrol loops in the dense brick maze where the AI and enemies
    // otherwise circle each other until lives run out.
    // 120-seed probe (2026-07-30): 68.3% vs naked 67.5% (+0.8pp, within
    // noise) but prevents base destruction (0 vs 2). Individual params
    // each 65.8% — the synergy is real but marginal. Kept for base safety.
    replanInterval: 30,
    suboptimalPathProb: 0.05,
  },
  Diamond: {
    // S32 (index 32): 10 armor (4 HP) + 7 fast + 3 power on a map with
    // large forest + fragmented steel + an OPEN bottom band.
    //
    // Core strategy: CLOSE COMBAT (贴身缠斗). The player only stops to
    // aim at enemies within 2 cells (32px), maximizing fire rate against
    // 4-HP armor. At point-blank, bullet travel time ≈ 0, so the player
    // kills armor in ~0.5s instead of ~4s at long range. Faster kills =
    // less exposure = fewer deaths AND more time to respond to base
    // threats between targets.
    //
    // Parameters:
    //   t2aMaxRange: 2 — point-blank stop-and-aim
    //   campTimeoutTicks: 50 — shorter unproductive camp (0.83s vs 1.5s)
    //   antiCampSuppressTicks: 50 — matching camp suppress
    //   damagedArmorBonus: 1 — finish damaged armor (damage is permanent)
    //   navStuckTicks: 90 — faster stuck recovery (1.5s vs 3s)
    //
    // Verified @120 seeds: 43.3% → 72.5% (+29.2pp) with close-combat params.
    //   Failure mode shift: base_destroyed 43→21, lives_exhausted 25→12.
    // 120-seed re-audit (2026-07-30): 72.5% vs naked 48.3% (+24.2pp) — STILL ESSENTIAL.
    // guardBandMode (T2a skip) was tested and REJECTED: it causes the
    // player to abandon in-progress armor kills to chase fast tanks,
    // which is strictly worse (50.8% @120 seeds vs 72.5% without).
    // smartThreatModel (Phase A) was also tested and REJECTED on S32:
    //   - Full model (isBaseUnderThreat + selectTarget + skipT2a): -20pp
    //   - selectTarget only (threatScore): -7.5pp
    //   - Extended race range for fast tanks: -10.8pp (base↓2 but lives↑15)
    //   Root cause: S32's close-combat strategy is fragile — ANY
    //   interruption to armor grinding causes lives_exhausted to spike.
    //   Diagnostic showed armor(10)+power(7) are the primary base killers,
    //   NOT fast tanks(2) — the plan's 'fast rusher' assumption was wrong.
    campTimeoutTicks: 50,
    antiCampSuppressTicks: 50,
    t2aMaxRange: 2,
    damagedArmorBonus: 1,
    navStuckTicks: 90,
  },
}

/**
 * Merge per-stage overrides (if any) into a base parameter set.
 * Returns the base unchanged when the stage has no overrides.
 */
export function applyStageOverrides(stageName: string, base: GodAIParams): GodAIParams {
  const o = GOD_AI_STAGE_OVERRIDES[stageName]
  return o ? { ...base, ...o } : base
}
