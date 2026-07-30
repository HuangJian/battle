import { TileMap } from '../game/TileMap'
import type { Direction } from '../constants'
import { GRID, CELL } from '../constants'

/**
 * pathfind.ts — pure-function pathfinding utilities.
 *
 * Shared by the God AI (navigation) and the level generator (reachability
 * validation). Both need to answer "can a 2×2-block tank get from A to B?"
 * on the same 26×26 sub-block grid the TileMap owns.
 *
 * Design principles (AGENTS §2.5, §5):
 * - Pure functions — no World mutation, no hidden state.
 * - Lives in `utils/` because the game layer (God AI) and the tools layer
 *   (level generator) both depend on it.
 * - A tank occupies a 2×2 area of sub-blocks; pathfinding checks the full
 *   footprint at every candidate position.
 */

/** A grid cell in sub-block coordinates. */
export interface Cell {
  col: number
  row: number
}

/** Optional traversal constraints. */
export interface PathConstraints {
  /** If true, water does not block movement (boat power-up / amphibious). */
  ignoreWater?: boolean
  /**
   * If true, treat **brick** as passable terrain with a higher traversal cost
   * (wall-breaking / "dig" mode). The God AI uses this to find paths through
   * brick walls when no pure-corridor path exists — the player follows the
   * path and fires at the bricks to clear them. Steel, water, and base remain
   * impassable.
   */
  breakBrick?: boolean
}

// ---- internal helpers -------------------------------------------------------

/**
 * String key for a grid cell, used by the **offline** helpers
 * (`isReachable`, `floodFill`) whose returned `Set<string>` is part of a
 * stable external contract (tests + level generator + evaluator). The hot
 * `findPath` uses its own integer key (see `cellKey`) to avoid string
 * allocation in its inner loop.
 */
function key(col: number, row: number): string {
  return `${col},${row}`
}

/**
 * Can a 2×2-block tank occupy the position whose top-left sub-block is
 * (col, row)? Checks all four sub-blocks for blocking terrain.
 *
 * `breakBrick`: when true, brick is treated as passable (the player can fire
 * to destroy it). Steel, water, and base always block.
 */

function isPassable(
  tileMap: TileMap,
  col: number,
  row: number,
  ignoreWater: boolean,
  breakBrick = false,
): boolean {
  // A 2×2 tank needs cols [col, col+1] and rows [row, row+1] inside the grid.
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      const type = tileMap.get(col + dc, row + dr)
      if (TileMap.blocksTank(type)) {
        if (ignoreWater && type === 'water') continue
        if (breakBrick && type === 'brick') continue
        return false
      }
    }
  }
  return true
}

/** The four cardinal directions as (dcol, drow, Direction) tuples. */
const STEPS: ReadonlyArray<readonly [number, number, Direction]> = [
  [0, -1, 'up'],
  [0, 1, 'down'],
  [-1, 0, 'left'],
  [1, 0, 'right'],
]

// ---- public API --------------------------------------------------------------

// Module-level reusable A* buffers (perf): findPath is called from the God AI
// every replan interval and from navigateTowards. Each call previously
// allocated 6 typed arrays (≈11 KB) that became garbage immediately after.
// findPath is synchronous and never reentrant (God AI think() and the offline
// level generator are the only callers, never concurrent), so the buffers can
// be safely reused across calls. The search result is byte-for-byte identical
// — only the allocations changed.
const PF_N = GRID * GRID
const _pfGScore = new Float64Array(PF_N)
const _pfFScore = new Float64Array(PF_N)
const _pfCameFrom = new Int32Array(PF_N)
const _pfCameDir = new Uint8Array(PF_N)
const _pfInOpen = new Uint8Array(PF_N)
const _pfClosed = new Uint8Array(PF_N)
const _pfOpenList: number[] = []

/**
 * A* pathfinding: returns the sequence of `Direction`s to move a 2×2-block
 * tank from `from` to `to`, or `null` if no path exists.
 *
 * Each step moves the tank one CELL (16px) in the given direction. The path
 * is optimal (shortest) with Manhattan-distance heuristic on a 4-connected
 * grid.
 */
export function findPath(
  tileMap: TileMap,
  from: Cell,
  to: Cell,
  constraints?: PathConstraints,
): Direction[] | null {
  const ignoreWater = constraints?.ignoreWater ?? false
  const breakBrick = constraints?.breakBrick ?? false

  // Quick reject: start or goal impassable.
  if (!isPassable(tileMap, from.col, from.row, ignoreWater, breakBrick)) return null
  if (!isPassable(tileMap, to.col, to.row, ignoreWater, breakBrick)) return null
  if (from.col === to.col && from.row === to.row) return []

  // A* over a 26×26 grid (≤ 676 nodes). State lives in flat typed arrays
  // indexed by integer cell key (row*GRID+col) — no Map/Set/string churn in
  // the hot loop. The open set is scanned linearly in **insertion order**
  // (entries are never reordered, only flagged closed), so the lowest-f
  // tie-break is identical to the original Set-iterated implementation and
  // the returned Direction[] sequence is byte-for-byte the same. Only the
  // allocations changed — search result is preserved.
  //
  // Reusable module-level buffers are reset here. Only the 3 arrays whose
  // "unvisited" default is non-zero need resetting (gScore→Infinity,
  // closed→0, inOpen→0). fScore/cameFrom/cameDir are only read for cells
  // discovered this call (they're written before read), so stale values from
  // a previous call are never observed.
  const gScore = _pfGScore
  gScore.fill(Infinity)
  const fScore = _pfFScore
  const cameFrom = _pfCameFrom
  const cameDir = _pfCameDir
  const inOpen = _pfInOpen
  inOpen.fill(0)
  const closed = _pfClosed
  closed.fill(0)
  const openList = _pfOpenList
  openList.length = 0
  // Local integer key for the hot loop (kept separate from `key()` so the
  // offline helpers can keep their string-keyed external contract).
  const cellKey = (col: number, row: number): number => row * GRID + col

  const startKey = cellKey(from.col, from.row)
  const goalKey = cellKey(to.col, to.row)

  // In breakBrick mode, stepping onto a brick cell costs more (5 instead
  // of 1) — the player must fire to clear it, which takes time + bullets.
  // This makes A* prefer corridor routes and only dig through walls when no
  // open path exists, which is exactly the desired behavior.
  const stepCost = (col: number, row: number): number => {
    if (!breakBrick) return 1
    // Check if any sub-block of the 2×2 footprint is brick.
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (tileMap.get(col + dc, row + dr) === 'brick') return 5
      }
    }
    return 1
  }

  gScore[startKey] = 0
  fScore[startKey] = manhattan(from.col, from.row, to.col, to.row)
  inOpen[startKey] = 1
  openList.push(startKey)

  while (openList.length > 0) {
    // Lowest fScore; on ties keep the earliest-inserted entry — matches the
    // original Set iteration order so the chosen path is unchanged.
    let currentKey = -1
    let currentF = Infinity
    for (let i = 0; i < openList.length; i++) {
      const k = openList[i]
      if (closed[k]) continue
      const f = fScore[k]
      if (f < currentF) {
        currentF = f
        currentKey = k
      }
    }
    if (currentKey === -1) break // open set exhausted, no path

    if (currentKey === goalKey) {
      // Reconstruct path by walking cameFrom back to the start.
      const path: Direction[] = []
      let ck = currentKey
      while (ck !== startKey) {
        path.push(STEPS[cameDir[ck]][2])
        ck = cameFrom[ck]
      }
      path.reverse()
      return path
    }

    closed[currentKey] = 1
    inOpen[currentKey] = 0
    const cc = currentKey % GRID
    const cr = (currentKey - cc) / GRID

    for (let s = 0; s < STEPS.length; s++) {
      const [dc, dr] = STEPS[s]
      const nc = cc + dc
      const nr = cr + dr
      if (!isPassable(tileMap, nc, nr, ignoreWater, breakBrick)) continue
      const nk = cellKey(nc, nr)
      if (closed[nk]) continue
      const cost = stepCost(nc, nr)
      const tentativeG = gScore[currentKey] + cost
      if (tentativeG < gScore[nk]) {
        cameFrom[nk] = currentKey
        cameDir[nk] = s
        gScore[nk] = tentativeG
        fScore[nk] = tentativeG + manhattan(nc, nr, to.col, to.row)
        if (!inOpen[nk]) {
          inOpen[nk] = 1
          openList.push(nk)
        }
      }
    }
  }

  return null // no path found
}

/**
 * BFS reachability check: returns `true` if a 2×2-block tank can travel from
 * `from` to `to` on passable terrain.
 */
export function isReachable(tileMap: TileMap, from: Cell, to: Cell): boolean {
  if (!isPassable(tileMap, from.col, from.row, false)) return false
  if (!isPassable(tileMap, to.col, to.row, false)) return false
  if (from.col === to.col && from.row === to.row) return true

  const visited = new Set<string>()
  const queue: Cell[] = [{ ...from }]
  visited.add(key(from.col, from.row))

  while (queue.length > 0) {
    const cur = queue.shift()!
    for (const [dc, dr, _dir] of STEPS) {
      const nc = cur.col + dc
      const nr = cur.row + dr
      const nk = key(nc, nr)
      if (visited.has(nk)) continue
      if (!isPassable(tileMap, nc, nr, false)) continue
      if (nc === to.col && nr === to.row) return true
      visited.add(nk)
      queue.push({ col: nc, row: nr })
    }
  }
  return false
}

/**
 * Flood-fill: returns the set of all cells reachable from `from` (as
 * `"col,row"` strings). Used by the level generator to verify map connectivity
 * — every spawn point and the base should be in the same connected component.
 */
export function floodFill(tileMap: TileMap, from: Cell): Set<string> {
  const reachable = new Set<string>()
  if (!isPassable(tileMap, from.col, from.row, false)) return reachable

  const queue: Cell[] = [{ ...from }]
  reachable.add(key(from.col, from.row))

  while (queue.length > 0) {
    const cur = queue.shift()!
    for (const [dc, dr] of STEPS) {
      const nc = cur.col + dc
      const nr = cur.row + dr
      const nk = key(nc, nr)
      if (reachable.has(nk)) continue
      if (!isPassable(tileMap, nc, nr, false)) continue
      reachable.add(nk)
      queue.push({ col: nc, row: nr })
    }
  }
  return reachable
}

// ---- pure utility ------------------------------------------------------------

/** Manhattan distance in grid cells. */
function manhattan(c1: number, r1: number, c2: number, r2: number): number {
  return Math.abs(c1 - c2) + Math.abs(r1 - r2)
}

/**
 * Convert a pixel position to a grid cell (top-left sub-block of the 2×2
 * tank footprint). Convenience for callers working in pixel space.
 */
export function pxToCell(x: number, y: number): Cell {
  return { col: Math.floor(x / CELL), row: Math.floor(y / CELL) }
}
