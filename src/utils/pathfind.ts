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
}

// ---- internal helpers -------------------------------------------------------

/** Key for Set/Map storage. */
function key(col: number, row: number): string {
  return `${col},${row}`
}

/**
 * Can a 2×2-block tank occupy the position whose top-left sub-block is
 * (col, row)? Checks all four sub-blocks for blocking terrain.
 */
function isPassable(tileMap: TileMap, col: number, row: number, ignoreWater: boolean): boolean {
  // A 2×2 tank needs cols [col, col+1] and rows [row, row+1] inside the grid.
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      const type = tileMap.get(col + dc, row + dr)
      if (TileMap.blocksTank(type)) {
        if (ignoreWater && type === 'water') continue
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

  // Quick reject: start or goal impassable.
  if (!isPassable(tileMap, from.col, from.row, ignoreWater)) return null
  if (!isPassable(tileMap, to.col, to.row, ignoreWater)) return null
  if (from.col === to.col && from.row === to.row) return []

  // A* with a simple binary-heap-free approach. The grid is at most 26×26 =
  // 676 nodes, so a flat-array open set with linear scan is fast enough and
  // keeps the code simple (no heap dependency).
  const cameFrom = new Map<string, { parent: string; dir: Direction }>()
  const gScore = new Map<string, number>()
  const fScore = new Map<string, number>()
  const open = new Set<string>()

  const startKey = key(from.col, from.row)
  const goalKey = key(to.col, to.row)

  gScore.set(startKey, 0)
  fScore.set(startKey, manhattan(from.col, from.row, to.col, to.row))
  open.add(startKey)

  while (open.size > 0) {
    // Find the node with the lowest fScore in the open set.
    let currentKey = ''
    let currentF = Infinity
    for (const k of open) {
      const f = fScore.get(k) ?? Infinity
      if (f < currentF) {
        currentF = f
        currentKey = k
      }
    }

    if (currentKey === goalKey) {
      // Reconstruct path.
      const path: Direction[] = []
      let ck = currentKey
      while (ck !== startKey) {
        const cf = cameFrom.get(ck)!
        path.push(cf.dir)
        ck = cf.parent
      }
      path.reverse()
      return path
    }

    open.delete(currentKey)
    const [cc, cr] = currentKey.split(',').map(Number)

    for (const [dc, dr, dir] of STEPS) {
      const nc = cc + dc
      const nr = cr + dr
      if (!isPassable(tileMap, nc, nr, ignoreWater)) continue
      const nk = key(nc, nr)
      const tentativeG = (gScore.get(currentKey) ?? Infinity) + 1
      if (tentativeG < (gScore.get(nk) ?? Infinity)) {
        cameFrom.set(nk, { parent: currentKey, dir })
        gScore.set(nk, tentativeG)
        fScore.set(nk, tentativeG + manhattan(nc, nr, to.col, to.row))
        open.add(nk)
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
