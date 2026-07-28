import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { Cell } from '../../utils/pathfind'
import { CELL, TANK, GRID, DIR_VECTORS, type Direction } from '../../constants'
import { findPath } from '../../utils/pathfind'
import { snap, aabb, opposite, ALL_DIRS } from '../../utils/helpers'

// ============================================================
// Navigator — movement, pathfinding, and wall-breaking (T1, S7, S10)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/** Get the player's grid-aligned cell (matches canMoveDir's snap). */
export function playerCellImpl(self: GodAIInput): Cell {
  const p = self.world.player!
  return { col: Math.round(p.x / CELL), row: Math.round(p.y / CELL) }
}

/** Get a tank's grid-aligned cell (consistent with playerCell). */
export function tankCellImpl(_self: GodAIInput, t: Tank): Cell {
  return { col: Math.round(t.x / CELL), row: Math.round(t.y / CELL) }
}

/**
 * Navigate towards a specific cell using A* pathfinding.
 * Returns the next movement direction, or null if no path.
 */
export function navigateTowardsImpl(self: GodAIInput, target: Cell): Direction | null {
  const w = self.world
  const p = w.player!
  const playerCell = self.playerCell()

  if (target.col === playerCell.col && target.row === playerCell.row) {
    return null
  }

  const path = findPath(w.tileMap, playerCell, target)
  if (!path || path.length === 0) return null

  // Suboptimal path: small chance of taking a different direction.
  if (self.rng.next() < self.params.suboptimalPathProb) {
    const altDirs = ALL_DIRS.filter((d) => d !== path[0] && self.canMoveDir(p, d))
    if (altDirs.length > 0) {
      return self.rng.pick(altDirs)
    }
  }

  const nextDir = path[0]
  if (self.canMoveDir(p, nextDir)) {
    return nextDir
  }

  // Path blocked — try alternative directions.
  for (const d of ALL_DIRS) {
    if (d === opposite(nextDir)) continue
    if (self.canMoveDir(p, d)) return d
  }

  return null
}

/**
 * Follow the current A* path, re-planning as needed.
 * Returns the next movement direction, or null if no path.
 */
export function followPathImpl(self: GodAIInput): Direction | null {
  const w = self.world
  const p = w.player!
  const playerCell = self.playerCell()

  // Only consume path steps when the player enters a new grid cell.
  // The player moves at ~0.7 px/tick, so it takes ~23 ticks per cell.
  // Shifting every tick would exhaust the path before arrival.
  if (
    !self._lastPathCell ||
    self._lastPathCell.col !== playerCell.col ||
    self._lastPathCell.row !== playerCell.row
  ) {
    if (self.path.length > 0) self.path.shift()
    self._lastPathCell = { col: playerCell.col, row: playerCell.row }
  }

  // Re-plan periodically or when the path is exhausted.
  self.replanTimer--
  if (self.replanTimer <= 0 || self.path.length === 0) {
    self.replan(playerCell)
    self.replanTimer = self.params.replanInterval
    self._lastPathCell = { col: playerCell.col, row: playerCell.row }
  }

  // Follow the path.
  if (self.path.length > 0) {
    const nextDir = self.path[0]

    // Check if we can actually move in the path direction.
    if (self.canMoveDir(p, nextDir)) {
      return nextDir
    }

    // Path blocked (by a tank?) — try alternative directions.
    for (const d of ALL_DIRS) {
      if (d === opposite(nextDir)) continue
      if (self.canMoveDir(p, d)) {
        return d
      }
    }

    // Fully stuck — re-plan next tick.
    self.path = []
    self.replanTimer = 0
  }

  // No path found — return null so the caller (think) can fall back to
  // directMove, which breaks through walls toward the target. This is
  // essential when the target is walled off and A* can't route around.
  return null
}

/** Re-plan the A* path to the current best target. */
export function replanImpl(self: GodAIInput, playerCell: Cell): void {
  const w = self.world
  const target = self.selectTarget(playerCell)
  if (!target) {
    self.path = []
    return
  }

  if (target.col === playerCell.col && target.row === playerCell.row) {
    self.path = []
    return
  }

  const path = findPath(w.tileMap, playerCell, target)
  if (path) {
    self.path = path
  } else {
    self.path = []
  }
}

/**
 * Direct movement toward the target, breaking through walls.
 */
export function directMoveImpl(self: GodAIInput, playerCell: Cell): Direction | null {
  const w = self.world
  const p = w.player!
  const target = self.selectTarget(playerCell)
  if (!target) return null
  if (target.col === playerCell.col && target.row === playerCell.row) return null

  const dx = target.col * CELL - p.x
  const dy = target.row * CELL - p.y

  // Build direction preference: prioritize vertical movement (up/down)
  // first to close the row gap with the enemy. This gives more chances
  // to fire at enemies in the same row once aligned. The old horizontal-
  // first approach made the player zigzag across the map without ever
  // getting into the same row as an enemy.
  const dirs: Direction[] = []
  if (Math.abs(dy) > CELL / 2) {
    if (dy > 0) dirs.push('down')
    else if (dy < 0) dirs.push('up')
    if (dx > 0) dirs.push('right')
    else if (dx < 0) dirs.push('left')
  } else {
    if (dx > 0) dirs.push('right')
    else if (dx < 0) dirs.push('left')
    if (dy > 0) dirs.push('down')
    else if (dy < 0) dirs.push('up')
  }

  // Return the first preferred direction that we can either move through
  // or break through (brick wall). This enables wall-breaking: the tank
  // faces the wall, shouldFireInDir fires at it, and the wall breaks.
  for (const dir of dirs) {
    if (self.canMoveOrBreak(p, dir)) return dir
  }

  // All preferred directions blocked by unbreakable terrain or tanks —
  // try any passable direction (excluding reverse of primary).
  for (const d of ALL_DIRS) {
    if (dirs.length > 0 && d === opposite(dirs[0])) continue
    if (self.canMoveDir(p, d)) return d
  }

  return null
}

/**
 * Check if the tank can move in a direction OR break through a brick wall.
 * Returns true if the direction is passable, or if it's blocked only by
 * non-base-protection brick walls (which can be destroyed by firing).
 * Returns false if blocked by steel, water, base, or another tank.
 */
export function canMoveOrBreakImpl(self: GodAIInput, tank: Tank, dir: Direction): boolean {
  if (self.canMoveDir(tank, dir)) return true

  const w = self.world
  const v = DIR_VECTORS[dir]
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
  const nx = gx + v.dx * CELL
  const ny = gy + v.dy * CELL

  // Out of bounds — can't break through
  if (!w.isInBounds(nx, ny, TANK, TANK)) return false

  // Blocked by a tank? — can't break through, need to go around
  for (const o of w.allTanks) {
    if (o === tank || !o.alive) continue
    if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
  }

  // Check terrain at new position — if any cell is unbreakable, can't break through
  const c0 = Math.floor(nx / CELL)
  const r0 = Math.floor(ny / CELL)
  const c1 = Math.floor((nx + TANK - 1) / CELL)
  const r1 = Math.floor((ny + TANK - 1) / CELL)

  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      if (c < 0 || c >= GRID || r < 0 || r >= GRID) continue
      const terrain = w.tileMap.get(c, r)
      if (terrain === 'steel' || terrain === 'water' || terrain === 'base') return false
      if (terrain === 'brick' && self.isBaseProtectionBrick(c, r)) return false
    }
  }

  // Only breakable brick walls blocking — can break through by firing
  return true
}

/** Check if the player tank can move one CELL in the given direction. */
export function canMoveDirImpl(self: GodAIInput, tank: Tank, dir: Direction): boolean {
  const w = self.world
  const v = DIR_VECTORS[dir]
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
  const nx = gx + v.dx * CELL
  const ny = gy + v.dy * CELL
  if (!w.isInBounds(nx, ny, TANK, TANK)) return false
  if (w.rectHitsTerrain(nx, ny, TANK, TANK)) return false
  for (const o of w.allTanks) {
    if (o === tank || !o.alive) continue
    if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
  }
  return true
}
