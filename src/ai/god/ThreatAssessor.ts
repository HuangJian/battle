import type { GodAIInput } from '../GodAIInput'
import type { Bullet } from '../../types'
import type { Direction } from '../../constants'
import { CELL, TANK, DIR_VECTORS, BASE_POS, FIELD } from '../../constants'
import { type Cell } from '../../utils/pathfind'
import { ALL_DIRS } from '../../utils/helpers'
import { BULLET_TRAJECTORY_MAX_CELLS } from './constants'

// ============================================================
// ThreatAssessor — bullet-threat assessment + dodging (T8, M3)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * Find the most dangerous incoming enemy bullet. "Dangerous" = aligned with
 * the player and approaching. Returns null if no threat.
 */
export function findMostDangerousBulletImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Bullet | null {
  const w = self.world
  let best: Bullet | null = null
  let bestDist = Infinity

  for (const b of w.bullets) {
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bcx - pcx) < TANK : Math.abs(bcy - pcy) < TANK
    if (!aligned) continue

    const approaching =
      (b.dir === 'down' && bcy < pcy) ||
      (b.dir === 'up' && bcy > pcy) ||
      (b.dir === 'right' && bcx < pcx) ||
      (b.dir === 'left' && bcx > pcx)
    if (!approaching) continue

    const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
    if (dist < bestDist) {
      bestDist = dist
      best = b
    }
  }
  return best
}

/**
 * T8: Find an enemy bullet whose trajectory will cross the base area.
 * This is the ultimate defense — intercept bullets heading for the base
 * even if they're not threatening the player.
 * Gap B: returns null when the stage has no base.
 */
export function findBulletThreatToBaseImpl(self: GodAIInput): Bullet | null {
  if (!self.hasBase) return null
  const w = self.world
  const baseCx = BASE_POS.col * CELL + CELL
  const baseCy = BASE_POS.row * CELL + CELL
  const baseHalf = CELL // base is 2×2 cells = 32px, half = 16px

  let best: Bullet | null = null
  let bestDist = Infinity

  for (const b of w.bullets) {
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Directional pre-filter (perf): the base sits at rows 24-25 (bottom of
    // the field). Skip bullets that physically cannot reach it, avoiding the
    // per-cell trajectory scan for the majority of in-flight bullets.
    // - 'up' bullets move away from the base → never a threat.
    // - 'left'/'right' bullets stay at their row → only a threat if already
    //   in the base's row band (≥ row 22, generous — base is rows 24-25).
    // - 'down' bullets move toward the base → always potentially a threat.
    // Strict superset: any filtered bullet provably cannot cross the base
    // area or hit base terrain, so no threat is missed.
    if (b.dir === 'up') continue
    if ((b.dir === 'left' || b.dir === 'right') && bcy < 22 * CELL) continue

    const v = DIR_VECTORS[b.dir]

    // Project the bullet's trajectory forward and check if it crosses the base.
    // Fix Bug 4: terrain check must come BEFORE base-area check — otherwise
    // walls protecting the base are ignored, causing false-positive threats
    // and wasted interception actions.
    let crossesBase = false
    for (let d = CELL; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
      const fx = bcx + v.dx * d
      const fy = bcy + v.dy * d

      // If the trajectory goes off-field, stop.
      if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

      // Check terrain FIRST — if a wall blocks the bullet, it never
      // reaches the base, so there's no threat.
      const col = Math.floor(fx / CELL)
      const row = Math.floor(fy / CELL)
      const terrain = w.tileMap.get(col, row)
      if (terrain === 'brick' || terrain === 'steel') break

      // If it hits the base itself, that's a direct hit.
      if (terrain === 'base') {
        crossesBase = true
        break
      }

      // Check if the trajectory point is within the base area.
      // Use baseHalf (16px = 1 cell) instead of baseHalf * 2 — the base
      // is 2×2 cells centered at (baseCx, baseCy), so half-width = CELL.
      if (Math.abs(fx - baseCx) < baseHalf && Math.abs(fy - baseCy) < baseHalf) {
        crossesBase = true
        break
      }
    }

    if (crossesBase) {
      const dist = Math.abs(bcx - baseCx) + Math.abs(bcy - baseCy)
      if (dist < bestDist) {
        bestDist = dist
        best = b
      }
    }
  }

  return best
}

/**
 * T8: Calculate the cell where the player should move to intercept
 * a bullet heading toward the base. This is the cell on the bullet's
 * trajectory that is closest to the player.
 */
export function baseBulletInterceptCellImpl(self: GodAIInput, bullet: Bullet): Cell | null {
  const w = self.world
  const p = self.controlledTank(self.world)!
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const v = DIR_VECTORS[bullet.dir]

  // Walk along the bullet's trajectory and find the closest point to the player
  // that is BETWEEN the bullet and the base (in front of the bullet).
  let bestCell: Cell | null = null
  let bestDist = Infinity

  for (let d = 0; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
    const fx = bcx + v.dx * d
    const fy = bcy + v.dy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

    const col = Math.floor(fx / CELL)
    const row = Math.floor(fy / CELL)

    // Stop if the trajectory hits a wall.
    const terrain = w.tileMap.get(col, row)
    if (terrain === 'brick' || terrain === 'steel') break

    // Check if the player can reach this cell.
    const cellCx = col * CELL + CELL / 2
    const cellCy = row * CELL + CELL / 2
    const dist = Math.abs(cellCx - pcx) + Math.abs(cellCy - pcy)
    if (dist < bestDist) {
      bestDist = dist
      bestCell = { col, row }
    }

    // If we've passed the base, stop searching.
    if (terrain === 'base') break
  }

  // Only intercept if the player can reach the intercept point in time.
  // If the closest point is too far, the player can't get there before
  // the bullet — intercepting would just send the player on a wild goose
  // chase, leaving the base undefended.
  if (bestDist > self.params.t8MaxInterceptDistCells * CELL) return null

  return bestCell
}

/**
 * Choose a dodge direction perpendicular to the incoming bullet.
 * M3: verify the candidate direction is safe (not into another bullet's path).
 */
export function dodgeDirectionImpl(
  self: GodAIInput,
  bullet: Bullet,
  pcx: number,
  pcy: number,
): Direction | null {
  const p = self.controlledTank(self.world)!
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  // Use module-level constants instead of allocating arrays on every dodge.
  const candA: Direction = vertical ? 'left' : 'up'
  const candB: Direction = vertical ? 'right' : 'down'

  // Try each candidate; prefer the one that's passable AND safe (M3).
  // Use local booleans instead of allocating an `open` array.
  let safeA = false
  let safeB = false
  if (self.canMoveDir(p, candA) && self.isSafeDir(pcx, pcy, candA, bullet.id)) safeA = true
  if (self.canMoveDir(p, candB) && self.isSafeDir(pcx, pcy, candB, bullet.id)) safeB = true

  // If no safe candidate, try passable but unsafe.
  if (!safeA && !safeB) {
    if (self.canMoveDir(p, candA)) safeA = true
    if (self.canMoveDir(p, candB)) safeB = true
  }

  // If still nothing, try any open direction.
  if (!safeA && !safeB) {
    if (self.hasBase) {
      // Find the open direction closest to the base (replicates sort-by-distance).
      const baseCx = BASE_POS.col * CELL + CELL
      const baseCy = BASE_POS.row * CELL + CELL
      let bestDist = Infinity
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        if (!self.canMoveDir(p, d)) continue
        const vd = DIR_VECTORS[d]
        const dist = Math.abs(pcx + vd.dx * CELL - baseCx) + Math.abs(pcy + vd.dy * CELL - baseCy)
        if (dist < bestDist) {
          bestDist = dist
        }
      }
      if (bestDist === Infinity) return null
      // Re-iterate to pick the first direction with the best distance
      // (matches sort-stable behavior: ALL_DIRS order on ties).
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        if (!self.canMoveDir(p, d)) continue
        const vd = DIR_VECTORS[d]
        const dist = Math.abs(pcx + vd.dx * CELL - baseCx) + Math.abs(pcy + vd.dy * CELL - baseCy)
        if (dist === bestDist) return d
      }
      return null
    } else {
      // No base — first open direction.
      for (let di = 0; di < ALL_DIRS.length; di++) {
        if (self.canMoveDir(p, ALL_DIRS[di])) return ALL_DIRS[di]
      }
      return null
    }
  }

  // We have at least one perpendicular candidate (safeA or safeB).
  // Prefer the direction that keeps the player closer to the base.
  if (self.hasBase) {
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    const va = DIR_VECTORS[candA]
    const vb = DIR_VECTORS[candB]
    const distA = Math.abs(pcx + va.dx * CELL - baseCx) + Math.abs(pcy + va.dy * CELL - baseCy)
    const distB = Math.abs(pcx + vb.dx * CELL - baseCx) + Math.abs(pcy + vb.dy * CELL - baseCy)
    if (safeA && safeB) return distA <= distB ? candA : candB
    return safeA ? candA : candB
  }
  // No base — first safe candidate.
  return safeA ? candA : candB
}

/**
 * M3: Check if moving in direction `d` would put the player into another
 * bullet's trajectory (excluding the one we're already dodging).
 */
export function isSafeDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  excludeBulletId: number,
): boolean {
  const w = self.world
  const v = DIR_VECTORS[dir]
  // Check the cell we'd move into.
  const newCx = pcx + v.dx * CELL
  const newCy = pcy + v.dy * CELL

  for (const b of w.bullets) {
    if (!b.alive || b.isPlayer || b.id === excludeBulletId) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical
      ? Math.abs(bcx - newCx) < CELL * 0.75
      : Math.abs(bcy - newCy) < CELL * 0.75
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && bcy < newCy) ||
      (b.dir === 'up' && bcy > newCy) ||
      (b.dir === 'right' && bcx < newCx) ||
      (b.dir === 'left' && bcx > newCx)
    if (approaching) return false
  }
  return true
}

/**
 * §49: Check if there's an enemy bullet traveling toward the player in the
 * given direction's line of fire. Used by the armor "对枪" (trade-shots)
 * logic to decide whether to fire for bullet cancellation.
 *
 * Returns true if an enemy bullet is in the line, approaching the player,
 * within a reasonable distance.
 */
export function hasEnemyBulletInLineImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  aimDir: Direction,
): boolean {
  const w = self.world
  const vertical = aimDir === 'up' || aimDir === 'down'
  const bullets = w.bullets

  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Bullet must be roughly aligned with the player in the aimDir line
    const aligned = vertical ? Math.abs(bcx - pcx) < TANK : Math.abs(bcy - pcy) < TANK
    if (!aligned) continue

    // Bullet must be approaching the player (in front, heading toward player)
    const approaching =
      (aimDir === 'up' && b.dir === 'down' && bcy < pcy) ||
      (aimDir === 'down' && b.dir === 'up' && bcy > pcy) ||
      (aimDir === 'left' && b.dir === 'right' && bcx < pcx) ||
      (aimDir === 'right' && b.dir === 'left' && bcx > pcx)
    if (!approaching) continue

    // Within a reasonable distance (8 cells)
    const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
    if (dist < TANK * 8) return true
  }
  return false
}
