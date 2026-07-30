import type { GodAIParams } from './GodAIInput'

/**
 * Per-stage GOD AI parameter overrides (P4, plan/God-AI-Next-Round).
 *
 * Why this exists: CMA-ES rounds 1-6 proved that a single global parameter
 * set CANNOT satisfy all 35 classic stages — the failure families demand
 * opposite behaviors:
 *
 *   - S18 Frozen Field (open ice, 8 armor tanks, 3-way crossfire): wants a
 *     WIDER outnumbered-retreat radius (fall back before the pincer closes)
 *     and perfect aim (armor tanks take 4 hits; wasted shots are lethal).
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
 * Validated 2026-07-29 against R6 base params, 60 seeds each:
 *   S18 Frozen Field: 52% -> 67%
 * Validated 2026-07-29 against R7 base params, 60 seeds each:
 *   S25 Ice Palace:   57% -> 73%
 *   S26 Brick Maze:   53% -> 65%
 *
 * S6 Iron Curtain override REMOVED (2026-07-30, DECISIONS.md §54):
 * The R8 override (outnumberedEnemyCount:2, maxPlayerDistFromBase:16,
 * defenseRowOffset:3, ...) was designed for a pre-RNG-split baseline.
 * After §47 (base protection ring) + RNG split + v7 evaluation changes,
 * the global default params became strong enough that the override's
 * conservative leash (maxPlayerDistFromBase:16 vs default 26) actively
 * harmed S6: it restricted mid-game map control, slowed kill tempo, and
 * paradoxically INCREASED base destructions (43 vs 30 per 120 seeds).
 * 120-seed probe: override 59.2% < naked default 62.5%. Minimal overrides
 * tested (maxPlayerDistFromBase:28, t8MaxInterceptDistCells:10) showed
 * no significant improvement over naked default at 120 seeds (60.0% vs
 * 62.5%). Conclusion: stale overrides must be removed, not patched.
 *
 * Known hard case (NOT override-tunable, verified at 60 seeds against both
 * R6 and R7 bases): S32 Diamond (~52%). Armor-heavy force (8 armor / 8
 * fast / 4 power) on a fragmented steel+forest map with an open bottom
 * band; every single/double param change scored at or below base. Needs a
 * structural fix (maze-aware navigation or an armor-stage base guard).
 */
export const GOD_AI_STAGE_OVERRIDES: Record<string, Partial<GodAIParams>> = {
  'Frozen Field': {
    // Wider retreat radius: fall back before the 3-way pincer closes on
    // the open ice field (deaths cluster at rows 5-10, the corridor band
    // below the enemy spawn rows).
    outnumberedRadiusCells: 14,
    // Perfect aim: 8 of 20 enemies are 4-hit armor tanks; wasted shots
    // extend exposure time in open terrain.
    aimError: 0,
  },
  'Ice Palace': {
    // Perfect aim: like Frozen Field this is an ice map where wasted
    // shots extend exposure; consistent +16pp across seed windows.
    aimError: 0,
  },
  'Brick Maze': {
    // Failure mode is pure lives_exhausted (base almost never falls).
    // Faster replanning + a dash of path randomness break the deadlock
    // patrol loops in the dense brick maze where the AI and enemies
    // otherwise circle each other until lives run out.
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
