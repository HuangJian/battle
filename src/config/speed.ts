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
