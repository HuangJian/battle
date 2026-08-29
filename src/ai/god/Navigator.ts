import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { Cell } from './pathfind'
import { CELL, TANK, GRID, TICK_MS, DIR_VECTORS, type Direction } from '../../constants'
import { findPath } from './pathfind'
import { blocksBullet } from './Chokepoint'
import { opposite, ALL_DIRS } from '../../utils/direction'
import { snap, aabb } from '../../utils/helpers'

// ============================================================
// Navigator — movement, pathfinding, and wall-breaking (T1, S7, S10)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * §nav-cost 3.2: Pre-compute the per-cell base-ring extra cost array for
 * A* breakBrick pathfinding. For each 2×2 tank footprint position, if any
 * sub-block is a brick AND a base-protection brick (isBaseProtectionBrick),
 * the cell gets extra cost = (navBaseRingMult - 1). Otherwise 0.
 *
 * Cached per tileMap.revision on self._baseRingCosts — same strict-pure-memo
 * discipline as buildCarveCosts / buildDigCosts (PathCarve.ts). Terrain
 * mutation bumps the revision the same tick, so the cache never goes stale.
 *
 * When navBaseRingMult < 1 (OFF / classic / degenerate), returns an all-zero
 * array (byte-identical — no extra cost in the A* hot loop). Values in (0,1)
 * are treated as OFF to prevent the ring penalty from reversing (making ring
 * bricks cheaper than normal bricks).
 *
 * Pure World read — no RNG, no mutation. O(GRID² × 4) per rebuild, only on
 * terrain revision change.
 */
export function buildBaseRingCosts(self: GodAIInput): Float64Array {
  const rev = self.world.tileMap.revision
  if (self._baseRingCosts && self._baseRingCostsRev === rev) return self._baseRingCosts
  const costs = new Float64Array(GRID * GRID)
  const mult = self.params.navBaseRingMult
  if (mult >= 1 && self.hasBase) {
    const grid = self.world.tileMap.grid
    // Extra cost = (mult - 1): total brick cost = 1 + (mult - 1) = mult.
    const extra = mult - 1
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (c + 1 >= GRID || r + 1 >= GRID) continue
        let baseRingInFootprint = false
        for (let dr = 0; dr <= 1 && !baseRingInFootprint; dr++) {
          const grow = grid[r + dr]
          for (let dc = 0; dc <= 1 && !baseRingInFootprint; dc++) {
            if (grow[c + dc] === 'brick' && self.isBaseProtectionBrick(c + dc, r + dr)) {
              baseRingInFootprint = true
            }
          }
        }
        if (baseRingInFootprint) {
          costs[r * GRID + c] = extra
        }
      }
    }
  }
  self._baseRingCosts = costs
  self._baseRingCostsRev = rev
  return costs
}

/**
 * §nav-cost 3.3(c): Build the fire-stop PathConstraints fields from the
 * tank's real fire state. When `navFireStopModel === 'firecontrol'`, this
 * injects `fireCooldownTicks`, `fireIntervalTicks`, `marchTicksPerCell`, and
 * `startDir` — activating the dynamic firecontrol cost model in A*.
 *
 * When `navFireStopModel === 'flat'`, this injects only `brickStopCost` and
 * `startDir` — the §190 flat constant model (byte-identical to pre-3.3(c)).
 *
 * Pure World read — no RNG, no mutation. Called once per breakBrick findPath
 * call (not per A* expansion).
 */
function buildFireStopConstraints(
  self: GodAIInput,
  p: Tank,
): Pick<
  import('./pathfind').PathConstraints,
  'brickStopCost' | 'startDir' | 'fireCooldownTicks' | 'fireIntervalTicks' | 'marchTicksPerCell'
> {
  const startDir = p.dir
  if (self.params.navFireStopModel === 'firecontrol') {
    // §3.3(c): compute real fire state from tank.lastFire + tank.nextFireInterval.
    const now = self.world.frame * TICK_MS
    const fireTimerMs = Math.max(0, p.nextFireInterval - (now - p.lastFire))
    const fireCooldownTicks = fireTimerMs / TICK_MS
    const fireIntervalTicks = p.nextFireInterval / TICK_MS
    const marchTicksPerCell = p.speed > 0 ? CELL / p.speed : 23
    return {
      brickStopCost: self.params.navBrickStopCost,
      startDir,
      fireCooldownTicks,
      fireIntervalTicks,
      marchTicksPerCell,
    }
  }
  // Flat model: constant brickStopCost + startDir for turn cost
  return {
    brickStopCost: self.params.navBrickStopCost,
    startDir,
  }
}

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

  // §187: Get the blocked cell (player tank) for guard/P2 A*.
  const blkCell = self.getNavBlockedCell()
  const blkCol = blkCell ? blkCell.col : -99
  const blkRow = blkCell ? blkCell.row : -99

  // (perf §68) Cache hit: same player + target cells, not expired.
  // NOTE: unlike _replanCache, this cache does NOT key tileMap.revision —
  // terrain changes (e.g. brick destruction, fence conversion) may occur
  // between cache fills. The stale path is gracefully degraded: canMoveDir /
  // canMoveOrBreak re-check passability every tick, so the player won't walk
  // into newly-destroyed walls. The safety timer (60 ticks) bounds staleness.
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
    self._navBlockedCol === blkCol &&
    self._navBlockedRow === blkRow &&
    self._navReplanTimer > 0
  ) {
    return self._navCache
  }

  // Cache miss — recompute.
  // Try regular A* (corridors only) first.
  const constraints = blkCell ? { blockedCell: blkCell } : undefined
  let path = findPath(w.tileMap, playerCell, target, constraints)

  // P3.1: If no corridor path, try dig-through-brick path.
  // This finds paths through brick walls — the player follows them and
  // fires at bricks to clear the way (handled by followPath + think()).
  // §nav-cost: inject base ring multiplier (3.2) and fire stop cost (3.3)
  // into the breakBrick A* cost model.
  if (!path || path.length === 0) {
    path = findPath(w.tileMap, playerCell, target, {
      breakBrick: true,
      ...(blkCell ? { blockedCell: blkCell } : {}),
      ...(self.params.navBaseRingMult > 0 ? { baseRingCosts: buildBaseRingCosts(self) } : {}),
      // NOTE: firecontrol model is gated by navBrickStopCost > 0 (the
      // flat/off master switch) — setting navFireStopModel:'firecontrol'
      // with navBrickStopCost:0 silently falls back to flat/off.
      ...(self.params.navBrickStopCost > 0 ? buildFireStopConstraints(self, p) : {}),
    })
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
  self._navBlockedCol = blkCol
  self._navBlockedRow = blkRow
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
    replanImpl(self, playerCell)
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

    // §162: path fully blocked — try BREAKABLE directions (same rationale
    // as directMoveImpl: sealed spawn pockets never get broken otherwise).
    if (self.params.navBreakStuck > 0) {
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        if (d === opposite(nextDir)) continue
        if (self.canMoveOrBreak(p, d)) {
          return d
        }
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
  // recomputation is deterministic when inputs are unchanged (unlike the §68
  // navigateTowards cache, which skipped an rng.next() and relaxed the
  // signature to win-rate-only). NOTE: when navFireStopModel === 'firecontrol',
  // the fire stop constraints include `now = frame * TICK_MS` which changes
  // every tick and is NOT in the cache key — the timer-based expiry bounds
  // the staleness window. The path is only consumed (followPath shift) when
  // the player enters a new cell — which changes the cache key → miss → fresh
  // path — so the cached array is never served pre-consumed.
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
  // §187: Get the blocked cell (player tank) for guard/P2 A*.
  const blkCell = self.getNavBlockedCell()
  const blkCol = blkCell ? blkCell.col : -99
  const blkRow = blkCell ? blkCell.row : -99

  if (self.params.replanCache > 0) {
    self._replanTimer--
    if (
      self._replanCacheValid &&
      self._replanPcCol === playerCell.col &&
      self._replanPcRow === playerCell.row &&
      self._replanTgtCol === target.col &&
      self._replanTgtRow === target.row &&
      self._replanRev === w.tileMap.revision &&
      self._replanBlockedCol === blkCol &&
      self._replanBlockedRow === blkRow &&
      self._replanTimer > 0
    ) {
      self.path = self._replanCache ? self._replanCache.slice() : []
      return
    }
  }

  // Cache miss — recompute.
  // Try regular A* (corridors only) first.
  const constraints = blkCell ? { blockedCell: blkCell } : undefined
  let path = findPath(w.tileMap, playerCell, target, constraints)

  // P3.1: If no corridor path, try dig-through-brick path.
  // §nav-cost: inject base ring multiplier (3.2) and fire stop cost (3.3).
  if (!path) {
    const p = self.controlledTank(w)!
    path = findPath(w.tileMap, playerCell, target, {
      breakBrick: true,
      ...(blkCell ? { blockedCell: blkCell } : {}),
      ...(self.params.navBaseRingMult > 0 ? { baseRingCosts: buildBaseRingCosts(self) } : {}),
      ...(self.params.navBrickStopCost > 0 ? buildFireStopConstraints(self, p) : {}),
    })
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
    self._replanBlockedCol = blkCol
    self._replanBlockedRow = blkRow
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
  //
  // §233 (perf): the 2-4 element dirs array was allocated per call — and
  // directMove runs EVERY tick from think's navigate branch (close-range
  // chase). Replaced with two locals (AGENTS §14.1) — pure allocation
  // elimination, byte-identical first-available selection.
  let prefA: Direction | null = null
  let prefB: Direction | null = null
  if (Math.abs(dy) > CELL / 2) {
    if (dy > 0) prefA = 'down'
    else if (dy < 0) prefA = 'up'
    if (dx > 0) prefB = 'right'
    else if (dx < 0) prefB = 'left'
  } else {
    if (dx > 0) prefA = 'right'
    else if (dx < 0) prefA = 'left'
    if (dy > 0) prefB = 'down'
    else if (dy < 0) prefB = 'up'
  }

  // Return the first preferred direction that we can either move through
  // or break through (brick wall). This enables wall-breaking: the tank
  // faces the wall, shouldFireInDir fires at it, and the wall breaks.
  // (AGENTS §14.1: directMove runs every tick from think's navigate branch.)
  if (prefA !== null && self.canMoveOrBreak(p, prefA)) return prefA
  if (prefB !== null && self.canMoveOrBreak(p, prefB)) return prefB

  // All preferred directions blocked by unbreakable terrain or tanks —
  // try any passable direction (excluding reverse of primary).
  const primaryOpposite = prefA !== null ? opposite(prefA) : prefB !== null ? opposite(prefB) : null
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const d = ALL_DIRS[di]
    if (primaryOpposite !== null && d === primaryOpposite) continue
    if (self.canMoveDir(p, d)) return d
  }

  // §162: still stuck — try BREAKABLE directions (canMoveOrBreak). The
  // Battlement spawn pocket is sealed by wide-box protection bricks: from
  // the cul-de-sac the preferred dirs (up/right toward the enemy) are all
  // unbreakable, and the passable fallback only ever returns the reverse
  // (back into the pocket), so the player oscillates at spawn for 17-30s
  // instead of breaking the thin side wall the user expects. Passable first
  // (the loop above), breakable second — behavior identical when
  // navBreakStuck=0 or when a passable direction exists.
  if (self.params.navBreakStuck > 0) {
    for (let di = 0; di < ALL_DIRS.length; di++) {
      const d = ALL_DIRS[di]
      if (primaryOpposite !== null && d === primaryOpposite) continue
      if (self.canMoveOrBreak(p, d)) return d
    }
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
// costs (computeThreatCostsImpl) retired; recoverable from git history
// (experimental.ts deleted) if the v2 survive candidate needs them.

/**
 * §302 mode 3: is the lane line from the player's would-be on-lane cell to the
 * target clear of bullet-blocking terrain?
 *
 * The check runs on the lane the player would merge onto — `eCol` for a
 * vertically travelling target (the shot then runs along its column), `eRow`
 * for a horizontal one — starting from the player's own along-axis coordinate
 * (where the merge lands it) and walking to the target. Water is transparent
 * to bullets (TileMap.blocksBullet), so only brick/steel/base stop the shot.
 */
function laneShotClear(
  self: GodAIInput,
  pc: { col: number; row: number },
  eCol: number,
  eRow: number,
  vertical: boolean,
): boolean {
  // Off-lane bounds guard: the vertical walk below bounds-checks its own
  // rows, but `grid[eRow]` / `grid[r][eCol]` index the TARGET's lane
  // coordinate directly — an out-of-grid lane (test fixtures place tanks
  // off-grid; production never should) must refuse the lane, not throw.
  if (eCol < 0 || eCol >= GRID || eRow < 0 || eRow >= GRID) return false
  const grid = self.world.tileMap.grid
  if (vertical) {
    if (pc.col === eCol) return true
    const step = eRow > pc.row ? 1 : -1
    for (let r = pc.row + step; r !== eRow; r += step) {
      if (r < 0 || r >= GRID) return false
      if (blocksBullet(grid[r][eCol])) return false
    }
  } else {
    if (pc.row === eRow) return true
    const step = eCol > pc.col ? 1 : -1
    for (let c = pc.col + step; c !== eCol; c += step) {
      if (c < 0 || c >= GRID) return false
      if (blocksBullet(grid[eRow][c])) return false
    }
  }
  return true
}

/**
 * §302 mode 4: is the lateral run from the player to the lane unobstructed?
 *
 * Autopsy of the mode-3 A/B (hard 35×60): every net-negative stage lost on
 * `base_destroyed`, not on player deaths (s26 15→26, s21 14→21, while the
 * positive outlier s32 went 15→7). The merge itself was fine on open ground
 * but on brick-dense boards (Checkers' checkerboard, Ice Palace's wall maze)
 * "merge sideways" turns into a corridor detour: the player burns seconds
 * walking around walls to reach a lane the target has already left, the kill
 * rate drops ~15%, and the extra live enemies eventually take the base.
 *
 * So the merge only earns its cost when the lateral run is a straight,
 * unobstructed sprint — one the player can actually complete before the
 * target turns. The check is 2 cells wide, matching the 2×2 tank footprint.
 */
function lateralRunClear(
  self: GodAIInput,
  pc: { col: number; row: number },
  eCol: number,
  eRow: number,
  vertical: boolean,
): boolean {
  const grid = self.world.tileMap.grid
  if (vertical) {
    if (pc.col === eCol) return true
    const step = eCol > pc.col ? 1 : -1
    for (let c = pc.col + step; c !== eCol + step; c += step) {
      if (c < 0 || c >= GRID) return false
      for (let r = pc.row; r <= pc.row + 1; r++) {
        if (r < 0 || r >= GRID) return false
        if (blocksBullet(grid[r][c])) return false
      }
    }
  } else {
    if (pc.row === eRow) return true
    const step = eRow > pc.row ? 1 : -1
    for (let r = pc.row + step; r !== eRow + step; r += step) {
      if (r < 0 || r >= GRID) return false
      for (let c = pc.col; c <= pc.col + 1; c++) {
        if (c < 0 || c >= GRID) return false
        if (blocksBullet(grid[r][c])) return false
      }
    }
  }
  return true
}

/**
 * §302: is the tail-merge cell a plausible place to stand?
 *
 * The tank footprint is 2×2 cells (TANK=32 vs CELL=16), so the whole
 * footprint must be on-grid and free of terrain the player can never enter
 * (steel / water / base). Brick is accepted — the player can shoot it away —
 * and `forest`/`ice` are passable.
 */
function tailCellUsable(self: GodAIInput, col: number, row: number): boolean {
  if (col < 0 || row < 0 || col + 1 >= GRID || row + 1 >= GRID) return false
  const tm = self.world.tileMap
  for (let r = row; r <= row + 1; r++) {
    for (let c = col; c <= col + 1; c++) {
      const t = tm.get(c, r)
      if (t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/**
 * §302 pursuit-tail navigation — HUNT lane merge (plan/Intent-Policy-NN-Plan.md
 * §12.1 defect #3: "追击走并行车道横向开火，不并入目标车道后方").
 *
 * ROOT CAUSE. `directMoveImpl` closes the ROW gap first — the historical
 * "align rows to get more shots" rule. Against an enemy travelling VERTICALLY
 * that is exactly wrong: the player converges onto the target's row while the
 * target keeps climbing its own column, so the player settles in a PARALLEL
 * lane and fires perpendicular at a moving target. Those are the worst shots
 * in the game — the hit window slides sideways during bullet flight and
 * `predictiveFireGate` (§193-D) suppresses a large share of them outright.
 *
 * FIX. Merge onto the target's LANE (its column when it travels vertically,
 * its row when it travels horizontally) and close that perpendicular gap
 * BEFORE the along-lane gap, so the player ends up on the same line as the
 * target and `shouldFireInDir` gets a stable aligned shot instead of a
 * perpendicular one at a sliding window.
 *
 * TWO MODES (measured — see DECISIONS §302):
 *   mode 1 (REJECTED, net −30 on hard 35×60): navigate to the tail CELL
 *     (perpendicular gap first, then along-lane). It also walks the player
 *     BACKWARD to reach a tail cell that sits farther behind than the
 *     player already is — pure wasted distance.
 *   mode 2 (shipped candidate): LATERAL MERGE ONLY. The override fires only
 *     while the player is off-lane; once on the lane the normal chase owns
 *     the along-lane pursuit. No backward step is ever taken.
 *
 * SCOPE / SAFETY (§12.2 — an archived-negative repo, so the override is narrow):
 *   - chase targets only: `navTarget` must be a live enemy's cell, so HUNT's
 *     center / defense-position fallbacks (nav-stuck escape, §179 emergency,
 *     M3 survival retreat) never get re-aimed;
 *   - behind only: `along > 0` (player ahead of the target on its travel axis)
 *     bails out — merging there would mean cutting across the target's front;
 *   - distance window [pursuitTailMinCells, pursuitTailMaxCells] plus a
 *     perpendicular budget (pursuitTailMaxLaneGap) — a long lateral detour
 *     costs more time than the aligned shot is worth;
 *   - `allowBreak` is only set inside the close-range directMove regime — at
 *     long range (A* corridor routing) a brick detour would dig the player off
 *     its corridor, so only genuinely passable lanes are merged into there.
 *
 * Pure World + params read: no RNG, no mutation (AGENTS §2.1/§2.3). Returns
 * null when the override does not apply — the caller then keeps whatever
 * direction the normal HUNT chain already chose. Returns `PURSUIT_TAIL_HOLD`
 * (a sentinel, not a Direction) when the override wants the player to STOP —
 * the caller maps that to `_moveDir = null` (release the throttle), the same
 * "hold this tick" value §182 and §153-W1 already use.
 */
/** §302 AlongMode=3 sentinel: hold this tick (release the throttle) so a
 * level/closing target can sweep past the player's row before the merge. */
export const PURSUIT_TAIL_HOLD = 'hold' as const
export type PursuitTailDir = Direction | typeof PURSUIT_TAIL_HOLD

/**
 * §302 AlongMode=4: resolve which enemy cell the tail geometry keys on.
 *
 * am ≤ 3 keys on HUNT's navTarget — which is the NAV chain's pick and can
 * diverge from the COMBAT lock (`_lastSelectTargetId`, §170 target sway):
 * the tail then goes blind on exactly the chase the player is fighting
 * (measured s21@30 t2523: `br=navigate`, mergeable geometry, override
 * silent because navTarget pointed elsewhere). am ≥ 4 keys on the locked
 * target whenever one is alive — the tail is a 1-2 cell movement tweak, and
 * the navStuck/carve gates still own the escape paths.
 *
 * Pure: reads observation state only. Returns `navTarget` unchanged for
 * am ≤ 3 (archived arms byte-faithful) and whenever no live locked tank
 * exists.
 */
export function pursuitTailTargetCell(self: GodAIInput, navTarget: Cell | null): Cell | null {
  if (self.params.pursuitTailAlongMode < 4) return navTarget
  const lockedId = self._lastSelectTargetId
  if (lockedId < 0) return navTarget
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (t.id === lockedId && t.alive && t.spawnTimer <= 0) {
      return { col: Math.round(t.x / CELL), row: Math.round(t.y / CELL) }
    }
  }
  return navTarget
}

/**
 * §302 AlongMode=4: the slide command for the ENGAGE/T2a duel branch.
 *
 * T2a commits `moveDir = aimDir` (turn to face) + fire when its scan sees
 * the enemy on a gun line — correct when the shot is real, but during a
 * tail SLIDE those 1-3 turning ticks abort the merge mid-run and the turn
 * fires at nothing (the player faces away from the enemy the next tick).
 * am ≥ 4 lets an active SLIDE preempt the T2a turn: movement = the slide,
 * fire = off (shooting while turned away wastes the bullet). A HOLD does
 * NOT preempt — the duel's stand-and-shoot is exactly what a hold wants
 * (the target is approaching/level, and a real shot beats repositioning).
 * The bullet-cancel commit (enemy bullet on the line) is safety-critical
 * and is never preempted (call-site choice).
 *
 * Pure; returns null whenever the tail has no slide this tick (HOLD, OFF,
 * no locked target, geometry not met) so the caller keeps its own plan.
 */
export function pursuitTailSlideDir(self: GodAIInput, p: Tank): Direction | null {
  if (self.params.pursuitTailMode <= 0 || self.params.pursuitTailAlongMode < 4) return null
  const lockedId = self._lastSelectTargetId
  if (lockedId < 0) return null
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  let cell: Cell | null = null
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (t.id === lockedId && t.alive && t.spawnTimer <= 0) {
      cell = { col: Math.round(t.x / CELL), row: Math.round(t.y / CELL) }
      break
    }
  }
  if (cell === null) return null
  const dir = pursuitTailDirImpl(self, p, self.playerCell(), cell, true)
  return dir === null || dir === PURSUIT_TAIL_HOLD ? null : dir
}

export function pursuitTailDirImpl(
  self: GodAIInput,
  p: Tank,
  pc: { col: number; row: number },
  navTarget: Cell | null,
  allowBreak: boolean,
): PursuitTailDir | null {
  const params = self.params
  if (params.pursuitTailMode <= 0 || navTarget === null) return null
  // Mode 6: never merge while the base is under threat. The §302 autopsy on
  // hard 35×60 traced every net-negative stage to `base_destroyed`, not to
  // player deaths (s26 15→26, s21 14→21; the positive outlier s32 went 15→7).
  // A lateral merge is time spent NOT closing on the enemy, and under a base
  // threat that is exactly the time the defense cannot spare.
  if (params.pursuitTailMode >= 6 && self.hasBase && self.isBaseUnderThreat()) return null
  // Mode 5: close-range engagement geometry only. `allowBreak` is already
  // scoped to the directMove regime (navDist ≤ 5); beyond that A* owns a
  // corridor route and a lateral steering command can push the player into a
  // dead end the pathfinder had already routed around.
  if (params.pursuitTailMode >= 5 && !allowBreak) return null

  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  let eCol = 0
  let eRow = 0
  let tdx = 0
  let tdy = 0
  let found = false
  let et: Tank | null = null
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (t.spawnTimer > 0) continue
    const c = Math.round(t.x / CELL)
    const r = Math.round(t.y / CELL)
    if (c !== navTarget.col || r !== navTarget.row) continue
    // Travel axis: real velocity when the tank is actually moving (an enemy
    // can be FACING a wall it is pressed against) — except on ice, where the
    // velocity is a SLIDE, not an intent, and the facing direction is the
    // reliable signal (same reasoning as the player-side exclusion above).
    if (!self.world.isTankOnIce(t) && Math.abs(t.vx) + Math.abs(t.vy) > 0.01) {
      if (Math.abs(t.vx) >= Math.abs(t.vy)) tdx = t.vx > 0 ? 1 : -1
      else tdy = t.vy > 0 ? 1 : -1
    } else {
      const v = DIR_VECTORS[t.dir]
      tdx = v.dx
      tdy = v.dy
    }
    eCol = c
    eRow = r
    et = t
    found = true
    break
  }
  if (!found) return null

  const dCol = pc.col - eCol
  const dRow = pc.row - eRow
  // Signed offset ALONG the target's travel axis. `along < 0` ⇒ the player
  // sits in the target's wake (tail-chase); `along > 0` ⇒ the player is in
  // front of it and it is closing (head-on intercept); `0` ⇒ level with it.
  const along = dCol * tdx + dRow * tdy
  const dist = Math.abs(dCol) + Math.abs(dRow)

  // ------------------------------------------------------------------
  // mode 7 — ADJACENT-LANE MERGE: what §12.1 #3 actually asks for.
  //
  // The defect is NOT "the player is far from the target's lane". It is that
  // directMove runs the player PARALLEL in the ADJACENT lane, catches up
  // level with the target, then turns 90° to snap-fire sideways — and misses,
  // because by the time the turn completes the target has moved on.
  //
  // The cure is to slip ONE lane over while the target is in the next lane
  // and is passing / about to pass: the player then stands IN the target's
  // lane and fights along the lane axis (tail-chase when behind, intercept
  // when the target is closing), instead of firing across it. One cell of
  // lateral movement costs ~0.4 s — nothing like the 2-4 cell cross-map
  // detours modes 1-6 made, which the user rejected on replay review.
  // ------------------------------------------------------------------
  if (params.pursuitTailMode >= 7) {
    const laneGap = tdy !== 0 ? Math.abs(pc.col - eCol) : Math.abs(pc.row - eRow)
    const vertical = tdy !== 0
    // ----------------------------------------------------------------
    // §302 AlongMode ≥ 3 — YIELD-THEN-TAIL, self-contained state machine
    // (user directive 2026-08-29, refined after replay review the same
    // day). The user's spec, for a target climbing the adjacent lane:
    //   wait until it is TWO cells up-left of the player (the 2×2 bodies
    //   vertically staggered, so the lateral slide cannot grind its
    //   flank), then slide ONTO its lane (through laneGap 1 — the
    //   half-entered sub-cell — all the way to gap 0), then turn up the
    //   lane and shoot it in the back. The first cut of this mode held
    //   correctly but handed the slide back to directMove at gap 1, and
    //   directMove's vertical-first priority yanked the player back into
    //   the parallel chase one sub-cell short of the lane — "并道" never
    //   completed on film (replay review: s9@11 / s21@8, both arms).
    // ----------------------------------------------------------------
    if (params.pursuitTailAlongMode >= 3) {
      // `>= 3`, never `=== 3`: am=4 layers the sticky-target/T2a-preemption
      // call-site fixes ON TOP of this machine. A strict equality here
      // silently dropped am=4 into the archived arms' path below — which
      // merges head-on at along > 0 (the measured −58 geometry) — and the
      // am=4 arm lost by net −59 before the A/B caught it (2026-08-29).
      // The adjacent band includes the half-entered laneGap 1: the merge is
      // a 2-sub-cell slide and must be owned through its whole duration.
      // gap 0 (on-lane) hands back to the normal chase; gap ≥ 3 is not the
      // defect geometry.
      if (laneGap !== 1 && laneGap !== 2) return null
      // MERGE: the target has cleared the player's row by the 2-row stagger
      // (its body vertically adjacent to the player's — the slide cannot
      // collide). No wake-side along cap: once staggered, the lane IS the
      // chase, and MaxCells bounds how far the target may drift before the
      // merge is given up. This is also why MinCells is NOT consulted here:
      // the stagger is body geometry (2 rows = 2×2 bodies adjacent), not a
      // tunable stand-off — MinCells ≥ 5 would self-lock (the vertical-first
      // chase pins the gap before the window opens).
      if (along <= -2) {
        if (dist > params.pursuitTailMaxCells) return null
        if (!laneShotClear(self, pc, eCol, eRow, vertical)) return null
        const want: Direction = vertical
          ? eCol > pc.col
            ? 'right'
            : 'left'
          : eRow > pc.row
            ? 'down'
            : 'up'
        if (self.canMoveDir(p, want)) return want
        if (allowBreak && self.canMoveOrBreak(p, want)) return want
        // The slide is refused while the shot line checked clear ⇒ the
        // blocker is a TANK (steel/base would have failed laneShotClear;
        // breakable brick returns true from canMoveOrBreak). When the
        // blocker is the TARGET'S OWN BODY — `along = -2` is a ROUNDED cell
        // distance, and a mid-cell target can still physically overlap the
        // slide footprint by most of a sub-cell (s21@30 t2475: its y wobble
        // 153→174 blocked the slide for ~24 ticks) — do NOT hand the tick
        // back: directMove's vertical-first priority chases the target and
        // closes the very stagger this phase is waiting for (measured: the
        // player oscillated down-chase → hold → blocked-slide for 1.5 s).
        // HOLD; the target's own travel opens the pixel gap within a few
        // ticks and the slide fires (verified per-tick on film).
        if (et !== null) {
          const v = DIR_VECTORS[want]
          const nx = snap(p.x, CELL) + v.dx * CELL
          const ny = snap(p.y, CELL) + v.dy * CELL
          if (aabb(nx, ny, TANK, TANK, et.x, et.y, et.w, et.h)) return PURSUIT_TAIL_HOLD
        }
        return null
      }
      // YIELD: the target is level with, closing on, or only 1 row clear of
      // the player — merging now would cut its bow or grind its flank
      // (measured net −58 for exactly this). Release the throttle and let
      // it climb past until the stagger opens.
      if (along > params.pursuitTailAlongWindow) return null
      if (dist > params.pursuitTailMaxCells) return null
      // At along ≤ 0 skip the shot-line check: laneShotClear steps AWAY from
      // the target when the rows coincide and walks off-grid to `false`,
      // which would drop the hold on the very ticks the target passes the
      // player's row. The merge phase re-validates before every slide tick.
      if (along > 0 && !laneShotClear(self, pc, eCol, eRow, vertical)) return null
      return PURSUIT_TAIL_HOLD
    }
    // The NEIGHBOURING lane — and because the tank body is 2×2 cells
    // (TANK=32 vs CELL=16), "neighbouring" is a gap of 2, not 1. Measured
    // laneGap distribution while the target is moving: gap2 = 26.9% (the
    // single largest bucket), gap1 = 7.9% (bodies already overlap laterally,
    // so the lateral step is refused by canMoveDir and the override is a
    // no-op). The first implementation used gap1 and scored a 0.12% effective
    // dose — indistinguishable from OFF.
    if (laneGap !== 2) return null
    // Distance window. dist = laneGap + |along| = 2 + |along|, so
    // `pursuitTailMinCells` doubles as a minimum tail-chase stand-off:
    // minCells 3 ⇒ |along| ≥ 1; 4 ⇒ ≥ 2; 5 ⇒ ≥ 3. Wire the generic window in
    // here too — it was silently dead for mode 7 (the branch returns before
    // the shared dist check), which made the first minCells=4 experiment a
    // no-op mislabelled as a result.
    //
    // AlongMode 0/1/2 below are ARCHIVED arms (round-2 A/B evidence): their
    // evaluation sequence must stay exactly as measured.
    if (dist < params.pursuitTailMinCells || dist > params.pursuitTailMaxCells) return null
    // Passing or about to pass: within `pursuitTailAlongWindow` cells along
    // the travel axis, either side. Not restricted to strictly-behind — a
    // target about to sweep past in the next lane is exactly when the merge
    // is cheap and the payoff immediate (user review, 2026-08-29).
    if (Math.abs(along) > params.pursuitTailAlongWindow) return null
    // Diagnostic split (§302 forensics): is the merge helpful when the player
    // is in the target's WAKE (tail-chase) or when the target is level with /
    // closing on the player (side-by-side / head-on intercept)? Measured as
    // one net −39 bundle; this splits it so the two cases can be judged apart.
    //   0 = both (default)   1 = wake only (along < 0)   2 = level/ahead only
    if (params.pursuitTailAlongMode === 1 && along >= 0) return null
    if (params.pursuitTailAlongMode === 2 && along < 0) return null
    // Still require the lane to buy a shot: merging onto a lane whose line
    // to the target is walled off just swaps one wasted shot for another.
    if (!laneShotClear(self, pc, eCol, eRow, vertical)) return null
    const want: Direction | null = vertical
      ? eCol > pc.col
        ? 'right'
        : 'left'
      : eRow > pc.row
        ? 'down'
        : 'up'
    if (self.canMoveDir(p, want)) return want
    if (allowBreak && self.canMoveOrBreak(p, want)) return want
    return null
  }

  if (along > 0) return null
  if (dist < params.pursuitTailMinCells || dist > params.pursuitTailMaxCells) return null

  // The lane coordinate is the target's own coordinate on the perpendicular
  // axis — vertical travel ⇒ its column, horizontal travel ⇒ its row. (That is
  // also `eCol - tdx*k` / `eRow - tdy*k` for any k, which is why mode 2 needs
  // no tail-cell validation: the merge target is the lane LINE, not a cell.)
  if (params.pursuitTailMode >= 2) {
    const laneGap = tdy !== 0 ? Math.abs(pc.col - eCol) : Math.abs(pc.row - eRow)
    // Off-lane only: on the lane the normal chase (directMove) already owns
    // the along-lane pursuit, and steering here could only push the player
    // BACKWARD toward a tail cell it has already passed.
    if (laneGap === 0) return null
    if (laneGap > params.pursuitTailMaxLaneGap) return null
    // Mode 3: only pay the lateral detour when it actually buys a shot. A
    // merge onto a lane whose line back to the target is blocked by terrain
    // produces the same perpendicular nothing-shot as before, minus the time
    // spent driving sideways — measured: on-lane fire quality is unchanged by
    // the merge (probe §302, 73.2% → 72.6% aligned), so this gate is what
    // separates "merge that buys an aligned shot" from "merge that doesn't".
    if (params.pursuitTailMode >= 3 && !laneShotClear(self, pc, eCol, eRow, tdy !== 0)) {
      return null
    }
    if (params.pursuitTailMode >= 4 && !lateralRunClear(self, pc, eCol, eRow, tdy !== 0)) {
      return null
    }
    const want: Direction | null =
      tdy !== 0 ? (eCol > pc.col ? 'right' : 'left') : eRow > pc.row ? 'down' : 'up'
    if (self.canMoveDir(p, want)) return want
    if (allowBreak && self.canMoveOrBreak(p, want)) return want
    return null
  }

  // Mode 1 (measured net-negative — kept for the record, see the doc block).
  // Merge point, walked back toward the target when the far cell is off-grid
  // or unbreakable terrain (a steel tail is a dead end, not a lane).
  let k = params.pursuitTailCells
  let tc = eCol - tdx * k
  let tr = eRow - tdy * k
  while (k > 1 && !tailCellUsable(self, tc, tr)) {
    k--
    tc = eCol - tdx * k
    tr = eRow - tdy * k
  }
  if (!tailCellUsable(self, tc, tr)) return null

  // Perpendicular (lane) gap first, then chase along the lane. Vertical travel
  // ⇒ the lane is the target's COLUMN; horizontal travel ⇒ its ROW.
  let want: Direction | null = null
  if (tdy !== 0) {
    if (pc.col !== tc) want = tc > pc.col ? 'right' : 'left'
    else if (pc.row !== tr) want = tr > pc.row ? 'down' : 'up'
  } else {
    if (pc.row !== tr) want = tr > pc.row ? 'down' : 'up'
    else if (pc.col !== tc) want = tc > pc.col ? 'right' : 'left'
  }
  if (want === null) return null
  if (self.canMoveDir(p, want)) return want
  if (allowBreak && self.canMoveOrBreak(p, want)) return want
  return null
}

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
