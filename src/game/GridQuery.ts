import { FIELD, TANK } from '../constants'
import { clamp } from '../utils/helpers'

// ================================================================
// GridQuery — shared "find a free cell" primitives
//
// Unifies the nearest-free-32px-cell scan that was copy-pasted between
// World.findFreeSpawnCell and SimulationPowerUps.findFreeDropCell
// (plan/refactor.agy.md §2.3). Callers inject their own freeness
// predicate because the two differ deliberately:
//   - spawns need terrain-clear AND no tank overlap;
//   - item drops need terrain-clear AND off the spawn points (tanks
//     are allowed — an item may land next to / under a tank).
// SimulationPlayer.decoySpawnCell intentionally stays local: it is a
// ring-limited scan (±3 cells) with a non-orthogonal-preference tier
// and a null fallback — a different contract, not a copy.
// ================================================================

/** Predicate: a tank-sized footprint at (x, y) is acceptable. */
export type CellFreePredicate = (x: number, y: number) => boolean

/**
 * Nearest free cell to (originX, originY) on the 32px (TANK-aligned) grid,
 * scanning the whole field in fixed row-major order — deterministic, no RNG
 * (AGENTS §2.3). If the origin itself passes `free` it is returned directly;
 * if no cell on the field qualifies, the clamped origin is returned as a
 * best-effort fallback (matches both historical call sites).
 */
export function findNearestFreeCell(
  originX: number,
  originY: number,
  free: CellFreePredicate,
): { x: number; y: number } {
  const step = TANK // tanks are 2×2 tiles ⇒ candidates live on the 32px grid
  const maxX = FIELD - TANK
  const maxY = FIELD - TANK
  const rx = clamp(originX, 0, maxX)
  const ry = clamp(originY, 0, maxY)
  if (free(rx, ry)) return { x: rx, y: ry }
  let best: { x: number; y: number } | null = null
  let bestD = Infinity
  for (let gy = 0; gy <= maxY; gy += step) {
    for (let gx = 0; gx <= maxX; gx += step) {
      if (!free(gx, gy)) continue
      const dx = gx - rx
      const dy = gy - ry
      const d = dx * dx + dy * dy
      if (d < bestD) {
        bestD = d
        best = { x: gx, y: gy }
      }
    }
  }
  return best ?? { x: rx, y: ry }
}
