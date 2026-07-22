import type { TankKind } from '../types'
import type { CombatProfile, CombatDimension, TankStats } from '../types'

/**
 * config/combat.ts — the heart of the Combat Capability System.
 *
 * Every tank, including the player, is described by the same six capability
 * dimensions (firepower / projectileSpeed / fireControl / mobility / armor /
 * special).  Tank types are nothing more than different *distributions* of a
 * fixed budget; the concrete gameplay numbers (speed, HP, cooldown, …) are
 * derived from the profile by `profileToStats`.
 *
 * Design consequences (plan §18 "Definition of Done"):
 *  - Tank identity comes from capability distribution, not special rules.
 *  - Normal enemies share a similar budget (300); elites break it (+15%).
 *  - Player progression raises ALL dimensions together (universal growth).
 *  - New tank types = a new profile entry here, never an engine branch.
 *  - AI reads the profile to weight its decisions (see `capabilityBias`).
 *
 * All profiles/derived data are immutable shared constants — safe to store by
 * reference on the World (RecoverySystem shallow-clones tanks) and to read from
 * the Simulation / AI without copying.
 */

// ============================================================
// Budgets
// ============================================================

/** Standard combat budget for every normal enemy (plan §6). */
export const BASELINE_BUDGET = 300

/** Raised budget available to elite commanders (plan §10). Not a hard cap —
 *  the +15% elite modifier lifts a normal profile above 300 organically. */
export const ELITE_BUDGET = 360

// ============================================================
// Tank-type profiles (normal enemies) — budget = 300 each
// ============================================================

/**
 * The four enemy archetypes.  Each sums to BASELINE_BUDGET; differences are
 * entirely in *distribution*, never the total (plan §9 — "difficulty comes
 * from variety, not inflation").
 *
 *   balanced (basic): everything near average.
 *   fast:             high mobility, weaker everything else.
 *   power:            high firepower, low mobility / armor.
 *   heavy  (armor):   high armor, low mobility.
 */
export const TANK_PROFILES: Record<Exclude<TankKind, 'player'>, CombatProfile> = {
  basic: { firepower: 50, projectileSpeed: 50, fireControl: 50, mobility: 50, armor: 50, special: 50 },
  fast: { firepower: 40, projectileSpeed: 45, fireControl: 45, mobility: 80, armor: 45, special: 45 },
  power: { firepower: 75, projectileSpeed: 50, fireControl: 55, mobility: 30, armor: 45, special: 45 },
  armor: { firepower: 55, projectileSpeed: 40, fireControl: 45, mobility: 30, armor: 90, special: 40 },
}

// ============================================================
// Player progression (plan §11, §13)
// ============================================================

/**
 * Player capability grows *universally* with star level (unlike enemy
 * specialization).  At the default `maxMultiplier = 1.0` the curve is exactly
 * the plan's §11 ladder: level 0 → 50, 1 → 60, 2 → 70, 3 → 80.
 *
 * `maxMultiplier` is the configurable ceiling (plan §13 Option A/B/C): raise it
 * for hardcore/challenge modes so the player can out-scale even commanders.
 */
export interface PlayerProgressionConfig {
  /** Highest reachable star level (cap on universal growth). */
  maximumLevel: number
  /** Baseline dimension at level 0. */
  baseDim: number
  /** Dimension gain per star level. */
  perLevel: number
  /** Global multiplier on the final dimensions (player power ceiling). */
  maxMultiplier: number
}

export const PLAYER_PROGRESSION: PlayerProgressionConfig = {
  maximumLevel: 3,
  baseDim: 50,
  perLevel: 10,
  maxMultiplier: 1.0,
}

// ============================================================
// Elite commander modifier (plan §8, §10)
// ============================================================

/**
 * The dimension each kind emphasizes when promoted to elite commander.
 * A +15% boost to this dimension makes the commander feel like an *exceptional*
 * unit (plan §10) — strong specialization on top of the commander AI.
 */
export const ELITE_DIMENSION: Record<TankKind, CombatDimension> = {
  player: 'firepower',
  basic: 'armor', // balanced commander → tougher frontline
  fast: 'projectileSpeed', // fast commander → harder-to-dodge bullets
  power: 'firepower', // power commander → hits even harder
  armor: 'armor', // heavy commander → near-unkillable
}

/** Elite combat bonus applied to the chosen dimension (plan §8: "+15%"). */
export const ELITE_BONUS = 0.15

// ============================================================
// Derivation helpers
// ============================================================

function clamp(v: number, min: number, max: number): number {
  return v < min ? min : v > max ? max : v
}

/** Player profile for a given star level (universal growth, capped & scaled). */
export function playerProfile(level: number): CombatProfile {
  const { baseDim, perLevel, maximumLevel, maxMultiplier } = PLAYER_PROGRESSION
  const L = clamp(Math.round(level), 0, maximumLevel)
  const dim = Math.min(100, Math.round((baseDim + L * perLevel) * maxMultiplier))
  return {
    firepower: dim,
    projectileSpeed: dim,
    fireControl: dim,
    mobility: dim,
    armor: dim,
    special: dim,
  }
}

/** Resolve the effective profile for a tank kind at an optional star level. */
export function resolveProfile(kind: TankKind, level = 0): CombatProfile {
  if (kind === 'player') return playerProfile(level)
  return TANK_PROFILES[kind]
}

/** Return a NEW profile with the kind's elite dimension boosted by ELITE_BONUS.
 *  Never mutates the shared base profile (safe for shallow clone / multiple tanks). */
export function applyEliteModifier(profile: CombatProfile, kind: TankKind): CombatProfile {
  const dim = ELITE_DIMENSION[kind]
  const next: CombatProfile = { ...profile }
  next[dim] = Math.min(100, Math.round(profile[dim] * (1 + ELITE_BONUS)))
  return next
}

/** Sum of all six dimensions — used to verify budgets (plan §17 balance). */
export function totalBudget(profile: CombatProfile): number {
  return (
    profile.firepower +
    profile.projectileSpeed +
    profile.fireControl +
    profile.mobility +
    profile.armor +
    profile.special
  )
}

/**
 * Map an abstract 0..100 capability profile onto concrete gameplay stats.
 * The mappings are deliberately centralized & linear so they are trivial to
 * tune; they preserve the relative ordering the plan assigns each role
 * (fast = fastest, heavy = toughest, power = hardest-hitting).
 */
export function profileToStats(profile: CombatProfile): TankStats {
  // ── Speed ratio (fixes "fast enemy outruns its own bullet") ───────────────
  // Both numbers are px/tick (the sim is fixed at 60 Hz). The hard invariant:
  // EVERY bullet must travel clearly faster than the tank that fired it, and
  // than the fastest tank on the field. We anchor the two ranges to disjoint
  // bands so this can never invert.
  //
  // 2026-07-22 tuning: all tank speeds were cut 40% (SPEED_SCALE) and bullet
  // speeds cut by the SAME proportion (BULLET_SPEED_SCALE) so the relative
  // race holds at the slower scale:
  //   tank speed   : 0.9 – 2.1 px/tick   (mobility 30 → 100)   [was 1.5 – 3.5]
  //   bullet speed : 3.6 – 6.0 px/tick   (projectileSpeed 40 → 100) [was 6–10]
  // Even the fastest tank (mobility 100 → 2.1) is outrun ~1.7× by the weakest
  // bullet (3.6); a fast enemy (mobility 80 → 1.76) is outrun ~2.2× by its own
  // bullet (projectileSpeed 45 → 3.8). Bullets always win the race.
  // The player's per-star "speed buff" is proportional: each star lifts
  // mobility +10 → speed +~0.17 px/tick and bullet speed +~0.24 px/tick.
  const speed = clamp((1.5 + ((profile.mobility - 30) * 2) / 70) * SPEED_SCALE, 1.5 * SPEED_SCALE, 3.5 * SPEED_SCALE)
  const bulletSpeed = clamp((6 + (profile.projectileSpeed - 40) * 0.05) * BULLET_SPEED_SCALE, 6 * BULLET_SPEED_SCALE, 10 * BULLET_SPEED_SCALE)
  // armor 45→1, 50→1, 70→3, 90→4, 100→5
  const maxHp = clamp(Math.round((profile.armor - 35) / 13), 1, 8)
  // Steel is only destroyed by bulletPower 2. We set the firepower threshold so
  // that the DEFAULT power tank (firepower 75) CANNOT pierce steel; only an
  // ELITE power tank reaches it — its +15% firepower boost lifts 75 → 86 — and
  // the max-level player (firepower 80) does too, matching classic Battle City.
  const bulletPower = profile.firepower >= STEEL_PIERCE_FIREPOWER ? 2 : 1
  // fireControl 45→440ms, 50→420, 80→300
  const fireCooldown = clamp(Math.round(620 - profile.fireControl * 4), 220, 800)
  return { speed, bulletSpeed, bulletPower, maxHp, fireCooldown }
}

/** Global tank-speed multiplier — 0.6 = −40% (user tuning, 2026-07-22). */
const SPEED_SCALE = 0.6

/** Global bullet-speed multiplier — same proportion as tanks so the
 *  bullet-always-faster invariant holds at the slower scale. */
const BULLET_SPEED_SCALE = 0.6

/** Minimum firepower for a bullet to destroy steel (bulletPower 2).
 *  Tuned so default power (75) cannot pierce steel; only elite power (~86)
 *  and max-level player (80) can. */
export const STEEL_PIERCE_FIREPOWER = 80


// ============================================================
// AI capability bias (plan §14) — combat attributes steer decisions
// ============================================================

export interface CapabilityBias {
  /** Mobility advantage → cheaper movement / stronger flanking press. */
  flank: number
  /** Armor advantage → lower risk, more aggressive push. */
  push: number
  /** Firepower advantage → higher attack score. */
  attack: number
}

export const NEUTRAL_BIAS: CapabilityBias = { flank: 0, push: 0, attack: 0 }

/**
 * Convert a profile into decision-weight biases in roughly [-1, +1].
 * 50 (baseline) → 0; 80 (fast mobility) → +0.6 flank; 90 (heavy armor) → +0.8
 * push; 75 (power firepower) → +0.5 attack.  The AI adds these to its goal
 * scores so every tank "plays to its strengths" without bespoke scripts.
 */
export function capabilityBias(profile: CombatProfile): CapabilityBias {
  return {
    flank: (profile.mobility - 50) / 50,
    push: (profile.armor - 50) / 50,
    attack: (profile.firepower - 50) / 50,
  }
}
