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
  // Try regular A* (corridors only) first.
  let path = findPath(w.tileMap, playerCell, target)

  // P3.1: If no corridor path, try dig-through-brick path.
  // This finds paths through brick walls — the player follows them and
  // fires at bricks to clear the way (handled by followPath + think()).
  if (!path || path.length === 0) {
    path = findPath(w.tileMap, playerCell, target, { breakBrick: true })
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
    // (perf §127) Invalidate the replan cache: the cached path is what got
    // stuck (e.g. a fence power-up steeled it), so force a fresh A* next tick
    // instead of re-serving the same dead path until the 60-tick timer.
    self._replanCacheValid = false
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

  // (perf §127) Cross-tick cache hit: same player + target cells, same
  // terrain revision, not expired. replanInterval defaults to 1, so without
  // this replanImpl ran full A* every tick — measured 73-89% of all findPath
  // calls (§2.10). replanImpl draws NO RNG, so skipping identical
  // recomputation is byte-identical (unlike the §68 navigateTowards cache,
  // which skipped an rng.next() and relaxed the signature to win-rate). The
  // path is only consumed (followPath shift) when the player enters a new
  // cell — which changes the cache key → miss → fresh path — so the cached
  // array is never served pre-consumed.
  //
  // The terrain revision check (tileMap.revision) is what makes this a
  // STRICT pure memo: any brick destroyed by a bullet bumps the revision the
  // same tick, so the cache invalidates exactly when the terrain does — no
  // staleness window for the 60-tick safety timer to bridge (the timer is now
  // only a defence-in-depth bound). followPath's stuck branch clears
  // _replanCacheValid as a self-healing valve.
  //
  // ALIASING (found by the 13/350-cell divergence sweep): the cached array is
  // the SAME array followPathImpl consumes via `self.path.shift()` on cell
  // change. A reference assignment (`self.path = self._replanCache`) made the
  // shift mutate the cache in place — the cache slowly drained to [] and the
  // player re-served the empty path every tick until the key changed.
  // MUST return a COPY so followPath's shift consumes the working copy, not
  // the cached array (B arm recomputes a fresh array every replan, so it
  // never had this hazard). Slice cost is negligible: replan runs at most
  // once per replanInterval (50) or on empty-path ticks.
  if (self.params.replanCache > 0) {
    self._replanTimer--
    if (
      self._replanCacheValid &&
      self._replanPcCol === playerCell.col &&
      self._replanPcRow === playerCell.row &&
      self._replanTgtCol === target.col &&
      self._replanTgtRow === target.row &&
      self._replanRev === w.tileMap.revision &&
      self._replanTimer > 0
    ) {
      self.path = self._replanCache ? self._replanCache.slice() : []
      return
    }
  }

  // Cache miss — recompute.
  // Try regular A* (corridors only) first.
  let path = findPath(w.tileMap, playerCell, target)

  // P3.1: If no corridor path, try dig-through-brick path.
  if (!path) {
    path = findPath(w.tileMap, playerCell, target, { breakBrick: true })
  }

  if (path) {
    self.path = path
  } else {
    self.path = []
  }

  if (self.params.replanCache > 0) {
    self._replanCacheValid = true
    self._replanPcCol = playerCell.col
    self._replanPcRow = playerCell.row
    self._replanTgtCol = target.col
    self._replanTgtRow = target.row
    self._replanRev = w.tileMap.revision
    // Store an independent copy (see the hit branch note): followPath's
    // shift() consumes self.path, which must not alias the cached array.
    // findPath returns a fresh array every call, so self.path is already a
    // private copy at this point; slice() decouples the cache from it.
    self._replanCache = self.path.slice()
    self._replanTimer = self._replanMax
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
  // M0.5 (2026-08-03): canMoveDirFloorSnap retired (A/B -2.6pp, never shipped) — fixed snap.
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
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

// M0.5 (2026-08-03): trapAvoidance (Navigator) + crossfirePathCost A* threat
// costs (computeThreatCostsImpl) retired. Archived verbatim in experimental.ts
// for the v2 survive candidate / EnemyModel features (design §4.4).

/**
 * §145 iceGlideAdjust — 冰上滑行控制（纯函数，供 HUNT 的 navigate 段调用）。
 *
 * 冰面物理（SimulationCombat.updateMovement）：反向输入 = 以 ICE_ACCEL_TRACTION
 * (0.35) 向反方向加速——不是纯制动，而是**真倒车**。倒过头后 Math.round 玩家格
 * 在格边界抖动 → A* 路径缓存（按玩家格键控）翻转 → 方向振荡（S24 seed 23
 * 实测 t4506-4511：md 上下翻转 6 tick，浪费控制且可能冲入敌人）。
 *
 * 正确冰上操控 = 需要反向时先松键（null），让滑行以 ICE_DECEL_TRACTION (0.05)
 * 自然衰减，不倒退、不过冲；下一 tick 路径从滑停位置重新规划。垂直转弯不动：
 * axis-lock 会让旧轴自然衰减，转弯本身有效。低于 minSpeed（刚起步/将停）不干预。
 *
 * 纯函数：不读 RNG、不读 World 可变状态 —— 可直接单测，且旋钮默认 0 时 HUNT
 * 不调用 → byte-identical。
 */
export function iceGlideAdjust(
  moveDir: Direction | null,
  onIce: boolean,
  vx: number,
  vy: number,
  minSpeed: number,
): Direction | null {
  if (!moveDir || !onIce) return moveDir
  const glideSpeed = Math.abs(vx) + Math.abs(vy)
  if (glideSpeed < minSpeed) return moveDir
  // axis-lock（SimulationCombat）每 tick 只保留主导轴，|vx| === |vy| 的"对角"
  // 是瞬时态；tie-break 归 x 轴意味着此时 y 轴反向不会被判为 reverse（视为垂直
  // 转弯放行）——无害，勿"修复"。
  const axisX = Math.abs(vx) >= Math.abs(vy)
  const reverse = axisX
    ? (moveDir === 'right' && vx < 0) || (moveDir === 'left' && vx > 0)
    : (moveDir === 'down' && vy < 0) || (moveDir === 'up' && vy > 0)
  return reverse ? null : moveDir
}
