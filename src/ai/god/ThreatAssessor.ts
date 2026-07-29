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
  const p = w.player!
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
  const w = self.world
  const p = w.player!
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  const candidates: Direction[] = vertical ? ['left', 'right'] : ['up', 'down']

  // Try each candidate; prefer the one that's passable AND safe (M3).
  const open: Direction[] = []
  for (const d of candidates) {
    if (self.canMoveDir(p, d) && self.isSafeDir(pcx, pcy, d, bullet.id)) {
      open.push(d)
    }
  }

  // If no safe candidate, try passable but unsafe.
  if (open.length === 0) {
    for (const d of candidates) {
      if (self.canMoveDir(p, d)) open.push(d)
    }
  }

  // If still nothing, try any open direction.
  if (open.length === 0) {
    for (const d of ALL_DIRS) {
      if (self.canMoveDir(p, d)) open.push(d)
    }
  }
  if (open.length === 0) return null

  // Prefer the direction that keeps the player closer to the base.
  // This prevents dodge from sending the player on a wild chase away
  // from the defense position.
  // Gap B: when the stage has no base, skip the base-preference sort —
  // the first safe candidate is fine (no defense position to maintain).
  if (self.hasBase) {
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    open.sort((a, b) => {
      const va = DIR_VECTORS[a]
      const vb = DIR_VECTORS[b]
      const distA = Math.abs(pcx + va.dx * CELL - baseCx) + Math.abs(pcy + va.dy * CELL - baseCy)
      const distB = Math.abs(pcx + vb.dx * CELL - baseCx) + Math.abs(pcy + vb.dy * CELL - baseCy)
      return distA - distB
    })
  }

  return open[0]
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
