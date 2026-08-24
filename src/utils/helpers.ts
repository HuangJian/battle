import type { Direction } from './direction'

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

// Direction helpers live in utils/direction.ts (§2.8). The compatibility
// re-export that used to live here was removed when the God-AI protected-file
// rule was abolished (§262) — import direction symbols from utils/direction.

/**
 * Manhattan distance between two axis-aligned points. Unit-agnostic — both
 * the cell-space scoring sites (god AI target selection) and the pixel-space
 * perception sites pass their own coordinates. Single canonical implementation
 * (遗留 #2): perception.ts re-exports it; the god layer's ~100 inline
 * `Math.abs(dx) + Math.abs(dy)` spellings delegate here after the measured
 * A/B showed call cost inside noise (DECISIONS §266). Deliberate raw keeps:
 * delta-form sites whose |dx|/|dy| are reused downstream (Hunt axis test,
 * DefenseIntercept direction pick) and non-point-distance sums
 * (Navigator glideSpeed = |vx|+|vy|).
 */
export function manhattan(ax: number, ay: number, bx: number, by: number): number {
  return Math.abs(ax - bx) + Math.abs(ay - by)
}

/** Sentinel returned by {@link bulletLaneDist} / {@link bulletInFrontDist} when
 * the bullet is off-lane or fails the directional test. */
export const BULLET_LANE_MISS = -1

/** Shared axis-alignment check: bullet center within `thresholdPx` of the
 * row/column through (tx,ty), measured on the axis perpendicular to `dir`. */
function laneAligned(
  dir: Direction,
  bcx: number,
  bcy: number,
  tx: number,
  ty: number,
  thresholdPx: number,
): boolean {
  const vertical = dir === 'up' || dir === 'down'
  return vertical ? Math.abs(bcx - tx) < thresholdPx : Math.abs(bcy - ty) < thresholdPx
}

/**
 * Shared bullet-lane predicate (refactor.zcode.md §3.1, replaces 11 inline
 * `vertical/aligned/approaching` chains): a bullet is a lane threat to the
 * point (tx,ty) when it travels on that row/column within `thresholdPx` of
 * the axis AND its direction closes on the point.
 *
 * Returns the along-axis distance from bullet center to the point (≥ 0) when
 * both conditions hold, else {@link BULLET_LANE_MISS}. Monomorphic numbers
 * only — no allocation (AGENTS §14.2). Allegiance-agnostic: callers filter
 * bullets before calling.
 */
export function bulletLaneDist(
  dir: Direction,
  bcx: number,
  bcy: number,
  tx: number,
  ty: number,
  thresholdPx: number,
): number {
  if (!laneAligned(dir, bcx, bcy, tx, ty, thresholdPx)) return BULLET_LANE_MISS
  const approaching =
    (dir === 'down' && bcy < ty) ||
    (dir === 'up' && bcy > ty) ||
    (dir === 'right' && bcx < tx) ||
    (dir === 'left' && bcx > tx)
  if (!approaching) return BULLET_LANE_MISS
  const vertical = dir === 'up' || dir === 'down'
  return vertical ? Math.abs(bcy - ty) : Math.abs(bcx - tx)
}

/**
 * Positional twin of {@link bulletLaneDist} for STATIC facing: "is the bullet
 * in front of a shooter facing `dir`, on the shooter's lane?" Here `dir` is
 * the SHOOTER's orientation (not a travel direction), so the half-plane
 * polarity is inverted relative to {@link bulletLaneDist}'s approaching test.
 * Used by the T2a aim-error gate (FireControl).
 */
export function bulletInFrontDist(
  dir: Direction,
  bcx: number,
  bcy: number,
  tx: number,
  ty: number,
  thresholdPx: number,
): number {
  if (!laneAligned(dir, bcx, bcy, tx, ty, thresholdPx)) return BULLET_LANE_MISS
  const inFront =
    (dir === 'up' && bcy < ty) ||
    (dir === 'down' && bcy > ty) ||
    (dir === 'left' && bcx < tx) ||
    (dir === 'right' && bcx > tx)
  if (!inFront) return BULLET_LANE_MISS
  const vertical = dir === 'up' || dir === 'down'
  return vertical ? Math.abs(bcy - ty) : Math.abs(bcx - tx)
}
