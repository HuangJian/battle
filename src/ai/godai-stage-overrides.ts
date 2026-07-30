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
 * STALE OVERRIDE AUDIT (2026-07-30, DECISIONS.md §54-§56):
 * All overrides were re-probed at 120 seeds against the current global
 * default (post-RNG-split, post-§47 base protection ring). Results:
 *
 *   S6  Iron Curtain  — REMOVED (§54): override 59.2% < naked 62.5%.
 *   S18 Frozen Field  — REMOVED (§55): override 56.7% < naked 60.8%.
 *   S25 Ice Palace    — REMOVED (§55): override 77.5% = naked 77.5% (identical).
 *   S32 Diamond       — PARTIALLY REMOVED (§56): the core close-combat
 *     strategy (t2aMaxRange:2) was generalized into `t2aHighHpMaxRange`
 *     (default 2), triggered by `enemyKind === 'armor'` instead of stage
 *     name. The `damagedArmorBonus` was tested but HURT S32 (-8.4pp) by
 *     causing target-switching that interrupts armor grinding. Only the
 *     camp/nav params remain as stage-specific tuning — they cannot be
 *     generalized because shorter camp/nav helps S32/S18/S25 but hurts
 *     S6/S10/S15 (mixed results at 60 seeds).
 *
 *   S26 Brick Maze    — KEPT: override 68.3% ≈ naked 67.5% (+0.8pp, within
 *     noise) but prevents base destruction (0 vs 2). The faster replanning +
 *     path randomness synergy breaks deadlock patrol loops.
 *
 * LESSON: overrides are data, and data goes stale. After RNG split, §47
 * collision fix, and v7 evaluation changes, the global default improved
 * dramatically. Every override must be re-validated after major baseline
 * shifts. When an override's core mechanism can be abstracted into a
 * universal parameter (as with S32's close-combat → t2aHighHpMaxRange),
 * prefer the generalization over the per-stage override. Auxiliary params
 * that don't generalize (camp/nav timing) may remain as minimal overrides.
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
    // S32 (index 32): 8 armor (4 HP) + 8 fast + 4 power on a map with
    // large forest + fragmented steel + an OPEN bottom band.
    //
    // §56: the core close-combat strategy (t2aMaxRange:2) was generalized
    // into `t2aHighHpMaxRange` (default 2, triggered by enemyKind === 'armor').
    // The `damagedArmorBonus` was tested at 1 but HURT S32 (-8.4pp) by
    // causing target-switching that interrupts armor grinding.
    //
    // What remains: shorter camp/nav timing. S32's fragmented map with
    // forest cover creates frequent stuck/loop situations that the default
    // camp/nav timers (90/60/180) are too slow to escape. The shorter
    // values (50/50/90) break these loops faster, recovering +8pp on S32.
    // These were tested globally but showed mixed results (helped S18/S25,
    // hurt S6/S10/S15), so they remain stage-specific.
    //
    // 120-seed probe (2026-07-30): 64.2% without override → 72.5% with
    // camp/nav override. The +8.3pp gap is entirely from camp/nav timing.
    campTimeoutTicks: 50,
    antiCampSuppressTicks: 50,
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
