/**
 * pathfind.ts — the God AI A* navigation engine.
 *
 * Moved verbatim from src/utils/pathfind.ts (plan/refactor.agy.md §2.7):
 * every consumer is AI-side, so the engine belongs with its domain. The
 * offline connectivity helpers stayed in utils/grid-search.ts;
 * utils/pathfind.ts re-exports this module for compatibility.
 *
 * All buffers/loops are byte-identical to the pre-move version — do not
 * reorder the A* inner loop (determinism, AGENTS §2.3).
 */
import type { Direction } from '../../constants'
import { GRID } from '../../constants'
import { DIR_DX, DIR_DY, ALL_DIRS } from '../../utils/direction'
import type { TileMap } from '../../game/TileMap'
import { isPassable, type Cell } from '../../utils/grid-search'
export type { Cell } from '../../utils/grid-search'

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

  /**
   * §nav-cost 3.2: Per-cell EXTRA cost for base-protection brick cells,
   * indexed by row*GRID+col (same as threatCosts). When provided, the value
   * is ADDED to the step cost ONLY when the step goes through a brick cell
   * (hasBrick=true). This makes A* slightly prefer routes that avoid
   * breaking the base's defensive walls, without making them impassable.
   *
   * The array is pre-computed by Navigator.buildBaseRingCosts, cached per
   * tileMap.revision (a strict pure memo — terrain mutation bumps the
   * revision the same tick). Values are 0 for non-base-ring cells and
   * (navBaseRingMult - 1) for cells whose 2×2 footprint includes at least
   * one base-protection brick.
   *
   * Unlike threatCosts (which applies to ALL edges), baseRingCosts applies
   * ONLY to brick edges — an empty cell in the base ring area has no extra
   * cost (there's nothing to break).
   */
  baseRingCosts?: Float64Array

  /**
   * §nav-cost 3.3: Fire stop cost added to every brick edge in breakBrick
   * mode. When `fireIntervalTicks` and `marchTicksPerCell` are NOT provided
   * (flat model), this is a constant worst-case estimate added to every brick
   * edge. When they ARE provided (firecontrol model), this value is ignored —
   * the real stop ticks are computed dynamically from the tank's fire state.
   *
   * 0 = no extra cost (brick = 1 = empty, per §3.1 — byte-identical to
   * the 3.1-only behavior). Typical tuned values: 1-3.
   */
  brickStopCost?: number

  /**
   * §nav-cost 3.3: The tank's current facing direction, used to compute the
   * turn cost at the START cell. When the first step direction differs from
   * the tank's facing, the tank loses 1 tick turning (during which it cannot
   * fire). If not provided, no turn cost is applied at the start cell.
   */
  startDir?: Direction

  /**
   * §nav-cost 3.3(c): Remaining fire cooldown at path start, in TICKS.
   * 0 = ready to fire immediately. When > 0, the tank must wait this many
   * ticks before it can fire. Computed from:
   *   `max(0, (tank.nextFireInterval - (now - tank.lastFire)) / TICK_MS)`
   *
   * When `fireIntervalTicks` and `marchTicksPerCell` are also provided, the
   * firecontrol model is activated: the A* loop tracks the cooldown state
   * along the path and computes real stop ticks per brick edge, replacing the
   * flat `brickStopCost` constant. This is the §3.3(c) "与 FireControl 联动"
   * implementation — the geometric alignment (curDir === stepDir) and cooldown
   * logic mirror `shouldFireInDir` + `tank.lastFire` / `tank.nextFireInterval`.
   */
  fireCooldownTicks?: number

  /**
   * §nav-cost 3.3(c): Full fire cooldown interval in TICKS
   * (`tank.nextFireInterval / TICK_MS`). After each brick-clearing fire, the
   * cooldown resets to this value. This is what makes consecutive brick walls
   * expensive: if the march time between two bricks is less than this interval,
   * the second brick forces a wait.
   */
  fireIntervalTicks?: number

  /**
   * §nav-cost 3.3(c): March time per cell in TICKS (`CELL / tank.speed`).
   * Player ≈ 23 ticks/cell. Used to compute arrival time at each brick cell
   * along the path, which determines whether the cooldown will have expired
   * before the tank reaches the brick.
   */
  marchTicksPerCell?: number
}


/** The four cardinal directions as flat arrays (perf): tuple destructuring
 * `const [dc, dr] = STEPS[s]` allocates an iterator per expansion in the A*
 * inner loop. Two parallel arrays let the loop read `DIR_DX[s]` and
 * `DIR_DY[s]` directly — no iterator, no tuple, no allocation. The
 * Direction label is recovered from the index via ALL_DIRS (used only at
 * path reconstruction, not in the hot loop).
 * (§2.8) These are the shared DIR_DX/DIR_DY/ALL_DIRS from utils/direction —
 * the former private STEP_DX/STEP_DY/STEP_DIR duplicates were byte-identical. */
const STEP_DX = DIR_DX
const STEP_DY = DIR_DY
const STEP_DIR = ALL_DIRS

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


// ---- §nav-cost 3.3(c): fireClearStopTicks -------------------------------------

/**
 * §3.3(c): Compute the fire-stop ticks for clearing a brick wall, based on
 * the tank's real fire state. This is the shared pure function between A*
 * pathfinding (pathfind.ts) and FireControl (conceptually — both use the
 * same alignment + cooldown logic).
 *
 * The geometric alignment (`curDirIdx === stepDirIdx`) mirrors
 * `shouldFireInDir`'s internal check: `shouldFireInDir` fires only when
 * the tank faces the target direction (or is about to turn to face it).
 * Here, `curDirIdx` is the tank's incoming direction at the current cell,
 * and `stepDirIdx` is the direction of the brick edge. If they differ, the
 * tank must turn (1 tick, no fire during turn).
 *
 * The cooldown logic mirrors `think.ts`'s `onCooldown = now - p.lastFire <
 * p.nextFireInterval`: `cooldownExpiry` is the tick at which the cooldown
 * expires (analogous to `p.lastFire + p.nextFireInterval` in absolute ticks).
 * If `cooldownExpiry > arriveTick`, the tank must wait.
 *
 * Pure function — no World reads, no RNG, no allocation.
 *
 * @param curDirIdx       Tank's facing at current cell (-1 = unknown).
 * @param cooldownExpiry  Absolute tick at which fire cooldown expires.
 * @param arriveTick      Absolute tick at which tank arrives at brick (march only).
 * @param stepDirIdx      Direction of the brick edge (0=up,1=down,2=left,3=right).
 * @returns Stop ticks: 0 if fire-while-marching suffices, > 0 if the tank
 *          must stop (turn + cooldown wait).
 */
export function fireClearStopTicks(
  curDirIdx: number,
  cooldownExpiry: number,
  arriveTick: number,
  stepDirIdx: number,
): number {
  const turnCost = curDirIdx >= 0 && curDirIdx !== stepDirIdx ? 1 : 0
  // If cooldownExpiry <= arriveTick, the cooldown expires before or at
  // arrival → the tank fires during march → stop = turnCost only.
  // If cooldownExpiry > arriveTick, the tank must wait → stop = wait time.
  // The max(turnCost, ...) handles both: when the wait < turnCost (cooldown
  // expires during the turn), the turn cost dominates.
  const wait = cooldownExpiry - arriveTick
  return wait > turnCost ? wait : turnCost
}

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
 * §nav-cost 3.3(c): Per-cell arrival tick (in real ticks from path start).
 * Tracks the actual time the tank arrives at each cell, accounting for march
 * time + fire stop waits. Used to compute whether the fire cooldown will have
 * expired before the tank reaches the next brick cell.
 *
 * Only written when the firecontrol model is active (fireIntervalTicks > 0
 * && marchTicksPerCell > 0). When inactive, stale values are never read
 * (guarded by `useFireControl` check in the hot loop).
 */
const _pfArriveTick = new Float64Array(PF_N)
/**
 * §nav-cost 3.3(c): Per-cell cooldown expiry tick (absolute ticks from path
 * start). At the start cell, this is the initial remaining cooldown. After a
 * brick-clearing fire, it resets to `fireTick + fireIntervalTicks`. For
 * non-brick edges, it carries forward unchanged (cooldown keeps burning).
 */
const _pfCooldownExpiry = new Float64Array(PF_N)

/**
 * (perf §130) Per-call generation stamps, replacing four full-array resets.
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
 * Reentrancy guard: findPath uses module-level buffers that are NOT safe for
 * concurrent/reentrant access. If findPath is called while another findPath
 * is still on the stack (e.g. from a callback inside the A* loop), the shared
 * buffers would be silently corrupted. This flag turns that into an immediate
 * crash instead of a silent data-corruption bug.
 *
 * Set to `true` on entry, released in a `finally` block on every exit path.
 */
let _pfInUse = false

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
  if (_pfInUse) throw new Error('findPath reentered — module buffers are not reentrant')
  _pfInUse = true
  try {
    const ignoreWater = constraints?.ignoreWater ?? false
    const breakBrick = constraints?.breakBrick ?? false
    // §69: optional per-cell threat costs. When provided, added to step cost.
    const threatCosts = constraints?.threatCosts
    // §nav-cost 3.2: optional per-cell base ring extra cost (brick edges only).
    const baseRingCosts = constraints?.baseRingCosts
    // §nav-cost 3.3: fire stop cost for brick edges.
    const brickStopCost = constraints?.brickStopCost ?? 0
    // §nav-cost 3.3: tank's current facing direction (for start-cell turn cost).
    const startDir = constraints?.startDir
    const startDirIdx = startDir
      ? startDir === 'up'
        ? 0
        : startDir === 'down'
          ? 1
          : startDir === 'left'
            ? 2
            : 3
      : -1
    // §187: optional blocked cell (player tank) — impassable even with breakBrick.
    const blkCell = constraints?.blockedCell ?? null
    const blkCol = blkCell ? blkCell.col : -99
    const blkRow = blkCell ? blkCell.row : -99
    const hasBlk = blkCell !== null

    // §nav-cost 3.3(c): firecontrol model params. When all three are > 0, the
    // A* loop tracks fire cooldown along the path and computes real stop ticks
    // per brick edge, replacing the flat brickStopCost constant.
    const fireCooldownTicks = constraints?.fireCooldownTicks ?? 0
    const fireIntervalTicks = constraints?.fireIntervalTicks ?? 0
    const marchTicksPerCell = constraints?.marchTicksPerCell ?? 0
    const useFireControl = fireIntervalTicks > 0 && marchTicksPerCell > 0

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
    // §nav-cost 3.3(c): initialize firecontrol state at the start cell.
    // arriveTick = 0 (tank is at the start now), cooldownExpiry = initial
    // remaining cooldown (0 = ready to fire).
    if (useFireControl) {
      _pfArriveTick[startKey] = 0
      _pfCooldownExpiry[startKey] = fireCooldownTicks
    }
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
          const nc = cc + STEP_DX[s]
          const nr = cr + STEP_DY[s]
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
          const nc = cc + STEP_DX[s]
          const nr = cr + STEP_DY[s]
          // (perf §130) A single 2×2 pass yields BOTH passability and step cost.
          // The §130 leading-edge trick does not apply here because the cost
          // depends on all four sub-blocks, not just the new ones.
          if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
          let blocked = false
          let hasBrick = false
          for (let dr = 0; dr <= 1; dr++) {
            const grow = grid[nr + dr]
            for (let dc = 0; dc <= 1; dc++) {
              const type = grow[nc + dc]
              if (type === 'brick') {
                hasBrick = true // §3.1: passable, cost = 1 (same as empty)
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
          // §nav-cost: compute step cost.
          // When the new cost model is active (baseRingCosts or brickStopCost
          // provided), use 3.1 (brick=1=empty) + 3.2 (base ring extra) + 3.3
          // (fire stop + turn cost). When NOT active (PathCarve calls, classic
          // mode), use the old brick=5 behavior — byte-identical to pre-change.
          const useNewCostModel = brickStopCost > 0 || !!baseRingCosts || useFireControl
          let cost = 1
          // §nav-cost 3.3(c): firecontrol stop ticks (computed for brick edges
          // when useFireControl is active). Also updated for non-brick edges to
          // track arrival time + cooldown state along the path.
          let fcStop = 0
          if (hasBrick) {
            if (useNewCostModel) {
              // 3.1: brick = 1 (same as empty — already set above)
              // 3.2: base ring multiplier
              if (baseRingCosts) cost += baseRingCosts[nk]
              // 3.3: fire stop cost
              if (useFireControl) {
                // §3.3(c): compute real stop ticks from the tank's fire state.
                // arriveTick at the brick cell (march only, no stop yet):
                //   current arrive + one cell of march.
                const arriveNk = _pfArriveTick[currentKey] + marchTicksPerCell
                const incomingDir = currentKey === startKey ? startDirIdx : cameDir[currentKey]
                fcStop = fireClearStopTicks(incomingDir, _pfCooldownExpiry[currentKey], arriveNk, s)
                cost += fcStop
              } else if (brickStopCost > 0) {
                // Flat model: constant worst-case cost + turn cost
                cost += brickStopCost
                const incomingDir = currentKey === startKey ? startDirIdx : cameDir[currentKey]
                if (incomingDir >= 0 && incomingDir !== s) cost += 1
              }
            } else {
              // Old behavior: brick = 5 (byte-identical to pre-change)
              cost = 5
            }
          }
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
            // §nav-cost 3.3(c): update firecontrol tracking arrays.
            // arriveTick[nk] = current arrive + march + stop (for brick edges).
            // cooldownExpiry[nk]:
            //   - brick edge: fireTick + fireIntervalTicks (cooldown resets).
            //   - non-brick edge: carry forward (cooldown keeps burning).
            if (useFireControl) {
              const arriveNk = _pfArriveTick[currentKey] + marchTicksPerCell + fcStop
              _pfArriveTick[nk] = arriveNk
              if (hasBrick && fcStop >= 0) {
                // The tank fires at arriveNk (the moment it can), then cooldown
                // resets to fireIntervalTicks from that point.
                _pfCooldownExpiry[nk] = arriveNk + fireIntervalTicks
              } else {
                _pfCooldownExpiry[nk] = _pfCooldownExpiry[currentKey]
              }
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
  } finally {
    _pfInUse = false
  }
}
