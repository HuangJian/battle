import type { PowerUpType } from '../types'

// ================================================================
// Power-up tier system (plan/new-powerups-plan.md §3.1)
//
// Drops are no longer uniform: a weighted 3-tier system replaces the
// old single-pool pick. This gives designers control over rare vs common
// drops while keeping the logic in config (MANIFEST §2.4).
// ================================================================

/**
 * SUPER_TIER_WEIGHT — probability weight for the "super" (强力) tier.
 * Renamed from SUPER_POWERUP_DROP_CHANCE to avoid semantic confusion
 * (was: "probability of super drop"; now: "weight of super tier in
 * the 3-tier weighted pick").
 */
export const SUPER_TIER_WEIGHT = 0.1

/** Backward-compat alias — existing code that references
 * SUPER_POWERUP_DROP_CHANCE continues to work. */
export const SUPER_POWERUP_DROP_CHANCE = SUPER_TIER_WEIGHT

/**
 * 3-tier power-up pool (plan §3.1). Each tier contains the PowerUpType
 * values that may drop from it; picks within a tier are uniform.
 */
export const POWERUP_TIERS: { super: PowerUpType[]; practical: PowerUpType[]; normal: PowerUpType[] } = {
  super: ['frenzy', 'sacrifice', 'guard', 'rewind'],
  practical: ['star', 'tank', 'bomb', 'freeze'],
  normal: ['shield', 'fence', 'boat', 'repair', 'emp', 'decoy', 'mine'],
}

/**
 * Tier weights — must sum to 1.0 after normalization.
 * Super: 10%, Practical: 40%, Normal: 50%.
 */
export const POWERUP_TIER_WEIGHTS: Record<string, number> = {
  super: SUPER_TIER_WEIGHT,
  practical: 0.4,
  normal: 0.5,
}

/** All super power-ups (强力道具). `rewind` (时光宝盒) joined the pool with
 *  the new-powerups-plan expansion. */
export const SUPER_POWERUP_TYPES: PowerUpType[] = ['frenzy', 'sacrifice', 'guard', 'rewind']

/** 狂暴宣泄: number of shells fired during one activation. */
export const FRENZY_SHOTS = 20

/** 同归于尽: base blast radius (cells) at inventory count = 1; +1 cell per extra. */
export const SACRIFICE_BASE_RADIUS_CELLS = 5
