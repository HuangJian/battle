// ================================================================
// grid-search.ts — offline grid connectivity helpers.
//
// Split out of utils/pathfind.ts (plan/refactor.agy.md §2.7): these are
// generic utilities consumed by tools/ (level generator, evaluator) and
// tests. The AI A* engine lives in ai/god/pathfind.ts; utils/pathfind.ts
// re-exports both halves for compatibility.
// ================================================================
import { CELL, GRID } from '../constants'
import { DIR_DX, DIR_DY } from './direction'
import type { TileMap } from '../game/TileMap'

export interface Cell {
  col: number
  row: number
}

/** Optional traversal constraints. */

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
 *
 * Inlined `tileMap.get` + `TileMap.blocksTank` (perf): isPassable is called
 * 4× per A* node expansion in findPath, which is called every God-AI replan
 * interval. Direct grid[row][col] access skips two method calls per cell.
 * Bounds check mirrors `TileMap.get`'s 'steel' fallback for OOB cells.
 */
/** Exported for the A* engine (ai/god/pathfind.ts) which shares the check. */
export function isPassable(
  tileMap: TileMap,
  col: number,
  row: number,
  ignoreWater: boolean,
  breakBrick = false,
): boolean {
  // A 2×2 tank needs cols [col, col+1] and rows [row, row+1] inside the grid.
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  const grid = tileMap.grid
  for (let dr = 0; dr <= 1; dr++) {
    const r = row + dr
    const grow = grid[r]
    for (let dc = 0; dc <= 1; dc++) {
      const type = grow[col + dc]
      // Inlined TileMap.blocksTank: brick|steel|water|base block tanks.
      if (type === 'brick') {
        if (breakBrick) continue
        return false
      }
      if (type === 'steel' || type === 'base') return false
      if (type === 'water') {
        if (ignoreWater) continue
        return false
      }
    }
  }
  return true
}


const STEP_DX = DIR_DX
const STEP_DY = DIR_DY

export function isReachable(tileMap: TileMap, from: Cell, to: Cell): boolean {
  if (!isPassable(tileMap, from.col, from.row, false)) return false
  if (!isPassable(tileMap, to.col, to.row, false)) return false
  if (from.col === to.col && from.row === to.row) return true

  const visited = new Set<string>()
  const queue: Cell[] = [{ ...from }]
  visited.add(key(from.col, from.row))

  while (queue.length > 0) {
    const cur = queue.shift()!
    for (let s = 0; s < 4; s++) {
      const nc = cur.col + STEP_DX[s]
      const nr = cur.row + STEP_DY[s]
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
    for (let s = 0; s < 4; s++) {
      const nc = cur.col + STEP_DX[s]
      const nr = cur.row + STEP_DY[s]
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

/**
 * Convert a pixel position to a grid cell (top-left sub-block of the 2×2
 * tank footprint). Convenience for callers working in pixel space.
 */
export function pxToCell(x: number, y: number): Cell {
  return { col: Math.floor(x / CELL), row: Math.floor(y / CELL) }
}