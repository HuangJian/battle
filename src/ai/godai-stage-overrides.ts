import type { GodAIParams } from './GodAIInput'

/**
 * Per-stage GOD AI parameter overrides (P4, plan/God-AI-Next-Round).
 *
 * Why this exists: CMA-ES rounds 1-6 proved that a single global parameter
 * set CANNOT satisfy all 35 classic stages — the failure families demand
 * opposite behaviors:
 *
 *   - S6 Iron Curtain (steel maze, flanking base-runs): wants the
 *     outnumbered-retreat OFF (count=5 disables it; retreating just cedes
 *     map control and the flankers race the base) and a tighter threat
 *     range so defense only triggers on real threats.
 *   - S18 Frozen Field (open ice, 8 armor tanks, 3-way crossfire): wants a
 *     WIDER outnumbered-retreat radius (fall back before the pincer closes)
 *     and perfect aim (armor tanks take 4 hits; wasted shots are lethal).
 *
 * Tuning one of these globally regresses the other (verified at 60 seeds:
 * radius=14 lifts S18 +15pp but costs S6 -30pp and S32 -30pp).
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
 *   S6  Iron Curtain: 57% -> 63%
 *   S18 Frozen Field: 52% -> 67%
 * Validated 2026-07-29 against R7 base params, 60 seeds each:
 *   S25 Ice Palace:   57% -> 73%
 *   S26 Brick Maze:   53% -> 65%
 *
 * Known hard case (NOT override-tunable, verified at 60 seeds against both
 * R6 and R7 bases): S32 Diamond (~52%). Armor-heavy force (8 armor / 8
 * fast / 4 power) on a fragmented steel+forest map with an open bottom
 * band; every single/double param change scored at or below base. Needs a
 * structural fix (maze-aware navigation or an armor-stage base guard).
 */
export const GOD_AI_STAGE_OVERRIDES: Record<string, Partial<GodAIParams>> = {
  'Iron Curtain': {
    // Disable outnumbered retreat (5 > max 4 enemies on field): in the
    // steel maze, retreating cedes the map and flankers race the base.
    outnumberedEnemyCount: 5,
    // Tighter threat range: only true base threats trigger defense mode,
    // keeping the player hunting in the maze where the kills are.
    threatRangeCells: 14,
  },
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
    // Verified @120 seeds: 43.3% → 72.5% (+29.2pp). Failure mode shift:
    //   base_destroyed: 43→21, lives_exhausted: 25→12.
    // guardBandMode (T2a skip) was tested and REJECTED: it causes the
    // player to abandon in-progress armor kills to chase fast tanks,
    // which is strictly worse (50.8% @120 seeds vs 72.5% without).
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
