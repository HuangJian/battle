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

/**
 * (perf §130) Leading-edge sub-blocks per step direction.
 *
 * A tank occupies a 2×2 footprint. Stepping one sub-block from a cell whose
 * footprint is ALREADY known passable, the candidate footprint overlaps the
 * current one in exactly two sub-blocks — those cannot block. Only the two
 * sub-blocks on the leading edge are new, so the 2×2 scan collapses to two
 * reads with identical results.
 *
 * Offsets are relative to the candidate top-left (nc, nr), indexed by step:
 *   0 up    → new row nr    : (0,0) (1,0)
 *   1 down  → new row nr+1  : (0,1) (1,1)
 *   2 left  → new col nc    : (0,0) (0,1)
 *   3 right → new col nc+1  : (1,0) (1,1)
 *
 * The precondition (current footprint passable) holds for every popped cell:
 * the start is validated by the quick-reject, and every other cell was
 * footprint-checked before it was pushed.
 */
const EDGE_DC0: readonly number[] = [0, 0, 0, 1]
const EDGE_DR0: readonly number[] = [0, 1, 0, 0]
const EDGE_DC1: readonly number[] = [1, 1, 0, 1]
const EDGE_DR1: readonly number[] = [0, 1, 1, 1]

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
const _pfCameFrom = new Int32Array(PF_N)
const _pfCameDir = new Uint8Array(PF_N)

/**
 * (perf §129) Per-call generation stamps, replacing four full-array resets.
 *
 * The previous version reset four buffers at the top of EVERY findPath call
 * (`gScore.fill(Infinity)`, `firstSeq.fill(-1)`, `closed.fill(0)`,
 * `inOpen.fill(0)`) — ≈9.4 KB of memset per call, paid in full even when the
 * search only expands a handful of nodes. `_pfState` folds both "seen" and
 * "closed" for the CURRENT call into one Int32 lane:
 *
 *   _pfState[k] <   g2  → untouched this call (gScore/firstSeq hold stale data)
 *   _pfState[k] === g2  → seen: gScore/firstSeq/cameFrom valid, still open
 *   _pfState[k] === g2+1 → closed
 *
 * where `g2 = 2 * _pfGen` and `_pfGen` increments once per call. Any value
 * written by an older call is at most `2*(gen-1)+1 = g2-1`, so the single
 * `< g2` compare is an exact "unvisited" test and no reset is needed.
 *
 * Semantically identical to the fills: `gScore === Infinity` ⟺ never relaxed
 * ⟺ `state < g2`, and the first relaxation of a node is exactly the `state <
 * g2` branch — so `firstSeq` is still assigned once, in the same order, and
 * the heap tie-break (and therefore the returned path) is unchanged.
 *
 * Also removed here: `_pfFScore` and `_pfInOpen`, both write-only (fScore was
 * only ever read back on the line that wrote it; inOpen was never read at all).
 */
const _pfState = new Int32Array(PF_N)
let _pfGen = 0
/** Wrap threshold — `2 * gen + 1` must stay inside Int32. */
const PF_GEN_MAX = 0x3ffffffe

/**
 * key → col / row lookup tables (perf). GRID (26) is not a power of two, so
 * `key % GRID` compiles to an integer division on every A* pop. Two 676-byte
 * tables turn that into two typed-array loads.
 */
const _pfKeyCol = new Uint8Array(PF_N)
const _pfKeyRow = new Uint8Array(PF_N)
for (let r = 0; r < GRID; r++) {
  for (let c = 0; c < GRID; c++) {
    _pfKeyCol[r * GRID + c] = c
    _pfKeyRow[r * GRID + c] = r
  }
}
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
  // Reusable module-level buffers — NOT reset here. The `_pfState` generation
  // stamp (see its declaration) makes every stale lane self-identifying, so
  // the four per-call `fill()`s are gone. gScore/firstSeq/cameFrom/cameDir are
  // only read for cells whose state stamp matches this call, i.e. always
  // written before read.
  const gScore = _pfGScore
  const cameFrom = _pfCameFrom
  const cameDir = _pfCameDir
  const firstSeq = _pfFirstSeq
  const state = _pfState
  if (++_pfGen >= PF_GEN_MAX) {
    state.fill(0)
    _pfGen = 1
  }
  const g2 = _pfGen * 2 // "seen this call"
  const g2c = g2 + 1 // "closed this call"
  _pfHeapSize = 0
  let seqCounter = 0

  const startKey = from.row * GRID + from.col
  const goalKey = to.row * GRID + to.col
  // Hoist goal coords (used in heuristic every neighbor expansion).
  const toCol = to.col
  const toRow = to.row
  const grid = tileMap.grid

  gScore[startKey] = 0
  firstSeq[startKey] = seqCounter++
  state[startKey] = g2
  pfPush(Math.abs(from.col - toCol) + Math.abs(from.row - toRow), firstSeq[startKey], startKey)

  // Two specialized hot loops: the common case (breakBrick=false) inlines a
  // constant stepCost=1 and skips the brick-footprint scan, saving 4
  // tileMap.get calls per neighbor. breakBrick=true keeps the scan.
  if (!breakBrick) {
    for (;;) {
      const currentKey = pfPop()
      if (currentKey < 0) break
      if (state[currentKey] === g2c) continue // lazy deletion: skip stale entries
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

      state[currentKey] = g2c
      const cc = _pfKeyCol[currentKey]
      const cr = _pfKeyRow[currentKey]
      const gCur = gScore[currentKey]

      for (let s = 0; s < 4; s++) {
        const nc = cc + STEP_DC[s]
        const nr = cr + STEP_DR[s]
        // Inline isPassable (breakBrick=false branch): bounds + leading-edge
        // footprint check (§130 — only the two sub-blocks not shared with the
        // already-passable current footprint can block). Avoids the function
        // call, the breakBrick parameter check, and half the terrain reads.
        if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
        const t0 = grid[nr + EDGE_DR0[s]][nc + EDGE_DC0[s]]
        if (t0 === 'brick' || t0 === 'steel' || t0 === 'base') continue
        if (t0 === 'water' && !ignoreWater) continue
        const t1 = grid[nr + EDGE_DR1[s]][nc + EDGE_DC1[s]]
        if (t1 === 'brick' || t1 === 'steel' || t1 === 'base') continue
        if (t1 === 'water' && !ignoreWater) continue
        // §187: blocked cell (player) — impassable even in corridor mode.
        if (hasBlk && Math.abs(nc - blkCol) <= 1 && Math.abs(nr - blkRow) <= 1) continue
        const nk = nr * GRID + nc
        const st = state[nk]
        if (st === g2c) continue // already closed
        // stepCost=1 in this branch (breakBrick=false). §69: add threat cost.
        const tentativeG = gCur + 1 + (threatCosts ? threatCosts[nk] : 0)
        // `fresh` == the old `gScore[nk] === Infinity` case: a cell never
        // relaxed this call always accepts the first relaxation.
        const fresh = st < g2
        if (fresh || tentativeG < gScore[nk]) {
          if (fresh) {
            state[nk] = g2
            firstSeq[nk] = seqCounter++
          }
          cameFrom[nk] = currentKey
          cameDir[nk] = s
          gScore[nk] = tentativeG
          pfPush(tentativeG + Math.abs(nc - toCol) + Math.abs(nr - toRow), firstSeq[nk], nk)
        }
      }
    }
  } else {
    // breakBrick=true branch — keeps the brick-footprint stepCost scan.
    for (;;) {
      const currentKey = pfPop()
      if (currentKey < 0) break
      if (state[currentKey] === g2c) continue
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

      state[currentKey] = g2c
      const cc = _pfKeyCol[currentKey]
      const cr = _pfKeyRow[currentKey]
      const gCur = gScore[currentKey]

      for (let s = 0; s < 4; s++) {
        const nc = cc + STEP_DC[s]
        const nr = cr + STEP_DR[s]
        // (perf §130) A single 2×2 pass yields BOTH passability and step cost.
        // The old code called isPassable (4 terrain reads) and then re-scanned
        // the very same 4 sub-blocks for brick — 8 reads for one neighbor.
        // The §130 leading-edge trick does not apply here because the cost
        // depends on all four sub-blocks, not just the new ones.
        if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
        let blocked = false
        let cost = 1 // stepCost: 5 if any sub-block is brick, else 1
        for (let dr = 0; dr <= 1; dr++) {
          const grow = grid[nr + dr]
          for (let dc = 0; dc <= 1; dc++) {
            const type = grow[nc + dc]
            if (type === 'brick') {
              cost = 5 // passable in breakBrick mode, just expensive
              continue
            }
            if (type === 'steel' || type === 'base') {
              blocked = true
              break
            }
            if (type === 'water' && !ignoreWater) {
              blocked = true
              break
            }
          }
          if (blocked) break
        }
        if (blocked) continue
        // §187: blocked cell (player) — impassable even in breakBrick mode.
        if (hasBlk && Math.abs(nc - blkCol) <= 1 && Math.abs(nr - blkRow) <= 1) continue
        const nk = nr * GRID + nc
        const st = state[nk]
        if (st === g2c) continue // already closed
        // §69: add threat cost.
        const tentativeG = gCur + cost + (threatCosts ? threatCosts[nk] : 0)
        // `fresh` == the old `gScore[nk] === Infinity` case (see the
        // breakBrick=false branch).
        const fresh = st < g2
        if (fresh || tentativeG < gScore[nk]) {
          if (fresh) {
            state[nk] = g2
            firstSeq[nk] = seqCounter++
          }
          cameFrom[nk] = currentKey
          cameDir[nk] = s
          gScore[nk] = tentativeG
          pfPush(tentativeG + Math.abs(nc - toCol) + Math.abs(nr - toRow), firstSeq[nk], nk)
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
