import type { GodAIInput } from '../GodAIInput'
import type { Cell } from './pathfind'
import type { Direction } from '../../constants'
import { BASE_POS, GRID, CELL, DIR_VECTORS, BULLET } from '../../constants'
import { findPath } from './pathfind'
import { computeBaseGuardAnchorImpl, getDefaultDefensePositionImpl } from './StrategyPlanner'
import { enemyCanShootBase, enemyCanBreachRing } from './SmartThreatModel'

import { manhattan } from '../../utils/helpers'
import { isBaseRingCell } from './ThreatBudget'

// ============================================================
// PathCarve — §161 / 开路策略 (carve path, user request 2026-08-06).
//
// When the spawn point is trapped in a brick maze and cannot smoothly
// reach the standable defense post near the base, the player shoots
// through LOWER-HALF brick walls to carve a through-route to the post
// (R1/R2) — generalized to every map, no stage names. A second phase
// (R3) digs from the post toward the most base-threatening enemy when
// nothing at the post is fightable.
//
// Constraints on every carve path (R5/R6):
//   - NEVER route through steel (even when the player could pierce it).
//   - NEVER break base-ring bricks (the exact 8-cell ring — the base's
//     last line of defense).
//   - Break AT MOST `carveMaxBaseColumn` bricks in the base's own
//     columns (BASE_POS.col..+1, rows above the ring) when no
//     alternative route exists — prefer a 0-break route, allow 1 only
//     if forced, reject 2+.
//
// All functions are pure reads of World state + params (no RNG, no
// mutation) — gated by params.carvePathMode (0 = OFF, byte-identical).
// ============================================================

/**
 * §161: the exact 8-cell base ring predicate — byte-identical to
 * SimulationCombat.isBaseProtectionCell (the base occupies rows
 * BASE_POS.row..+1 × cols BASE_POS.col..+1):
 *   row br-1, cols bc-1..bc+2   (the ring's top row)
 *   col bc-1 / bc+2, rows br..br+1   (the ring's left/right columns)
 * Ring bricks are NEVER carveable — breaking one opens a direct lane to
 * the eagle. (This is the EXACT ring, deliberately NOT the wide
 * `isBaseProtectionBrick` radius box — R6 explicitly permits up to
 * `carveMaxBaseColumn` base-column breaks, which the wide box would
 * forbid; the carve is a deliberate, constrained operation.)
 */
export function isCarveRingBrickImpl(self: GodAIInput, col: number, row: number): boolean {
  const tm = self.world.tileMap
  // §3.2: ring membership via the shared ThreatBudget predicate.
  return self.hasBase && tm.get(col, row) === 'brick' && isBaseRingCell(col, row)
}

/**
 * §161: a brick in the base's OWN columns (BASE_POS.col..+1) ABOVE the
 * ring (rows 0..br-2). These are the "基地所在列" walls — carving through
 * them opens a straight vertical lane to the eagle, so the carve avoids
 * them whenever an alternative exists and breaks at most
 * `carveMaxBaseColumn` of them (R6). Ring cells (row br-1 and the ring
 * columns) are excluded — they are handled by isCarveRingBrickImpl.
 */
export function isBaseColumnBrickImpl(self: GodAIInput, col: number, row: number): boolean {
  if (self.world.tileMap.get(col, row) !== 'brick') return false
  const bc = BASE_POS.col
  const br = BASE_POS.row
  return (col === bc || col === bc + 1) && row <= br - 2
}

/**
 * §161: per-cell cost array for the carve dig A*. Ring bricks cost 1e9
 * (never carveable — R5/R6 hard constraint). Base-column bricks cost
 * `carveBaseColumnCost` (default 1e9 ⇒ effectively unbreakable, the
 * "尽量不打" preference) — §178 lowers it in dual central-breach so the
 * nav-stuck carve-dig escape punches through the central wall. Steel is
 * already impassable in A* (R5 by construction). Cached per tileMap.revision
 * — a strict pure memo (same discipline as the §127 replan cache): a brick
 * destroyed bumps the revision the same tick, so the cache never goes
 * stale.
 */
export function buildCarveCosts(self: GodAIInput): Float64Array {
  const rev = self.world.tileMap.revision
  if (self._carveCosts && self._carveCostsRev === rev) return self._carveCosts
  const costs = new Float64Array(GRID * GRID)
  const grid = self.world.tileMap.grid
  const baseCost = self.params.carveBaseColumnCost
  for (let r = 0; r < GRID; r++) {
    const grow = grid[r]
    for (let c = 0; c < GRID; c++) {
      if (grow[c] !== 'brick') continue
      if (isCarveRingBrickImpl(self, c, r)) {
        costs[r * GRID + c] = 1e9
      } else if (isBaseColumnBrickImpl(self, c, r)) {
        costs[r * GRID + c] = baseCost
      }
    }
  }
  self._carveCosts = costs
  self._carveCostsRev = rev
  return costs
}

/**
 * §161 Mode B: the enemy most likely to threaten the base, within
 * `carveThreatDistCells` of the base — a breacher / direct-shooter
 * (enemyCanBreachRing / enemyCanShootBase — it can destroy the ring or
 * the base with its next bullet) ranks above everything else; among
 * equal rank, nearest to the base wins. Returns a FRESH Cell (tankCell
 * shares a buffer). Pure World read — no RNG.
 */
export function carveThreatEnemyImpl(self: GodAIInput): Cell | null {
  const w = self.world
  const prm = self.params
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  let bestCol = -1
  let bestRow = -1
  let bestDist = Infinity
  let bestBreacher = false
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    const dist = manhattan(tc.col, tc.row, bc, br)
    if (dist > prm.carveThreatDistCells) continue
    const breacher = enemyCanShootBase(self, t) || enemyCanBreachRing(self, t)
    if (breacher && !bestBreacher) {
      // First breacher outranks every non-breacher regardless of distance.
      bestCol = tc.col
      bestRow = tc.row
      bestDist = dist
      bestBreacher = true
    } else if (breacher === bestBreacher && dist < bestDist) {
      bestCol = tc.col
      bestRow = tc.row
      bestDist = dist
    }
  }
  return bestCol >= 0 ? { col: bestCol, row: bestRow } : null
}

/**
 * §161: the carve target post — the standable base-guard anchor (the
 * same data-driven §137/D1 computation, mode-independent) with a fallback
 * to the default defense position. Cached per stage (recomputed on
 * reset). Pure terrain function — no RNG.
 */
export function carvePostImpl(self: GodAIInput): Cell | null {
  if (!self._carvePostComputed) {
    self._carvePostComputed = true
    self._carvePost = computeBaseGuardAnchorImpl(self) ?? getDefaultDefensePositionImpl(self)
  }
  return self._carvePost
}

/**
 * §161 R6 acceptance: is the (unrestricted) dig path carve-safe? Walk the
 * tank cells of the path; reject any steel or ring brick in a footprint;
 * count base-column bricks — must be <= carveMaxBaseColumn. Returns false
 * on out-of-bounds.
 */
export function pathCarveSafeImpl(self: GodAIInput, from: Cell, path: Direction[]): boolean {
  const grid = self.world.tileMap.grid
  const maxBase = self.params.carveMaxBaseColumn
  let col = from.col
  let row = from.row
  let baseColCount = 0
  // Footprint dedupe: consecutive tank cells share 1 row (vertical step) or 1
  // col (horizontal step) in their 2×2 footprints — a base-column brick in the
  // shared strip would otherwise be counted twice and over-reject a LEGAL
  // single-break path (R6's "at most 1 cell" is a per-PATH cap, not per-cell).
  let prevR0 = -1
  let prevR1 = -1
  let prevC0 = -1
  let prevC1 = -1
  for (let pi = 0; pi < path.length; pi++) {
    const v = DIR_VECTORS[path[pi]]
    col += v.dx
    row += v.dy
    if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
    for (let dr = 0; dr <= 1; dr++) {
      const grow = grid[row + dr]
      for (let dc = 0; dc <= 1; dc++) {
        const t = grow[col + dc]
        if (t === 'steel') return false
        if (t !== 'brick') continue
        // Ring bricks are never carveable (R5/R6 hard constraints).
        if (isCarveRingBrickImpl(self, col + dc, row + dr)) return false
        // Base-column bricks are capped at carveMaxBaseColumn (R6).
        if (
          isBaseColumnBrickImpl(self, col + dc, row + dr) &&
          !(row + dr >= prevR0 && row + dr <= prevR1 && col + dc >= prevC0 && col + dc <= prevC1)
        ) {
          baseColCount++
        }
      }
    }
    prevR0 = row
    prevR1 = row + 1
    prevC0 = col
    prevC1 = col + 1
  }
  return baseColCount <= maxBase
}

/** §161: the carve path from `from` to `to`, preferring the least damage:
 *   1. corridor A* (no digging at all) — a smooth route;
 *   2. restricted dig A* (ring + base-column bricks = 1e9 cost) — zero
 *      base-column damage whenever any alternative route exists, then
 *      pathCarveSafeImpl caps base-column bricks at carveMaxBaseColumn.
 * Returns null when no carve-safe route exists. Pure World read.
 * (perf §132) The former step-3 unrestricted dig fallback was removed — see
 * findCarvePathImpl. It was a second full-map A* attempted on every
 * restricted-unsafe / restricted-null outcome but returned a carve-safe path
 * only 3/17683 times across all 2100 gate sims (all chaos, all
 * restricted-unsafe). The godai-gate (620/508/473) still passes with margin.
 */
export function findCarvePathImpl(self: GodAIInput, from: Cell, to: Cell): Direction[] | null {
  const tm = self.world.tileMap
  if (from.col === to.col && from.row === to.row) return null
  // The restricted A* penalizes ring/base-column cells (1e9) but that cost is
  // FINITE — when no alternative route exists A* still returns a path through
  // them. The footprint check (pathCarveSafeImpl) is therefore the real gate:
  // a tank cell adjacent to the base column overlaps column bricks in its 2×2
  // footprint that A* never sees (it prices cells, not footprints).
  // (perf §132) The unrestricted dig fallback — a SECOND full-map A* with no
  // threatCosts, attempted on every restricted-unsafe / restricted-null outcome
  // — was removed. Instrumented across all 2100 gate sims it returned a
  // carve-safe path only 3/17683 times (all chaos, all restricted-unsafe). The
  // godai-gate (620/508/473) still passes with margin, so dropping it is
  // accepted as non-regressing per the gate contract.
  const restricted = findPath(tm, from, to, {
    breakBrick: true,
    threatCosts: buildCarveCosts(self),
  })
  if (restricted && restricted.length > 0 && pathCarveSafeImpl(self, from, restricted)) {
    return restricted
  }
  return null
}

/**
 * (perf §131) Row stride for the second-level memo key: the two cell keys are
 * packed as `fromKey * STRIDE + toKey`, both in [0, GRID*GRID), so the packed
 * key is a plain int32 and the Map stays on its fast integer path.
 */
const CARVE_MEMO_STRIDE = GRID * GRID

/** §161: cached carve-path query result. */
export interface CarvePathInfo {
  /** The path, or null when no carve-safe route exists. */
  path: Direction[] | null
  /** True when the path is corridor-only (no digging needed — R4). */
  corridor: boolean
}

/**
 * §161: cached carve-path query, keyed on (from cell, to cell,
 * tileMap.revision) with a carveReplanTicks safety timer — the same
 * strict-pure-memo discipline as the §127 replan cache. Terrain mutations
 * bump revision and invalidate the same tick; the timer only bounds
 * staleness if findPath ever gains an input outside the key.
 *
 * (perf §131) Backed by a SECOND-level memo (`self._carveMemo`) holding every
 * (from,to) answer computed under the current revision + param set. The
 * 1-entry cache above remembers exactly one pair, so a caller that sweeps
 * many targets from the same cell — findLaneDefensePointImpl walks up to 48 —
 * misses it on literally every candidate (measured: 0.0% hit rate on that
 * path) and pays a fresh corridor A* plus one or two dig A* searches for each
 * one, every tick. The memo turns 91% of those into a map lookup.
 *
 * The 1-entry cache's own behavior is untouched: it is still consulted first,
 * and the memo-hit path performs exactly the same field writes and timer reset
 * a recomputation would, so every later hit/miss decision is unchanged. The
 * memo is a strict pure memo — the answer is a function of (tileMap, from, to,
 * carveBaseColumnCost, carveMaxBaseColumn), and all five are in its key.
 */
export function carvePathInfoCached(self: GodAIInput, from: Cell, to: Cell): CarvePathInfo {
  const rev = self.world.tileMap.revision
  self._carvePathTimer--
  if (
    self._carvePathCacheValid &&
    self._carvePathFromCol === from.col &&
    self._carvePathFromRow === from.row &&
    self._carvePathToCol === to.col &&
    self._carvePathToRow === to.row &&
    self._carvePathRev === rev &&
    self._carvePathTimer > 0
  ) {
    return { path: self._carvePathCache, corridor: self._carvePathCorridor }
  }

  // ---- second-level memo (perf §131) ----
  // buildCarveCosts prices base-column bricks with carveBaseColumnCost and
  // pathCarveSafeImpl caps them at carveMaxBaseColumn; §178 overrides both in
  // dual central breach, so both join the revision in the validity key. Any
  // mismatch — or the carveReplanTicks staleness bound running out — drops the
  // whole map, so a stale answer can never be served.
  // NOTE: memo entries are stored by reference (NOT copied). Current callers
  // only read .length/.corridor, so aliasing is safe. If a future caller
  // mutates the returned path array (e.g. shift/pop), it would corrupt the
  // memo. Such a caller must clone the result.
  const baseCost = self.params.carveBaseColumnCost
  const maxBase = self.params.carveMaxBaseColumn
  let memo = self._carveMemo
  self._carveMemoTtl--
  if (
    memo === null ||
    self._carveMemoRev !== rev ||
    self._carveMemoBaseCost !== baseCost ||
    self._carveMemoMaxBase !== maxBase ||
    self._carveMemoTtl <= 0
  ) {
    if (memo === null) {
      memo = new Map()
      self._carveMemo = memo
    } else {
      memo.clear()
    }
    self._carveMemoRev = rev
    self._carveMemoBaseCost = baseCost
    self._carveMemoMaxBase = maxBase
    self._carveMemoTtl = self.params.carveReplanTicks
  }
  const memoKey = (from.row * GRID + from.col) * CARVE_MEMO_STRIDE + (to.row * GRID + to.col)
  const memoHit = memo.get(memoKey)

  let path: Direction[] | null = null
  let corridor = false
  if (memoHit !== undefined) {
    path = memoHit.path
    corridor = memoHit.corridor
  } else {
    if (!(from.col === to.col && from.row === to.row)) {
      // 1. Corridor first — a smooth route needs no digging.
      const c = findPath(self.world.tileMap, from, to)
      if (c && c.length > 0) {
        path = c
        corridor = true
      } else {
        path = findCarvePathImpl(self, from, to)
      }
    }
    memo.set(memoKey, { path, corridor })
  }
  self._carvePathCache = path
  self._carvePathCorridor = corridor
  self._carvePathCacheValid = true
  self._carvePathFromCol = from.col
  self._carvePathFromRow = from.row
  self._carvePathToCol = to.col
  self._carvePathToRow = to.row
  self._carvePathRev = rev
  self._carvePathTimer = self.params.carveReplanTicks
  return { path, corridor }
}

/**
 * §162: carve-fire check — may the player fire into `dir` to break out of
 * a sealed spawn pocket? The carve dig path is already exact-ring-safe
 * (R5/R6) by construction, but the immediate one-step footprint is
 * re-verified here so a terrain change mid-dig can never lead to breaking
 * steel / ring / base-column bricks. Returns true only when the ONE step
 * ahead is a carve-safe brick wall (nothing else blocks it).
 */
export function carveFireAheadImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): boolean {
  const w = self.world
  const grid = w.tileMap.grid
  const v = DIR_VECTORS[dir]
  // Start one cell ahead of the player center.
  const sx = pcx + v.dx * CELL
  const sy = pcy + v.dy * CELL
  const c0 = Math.floor((sx - 16) / CELL)
  const r0 = Math.floor((sy - 16) / CELL)
  const c1 = Math.floor((sx + 15) / CELL)
  const r1 = Math.floor((sy + 15) / CELL)
  let sawBrick = false
  for (let r = r0; r <= r1; r++) {
    if (r < 0 || r >= GRID) continue
    const row = grid[r]
    for (let c = c0; c <= c1; c++) {
      if (c < 0 || c >= GRID) continue
      const t = row[c]
      if (t === 'brick') {
        // Never break ring bricks (R5) — the carve path never routes
        // through them, but terrain can change between path build and fire.
        if (isCarveRingBrickImpl(self, c, r)) return false
        // Base-column bricks: only allowed within the carve's own cap
        // (R6). Re-check the exact 2×2 footprint count here.
        if (isBaseColumnBrickImpl(self, c, r)) {
          // Count base-column bricks in this footprint.
          let bcCount = 0
          for (let rr = r0; rr <= r1; rr++) {
            if (rr < 0 || rr >= GRID) continue
            const row2 = grid[rr]
            for (let cc = c0; cc <= c1; cc++) {
              if (cc < 0 || cc >= GRID) continue
              if (row2[cc] === 'brick' && isBaseColumnBrickImpl(self, cc, rr)) bcCount++
            }
          }
          if (bcCount > self.params.carveMaxBaseColumn) return false
        }
        sawBrick = true
      } else if (t === 'steel' || t === 'water' || t === 'base') {
        // Unbreakable — the carve fire must NOT touch it (R5: never steel;
        // water/base can't be broken anyway).
        return false
      }
    }
  }
  return sawBrick
}

/**
 * §162: pick the carve-dig escape target when nav-stuck in a sealed pocket.
 * Preference order (each via carvePathInfoCached, corridor-first):
 *   1. the nearest enemy (hunt the real threat);
 *   2. four compass points around the player at carveEscapeRadius cells
 *      (generic "get out of this pocket" directions);
 *   3. the map center;
 *   4. the default defense position.
 * Returns the FIRST target with a valid carve path (dig or corridor), or
 * null when every candidate is unreachable by a carve-safe route. Pure
 * World read — no RNG, no mutation.
 */
export function findCarveEscapeImpl(self: GodAIInput, pc: Cell): Cell | null {
  const radius = 8
  // 1. Nearest enemy (via selectTarget — same source HUNT uses).
  const enemy = self.selectTarget(pc)
  if (enemy) {
    const info = carvePathInfoCached(self, pc, enemy)
    if (info.path && info.path.length > 0) return enemy
  }
  // 2. Compass points around the player.
  const compass: Cell[] = [
    { col: pc.col, row: Math.max(0, pc.row - radius) },
    { col: pc.col, row: Math.min(GRID - 1, pc.row + radius) },
    { col: Math.max(0, pc.col - radius), row: pc.row },
    { col: Math.min(GRID - 1, pc.col + radius), row: pc.row },
    { col: Math.max(0, pc.col - radius), row: Math.max(0, pc.row - radius) },
    { col: Math.min(GRID - 1, pc.col + radius), row: Math.max(0, pc.row - radius) },
    { col: Math.max(0, pc.col - radius), row: Math.min(GRID - 1, pc.row + radius) },
    { col: Math.min(GRID - 1, pc.col + radius), row: Math.min(GRID - 1, pc.row + radius) },
  ]
  for (const t of compass) {
    if (t.col === pc.col && t.row === pc.row) continue
    const info = carvePathInfoCached(self, pc, t)
    if (info.path && info.path.length > 0) return t
  }
  // 3. Map center.
  const center = carvePathInfoCached(self, pc, { col: 12, row: 12 })
  if (center.path && center.path.length > 0) return { col: 12, row: 12 }
  // 4. Default defense position.
  const post = getDefaultDefensePositionImpl(self)
  if (post) {
    const info = carvePathInfoCached(self, pc, post)
    if (info.path && info.path.length > 0) return post
  }
  return null
}

/**
 * §163: the lane defense point — a STANDABLE cell in the base's own column
 * (BASE_POS.col..+1) directly ABOVE the base, closest to it, that the
 * player can actually reach by a carve-safe dig from the current position.
 * This is where the player HOLDS the mid lane: face up, fire to cancel
 * enemy shells carving down the base column / kill the carver. Returns
 * null when the column is unreachable or the stage has no base.
 *
 * Scan order (round-3 tuning): the FULL base column above the ring
 * (rows br-1 .. 0, nearest the base first — the hold only needs to be IN
 * the column, since 对消 requires line alignment, not base proximity), cols
 * bc .. bc+1. Each candidate must (a) be standable (no tank footprint
 * blocker) and (b) have a carve-safe path from the current cell. Near-base
 * rows may be sealed (Battlement: (12,21) inside the fortress) while a
 * higher open column cell is corridor-reachable — still a valid hold anchor.
 */
export function findLaneDefensePointImpl(self: GodAIInput, pc: Cell): Cell | null {
  if (!self.hasBase) return null
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  // Scan the FULL base column above the ring (rows br-1 .. 0), nearest the
  // base first. The hold point only needs to be IN the column — 对消
  // (bullet-bullet cancellation) needs column alignment, not proximity to
  // the base. Near-base rows may be sealed (Battlement: (12,21) sits inside
  // the fortress, unreachable) while a higher open column cell is corridor-
  // reachable — that cell is still a valid hold anchor for up-lane fire.
  for (let r = br - 1; r >= 0; r--) {
    for (let c = bc; c <= bc + 1; c++) {
      if (c < 0 || c + 1 >= GRID || r + 1 >= GRID) continue
      // Standable: the 2×2 tank footprint (rows r..r+1, cols c..c+1) must
      // contain no blocking terrain (brick may be carved, but a standable
      // point shouldn't require standing on a wall the moment we arrive).
      let blocked = false
      for (let dr = 0; dr <= 1 && !blocked; dr++) {
        const row = w.tileMap.grid[r + dr]
        for (let dc = 0; dc <= 1 && !blocked; dc++) {
          const t = row[c + dc]
          if (t === 'steel' || t === 'water' || t === 'base') blocked = true
        }
      }
      if (blocked) continue
      const info = carvePathInfoCached(self, pc, { col: c, row: r })
      if (info.path && info.path.length > 0) return { col: c, row: r }
    }
  }
  return null
}

/**
 * §163 换持枪判定 (round 3): the x-offset to the nearest cancellable enemy
 * shell ABOVE the player in the base column, or null when none is
 * acquirable. 对消 (bullet-bullet AABB collision) needs the player's UPWARD
 * bullet to overlap the shell's line — both bullets are 6px wide, so the
 * lines cross only when |bx − pcx| < BULLET. A player standing still in the
 * 32px column matches a random shell ~37% of the time, so the candidate
 * must WALK the column to acquire a shell line. Returns the signed px
 * offset (bx − pcx): |offset| < BULLET → hold & fire; |offset| ≤ TANK
 * (reachable with one side-step) → step toward it; larger → unacquirable
 * now (fall through to point navigation). Same steel/water unblocked check
 * as laneShellInColumnImpl + player-position filters (shell above the
 * player: `by < pcy`, so the upward bullet meets it before it passes).
 */
export function laneShellAboveImpl(self: GodAIInput, pcx: number, pcy: number): number | null {
  if (!self.hasBase) return null
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const colMinX = bc * CELL
  const colMaxX = (bc + 2) * CELL
  const baseMaxY = (br + 2) * CELL
  const bullets = w.bullets
  let best: number | null = null
  let bestAbs = Infinity
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    if (b.dir !== 'down') continue
    const bx = b.x + b.w / 2
    if (bx < colMinX || bx >= colMaxX) continue
    const by = b.y + b.h / 2
    if (by >= pcy) continue // shell at/below the player — cannot cancel by firing up
    if (by >= baseMaxY) continue
    // Bricks don't stop bullets; only steel between shell and base would.
    // Water does NOT block bullets (TileMap.blocksBullet = brick/steel/base
    // only). §165 fix: the old `steel || water` check prevented
    // midLaneDefense from ever triggering on maps with water in the base
    // column (S8 Riverbed rows 6-7/16-17), leaving the base undefended.
    const bcol = Math.floor(bx / CELL)
    let blocked = false
    for (let r = Math.floor(by / CELL) + 1; r <= br + 1; r++) {
      const t = w.tileMap.get(bcol, r)
      if (t === 'steel') {
        blocked = true
        break
      }
    }
    if (blocked) continue
    const off = bx - pcx
    const abs = Math.abs(off)
    if (abs < bestAbs) {
      bestAbs = abs
      best = off
    }
  }
  return best
}

/**
 * §163: is the base's mid lane under threat RIGHT NOW? True when any alive
 * enemy (a) is in the base's own column NEAR the base (rows br-8..br-2 —
 * close enough that its next few shots can reach the base through the
 * column bricks; a fast enemy at (12,2) is NOT a threat yet, one at
 * (12,20) is), (b) can shoot the base this tick (enemyCanShootBase), or
 * (c) is approaching the base's vertical lane within `predictCells` AND
 * within `nearRows` rows. Pure World read.
 */
/**
 * §163 中路威胁检测 — 只认「真实正在凿穿」的信号，不做泛化的敌人存在检测。
 *
 * 敌人呆在基地列 ≠ 威胁：基地列是常见通道，敌人路过/横向移动时用存在检测
 * 会把 player 拽回中路防守点（§163 A/B 实测 29/35 关变差）。真正的威胁是：
 *
 * 1. 敌人子弹正在基地列（col bc..bc+1）向下飞行，且弹道与基地之间没有钢铁/水
 *    ——子弹会一路凿穿砖墙直抵基地（多砖击穿机制），只有在中路防守点向上开火
 *    对消才能拦住它。这是「对消敌人炮弹」的精确触发。
 * 2. 敌人已在基地列、面朝下、距基地 ≤ 4 格 —— 下一步就是向下开火凿墙。
 * 3. 敌人对基地有干净射界（enemyCanShootBase，与 defenseIntercept 同源）。
 *
 * 纯 World 观察，无 RNG —— 回放安全。
 */
/**
 * §163 中路威胁检测 — 只认「真实正在凿穿」的信号：敌人子弹正位于基地列
 * （col bc..bc+1）向下飞行、弹道与基地之间没有钢铁/水。子弹会一路凿穿砖墙
 * 直抵基地（多砖击穿机制），只有在中路防守点向上开火对消才能拦住它。
 *
 * 敌人存在/面朝下的检测已移除（§163 A/B 实测：敌人路过基地列触发 14-35% 的
 * 常驻锚定，29/35 关变差；enemyCanShootBase 已有 defenseIntercept(550) 覆盖，
 * 权重高于本候选）。纯 World 观察，无 RNG —— 回放安全。
 */
export function laneThreatImpl(self: GodAIInput): boolean {
  return laneShellInColumnImpl(self)
}

/**
 * §163 中路威胁/压制检测 — 只认「真实正在凿穿」的信号：敌人子弹正位于基地列
 * （col bc..bc+1）向下飞行、弹道与基地之间没有钢铁/水。子弹会一路凿穿砖墙
 * 直抵基地（多砖击穿机制），只有在中路防守点向上开火对消才能拦住它。
 *
 * 敌人存在/面朝下的检测已移除（§163 A/B 实测：敌人路过基地列触发 14-35% 的
 * 常驻锚定，29/35 关变差；enemyCanShootBase 已有 defenseIntercept(550) 覆盖，
 * 权重高于本候选）。纯 World 观察，无 RNG —— 回放安全。
 */
export function laneShellInColumnImpl(self: GodAIInput): boolean {
  if (!self.hasBase) return false
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const colMinX = bc * CELL
  const colMaxX = (bc + 2) * CELL
  const baseMaxY = (br + 2) * CELL
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    if (b.dir !== 'down') continue
    const bx = b.x + b.w / 2
    if (bx < colMinX || bx >= colMaxX) continue
    const by = b.y + b.h / 2
    if (by >= baseMaxY) continue // already past the base
    // Bricks don't stop bullets (multi-brick breakthrough); only steel
    // in the column between the bullet and the base would neutralize it.
    // Water does NOT block bullets (§165 fix — same as laneShellAboveImpl).
    const bcol = Math.floor(bx / CELL)
    let blocked = false
    for (let r = Math.floor(by / CELL) + 1; r <= br + 1; r++) {
      const t = w.tileMap.get(bcol, r)
      if (t === 'steel') {
        blocked = true
        break
      }
    }
    if (blocked) continue
    return true
  }
  return false
}

// ============================================================
// §164 中路列旁主动驻守 (proactive mid-lane flank hold, user request
// 2026-08-06: 让 §162 出袋后的玩家优先走中路走廊（而非左侧），在列旁持枪
// 对消). All pure World reads — no RNG, no mutation. Gated by
// params.midLaneHold (0 = OFF, byte-identical).
// ============================================================

/**
 * §164: is the base's own column (cols bc..bc+1) OPEN to the base — i.e., no
 * steel/water anywhere in rows 0..br-1 (above the ring)? When steel/water
 * blocks the column, enemy shells cannot carve down to the eagle through it,
 * so a mid-lane parry hold has zero value (S13-style steel-protected stages).
 * Uncached — 2×24 grid reads, trivial per tick. Pure World read.
 */
export function laneColumnOpenToBaseImpl(self: GodAIInput): boolean {
  if (!self.hasBase) return false
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const grid = w.tileMap.grid
  for (let r = 0; r < br; r++) {
    const row = grid[r]
    const t0 = row[bc]
    if (t0 === 'steel' || t0 === 'water') return false
    const t1 = row[bc + 1]
    if (t1 === 'steel' || t1 === 'water') return false
  }
  return true
}

/**
 * §164: the best PROACTIVE parry hold cell — a standable cell whose x-center
 * (pcx) lies within BULLET-px of the base column x-range (so an UPWARD bullet
 * can overlap a shell travelling down the column: |bx − pcx| < BULLET), in
 * rows 4..midLaneHoldMaxRow (top/mid half — never a spawn-row duel, never a
 * deep-maze hold), CORRIDOR-reachable from the player (no digging in the top
 * plaza — bricks there are the base column itself, R5/R6 spirit). Picks the
 * cell whose pcx is closest to the column center ((bc+1)*CELL — where the
 * (12,0)-style spawn shells fly), then the smallest row/col (deterministic,
 * position-independent — a stable navigation target per terrain state).
 *
 * Cached per tileMap.revision (strict pure memo — terrain mutation bumps the
 * revision the same tick; the player-position-independent selection makes the
 * per-revision cache exact). Returns null when no such cell exists. Pure
 * World read — no RNG.
 */
/** §164: position-independent selection scan — the best standable parry-window
 *  cell in rows 4..min(midLaneHoldMaxRow, br-8). Purely a terrain function, so
 *  it is safe to cache per tileMap.revision. Pure World read. */
function scanParryHoldCellImpl(self: GodAIInput): Cell | null {
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const colMinX = bc * CELL
  const colMaxX = (bc + 2) * CELL
  const colCx = (bc + 1) * CELL
  const maxRow = Math.min(self.params.midLaneHoldMaxRow, br - 8)
  const grid = w.tileMap.grid
  let bestC = -1
  let bestR = -1
  let bestCx = Infinity
  for (let r = 4; r <= maxRow; r++) {
    if (r + 1 >= GRID) break
    for (let c = bc - 1; c <= bc + 2; c++) {
      if (c < 0 || c + 1 >= GRID) continue
      const pcx = c * CELL + CELL / 2
      if (pcx < colMinX - BULLET || pcx >= colMaxX + BULLET) continue
      let blocked = false
      for (let dr = 0; dr <= 1 && !blocked; dr++) {
        const row = grid[r + dr]
        for (let dc = 0; dc <= 1 && !blocked; dc++) {
          const t = row[c + dc]
          if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') {
            blocked = true
          }
        }
      }
      if (blocked) continue
      const cx = Math.abs(pcx - colCx)
      if (cx < bestCx) {
        bestCx = cx
        bestC = c
        bestR = r
      }
    }
  }
  return bestC >= 0 ? { col: bestC, row: bestR } : null
}

/**
 * §164: the best PROACTIVE parry hold cell — see scanParryHoldCellImpl for
 * the selection (standable, x-center within BULLET-px of the column, top/mid
 * half, closest to the column center — deterministic per terrain state).
 *
 * The SELECTION is cached per tileMap.revision (strict pure memo — terrain
 * mutation bumps the revision the same tick); the corridor-reachability check
 * is deliberately per-call (it depends on the PLAYER's cell, which changes
 * constantly, and carvePathInfoCached makes repeat calls from the same cell
 * free). A player already ON the cell trivially qualifies. Returns null when
 * no such cell exists or none is corridor-reachable now. Pure World read —
 * no RNG.
 */
export function findParryHoldCellImpl(self: GodAIInput, pc: Cell): Cell | null {
  if (!self.hasBase) return null
  const rev = self.world.tileMap.revision
  if (self._parryHoldRev !== rev) {
    self._parryHoldRev = rev
    self._parryHoldCell = scanParryHoldCellImpl(self)
  }
  const cell = self._parryHoldCell
  if (!cell) return null
  if (pc.col === cell.col && pc.row === cell.row) return cell
  const info = carvePathInfoCached(self, pc, cell)
  if (info.path && info.path.length > 0 && info.corridor) return cell
  return null
}

/**
 * §164: is any live enemy within `dist` cells (manhattan) of the base lane
 * (cols bc-1..bc+2 × rows 0..br-1 — the column + one margin on each side,
 * above the ring)? Keeps the mid-lane hold active when no shell is in the
 * column YET — the carver is on its way. Pure World read.
 */
export function enemyNearLaneImpl(self: GodAIInput, dist: number): boolean {
  if (!self.hasBase || dist <= 0) return false
  const w = self.world
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    const dx = tc.col < bc - 1 ? bc - 1 - tc.col : tc.col > bc + 2 ? tc.col - (bc + 2) : 0
    const dy = tc.row > br - 1 ? tc.row - (br - 1) : 0
    if (dx + dy <= dist) return true
  }
  return false
}

// ============================================================
// §189 / 开局联通清墙 (base connectivity clear, user request 2026-08-11).
//
// At game start, the player observes the lower-half layout and checks whether
// the three key strategic points — base-left, base-right, and the central
// defense post — are connected by SMOOTH corridor paths. If brick walls
// partition these points, the player proactively fires to clear a through-
// route BEFORE enemies arrive, so that when a threat appears on either side
// the player can quickly reposition without pathfinding failure.
//
// All functions are pure reads of World state + params (no RNG, no
// mutation) — gated by params.baseConnectClearMode (0 = OFF, byte-identical).
// ============================================================

/**
 * §189: find a standable cell in the lower-half wing nearest to the base.
 * `side` = -1 for left wing (cols 0..bc-3), +1 for right wing (cols bc+4..GRID-2).
 * The ±1 column gap from bc accounts for the 2×2 tank footprint:
 * a tank at col bc-2 has footprint cols bc-2..bc-1 which overlaps the
 * base columns, so the leftmost safe column is bc-3. Similarly bc+4
 * on the right. Scans rows br-4..br (the lower-half band where the
 * player needs mobility), nearest to the base first. A "standable" cell
 * has no brick/steel/water/base in its 2×2 tank footprint. Returns null
 * when no standable cell exists. Pure World read — no RNG.
 */
export function findWingAnchorImpl(self: GodAIInput, side: -1 | 1): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const grid = tm.grid
  // Column range: left wing 0..bc-3, right wing bc+4..GRID-2 (tank footprint
  // needs col+1 < GRID). Scan nearest to the base first.
  const colStart = side < 0 ? bc - 3 : bc + 4
  const colEnd = side < 0 ? 0 : GRID - 2
  const colStep = side < 0 ? -1 : 1
  // Row range: br-4..br (lower-half band). Scan nearest to base first.
  for (let dr = 0; dr <= 4; dr++) {
    const r = br - dr
    if (r < 0 || r + 1 >= GRID) continue
    for (let c = colStart; ; c += colStep) {
      if (c < 0 || c + 1 >= GRID) break
      if ((side < 0 && c < colEnd) || (side > 0 && c > colEnd)) break
      // Check 2×2 footprint standability.
      let blocked = false
      for (let fr = 0; fr <= 1 && !blocked; fr++) {
        const row = grid[r + fr]
        for (let fc = 0; fc <= 1 && !blocked; fc++) {
          const t = row[c + fc]
          if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') blocked = true
        }
      }
      if (!blocked) return { col: c, row: r }
    }
  }
  return null
}

/**
 * §189: the carve target for base-connectivity clearing. Checks whether the
 * central defense post is corridor-connected to the left and right wing
 * anchors. If either connection requires digging through brick, returns the
 * disconnected wing anchor as the carve target — the player should navigate
 * toward it, firing to clear walls along the way.
 *
 * Priority: the wing that is NOT corridor-connected wins. If both are
 * disconnected, the nearer to the player (by manhattan distance) wins — the
 * player clears that side first, then the other on a later tick once the
 * first corridor is open.
 *
 * Returns null when both wings are corridor-connected to the post (no
 * carving needed), or when the post / wing anchors don't exist. Pure World
 * read — no RNG.
 */
export function findConnectCarveTargetImpl(self: GodAIInput, pc: Cell): Cell | null {
  if (!self.hasBase) return null
  const post = carvePostImpl(self)
  if (!post) return null
  const left = findWingAnchorImpl(self, -1)
  const right = findWingAnchorImpl(self, 1)
  if (!left && !right) return null

  // Check corridor connectivity from post to each wing.
  let leftConnected = true
  let rightConnected = true
  if (left) {
    const info = digPathInfoCached(self, post, left)
    leftConnected = info.corridor && info.path !== null && info.path.length > 0
  }
  if (right) {
    const info = digPathInfoCached(self, post, right)
    rightConnected = info.corridor && info.path !== null && info.path.length > 0
  }

  // Both connected → no carving needed.
  if (leftConnected && rightConnected) return null

  // Pick the disconnected wing to carve toward. If both disconnected, pick
  // the nearer to the player (clear that side first).
  if (left && !leftConnected && (!right || rightConnected)) return left
  if (right && !rightConnected && (!left || leftConnected)) return right
  // Both disconnected — nearer wing wins.
  if (left && right) {
    const distL = manhattan(pc.col, pc.row, left.col, left.row)
    const distR = manhattan(pc.col, pc.row, right.col, right.row)
    return distL <= distR ? left : right
  }
  return left ?? right
}

/**
 * §189: per-cell cost array for the dig-path A*. Only ring bricks cost 1e9
 * (impassable — the fire control refuses to break them, so routing through
 * them would create a dead-end). Base-column bricks and regular bricks stay
 * at 0 (passable in breakBrick mode with the normal step cost). Cached per
 * tileMap.revision — same discipline as buildCarveCosts.
 */
export function buildDigCosts(self: GodAIInput): Float64Array {
  const rev = self.world.tileMap.revision
  if (self._digCosts && self._digCostsRev === rev) return self._digCosts
  const costs = new Float64Array(GRID * GRID)
  const grid = self.world.tileMap.grid
  // For each cell, check if any cell in its 2×2 tank footprint is a ring
  // brick. If so, the cell is effectively impassable — the player can't
  // break ring bricks, so standing on a cell whose footprint includes one
  // is a dead-end. This forces A* to route AROUND the ring, not through
  // cells adjacent to it.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      if (c + 1 >= GRID || r + 1 >= GRID) continue
      let ringInFootprint = false
      for (let dr = 0; dr <= 1 && !ringInFootprint; dr++) {
        const grow = grid[r + dr]
        for (let dc = 0; dc <= 1 && !ringInFootprint; dc++) {
          if (grow[c + dc] === 'brick' && isCarveRingBrickImpl(self, c + dc, r + dr)) {
            ringInFootprint = true
          }
        }
      }
      if (ringInFootprint) {
        costs[r * GRID + c] = 1e9
      }
    }
  }
  self._digCosts = costs
  self._digCostsRev = rev
  return costs
}

/**
 * §189: cached dig-path query for base connectivity clear. Unlike
 * carvePathInfoCached (which uses pathCarveSafeImpl that rejects paths
 * whose 2×2 footprint touches ring bricks), this uses findPath with
 * breakBrick directly — the player CAN stand adjacent to ring bricks
 * without breaking them. Ring bricks are made impassable via threatCosts
 * (buildDigCosts) so the A* routes AROUND the ring, not through it.
 * The fire control (carveFire) already prevents firing at ring/base walls.
 * Cached per (from, to, tileMap.revision) with a safety timer.
 * Pure World read — no RNG.
 */
export function digPathInfoCached(self: GodAIInput, from: Cell, to: Cell): CarvePathInfo {
  const rev = self.world.tileMap.revision
  self._digPathTimer--
  if (
    self._digPathCacheValid &&
    self._digPathFromCol === from.col &&
    self._digPathFromRow === from.row &&
    self._digPathToCol === to.col &&
    self._digPathToRow === to.row &&
    self._digPathRev === rev &&
    self._digPathTimer > 0
  ) {
    return { path: self._digPathCache, corridor: self._digPathCorridor }
  }

  let path: Direction[] | null = null
  let corridor = false
  if (!(from.col === to.col && from.row === to.row)) {
    // 1. Corridor first — a smooth route needs no digging.
    const c = findPath(self.world.tileMap, from, to)
    if (c && c.length > 0) {
      path = c
      corridor = true
    } else {
      // 2. Dig path — breakBrick with ring bricks made impassable via
      // threatCosts. The fire control (carveFire) prevents firing at
      // ring/base walls; the cost array ensures A* routes AROUND the ring.
      path = findPath(self.world.tileMap, from, to, {
        breakBrick: true,
        threatCosts: buildDigCosts(self),
      })
    }
  }

  self._digPathCache = path
  self._digPathCorridor = corridor
  self._digPathCacheValid = true
  self._digPathFromCol = from.col
  self._digPathFromRow = from.row
  self._digPathToCol = to.col
  self._digPathToRow = to.row
  self._digPathRev = rev
  self._digPathTimer = self.params.carveReplanTicks
  return { path, corridor }
}
