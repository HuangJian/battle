import type { TankKind } from '../types'
import type { CombatProfile, CombatDimension, TankStats } from '../types'
import { baseSpeedPxPerTick, baseBulletSpeedPxPerTick } from './speed'
import { baseFireIntervalMs } from './fire-rate'

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
// Firepower / HP scales — the Combat Capability System maps the abstract
// 0..100 `firepower` / `armor` dimensions onto concrete per-shot damage and
// maximum HP via these linear scales. They are chosen so the four archetypes
// reproduce the user-specified hits-to-kill matrix exactly (see
// tests/combat-power-hp.test.ts):
//
//   damage   = round(firepower * DAMAGE_SCALE)          (enemies)
//   maxHp    = round(armor    * HP_SCALE)              (enemies)
//
// with basic = firepower 50 / armor 50  →  damage 100 / HP 250, which gives
// the reference "3 shots to kill a peer" cell (250/100 = 2.5 → 3).
// ============================================================

/** Multiplier from the `firepower` capability (0..100) to per-shot damage. */
export const DAMAGE_SCALE = 2

/** Multiplier from the `armor` capability (0..100) to maximum HP. */
export const HP_SCALE = 5

/**
 * The no-star player's firepower and HP are each the balanced (basic) enemy's
 * value × 1.05 (user spec: 无星星玩家 = 均衡敌人 × 105%). Player stats then
 * grow universally with star level on top of this baseline bonus.
 */
export const PLAYER_FIREPOWER_MULT = 1.05
export const PLAYER_HP_MULT = 1.05

/**
 * Steel (the indestructible-by-default terrain) can ONLY be destroyed by the
 * player, and only once the player reaches this star level (classic Battle
 * City: the top-tier tank breaks steel; enemies never do). This deliberately
 * keeps elite power from breaking steel. Power's firepower was lowered from
 * 80 to 64 (2026-07-26) because its specialty shifted to firing frequency;
 * power stays the highest-damage enemy per the hits matrix, and steel-pierce
 * is decoupled from raw firepower magnitude entirely.
 */
export const STEEL_PIERCE_PLAYER_LEVEL = 3

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
 *   power:            high firepower + fast bullets + fast fire rate, low mobility / armor.
 *   heavy  (armor):   high armor, low mobility.
 */
export const TANK_PROFILES: Record<Exclude<TankKind, 'player'>, CombatProfile> = {
  basic: {
    // The reference archetype: every dimension at the 50 baseline. Its
    // firepower (50) and armor (50) are the anchors for the player's
    // no-star stats (player = basic × 1.05, see PLAYER_*_MULT).
    firepower: 50,
    projectileSpeed: 50,
    fireControl: 50,
    mobility: 50,
    armor: 50,
    special: 50,
  },
  fast: {
    // Weakest gun (firepower 36), lowest durability (armor 30), highest
    // mobility (80). Glass cannon that zips around but melts fast.
    firepower: 36,
    projectileSpeed: 45,
    fireControl: 45,
    mobility: 80,
    armor: 30,
    special: 64,
  },
  power: {
    // Strongest gun (firepower 64 — the "power" role) and a fast bullet, but
    // slightly low durability (armor 40). Its identity is now BOTH a high
    // fire RATE (1.10× frequency, config/fire-rate.ts) AND the highest
    // per-shot damage — a glass cannon. Firepower was lowered from 80 to 64
    // (2026-07-26) because the specialty shifted to firing frequency: with
    // 1.10× fire rate AND the old damage 160, power was too dominant (it
    // one-shot the fast tank and elite power reached damage 184). At damage
    // 128 power still kills most enemies in 2 hits but can no longer one-shot
    // any archetype — and elite power (firepower 74, damage 148) also cannot
    // one-shot the frailest fast (HP 150). Steel-piercing is intentionally
    // withheld from every enemy (see STEEL_PIERCE_PLAYER_LEVEL): elite power
    // may hit harder but still cannot destroy steel, which stays a player-only
    // privilege (classic Battle City). The +20 projectileSpeed (funded by
    // dropping `special`) keeps the bullet noticeably faster without touching
    // firepower.
    firepower: 64,
    projectileSpeed: 70,
    fireControl: 50,
    mobility: 30,
    armor: 40,
    special: 46,
  },
  armor: {
    // Highest durability (armor 70), slightly-below-average gun (firepower
    // 43), sluggish (mobility 30). A wall that wears you down.
    firepower: 43,
    projectileSpeed: 40,
    fireControl: 45,
    mobility: 30,
    armor: 70,
    special: 72,
  },
}

// ============================================================
// Player progression (plan §11, §13)
// ============================================================

/**
 * Player capability grows *universally* with star level (unlike enemy
 * specialization).  The growth is now *decaying*: every star adds `gainFull`
 * (the spec's initial "+10%") to all six dimensions while the dimension is
 * below the decay threshold — balanced-enemy firepower × `thresholdMult`
 * (= 50 × 1.5 = 75) — and `gainDecay` (the spec's "+2%") once that threshold
 * is crossed.  Classic mode additionally caps the *level* at `maximumLevel`
 * (enforced at pickup time in Simulation); every other mode lets the level
 * accumulate WITHOUT bound, so the dimension keeps creeping up (and saturates
 * at 100) long after the player has far out-scaled the balanced enemy.
 *
 * `maxMultiplier` is the configurable ceiling (plan §13 Option A/B/C): raise it
 * for hardcore/challenge modes so the player can out-scale even commanders.
 */
export interface PlayerProgressionConfig {
  /** Classic-mode hard cap on star *level*. Other modes accumulate unbounded
   *  (the dimension still saturates at 100 in playerProfile). */
  maximumLevel: number
  /** Baseline dimension (all six capabilities) at level 0. */
  baseDim: number
  /** Per-star dimension gain while below the decay threshold (initial boost).
   *  On the 0..100 scale this is the spec's "initial +10%". */
  gainFull: number
  /** Per-star dimension gain once the decay threshold is crossed (decayed
   *  boost — the spec's "+2%"). */
  gainDecay: number
  /** Decay threshold = balanced-enemy firepower × this multiplier. Once the
   *  player's dimension exceeds it, the per-star gain drops to `gainDecay`. */
  thresholdMult: number
  /** Global multiplier on the final dimensions (player power ceiling). */
  maxMultiplier: number
}

export const PLAYER_PROGRESSION: PlayerProgressionConfig = {
  maximumLevel: 3,
  baseDim: 50,
  gainFull: 10,
  gainDecay: 2,
  thresholdMult: 1.5,
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

/**
 * Player profile for a given star level (universal growth with decay).
 *
 * Classic mode caps the *level* at `maximumLevel` (enforced at pickup time in
 * Simulation), so this function only ever sees levels ≤ 3 there and the decay
 * branch below is inert for classic — the curve is exactly the plan §11 ladder
 * (level 0 → 50 … 3 → 80). Other modes let the level grow WITHOUT bound; the
 * per-star dimension gain is `gainFull` while the dimension is below the decay
 * threshold (balanced-enemy firepower × `thresholdMult`), then `gainDecay`
 * afterwards — so early stars hit hard (+10%) and later stars taper (+2%),
 * matching the spec. The dimension itself is hard-clamped to 100.
 */
export function playerProfile(level: number): CombatProfile {
  const { baseDim, gainFull, gainDecay, thresholdMult, maxMultiplier } = PLAYER_PROGRESSION
  const L = Math.max(0, Math.round(level))
  // Threshold in dimension units, data-driven off the balanced enemy's
  // firepower (the "均衡敌人 × 150%" the spec references).
  const threshold = TANK_PROFILES.basic.firepower * thresholdMult
  // How many of the first stars still receive the full gain before the
  // threshold is crossed. ceil() so the star that *pushes* past the threshold
  // keeps the full gain; decay applies to the following star.
  const fullStars = Math.max(0, Math.ceil((threshold - baseDim) / gainFull))
  const dim = Math.min(
    100,
    Math.round(
      (baseDim + gainFull * Math.min(L, fullStars) + gainDecay * Math.max(0, L - fullStars)) *
        maxMultiplier,
    ),
  )
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
  // ── Firepower → per-shot damage ─────────────────────────────────────────
  // The "火力强度值" (firepower strength) the user spec talks about is this
  // concrete per-shot damage. Linear in the `firepower` capability:
  //   damage = round(firepower * DAMAGE_SCALE)   (×1.05 for the no-star player)
  // basic → 50×2 = 100 (reference); the player at level 0 → 105.
  const isPlayer = kind === 'player'
  const effFirepower = profile.firepower * (isPlayer ? PLAYER_FIREPOWER_MULT : 1)
  const damage = Math.round(effFirepower * DAMAGE_SCALE)
  // ── Armor → max HP ──────────────────────────────────────────────────────
  // The "HP 值" is linear in the `armor` capability:
  //   maxHp = round(armor * HP_SCALE)   (×1.05 for the no-star player)
  // basic → 50×5 = 250 (reference: a peer kills it in 3 shots); player L0 → 263.
  const effArmor = profile.armor * (isPlayer ? PLAYER_HP_MULT : 1)
  const maxHp = Math.round(effArmor * HP_SCALE)
  // ── Steel-pierce: player-only, level-gated ─────────────────────────────
  // Steel is destroyed solely by the player at/above STEEL_PIERCE_PLAYER_LEVEL.
  // Enemies — including ELITE power — NEVER pierce steel, regardless of how
  // high their firepower climbs. `bulletPower` keeps its 1/2 meaning (2 = can
  // destroy steel) for the terrain code; `canPierceSteel` is the canonical flag.
  const canPierceSteel = isPlayer && level >= STEEL_PIERCE_PLAYER_LEVEL
  const bulletPower = canPierceSteel ? 2 : 1
  // Fire cadence is now driven by the fire-rate standard (config/fire-rate.ts),
  // NOT the `fireControl` capability: the balanced (basic) enemy's interval is
  // pinned by the "3 bullets on the vertical route" constraint, and every other
  // kind's interval is that baseline divided by its firing-frequency multiplier
  // (so a higher multiplier ⇒ shorter interval ⇒ fires more often). This makes
  // fire rate a single, testable, data-driven standard instead of a per-profile
  // formula. Note: this intentionally supersedes the old 620 − fireControl×4
  // mapping (and the 2026-07-23 "player never out-fired" fairness invariant —
  // see DECISIONS.md: the new spec lets the power enemy out-rate the no-star
  // player, which is the explicit user design).
  const fireCooldown = Math.round(baseFireIntervalMs(kind ?? 'basic', level))
  return { speed, bulletSpeed, bulletPower, maxHp, fireCooldown, damage, canPierceSteel }
}

/** Global bullet-speed scale removed (2026-07-26): bullet speed is now a
 *  per-kind data table anchored to the balanced-enemy movement speed × 4 in
 *  config/speed.ts (BASE_BULLET_SPEED_CPS), per the bullet-speed design spec.
 *  The `projectileSpeed` capability dimension is retained on CombatProfile for
 *  AI/extensibility symmetry but no longer drives bullet speed. */

// NOTE: the old `STEEL_PIERCE_FIREPOWER` threshold is gone. Steel-pierce is
// now player-only and level-gated (STEEL_PIERCE_PLAYER_LEVEL); no enemy —
// elite or not — can destroy steel regardless of firepower.

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
 * push; 64 (power firepower) → +0.28 attack.  The AI adds these to its goal
 * scores so every tank "plays to its strengths" without bespoke scripts.
 */
export function capabilityBias(profile: CombatProfile): CapabilityBias {
  return {
    flank: (profile.mobility - 50) / 50,
    push: (profile.armor - 50) / 50,
    attack: (profile.firepower - 50) / 50,
  }
}
