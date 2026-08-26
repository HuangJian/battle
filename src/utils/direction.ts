// ================================================================
// Direction — the single source of truth for cardinal-direction data
// and helpers (plan/refactor.agy.md §2.8).
//
// Everything direction-shaped lives here: the `Direction` union, the
// vector tables (object + flat hot-path forms), index conversion, and
// the turn/step helpers. `constants.ts` re-exports the data so existing
// imports keep working; new code should import from this module.
// ================================================================

export type Direction = 'up' | 'down' | 'left' | 'right'

/** Direction vectors */
export const DIR_VECTORS: Record<Direction, { dx: number; dy: number }> = {
  up: { dx: 0, dy: -1 },
  down: { dx: 0, dy: 1 },
  left: { dx: -1, dy: 0 },
  right: { dx: 1, dy: 0 },
}

/**
 * Flat parallel arrays for hot-path direction lookups (perf §64):
 * `DIR_VECTORS[dir]` is a string-keyed Record lookup that allocates a
 * {dx,dy} object reference and forces a dict hash probe. The flat arrays
 * let callers do `const i = dirIdx(dir); DIR_DX[i], DIR_DY[i]` — index
 * access only, no dict probe. dirIdx is a 4-way ternary.
 *
 * `dirIdx` is provided as a helper so all hot-path callers share the same
 * string→index conversion; cold-path code can keep using `DIR_VECTORS`.
 */
export const DIR_DX: readonly number[] = [0, 0, -1, 1] // up, down, left, right
export const DIR_DY: readonly number[] = [-1, 1, 0, 0]
export function dirIdx(dir: Direction): number {
  return dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3
}

/** All four directions in order */
export const ALL_DIRS: Direction[] = ['up', 'down', 'left', 'right']

/** Get opposite direction */
export function opposite(dir: Direction): Direction {
  switch (dir) {
    case 'up':
      return 'down'
    case 'down':
      return 'up'
    case 'left':
      return 'right'
    case 'right':
      return 'left'
  }
}

/** Move a position by direction vector × distance */
export function moveDir(
  x: number,
  y: number,
  dir: Direction,
  dist: number,
): { x: number; y: number } {
  const v = DIR_VECTORS[dir]
  return { x: x + v.dx * dist, y: y + v.dy * dist }
}
