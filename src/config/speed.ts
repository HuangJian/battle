import { CELL } from '../constants'
import type { RNG } from '../utils/RNG'
import type { TankKind } from '../types'

/**
 * Movement speed design — data-driven, NOT derived from the mobility profile.
 *
 * Design note — why a per-kind table and not the CombatProfile mobility axis:
 * the spec anchors *absolute* speeds per role, and two archetypes (power &
 * armor) share the SAME mobility (30) yet must move at DIFFERENT speeds
 * (2.375 vs 2.125 cells/s). Speed therefore cannot be a pure function of one
 * capability dimension; it is authored directly as data (AGENTS.md §2.4 —
 * "tanks are config"). The Combat Capability System still owns every OTHER
 * stat (HP / bullet / fire cadence); this table only fixes the movement
 * baseline.
 *
 * All speeds are in CELLS PER SECOND on normal terrain. The simulation runs at
 * a fixed 60 Hz with CELL = 16 px, so we convert to the engine's px/tick unit
 * via `cpsToPxPerTick`.
 */

/** Reference baseline: a balanced (basic) enemy moves at 2.5 cells/s. */
export const BALANCED_ENEMY_CPS = 2.5

/**
 * Base movement speed on normal terrain, in cells/second, per tank kind.
 * Each value is exactly the spec multiplier applied to BALANCED_ENEMY_CPS:
 *   basic  = 1.00 ×  → 2.500   (balanced enemy — the reference)
 *   fast   = 1.20 ×  → 3.000
 *   power  = 0.95 ×  → 2.375
 *   armor  = 0.85 ×  → 2.125
 *   player = 1.05 ×  → 2.625   (no star; player scales up with stars below)
 */
export const BASE_SPEED_CPS: Record<TankKind, number> = {
  basic: BALANCED_ENEMY_CPS,
  fast: BALANCED_ENEMY_CPS * 1.2,
  power: BALANCED_ENEMY_CPS * 0.95,
  armor: BALANCED_ENEMY_CPS * 0.85,
  player: BALANCED_ENEMY_CPS * 1.05,
}

/**
 * Player universal-growth speed bonus: each star adds this many cells/sec on
 * top of the level-0 base. ≈ +5% of the balanced baseline per star, so a
 * max-level (3★) player reaches 2.625 + 3 × 0.125 = 3.0 cells/s — matching the
 * fastest enemy. Keeps the "every star makes you a bit faster" feel without
 * violating the spec (which only fixes the no-star speed).
 */
export const PLAYER_SPEED_PER_STAR_CPS = 0.125

/** Per-instance speed jitter band: actual = base × random(0.95, 1.05). */
export const SPEED_JITTER_MIN = 0.95
export const SPEED_JITTER_MAX = 1.05

/** Convert cells/second into the simulation's px/tick unit (60 Hz, CELL px/cell). */
export function cpsToPxPerTick(cps: number): number {
  return (cps * CELL) / 60
}

/**
 * Base (no jitter) speed in px/tick for a kind, with player star scaling.
 * Enemies ignore `level`; only the player grows with stars.
 */
export function baseSpeedPxPerTick(kind: TankKind, level = 0): number {
  const cps =
    kind === 'player'
      ? BASE_SPEED_CPS.player + level * PLAYER_SPEED_PER_STAR_CPS
      : BASE_SPEED_CPS[kind]
  return cpsToPxPerTick(cps)
}

/**
 * Draw a deterministic jitter multiplier in [SPEED_JITTER_MIN, SPEED_JITTER_MAX).
 * Uses the world RNG so per-tank variation is reproducible (same seed ⇒ same
 * speed) and never breaks replay / snapshot determinism (AGENTS.md §2.3).
 */
export function rollSpeedJitter(rng: RNG): number {
  return SPEED_JITTER_MIN + rng.next() * (SPEED_JITTER_MAX - SPEED_JITTER_MIN)
}

/**
 * Final per-instance spawn speed (px/tick) = base × jitter. The jitter is what
 * stops identical archetypes from moving in perfect lockstep while keeping the
 * distribution tight (±5%).
 */
export function spawnSpeedPxPerTick(kind: TankKind, level: number, rng: RNG): number {
  return baseSpeedPxPerTick(kind, level) * rollSpeedJitter(rng)
}

// ============================================================
// Bullet speed design — anchored to the BALANCED_ENEMY movement speed.
//
// Design note — why a per-kind table and not a CombatProfile dimension:
// the spec fixes bullet speed relative to the *balanced enemy's movement*
// speed (×4) with a per-kind multiplier. Two archetypes that share the same
// `projectileSpeed` capability (basic 50 and player 0★ 50) must still fire at
// different bullet speeds (×1.00 vs ×1.05 of the balanced bullet), so bullet
// speed cannot be a pure function of one capability axis. It is therefore
// authored directly as data — exactly like BASE_SPEED_CPS for movement — and
// mirrors that table's shape (AGENTS.md §2.4: "tanks are config"). Every other
// combat stat (HP / fire cadence / steel-pierce) is still derived from the
// profile in `config/combat.ts`; this table only fixes the projectile baseline.
//
// The `projectileSpeed` capability dimension is retained on CombatProfile for
// AI/extensibility symmetry but is intentionally NOT used to derive bullet
// speed anymore — the per-kind table below is the single source of truth.
// ============================================================

/**
 * Every bullet is at least this many times faster than the tank that fired it.
 * Anchored to the balanced (basic) enemy's movement speed, so the relationship
 * "bullet = 4 × what the shooter can move" holds for every kind (and beats the
 * fastest tank on the field by a wide margin).
 */
export const BULLET_SPEED_RATIO = 4

/**
 * Per-kind multiplier on the balanced-enemy bullet speed (the spec's ratios):
 *   basic  = 1.00 ×  → the reference (均衡敌人)
 *   fast   = 1.05 ×  → 快速敌人
 *   power  = 0.95 ×  → 强力敌人
 *   armor  = 0.90 ×  → 重甲敌人
 *   player = 1.05 ×  → 无星星玩家 (no-star player; scales up with stars below)
 */
export const BULLET_SPEED_MULT: Record<TankKind, number> = {
  basic: 1.0,
  fast: 1.05,
  power: 0.95,
  armor: 0.9,
  player: 1.05,
}

/**
 * Base (no jitter) bullet speed in cells/second, per kind. Carrying the ×4
 * anchor explicitly so the value is self-documenting:
 *   basic  = 2.5 × 4 × 1.00 = 10.00 cps
 *   fast   = 2.5 × 4 × 1.05 = 10.50 cps
 *   power  = 2.5 × 4 × 0.95 =  9.50 cps
 *   armor  = 2.5 × 4 × 0.90 =  9.00 cps
 *   player = 2.5 × 4 × 1.05 = 10.50 cps (no star)
 */
export const BASE_BULLET_SPEED_CPS: Record<TankKind, number> = {
  basic: BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT.basic,
  fast: BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT.fast,
  power: BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT.power,
  armor: BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT.armor,
  player: BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT.player,
}

/**
 * Player universal-growth bullet bonus: each star adds this many cells/sec on
 * top of the level-0 base (parallel to PLAYER_SPEED_PER_STAR_CPS for movement).
 * A max-level (3★) player reaches 10.5 + 3 × 0.5 = 12.0 cps — clearly faster
 * than the no-star value and every enemy bullet, while the no-star value stays
 * exactly the spec's 1.05 × balanced bullet.
 */
export const PLAYER_BULLET_SPEED_PER_STAR_CPS = 0.5

/**
 * Base (no jitter) bullet speed in px/tick for a kind, with player star scaling.
 * Enemies ignore `level`; only the player grows with stars. This is the
 * canonical per-kind bullet-speed lookup used by `profileToStats`.
 */
export function baseBulletSpeedPxPerTick(kind: TankKind, level = 0): number {
  const cps =
    kind === 'player'
      ? BASE_BULLET_SPEED_CPS.player + level * PLAYER_BULLET_SPEED_PER_STAR_CPS
      : BASE_BULLET_SPEED_CPS[kind]
  return cpsToPxPerTick(cps)
}

/**
 * Deterministic per-bullet jitter multiplier in [SPEED_JITTER_MIN,
 * SPEED_JITTER_MAX) — i.e. random(0.95, 1.05) per the spec. It is derived from
 * a pure hash of the firing tank's `id` and the world `frame`, NOT drawn from
 * the world RNG.
 *
 * Why a state hash instead of world.rng? Bullets are fired far more often than
 * tanks spawn, so drawing jitter from world.rng at every shot would interleave
 * thousands of draws into the AI's decision stream and silently alter enemy
 * behaviour — an unintended coupling between a cosmetic speed variation and
 * gameplay. A hash of (id, frame) is equally reproducible: both `id` and
 * `frame` live on the World and are captured by snapshots, so replay/snapshot
 * determinism (AGENTS.md §2.3) is fully preserved, while the AI's RNG stream
 * stays untouched. Same seed + same inputs ⇒ identical jitter every time.
 */
export function bulletSpeedJitter(id: number, frame: number): number {
  let h = (Math.imul(id, 0x9e3779b1) ^ Math.imul(frame, 0x85ebca77)) >>> 0
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b)
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35)
  h ^= h >>> 16
  const u = (h >>> 0) / 4294967296
  return SPEED_JITTER_MIN + u * (SPEED_JITTER_MAX - SPEED_JITTER_MIN)
}

/**
 * Final per-instance bullet speed (px/tick) = base × jitter. Each fired bullet
 * gets its own jitter from (tank id, world frame), so even two shells from the
 * same tank within a volley travel at slightly different speeds (±5%), while
 * the distribution stays tight and is reproducible from the world state.
 */
export function spawnBulletSpeedPxPerTick(
  kind: TankKind,
  level: number,
  id: number,
  frame: number,
): number {
  return baseBulletSpeedPxPerTick(kind, level) * bulletSpeedJitter(id, frame)
}
