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
  /**
   * §69: Per-cell threat cost array (GRID*GRID Float64Array). When provided,
   * each cell's threat cost is added to the A* step cost, making the pathfinder
   * naturally prefer routes that avoid cells where enemy bullets are expected
   * to arrive at the same time as the player. 0 = no threat (safe cell).
   *
   * The array is indexed by `row * GRID + col` (same as the A* internal
   * cellKey). The producer (computeThreatCostsImpl) was archived in
   * src/ai/god/experimental.ts (M0.5 retirement) — this field is kept for
   * the v2 survive candidate / A* risk-aversion reuse (design §4.4).
   *
   * Unlike post-hoc diversion (§68-v2), this bakes threat avoidance into the
   * path itself — A* finds the optimal trade-off between path length and
   * safety, avoiding the "divert into a dead-end" problem.
   */
  threatCosts?: Float64Array

  /**
   * §187: When set, the 2×2-block tank footprint at this cell is treated as
   * impassable AND indestructible (blocks even in breakBrick mode). Used by
   * the guard and P2 brains to route A* around the player tank — prevents
   * the guard/player mutual-block deadlock (S7@seed54: 23.9s stuck).
   * The overlap check is |nc - col| <= 1 && |nr - row| <= 1 (2×2 footprint).
   */
  blockedCell?: Cell | null
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
 *
 * Inlined `tileMap.get` + `TileMap.blocksTank` (perf): isPassable is called
 * 4× per A* node expansion in findPath, which is called every God-AI replan
 * interval. Direct grid[row][col] access skips two method calls per cell.
 * Bounds check mirrors `TileMap.get`'s 'steel' fallback for OOB cells.
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

/** The four cardinal directions as flat arrays (perf): tuple destructuring
 * `const [dc, dr] = STEPS[s]` allocates an iterator per expansion in the A*
 * inner loop. Two parallel typed arrays let the loop read `STEP_DC[s]` and
 * `STEP_DR[s]` directly — no iterator, no tuple, no allocation. The
 * Direction label is recovered from the index via STEP_DIR (used only at
 * path reconstruction, not in the hot loop). */
const STEP_DC: readonly number[] = [0, 0, -1, 1]
const STEP_DR: readonly number[] = [-1, 1, 0, 0]
const STEP_DIR: readonly Direction[] = ['up', 'down', 'left', 'right']

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
// Binary min-heap (lazy deletion) for the A* open set. Keyed by (fScore,
// firstSeq) so the extraction order is identical to the old linear-scan
// (lowest f, earliest-first-push tie-break) → byte-for-byte same path — but
// extract-min is O(log n) instead of O(n). Stale entries (from relaxations that
// re-push a node) are skipped on pop via closed[]; firstSeq is assigned ONCE per
// node (on first push) so a later re-push keeps the original tie-break order.
//
// Storage is three parallel preallocated typed arrays (struct-of-arrays) rather
// than a growable number[] of triples: no `push`/`length=` traffic on a JS array
// and no boxing. Capacity is provably sufficient — a node is only (re-)pushed
// when its gScore strictly improves, which can happen at most once per incoming
// edge (4-connected grid), so total pushes ≤ 4·PF_N + 1 (start node).
const PF_HEAP_CAP = PF_N * 4 + 8
const _pfHeapF = new Float64Array(PF_HEAP_CAP)
const _pfHeapS = new Int32Array(PF_HEAP_CAP)
const _pfHeapK = new Int32Array(PF_HEAP_CAP)
let _pfHeapSize = 0
const _pfFirstSeq = new Int32Array(PF_N)

function pfLess(a: number, b: number): boolean {
  const fa = _pfHeapF[a]
  const fb = _pfHeapF[b]
  if (fa !== fb) return fa < fb
  return _pfHeapS[a] < _pfHeapS[b]
}
function pfSwap(a: number, b: number): void {
  const f = _pfHeapF[a]
  const s = _pfHeapS[a]
  const k = _pfHeapK[a]
  _pfHeapF[a] = _pfHeapF[b]
  _pfHeapS[a] = _pfHeapS[b]
  _pfHeapK[a] = _pfHeapK[b]
  _pfHeapF[b] = f
  _pfHeapS[b] = s
  _pfHeapK[b] = k
}
function pfPush(f: number, seq: number, key: number): void {
  let i = _pfHeapSize++
  _pfHeapF[i] = f
  _pfHeapS[i] = seq
  _pfHeapK[i] = key
  while (i > 0) {
    const p = (i - 1) >> 1
    if (pfLess(i, p)) {
      pfSwap(i, p)
      i = p
    } else break
  }
}
/** Extract-min. Returns the cell key, or -1 when the heap is empty. */
function pfPop(): number {
  const n = _pfHeapSize
  if (n === 0) return -1
  const rkey = _pfHeapK[0]
  const li = n - 1
  _pfHeapSize = li
  if (li === 0) return rkey
  _pfHeapF[0] = _pfHeapF[li]
  _pfHeapS[0] = _pfHeapS[li]
  _pfHeapK[0] = _pfHeapK[li]
  let i = 0
  for (;;) {
    const l = 2 * i + 1
    if (l >= li) break
    const r = l + 1
    let smallest = pfLess(l, i) ? l : i
    if (r < li && pfLess(r, smallest)) smallest = r
    if (smallest === i) break
    pfSwap(i, smallest)
    i = smallest
  }
  return rkey
}

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
  // §69: optional per-cell threat costs. When provided, added to step cost.
  const threatCosts = constraints?.threatCosts
  // §187: optional blocked cell (player tank) — impassable even with breakBrick.
  const blkCell = constraints?.blockedCell ?? null
  const blkCol = blkCell ? blkCell.col : -99
  const blkRow = blkCell ? blkCell.row : -99
  const hasBlk = blkCell !== null

  // Quick reject: start or goal impassable.
  if (!isPassable(tileMap, from.col, from.row, ignoreWater, breakBrick)) return null
  if (!isPassable(tileMap, to.col, to.row, ignoreWater, breakBrick)) return null
  if (from.col === to.col && from.row === to.row) return []

  // A* over a 26×26 grid (≤ 676 nodes). State lives in flat typed arrays
  // indexed by integer cell key (row*GRID+col) — no Map/Set/string churn in
  // the hot loop. The open set is a binary min-heap (lazy deletion) keyed by
  // (fScore, firstSeq): extract-min is O(log n), and the (lowest-f,
  // earliest-first-push) tie-break is identical to the old linear-scan, so the
  // returned Direction[] sequence is byte-for-byte the same as before. Only the
  // extraction cost changed — search result is preserved.
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
  const firstSeq = _pfFirstSeq
  firstSeq.fill(-1)
  _pfHeapSize = 0
  let seqCounter = 0

  const startKey = from.row * GRID + from.col
  const goalKey = to.row * GRID + to.col
  // Hoist goal coords (used in heuristic every neighbor expansion).
  const toCol = to.col
  const toRow = to.row
  const grid = tileMap.grid

  gScore[startKey] = 0
  fScore[startKey] = Math.abs(from.col - toCol) + Math.abs(from.row - toRow)
  firstSeq[startKey] = seqCounter++
  inOpen[startKey] = 1
  pfPush(fScore[startKey], firstSeq[startKey], startKey)

  // Two specialized hot loops: the common case (breakBrick=false) inlines a
  // constant stepCost=1 and skips the brick-footprint scan, saving 4
  // tileMap.get calls per neighbor. breakBrick=true keeps the scan.
  if (!breakBrick) {
    for (;;) {
      const currentKey = pfPop()
      if (currentKey < 0) break
      if (closed[currentKey]) continue // lazy deletion: skip stale entries
      if (currentKey === goalKey) {
        // Reconstruct path by walking cameFrom back to the start.
        const path: Direction[] = []
        let ck = currentKey
        while (ck !== startKey) {
          path.push(STEP_DIR[cameDir[ck]])
          ck = cameFrom[ck]
        }
        path.reverse()
        return path
      }

      closed[currentKey] = 1
      const cc = currentKey % GRID
      const cr = (currentKey - cc) / GRID

      for (let s = 0; s < 4; s++) {
        const nc = cc + STEP_DC[s]
        const nr = cr + STEP_DR[s]
        // Inline isPassable (breakBrick=false branch): bounds + 2×2 footprint
        // scan against grid directly. Avoids the function call and the
        // breakBrick parameter check on every neighbor.
        if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
        let blocked = false
        for (let dr = 0; dr <= 1 && !blocked; dr++) {
          const grow = grid[nr + dr]
          for (let dc = 0; dc <= 1; dc++) {
            const type = grow[nc + dc]
            if (type === 'brick' || type === 'steel' || type === 'base') {
              blocked = true
              break
            }
            if (type === 'water') {
              if (!ignoreWater) {
                blocked = true
                break
              }
            }
          }
        }
        if (blocked) continue
        // §187: blocked cell (player) — impassable even in corridor mode.
        if (hasBlk && Math.abs(nc - blkCol) <= 1 && Math.abs(nr - blkRow) <= 1) continue
        const nk = nr * GRID + nc
        if (closed[nk]) continue
        // stepCost=1 in this branch (breakBrick=false). §69: add threat cost.
        const tentativeG = gScore[currentKey] + 1 + (threatCosts ? threatCosts[nk] : 0)
        if (tentativeG < gScore[nk]) {
          cameFrom[nk] = currentKey
          cameDir[nk] = s
          gScore[nk] = tentativeG
          const nf = tentativeG + Math.abs(nc - toCol) + Math.abs(nr - toRow)
          fScore[nk] = nf
          if (firstSeq[nk] === -1) {
            firstSeq[nk] = seqCounter++
            inOpen[nk] = 1
          }
          pfPush(nf, firstSeq[nk], nk)
        }
      }
    }
  } else {
    // breakBrick=true branch — keeps the brick-footprint stepCost scan.
    for (;;) {
      const currentKey = pfPop()
      if (currentKey < 0) break
      if (closed[currentKey]) continue
      if (currentKey === goalKey) {
        const path: Direction[] = []
        let ck = currentKey
        while (ck !== startKey) {
          path.push(STEP_DIR[cameDir[ck]])
          ck = cameFrom[ck]
        }
        path.reverse()
        return path
      }

      closed[currentKey] = 1
      const cc = currentKey % GRID
      const cr = (currentKey - cc) / GRID

      for (let s = 0; s < 4; s++) {
        const nc = cc + STEP_DC[s]
        const nr = cr + STEP_DR[s]
        if (!isPassable(tileMap, nc, nr, ignoreWater, true)) continue
        // §187: blocked cell (player) — impassable even in breakBrick mode.
        if (hasBlk && Math.abs(nc - blkCol) <= 1 && Math.abs(nr - blkRow) <= 1) continue
        const nk = nr * GRID + nc
        if (closed[nk]) continue
        // Inline stepCost: 5 if any sub-block is brick, else 1.
        // §69: add threat cost.
        let cost = 1
        for (let dr = 0; dr <= 1; dr++) {
          const grow = grid[nr + dr]
          for (let dc = 0; dc <= 1; dc++) {
            if (grow[nc + dc] === 'brick') {
              cost = 5
              break
            }
          }
          if (cost === 5) break
        }
        const tentativeG = gScore[currentKey] + cost + (threatCosts ? threatCosts[nk] : 0)
        if (tentativeG < gScore[nk]) {
          cameFrom[nk] = currentKey
          cameDir[nk] = s
          gScore[nk] = tentativeG
          const nf = tentativeG + Math.abs(nc - toCol) + Math.abs(nr - toRow)
          fScore[nk] = nf
          if (firstSeq[nk] === -1) {
            firstSeq[nk] = seqCounter++
            inOpen[nk] = 1
          }
          pfPush(nf, firstSeq[nk], nk)
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
    for (let s = 0; s < 4; s++) {
      const nc = cur.col + STEP_DC[s]
      const nr = cur.row + STEP_DR[s]
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
      const nc = cur.col + STEP_DC[s]
      const nr = cur.row + STEP_DR[s]
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
