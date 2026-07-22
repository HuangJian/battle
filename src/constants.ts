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
export const MAX_RENDER_FPS = 0

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

/** Power-up duration (ms) */
export const FREEZE_DURATION_MS = 8000
export const SHIELD_DURATION_MS = 10000

/** Star power-up respawn invulnerability */
export const RESPAWN_SHIELD_MS = 3000

/** Direction vectors */
export const DIR_VECTORS: Record<Direction, { dx: number; dy: number }> = {
  up: { dx: 0, dy: -1 },
  down: { dx: 0, dy: 1 },
  left: { dx: -1, dy: 0 },
  right: { dx: 1, dy: 0 },
}

export type Direction = 'up' | 'down' | 'left' | 'right'

// ============================================================
// Tactical Intelligence Framework (AI) timing
// ============================================================

/** Default interval between tactical (5s) re-evaluations. */
export const TACTICAL_INTERVAL_MS = 5000

/** Default interval between strategic (20s) re-evaluations. */
export const STRATEGIC_INTERVAL_MS = 20000

/** Commander broadcast cadence (20s). */
export const COMMANDER_INTERVAL_MS = 20000

/** Commander election is attempted every N ticks (~1s at 60fps). */
export const ELECTION_CHECK_TICKS = 60

/** How long (ms) a committed dodge direction is held before re-evaluating. */
export const DODGE_LOCK_MS = 350

// NOTE: player & bullet speeds are no longer hardcoded here. They are derived
// from each tank's CombatProfile by `profileToStats()` in `config/combat.ts`,
// which keeps bullets strictly faster than tanks (see that file for the
// speed-ratio invariant). These old constants are intentionally removed.
