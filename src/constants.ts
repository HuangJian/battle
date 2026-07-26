// ============================================================
// Game Constants
// ============================================================

/** Size of one sub-block in pixels (smallest terrain unit) */
export const CELL = 16

/** Number of sub-blocks per map side (26×26 grid) */
export const GRID = 26

/** Playfield pixel size = GRID × CELL */
export const FIELD = GRID * CELL // 416

/** Tank pixel size (2×2 cells) */
export const TANK = CELL * 2 // 32

/** Bullet pixel size */
export const BULLET = 6

/** Movement alignment grid (tanks snap to multiples of this when turning) */
export const ALIGN = CELL // 16

/** Fixed timestep for simulation (ms) */
export const TICK_MS = 1000 / 60

/**
 * Render-rate cap (frames/sec). Decoupled from the simulation: the sim always
 * ticks at 60Hz for responsiveness/determinism, but the *canvas repaint* can be
 * throttled to cut GPU load and power draw.
 *   0 = uncapped (paint every animation frame — default, preserves 60fps feel)
 *   e.g. 30 = halve GPU work during action (battery / low-power mode)
 * On-demand skip (PresentationLayer.shouldRender) already eliminates repaints
 * during idle/menu/pause regardless of this value.
 */
/** Render FPS cap applied while Performance Mode is ON (cuts GPU load ~half).
 *  0 would mean uncapped; the game uses this value only in perf mode. */
export const PERF_MODE_RENDER_FPS = 30

/** Max enemies alive at once */
export const MAX_ENEMIES_ALIVE = 4

/** Total enemies per stage */
export const ENEMIES_PER_STAGE = 20

/** Player lives at start */
export const START_LIVES = 3

/** Player respawn position (tile coords) */
export const PLAYER_SPAWN = { col: 8, row: 24 } // 4×8 grid → center-bottom

/** Base (eagle) position */
export const BASE_POS = { col: 12, row: 24 }

/** Enemy spawn positions (tile coords, 4×8 grid) */
export const ENEMY_SPAWNS = [
  { col: 0, row: 0 },
  { col: 12, row: 0 },
  { col: 6, row: 0 },
]

/** Spawn protection duration (ms) */
export const SPAWN_PROTECTION_MS = 2000

/** Power-up duration (ms) — unified to 20s for all timed power-ups */
export const POWERUP_DURATION_MS = 20000

/** Star power-up respawn invulnerability */
export const RESPAWN_SHIELD_MS = 3000

/** Power-up despawn timeout (ms) — how long a power-up stays on the field before disappearing */
export const POWERUP_TIMEOUT_MS = 20000

/** Fence power-up: number of steel tiles to place around base */
export const FENCE_STEEL_COUNT = 12

/** Boat power-up: amphibious movement duration (ms) */
export const BOAT_DURATION_MS = 20000

/**
 * After the last enemy is destroyed, if power-ups are still on the field the
 * player gets this long (ms) to drive over and collect them before the stage
 * auto-ends. Mirrors the classic "grab the bonus" grace period.
 */
export const POWERUP_PICKUP_WINDOW_MS = 10000

/**
 * Once the last remaining power-up is collected (or the pickup window expires
 * with items still unclaimed), the stage auto-ends after this grace delay (ms).
 */
export const POWERUP_PICKUP_END_DELAY_MS = 1000

/** Stage-clear transition delay (ms) when there is nothing left to collect. */
export const STAGE_CLEAR_DELAY_MS = 3000

/** Direction vectors */
export const DIR_VECTORS: Record<Direction, { dx: number; dy: number }> = {
  up: { dx: 0, dy: -1 },
  down: { dx: 0, dy: 1 },
  left: { dx: -1, dy: 0 },
  right: { dx: 1, dy: 0 },
}

export type Direction = 'up' | 'down' | 'left' | 'right'

// ================================================================
// Ice momentum (slide / glide) model — see Simulation.updateMovement.
//
// Tanks move with a per-tick velocity (vx/vy, px/tick). Each tick the velocity
// eases toward the desired velocity (= DIR_VECTORS[dir] * speed when moving,
// otherwise 0). TRACTION is the fraction of the remaining gap closed per tick:
//   1.0  = instant  → crisp, current control (normal ground, plain ice-free)
//   <1.0 = gradual  → slippery (ice)
// Acceleration (input held / turning onto a new axis) and deceleration (input
// released / still gliding) use SEPARATE coefficients so a tank ramps up
// responsively yet coasts a long way after you let go — the classic ice feel.
// These are pure simulation constants (no RNG), so determinism is preserved.
// ================================================================
/** Fraction of the velocity gap closed per tick while accelerating on ice. */
export const ICE_ACCEL_TRACTION = 0.35
/** Fraction of the velocity gap closed per tick while decelerating on ice. */
export const ICE_DECEL_TRACTION = 0.05

// ============================================================
// Tactical Intelligence Framework (AI) timing
// ============================================================

/** Default interval between tactical (5s) re-evaluations. */
export const TACTICAL_INTERVAL_MS = 5000

/** Default interval between strategic (20s) re-evaluations. */
export const STRATEGIC_INTERVAL_MS = 20000

/** Commander broadcast cadence (20s). */
export const COMMANDER_INTERVAL_MS = 20000

/** How long (ms) a committed dodge direction is held before re-evaluating. */
export const DODGE_LOCK_MS = 350

// ================================================================
// None-tier (classic) behavior branch timing
// ================================================================

/** Min ms before a None-tier tank re-rolls its wander direction. */
export const NONE_TURN_MIN_MS = 700
/** Uniform jitter (ms) added on top of NONE_TURN_MIN_MS. */
export const NONE_TURN_JITTER_MS = 900
/** Fire-cadence jitter (ms) added on top of the tank's base cooldown. */
export const NONE_FIRE_JITTER_MS = 1400

// NOTE: player & bullet speeds are no longer hardcoded here. They are derived
// from each tank's CombatProfile by `profileToStats()` in `config/combat.ts`,
// which keeps bullets strictly faster than tanks (see that file for the
// speed-ratio invariant). These old constants are intentionally removed.
