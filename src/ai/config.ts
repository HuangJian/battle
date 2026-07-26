import type { IntelligenceLevel } from '../types'
import type { RNG } from '../utils/RNG'
import type { IntelligenceConfig } from './types'

/**
 * ai/config.ts — the heart of "configuration defines intelligence".
 *
 * Every enemy tank above 'none' runs the exact same decision pipeline
 * (`TacticalIntelligence`). What makes a Rookie different from a Commander is
 * entirely described by the data below. Adding a new tier = appending one
 * entry to `INTELLIGENCE_LEVELS` (plus a distribution weight). No engine code
 * changes.
 *
 * Revision (plan/AI-Tier-System-Revision.md):
 * - Tiers are ROLLED AT SPAWN TIME from `DIFFICULTY_TIER_DISTRIBUTION`; tank
 *   kind no longer implies a tier (`KIND_TO_LEVEL` retired).
 * - Tier capability values are FIXED — difficulty never scales them
 *   (`DIFFICULTY_AI` / `resolveConfig` retired). The single difficulty→AI
 *   lever is the distribution.
 * - `teamwork` is split: issuing directives is exclusive to the ACTIVE
 *   Commander; obeying is universal, gated per-tier by `compliance` [D1].
 * - 'none' is a separate minimal classic-behavior branch (§3); its capability
 *   numbers here are unused placeholders except `compliance: 0` (deaf).
 */

export const INTELLIGENCE_LEVELS: Record<IntelligenceLevel, IntelligenceConfig> = {
  // ----- None: classic Battle City behavior — separate branch, no pipeline.
  // Values below (other than compliance) are never read: `updateNoneTank`
  // bypasses perception/goals/dodge entirely (AI-Tier-System-Revision §3).
  none: {
    name: 'None',
    strategicThinking: false,
    compliance: 0, // deaf — ignores directives entirely
    dodgeProbability: 0,
    predictionDepth: 0,
    routeLookAhead: 0,
    aggression: 0,
    reactionTime: 0,
    aimError: 0,
    routeNoise: 0,
    weights: {
      attackBase: 0,
      attackPlayer: 0,
      destroyWall: 0,
      retreat: 0,
      regroup: 0,
      advance: 0,
    },
  },

  // ----- Rookie: shortsighted, jumpy, forgetful -----
  rookie: {
    name: 'Rookie',
    strategicThinking: false,
    compliance: 0.5,
    dodgeProbability: 0.2,
    predictionDepth: 1,
    routeLookAhead: 2,
    aggression: 0.45,
    reactionTime: 420,
    aimError: 0.35,
    routeNoise: 0.4,
    weights: {
      attackBase: 1.0,
      attackPlayer: 0.7,
      destroyWall: 0.4,
      retreat: 0.5,
      regroup: 0.2,
      advance: 0.6,
    },
  },

  // ----- Soldier: better routing + basic dodging -----
  soldier: {
    name: 'Soldier',
    strategicThinking: false,
    compliance: 0.7,
    dodgeProbability: 0.45,
    predictionDepth: 2,
    routeLookAhead: 4,
    aggression: 0.6,
    reactionTime: 300,
    aimError: 0.2,
    routeNoise: 0.22,
    weights: {
      attackBase: 1.3,
      attackPlayer: 1.0,
      destroyWall: 0.7,
      retreat: 0.5,
      regroup: 0.3,
      advance: 0.7,
    },
  },

  // ----- Veteran: advanced prediction, strong base pressure.
  // No strategic thinking and no command — obeying directives at 80% is
  // baseline soldiering, not "teamwork" [D1].
  veteran: {
    name: 'Veteran',
    strategicThinking: false,
    compliance: 0.8,
    dodgeProbability: 0.75,
    predictionDepth: 4,
    routeLookAhead: 6,
    aggression: 0.72,
    reactionTime: 200,
    aimError: 0.1,
    routeNoise: 0.12,
    weights: {
      attackBase: 1.8,
      attackPlayer: 1.2,
      destroyWall: 1.1,
      retreat: 0.7,
      regroup: 0.5,
      advance: 0.8,
    },
  },

  // ----- Commander: full capability. Strategic thinking + broadcasting are
  // exercised only while holding ACTIVE command (world.activeCommanderId);
  // inactive Commanders fight as "super Veterans" (§4).
  commander: {
    name: 'Commander',
    strategicThinking: true,
    compliance: 0.9,
    dodgeProbability: 0.9,
    predictionDepth: 8,
    routeLookAhead: 10,
    aggression: 0.8,
    reactionTime: 150,
    aimError: 0.05,
    routeNoise: 0.05,
    weights: {
      attackBase: 2.0,
      attackPlayer: 1.3,
      destroyWall: 1.3,
      retreat: 0.8,
      regroup: 0.9,
      advance: 0.9,
    },
  },
}

/** Fixed roll order — determinism requires a stable cumulative walk. */
export const TIER_ROLL_ORDER: IntelligenceLevel[] = [
  'none',
  'rookie',
  'soldier',
  'veteran',
  'commander',
]

/**
 * The single difficulty→AI lever [D8]: per-difficulty tier distribution,
 * applied per spawn in `Simulation.updateSpawning`. Each row sums to 1
 * (unit-tested). Classic is 100% None — the faithful-recreation mode outside
 * the difficulty ladder (§3) — and consumes ZERO RNG for the tier roll.
 */
export const DIFFICULTY_TIER_DISTRIBUTION: Record<
  string,
  Partial<Record<IntelligenceLevel, number>>
> = {
  classic: { none: 1 },
  relax: { rookie: 0.6, soldier: 0.2, veteran: 0.15, commander: 0.05 },
  hard: { rookie: 0.3, soldier: 0.3, veteran: 0.28, commander: 0.12 },
  chaos: { rookie: 0.2, soldier: 0.3, veteran: 0.25, commander: 0.25 },
}

/**
 * Per-stage Commander floor [D9]: minimum Commander *attempts* (rolls) per
 * stage. Quota decrements on every Commander roll, INCLUDING cap-downgraded
 * ones [D9-fix] — the floor guarantees attempts, not survivors, so it is
 * always satisfiable within the stage's 20 spawns.
 */
export const COMMANDER_FLOOR: Record<string, number> = {
  classic: 0,
  relax: 1,
  hard: 2,
  chaos: 4,
}

/** Hard readability/fairness limit: at most 2 Commander-tier tanks alive. */
export const COMMANDER_ALIVE_CAP = 2

/**
 * Map one uniform draw in [0,1) to a tier via a cumulative walk over the
 * distribution in `TIER_ROLL_ORDER`. Pure — the caller supplies the draw
 * (from `world.rng`), keeping all randomness on the World's stream.
 */
export function pickTier(
  dist: Partial<Record<IntelligenceLevel, number>>,
  r: number,
): IntelligenceLevel {
  let acc = 0
  for (const tier of TIER_ROLL_ORDER) {
    acc += dist[tier] ?? 0
    if (r < acc) return tier
  }
  // Float-edge fallback (r ≈ 1 or a row summing < 1): last tier with weight.
  for (let i = TIER_ROLL_ORDER.length - 1; i >= 0; i--) {
    if ((dist[TIER_ROLL_ORDER[i]] ?? 0) > 0) return TIER_ROLL_ORDER[i]
  }
  return 'none'
}

/**
 * Roll a tier for one spawn on the given difficulty. Consumes exactly one
 * `rng` draw when the distribution has any non-None weight, and ZERO draws
 * for a 100%-None distribution (classic) — the tier-roll gate (§7).
 */
export function rollTier(difficultyKey: string, rng: RNG): IntelligenceLevel {
  const dist = DIFFICULTY_TIER_DISTRIBUTION[difficultyKey] ?? DIFFICULTY_TIER_DISTRIBUTION.classic
  if ((dist.none ?? 0) >= 1) return 'none'
  return pickTier(dist, rng.next())
}
