/** Clamp value to range */
export function clamp(v: number, min: number, max: number): number {
  return v < min ? min : v > max ? max : v
}

/** Snap value to nearest multiple of `grid` */
export function snap(v: number, grid: number): number {
  return Math.round(v / grid) * grid
}

/** AABB overlap test */
export function aabb(
  ax: number,
  ay: number,
  aw: number,
  ah: number,
  bx: number,
  by: number,
  bw: number,
  bh: number,
): boolean {
  return ax < bx + bw && ax + aw > bx && ay < by + bh && ay + ah > by
}

/**
 * Player-2 spawn column: mirror of player-1 across the field center (col 12).
 * Nudges one cell left when P1 is already at col 12, which would otherwise
 * place P2 on the same column and overlap P1's spawn. Used by co-op and
 * dual-supervise setups so the two tanks never share a cell.
 */
export function computePlayer2SpawnCol(p1Col: number): number {
  const col = 24 - p1Col
  return col === p1Col ? p1Col - 1 : col
}

// Direction helpers moved to utils/direction.ts (§2.8) — kept as
// compatibility aliases because protected files (ai/god/think.ts,
// AGENTS §5.1) still import from here. New code: use utils/direction.
export { opposite, turnCW, turnCCW, moveDir, ALL_DIRS } from './direction'

/** Random integer in [min, max] inclusive */
export function randInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}
