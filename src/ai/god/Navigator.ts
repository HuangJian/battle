import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { Cell } from '../../utils/pathfind'
import { CELL, TANK, GRID, DIR_VECTORS, FIELD, BASE_POS, type Direction } from '../../constants'
import { findPath } from '../../utils/pathfind'
import { TileMap } from '../../game/TileMap'
import { BULLET_TRAJECTORY_MAX_CELLS } from './constants'
import { snap, aabb, opposite, ALL_DIRS } from '../../utils/helpers'

// ============================================================
// Navigator — movement, pathfinding, and wall-breaking (T1, S7, S10)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/** Get the player's grid-aligned cell (matches canMoveDir's snap).
 * Per-tick cached: the player doesn't move during think() (movement is
 * applied later in Simulation.updateMovement), so the cell is constant
 * within a tick. Reuses a single Cell object — callers must not mutate it. */
export function playerCellImpl(self: GodAIInput): Cell {
  if (self._playerCellValid) return self._playerCellCache
  const p = self.controlledTank(self.world)!
  self._playerCellCache.col = Math.round(p.x / CELL)
  self._playerCellCache.row = Math.round(p.y / CELL)
  self._playerCellValid = true
  return self._playerCellCache
}

/** Get a tank's grid-aligned cell (consistent with playerCell).
 * Writes into `self._tankCellBuf` (a reusable object) to avoid allocating a
 * fresh {col, row} on every call — tankCell is called ~15× per think() (many
 * in enemy loops inside selectTargetImpl). Callers MUST consume the result
 * before calling tankCell again (same contract as playerCell). */
export function tankCellImpl(self: GodAIInput, t: Tank): Cell {
  const buf = self._tankCellBuf
  buf.col = Math.round(t.x / CELL)
  buf.row = Math.round(t.y / CELL)
  return buf
}

/**
 * Navigate towards a specific cell using A* pathfinding.
 * Returns the next movement direction, or null if no path.
 *
 * P3.1: tries regular A* first (corridors only). If it fails, falls back to
 * `breakBrick` A* which treats brick walls as passable (the player will fire
 * to clear them while following the path). This is the "systematic dig" fix
 * that breaks the navigation paralysis on maze stages — without it, A*
 * treats brick as impassable and the player gets stuck whenever the only
 * route to an enemy goes through brick walls.
 *
 * (perf §68 Round 9) Cross-tick cache: navigateTowards is called every tick
 * from think()'s navigate branch, but A* only needs to re-run when the
 * player or target cell changes (both are second-scale events). Cache the
 * last (playerCell, target, result) and reuse while inputs are stable. A
 * safety timer forces a replan every _navReplanMax ticks (1s default) to
 * bound staleness. Byte-identical: when inputs are unchanged the result is
 * identical because terrain changes coincide with cell changes.
 */
export function navigateTowardsImpl(self: GodAIInput, target: Cell): Direction | null {
  const w = self.world
  // §79: MUST be the controlled tank, not `w.player`. In co-op the God AI
  // drives `w.player2`; reading `w.player` here made every passability test
  // (canMoveDir / canMoveOrBreak) answer for the WRONG tank's surroundings.
  const p = self.controlledTank(w)!
  const playerCell = self.playerCell()

  if (target.col === playerCell.col && target.row === playerCell.row) {
    return null
  }

  // (perf §68) Cache hit: same player + target cells, not expired.
  // We do NOT draw rng.next() here — the original always called it for the
  // suboptimalPathProb gate, but suboptimalPathProb defaults to 0 (result
  // discarded). Skipping the draw desyncs RNG state but the user-accepted
  // contract is "win rate stable", not byte-identical signatures. This
  // saves one mul + add per cached tick.
  self._navReplanTimer--
  if (
    self._navCacheValid &&
    self._navPlayerCol === playerCell.col &&
    self._navPlayerRow === playerCell.row &&
    self._navTargetCol === target.col &&
    self._navTargetRow === target.row &&
    self._navReplanTimer > 0
  ) {
    return self._navCache
  }

  // Cache miss — recompute.
  // §69-B: compute threat costs for A* (undefined when crossfirePathCost <= 0).
  const tc = self.computeThreatCosts(playerCell, p.speed)
  // Try regular A* (corridors only) first.
  let path = findPath(w.tileMap, playerCell, target, tc ? { threatCosts: tc } : undefined)

  // P3.1: If no corridor path, try dig-through-brick path.
  // This finds paths through brick walls — the player follows them and
  // fires at bricks to clear the way (handled by followPath + think()).
  if (!path || path.length === 0) {
    path = findPath(w.tileMap, playerCell, target, { breakBrick: true, threatCosts: tc })
  }

  let result: Direction | null = null
  if (!path || path.length === 0) {
    result = null
  } else {
    // Suboptimal path: small chance of taking a different direction.
    // Only when the primary direction is passable (don't add noise to dig paths).
    // suboptimalPathProb defaults to 0 — this is a rare path, so the
    // ALL_DIRS.filter allocation is acceptable (no measurable impact).
    if (self.rng.next() < self.params.suboptimalPathProb && self.canMoveDir(p, path[0])) {
      const altDirs = ALL_DIRS.filter((d) => d !== path[0] && self.canMoveDir(p, d))
      if (altDirs.length > 0) {
        result = self.rng.pick(altDirs)
        // DO NOT cache when suboptimal path is taken — rng.next() advances
        // every call, so caching would change behavior across ticks.
        self._navCacheValid = false
        self._navReplanTimer = self._navReplanMax
        return result
      }
    }

    const nextDir = path[0]
    if (self.canMoveDir(p, nextDir)) {
      result = nextDir
    } else {
      // P3.1: Path direction blocked by a breakable wall — return it anyway so
      // the caller (think()) can face the wall and fire. canMoveOrBreak verifies
      // it's a breakable brick, not steel/water/base.
      if (self.canMoveOrBreak(p, nextDir)) {
        result = nextDir
      } else {
        // Path blocked by unbreakable terrain — try alternative directions.
        // Indexed loop (AGENTS §14.1): followPathImpl runs every tick (called
        // from think's navigate branch); `for (const d of ALL_DIRS)` allocates
        // an iterator per call.
        let alt: Direction | null = null
        for (let di = 0; di < ALL_DIRS.length; di++) {
          const d = ALL_DIRS[di]
          if (d === opposite(nextDir)) continue
          if (self.canMoveDir(p, d)) {
            alt = d
            break
          }
        }
        result = alt
      }
    }
  }

  // Cache the result for next tick (same player + target cells).
  self._navCacheValid = true
  self._navPlayerCol = playerCell.col
  self._navPlayerRow = playerCell.row
  self._navTargetCol = target.col
  self._navTargetRow = target.row
  self._navCache = result
  self._navReplanTimer = self._navReplanMax
  return result
}

/**
 * Follow the current A* path, re-planning as needed.
 * Returns the next movement direction, or null if no path.
 */
export function followPathImpl(self: GodAIInput): Direction | null {
  const w = self.world
  // §79: controlled tank, not `w.player` (see navigateTowardsImpl).
  const p = self.controlledTank(w)!
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

    // P3.1: Path direction blocked by a breakable wall — return it anyway
    // so the caller (think()) can face the wall and fire. This is the key
    // fix for maze-stage paralysis: the player follows a dig path through
    // brick walls, and when it hits a brick, it fires to clear it instead of
    // abandoning the path and getting stuck.
    if (self.canMoveOrBreak(p, nextDir)) {
      return nextDir
    }

    // Path blocked by unbreakable terrain or tank — try alternative directions.
    // Indexed loop (AGENTS §14.1).
    for (let di = 0; di < ALL_DIRS.length; di++) {
      const d = ALL_DIRS[di]
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

/**
 * Re-plan the A* path to the current best target.
 *
 * P3.1: tries regular A* first, then falls back to dig-through-brick A*.
 * This ensures the player always has a path to the target, even on maze
 * stages where the only route goes through brick walls.
 */
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

  // §69-B: compute threat costs for A* (undefined when crossfirePathCost <= 0).
  // §79: controlled tank, not `w.player` — P1 and P2 can have different speed.
  const p = self.controlledTank(w)!
  const tc = self.computeThreatCosts(playerCell, p.speed)
  // Try regular A* (corridors only) first.
  let path = findPath(w.tileMap, playerCell, target, tc ? { threatCosts: tc } : undefined)

  // P3.1: If no corridor path, try dig-through-brick path.
  if (!path) {
    path = findPath(w.tileMap, playerCell, target, { breakBrick: true, threatCosts: tc })
  }

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
  // §79: controlled tank, not `w.player`. This one also feeds canMoveOrBreak,
  // whose base-protection-brick guard is position-relative — reading P1 here
  // let the co-op God AI walk P2 into (and shoot through) the base wall.
  const p = self.controlledTank(w)!
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
  // Indexed loop (AGENTS §14.1): directMove runs every tick from think's
  // navigate branch (close-range chase).
  for (let dirI = 0; dirI < dirs.length; dirI++) {
    if (self.canMoveOrBreak(p, dirs[dirI])) return dirs[dirI]
  }

  // All preferred directions blocked by unbreakable terrain or tanks —
  // try any passable direction (excluding reverse of primary).
  const primaryOpposite = dirs.length > 0 ? opposite(dirs[0]) : null
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const d = ALL_DIRS[di]
    if (primaryOpposite !== null && d === primaryOpposite) continue
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
  // Cluster C: reuse the per-tick snapshot (same set+order as w.allTanks
  // filtered for o.alive; `o === tank` skip still applied below).
  // Indexed loop (AGENTS §14.1).
  const scan = self._otherTanks.length > 0 ? self._otherTanks : w.allTanks
  for (let oi = 0; oi < scan.length; oi++) {
    const o = scan[oi]
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

/** Check if the player tank can move one CELL in the given direction.
 * Per-tick cached for the player (perf): canMoveDir is called ~10× per think()
 * from navigateTowards / directMove / followPath / canMoveOrBreak /
 * dodgeDirection — always with the player. The player doesn't move during
 * think() (movement is applied later in Simulation.updateMovement), and no
 * other tank moves during think() either, so the 4 directional results are
 * byte-identical within a tick. A bitmask cache avoids redundant
 * rectHitsTerrain + tank-loop scans. Invalidated in endFrame(). */
export function canMoveDirImpl(self: GodAIInput, tank: Tank, dir: Direction): boolean {
  if (tank === self.controlledTank(self.world)) {
    const idx = dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3
    const bit = 1 << idx
    if (self._canMoveComputed & bit) return (self._canMoveResult & bit) !== 0
    const passable = canMoveDirRaw(self, tank, dir)
    self._canMoveComputed |= bit
    if (passable) self._canMoveResult |= bit
    else self._canMoveResult &= ~bit
    return passable
  }
  return canMoveDirRaw(self, tank, dir)
}

function canMoveDirRaw(self: GodAIInput, tank: Tank, dir: Direction): boolean {
  const w = self.world
  const v = DIR_VECTORS[dir]
  // §86: Use Math.floor instead of snap (Math.round) to eliminate the
  // discontinuity at cell midpoints that causes dodge direction oscillation.
  // snap(56, 16) = 64 (rounds up), snap(55, 16) = 48 (rounds down) — this
  // 16px jump flips canMoveDir results, causing the player to oscillate
  // between two positions every tick. With Math.floor, both y=55 and y=56
  // snap to 48, eliminating the oscillation at its source.
  // Gated by `canMoveDirFloorSnap` param: 0 = OFF (snap/round, byte-identical).
  const useFloor = self.params.canMoveDirFloorSnap > 0
  const gx = useFloor ? Math.floor(tank.x / CELL) * CELL : snap(tank.x, CELL)
  const gy = useFloor ? Math.floor(tank.y / CELL) * CELL : snap(tank.y, CELL)
  const nx = gx + v.dx * CELL
  const ny = gy + v.dy * CELL
  if (!w.isInBounds(nx, ny, TANK, TANK)) return false
  if (w.rectHitsTerrain(nx, ny, TANK, TANK)) return false
  // Cluster C: reuse the per-tick snapshot (same set+order as w.allTanks
  // filtered for o.alive; `o === tank` skip still applied below).
  // Indexed loop (AGENTS §14.1): canMoveDirRaw runs up to 4× per think
  // (one per direction in navigate/followPath fallbacks) when the per-tick
  // cache misses; each `for (const o of scan)` allocates an iterator.
  const scan = self._otherTanks.length > 0 ? self._otherTanks : w.allTanks
  for (let oi = 0; oi < scan.length; oi++) {
    const o = scan[oi]
    if (o === tank || !o.alive) continue
    if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
  }
  return true
}

/**
 * §48-revisit: Count the passable terrain exits of a grid cell (0-4). A cell
 * with ≤ 2 exits is a corridor / corner / dead-end — a position where
 * enemies can surround the player. Cell-level approximation (ignores the
 * 2×2 tank footprint) — good enough for a pre-move surround heuristic.
 * Indexed loop (AGENTS §14.1): called up to 5× per tick when trapAvoidance
 * is ON (1 trap check + up to 4 candidate directions).
 */
function countPassableExits(self: GodAIInput, col: number, row: number): number {
  const grid = self.world.tileMap.grid
  let n = 0
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const v = DIR_VECTORS[ALL_DIRS[di]]
    const c = col + v.dx
    const r = row + v.dy
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) continue
    if (!TileMap.blocksTank(grid[r][c])) n++
  }
  return n
}

/**
 * §48-revisit: Trap avoidance (user idea 2 — don't walk into surround
 * positions). Given the player's chosen move direction, check whether the
 * NEXT cell is a surround risk: few passable exits (≤ 2) AND
 * `trapEnemyCount`+ live enemies within `trapEnemyRadiusCells` of it. If so,
 * override to the open direction whose next cell has the most exits
 * (tie-broken toward the base), but only when that is STRICTLY more open
 * than the trap cell (never trade one dead-end for another).
 *
 * Returns the original direction when the next cell is not a trap.
 * Called from think()'s navigate branch (T2b), AFTER _moveDir is chosen —
 * it only perturbs the final move, never the dodge / T8 / T2a priorities.
 */
export function trapAvoidanceImpl(self: GodAIInput, p: Tank, moveDir: Direction): Direction {
  const pc = self.playerCell()
  const v = DIR_VECTORS[moveDir]
  const nx = pc.col + v.dx
  const ny = pc.row + v.dy

  // Out-of-bounds destination (near a wall) — treat as safe (no override).
  if (nx < 0 || nx >= GRID || ny < 0 || ny >= GRID) return moveDir

  const exits = countPassableExits(self, nx, ny)
  if (exits > 2) return moveDir // open cell — no surround risk

  // Count live enemies within trapEnemyRadiusCells of the destination cell.
  const radius = self.params.trapEnemyRadiusCells
  const need = self.params.trapEnemyCount
  let nearby = 0
  const enemies = self._enemies
  for (let i = 0; i < enemies.length; i++) {
    const ec = self.tankCell(enemies[i])
    const d = Math.abs(ec.col - nx) + Math.abs(ec.row - ny)
    if (d <= radius) {
      if (++nearby >= need) break
    }
  }
  if (nearby < need) return moveDir // no surround risk

  // Trap: pick the best open alternative from the CURRENT cell. Strictly
  // more exits than the trap cell, tie-broken toward the base.
  const baseCol = BASE_POS.col + 1
  const baseRow = BASE_POS.row + 1
  let bestDir: Direction | null = null
  let bestExits = exits // only strictly-more-open alternatives count
  let bestBaseDist = Infinity
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const d = ALL_DIRS[di]
    if (d === moveDir) continue
    if (!self.canMoveDir(p, d)) continue
    const dv = DIR_VECTORS[d]
    const cx = pc.col + dv.dx
    const cy = pc.row + dv.dy
    const dExits = countPassableExits(self, cx, cy)
    const baseDist = Math.abs(cx - baseCol) + Math.abs(cy - baseRow)
    if (dExits > bestExits || (dExits === bestExits && baseDist < bestBaseDist)) {
      bestDir = d
      bestExits = dExits
      bestBaseDist = baseDist
    }
  }
  return bestDir ?? moveDir
}

/**
 * §69-B: Compute per-cell threat costs for A* pathfinding.
 *
 * For each enemy bullet, projects its trajectory forward. For each cell along
 * the trajectory, estimates:
 *   - bulletArrivalTick = distance_from_bullet / bullet.speed
 *   - playerArrivalTick = manhattanDist(fromCell, cell) * CELL / playerSpeed
 *
 * If the times overlap within ±10 ticks (collision window), the cell gets a
 * threat cost penalty (= crossfirePathCost). This makes A* prefer routes that
 * avoid cells where the player and a bullet would arrive simultaneously.
 *
 * Returns undefined when crossfirePathCost <= 0 (no threat costs — byte-identical).
 * Returns the reusable _threatCostsBuf when crossfirePathCost > 0.
 *
 * Called from navigateTowardsImpl and replanImpl on A* cache miss / replan.
 * Not per-tick — only when A* is actually re-run (~every 23-60 ticks).
 */
export function computeThreatCostsImpl(
  self: GodAIInput,
  fromCell: Cell,
  playerSpeed: number,
): Float64Array | undefined {
  if (self.params.crossfirePathCost <= 0) return undefined

  const w = self.world
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0
  const penalty = self.params.crossfirePathCost
  const threatWin = 10 // same collision window as findPathThreatImpl
  const buf = self._threatCostsBuf
  buf.fill(0)

  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const v = DIR_VECTORS[b.dir]

    // Project the bullet's trajectory forward, cell by cell.
    for (let d = 0; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
      const fx = bcx + v.dx * d
      const fy = bcy + v.dy * d
      if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

      const col = Math.floor(fx / CELL)
      const row = Math.floor(fy / CELL)
      const terrain = w.tileMap.get(col, row)
      if (terrain === 'brick' || terrain === 'steel') break

      // Estimate arrival times.
      const bulletArrivalTick = d / b.speed
      const playerArrivalTick =
        ((Math.abs(col - fromCell.col) + Math.abs(row - fromCell.row)) * CELL) / ps

      // Collision window: bullet and player would be at the same cell at the
      // same time. Same ±10 tick threshold as findPathThreatImpl.
      if (
        bulletArrivalTick >= playerArrivalTick - threatWin &&
        bulletArrivalTick <= playerArrivalTick + threatWin
      ) {
        const idx = row * GRID + col
        if (buf[idx] < penalty) buf[idx] = penalty
      }
    }
  }

  return buf
}
