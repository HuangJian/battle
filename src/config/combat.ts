import type { TankKind } from '../types'
import type { CombatProfile, CombatDimension, TankStats } from '../types'
import { baseSpeedPxPerTick, baseBulletSpeedPxPerTick } from './speed'

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
 * reference on the World (the snapshot WorldSerializer shallow-clones tanks) and to read from
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
 *   power:            high firepower + fast bullets (matches lvl-2 player), low mobility / armor.
 *   heavy  (armor):   high armor, low mobility.
 */
export const TANK_PROFILES: Record<Exclude<TankKind, 'player'>, CombatProfile> = {
  basic: {
    firepower: 50,
    projectileSpeed: 50,
    fireControl: 50,
    mobility: 50,
    armor: 50,
    special: 50,
  },
  fast: {
    firepower: 40,
    projectileSpeed: 45,
    fireControl: 45,
    mobility: 80,
    armor: 45,
    special: 45,
  },
  power: {
    firepower: 75, // high firepower (the "power" role) but < 80 → cannot pierce steel
    // Bullet speed matches the level-2 player (player eats 2 stars → every
    // dimension 70, projectileSpeed 70). This makes power shells noticeably
    // faster / harder to dodge WITHOUT raising firepower — power still can't
    // destroy steel (firepower 75 < STEEL_PIERCE_FIREPOWER 80). The +20
    // projectileSpeed is funded by dropping `special` (no stat mapping) to 30,
    // keeping the total at BASELINE_BUDGET (300). Contract asserted in
    // tests/combat.test.ts (power-bullet-speed section).
    projectileSpeed: 70,
    // Fire-rate fairness invariant: NO enemy archetype may out-fire the
    // unbuffed player (level 0, fireControl 50 → 420 ms). fireCooldown is
    // 620 − fireControl×4, so any enemy fireControl above 50 would win a
    // head-on duel purely on cadence (bullets cancel 1:1; the faster firer
    // always lands the surplus shell). power was 55 (400 ms — strictly
    // faster than the player); rebalanced to 50 so the 300 budget still holds.
    // Guarded by tests/fire-rate-duel.test.ts.
    fireControl: 50,
    mobility: 30,
    armor: 45,
    special: 30,
  },
  armor: {
    firepower: 55,
    projectileSpeed: 40,
    fireControl: 45,
    mobility: 30,
    armor: 90,
    special: 40,
  },
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
 *
 * Speed note: movement speed is NOT derived from the `mobility` dimension
 * here. Per-kind base speeds live in `config/speed.ts` (BASE_SPEED_CPS) because
 * the spec anchors absolute cells/sec values, and two archetypes (power &
 * armor) share mobility 30 yet move at different speeds. So `speed` is taken
 * from the data table whenever a `kind` is supplied; only synthetic (test)
 * profiles without a kind fall back to the legacy mobility map. Every other
 * stat (bullet speed / HP / fire cadence) is still derived from the profile.
 */
export function profileToStats(profile: CombatProfile, kind?: TankKind, level = 0): TankStats {
  // ── Speed (per-kind base, see config/speed.ts) ──────────────────────────
  // The hard invariant: EVERY bullet must travel clearly faster than the tank
  // that fired it, and than the fastest tank on the field. The base speeds top
  // out at 3.0 cells/s = 0.8 px/tick (fast enemy / max-level player), while the
  // slowest bullet is 2.52 px/tick — a ~3.15× margin in the worst case. So the
  // race can never invert regardless of kind or jitter.
  const speed = kind
    ? baseSpeedPxPerTick(kind, level)
    : clamp(1.5 + ((profile.mobility - 30) * 2) / 70, 1.5, 3.5)
  // Bullet speed is a per-kind data table anchored to the balanced-enemy
  // movement speed × BULLET_SPEED_RATIO (see config/speed.ts), NOT derived from
  // the projectileSpeed capability — basic & player share projectileSpeed 50 yet
  // fire at different bullet speeds, so the table is the single source of truth.
  // Synthetic (test) profiles without a kind fall back to the balanced enemy.
  const bulletSpeed = baseBulletSpeedPxPerTick(kind ?? 'basic', level)
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

/** Global bullet-speed scale removed (2026-07-26): bullet speed is now a
 *  per-kind data table anchored to the balanced-enemy movement speed × 4 in
 *  config/speed.ts (BASE_BULLET_SPEED_CPS), per the bullet-speed design spec.
 *  The `projectileSpeed` capability dimension is retained on CombatProfile for
 *  AI/extensibility symmetry but no longer drives bullet speed. */

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
