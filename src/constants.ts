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

/** Tank fire cooldown per level (ms) */
export const FIRE_COOLDOWN: Record<number, number> = {
  0: 400, // level 0
  1: 350,
  2: 300,
  3: 250,
}

/** Player tank speed per level (px/tick) */
export const PLAYER_SPEED: Record<number, number> = {
  0: 2,
  1: 2,
  2: 2,
  3: 3,
}

/** Bullet speed per source (px/tick) */
export const BULLET_SPEED = {
  player: 4,
  basic: 2,
  fast: 4,
  power: 3,
  armor: 3,
}
