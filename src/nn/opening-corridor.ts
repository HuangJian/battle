/**
 * opening-corridor.ts — geometry of the P1↔P2 defence lane (plan/g4-opening-hybrid §3.1).
 *
 * The lane is NOT "some A* path between the two spawns". A path that loops up
 * through the top of the board satisfies plain reachability but is useless for
 * swapping defence between P1 and P2. This module defines the lane as an OPEN,
 * SHORT, DIRECT route that stays in the bottom third of the board and hugs the
 * base-ring wall.
 *
 * Geometry (26×26 tiles, 2×2 tank footprint):
 *   ring top row            = BASE_POS.row - 1 = 23
 *   southernmost tank row   = ring top - 2     = 21  (footprint rows 21..22)
 *   hug band (tank rows)    = 20..21                (directly above the ring)
 *   bottom third (tiles)    = rows >= 17
 *
 * Everything is derived from BASE_POS / GRID / the two spawn cells — no stage
 * IDs, no hardcoded map knowledge. Pure reads only: no World writes, no RNG,
 * no module-level mutable state. findPath's buffers are shared, so callers must
 * invoke these functions sequentially (never nested inside another findPath).
 */
import { BASE_POS, GRID } from '../constants'
import type { Direction } from '../utils/direction'
import { DIR_DX, DIR_DY, dirIdx } from '../utils/direction'
import type { TileMap } from '../game/TileMap'
import { isBaseRingCell, RING_CELLS } from '../utils/base-ring'
import { manhattan } from '../utils/helpers'
import { findPath, type Cell } from '../utils/pathfind'

/** Top tile row of the base protection ring. */
export const RING_TOP_ROW = BASE_POS.row - 1
/** Southernmost tank top-left row whose 2×2 footprint clears the ring. */
export const CORRIDOR_ROW = RING_TOP_ROW - 2
/** Preferred tank rows: the two rows directly above the ring wall. */
export const HUG_ROW_MIN = CORRIDOR_ROW - 1
/** First tile row of the bottom third. */
export const LOWER_THIRD_ROW = Math.floor((GRID * 2) / 3)
/** Allowed extra cells over the geometry-ideal lane (detours around bricks). */
export const CORRIDOR_SLACK = 6
/** The ONLY 2×2 central windows the corridor may open (tile rows wr, wr+1). */
export const CORRIDOR_WINDOW_ROWS: readonly number[] = [HUG_ROW_MIN, CORRIDOR_ROW]

/** Ring cells are always hard-blocked, even when breakBrick=true. */
export const RING_HARD_CELLS: readonly Cell[] = RING_CELLS.map((c) => ({
  col: c.col,
  row: c.row,
}))

export type CorridorBandName = 'hug' | 'lower'

export interface CorridorBand {
  name: CorridorBandName
  /** Hard-block every tile row strictly below this (tank top-left row >= this). */
  minTopLeftRow: number
  /** Longest acceptable lane, in cell steps. */
  maxLen: number
}

export interface PathGeometry {
  length: number
  minRow: number
  maxRow: number
  /** Fraction of cells (start included) whose tank row is >= HUG_ROW_MIN. */
  hugRatio: number
}

export interface CorridorProbe {
  band: CorridorBandName
  path: Direction[]
  geometry: PathGeometry
  /** Why a found path was rejected; null = acceptable lane. */
  reject: 'too_long' | 'footprint' | null
  acceptable: boolean
}

export interface CorridorEvaluation {
  connected: boolean
  band: CorridorBandName | null
  probe: CorridorProbe | null
  hug: CorridorProbe | null
  lower: CorridorProbe | null
}

export interface CorridorDigPlan {
  path: Direction[]
  bricks: number
  windowRow: number | null
  band: CorridorBandName
  geometry: PathGeometry
  /** true = the relaxed fallback (longer / more bricks) had to be used. */
  relaxed: boolean
}

/**
 * When the strict dig cannot produce ANY lane (e.g. Checkers: a steel band
 * seals the hug rows, the only bottom-third route is 28 steps / 35 bricks),
 * allow a longer, brick-heavy lane that still stays in the bottom third and
 * still opens exactly one low central window. Never a northern detour.
 */
export const RELAXED_MAX_BRICKS = 40
export const RELAXED_LEN_SLACK = 4

export interface CorridorDigOptions {
  /** Already locked central window (at most one per game), or null. */
  windowLock: number | null
  /** Candidate centre-column window rows (restricted to CORRIDOR_WINDOW_ROWS). */
  windowRows: readonly number[]
  /** Refuse plans that need more bricks than this. */
  maxBricks: number
  /** Extra hard-blocked cells from the caller (e.g. enemy-phase central bricks). */
  extraHardCells?: readonly Cell[]
  /** Enable the bottom-third relaxed fallback. Default true. */
  allowRelaxed?: boolean
  /** Brick budget for the relaxed fallback (default 40). */
  relaxedMaxBricks?: number
  /** Extra steps allowed over the band's strict cap (default 4). */
  relaxedLenSlack?: number
}

/**
 * The two acceptance bands. `ideal` is the length of the straight lane that
 * climbs to CORRIDOR_ROW, crosses horizontally, and climbs back down:
 *   manhattan(P1,P2) + (spawnRow - CORRIDOR_ROW) * 2.
 * The hug band gets one slack budget, the bottom-third band gets two (a
 * slightly wider detour is still a lower-third lane). Spawn rows are clamped
 * in so a stage with a higher spawn never hard-blocks its own start cell.
 */
export function corridorBands(from: Cell, to: Cell): [CorridorBand, CorridorBand] {
  const detour = Math.max(0, from.row - CORRIDOR_ROW) + Math.max(0, to.row - CORRIDOR_ROW)
  const ideal = manhattan(from.col, from.row, to.col, to.row) + detour
  const hugMin = Math.min(HUG_ROW_MIN, from.row, to.row)
  const lowerMin = Math.min(LOWER_THIRD_ROW, from.row, to.row)
  return [
    { name: 'hug', minTopLeftRow: hugMin, maxLen: ideal + CORRIDOR_SLACK },
    { name: 'lower', minTopLeftRow: lowerMin, maxLen: ideal + CORRIDOR_SLACK * 2 },
  ]
}

/**
 * All tile cells strictly above the band. With a 2×2 footprint, masking tile
 * rows < min forces every visited tank cell to have top-left row >= min.
 */
export function upperBandCells(minTopLeftRow: number): Cell[] {
  const out: Cell[] = []
  for (let row = 0; row < minTopLeftRow; row++) {
    for (let col = 0; col < GRID; col++) out.push({ col, row })
  }
  return out
}

/** Centre-column brick cells (BASE_POS.col..+1), excluding ring cells. */
export function centralBrickCells(tm: TileMap): Cell[] {
  const out: Cell[] = []
  for (let row = 0; row < GRID; row++) {
    for (let col = BASE_POS.col; col <= BASE_POS.col + 1; col++) {
      if (isBaseRingCell(col, row)) continue
      if (tm.get(col, row) === 'brick') out.push({ col, row })
    }
  }
  return out
}

export function windowAllowsCell(row: number, windowRow: number | null): boolean {
  return windowRow !== null && (row === windowRow || row === windowRow + 1)
}

/** Geometry of a path walked from `from` (start cell included). */
export function pathGeometry(from: Cell, path: readonly Direction[]): PathGeometry {
  let col = from.col
  let row = from.row
  let minRow = row
  let maxRow = row
  let hug = row >= HUG_ROW_MIN ? 1 : 0
  for (let i = 0; i < path.length; i++) {
    const di = dirIdx(path[i])
    col += DIR_DX[di]
    row += DIR_DY[di]
    if (row < minRow) minRow = row
    if (row > maxRow) maxRow = row
    if (row >= HUG_ROW_MIN) hug++
  }
  const cells = path.length + 1
  return { length: path.length, minRow, maxRow, hugRatio: hug / cells }
}

/**
 * A 2×2 tank must fit at this cell. A one-tile-high gap is NOT a corridor:
 * Checkers' row-22 gap looks open, but a tank centred there needs rows 22–23
 * and row 23 is the indestructible base ring.
 */
export function footprintClear(tm: TileMap, col: number, row: number): boolean {
  if (col < 0 || row < 0 || col + 1 >= GRID || row + 1 >= GRID) return false
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      const t = tm.get(col + dc, row + dr)
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/**
 * Explicit re-check of EVERY cell (start included) on an open path. findPath
 * already searches by 2×2 footprint; this is a hard assertion of the
 * acceptance criterion so a future planner swap / isPassable change cannot
 * silently turn a one-tile gap into a "connected" lane.
 */
export function validateCorridorPath(tm: TileMap, from: Cell, path: readonly Direction[]): boolean {
  if (!footprintClear(tm, from.col, from.row)) return false
  let col = from.col
  let row = from.row
  for (let i = 0; i < path.length; i++) {
    const di = dirIdx(path[i])
    col += DIR_DX[di]
    row += DIR_DY[di]
    if (!footprintClear(tm, col, row)) return false
  }
  return true
}

/** Count unique brick TILES swept by the 2×2 footprint along a path. */
export function countPathBricks(tm: TileMap, from: Cell, path: readonly Direction[]): number {
  const seen = new Set<number>()
  let col = from.col
  let row = from.row
  for (let i = 0; i < path.length; i++) {
    const di = dirIdx(path[i])
    col += DIR_DX[di]
    row += DIR_DY[di]
    if (col < 0 || row < 0 || col + 1 >= GRID || row + 1 >= GRID) continue
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (tm.grid[row + dr][col + dc] === 'brick') {
          seen.add((row + dr) * GRID + col + dc)
        }
      }
    }
  }
  return seen.size
}

function classify(probe: CorridorProbe, band: CorridorBand): CorridorProbe {
  // Both bands hard-block everything above their min row, so every found path
  // is already inside the bottom third. The only acceptance gate left is
  // "short and direct": a long northern detour is NOT a defence lane.
  const reject: CorridorProbe['reject'] = probe.geometry.length > band.maxLen ? 'too_long' : null
  probe.reject = reject
  probe.acceptable = reject === null
  return probe
}

/**
 * OPEN path probe for one band (bricks impassable, ring hard-blocked, upper
 * board hard-blocked). A found path can still be rejected for being long or
 * for wandering out of the lower third / away from the ring wall.
 */
export function probeCorridor(
  tm: TileMap,
  from: Cell,
  to: Cell,
  band: CorridorBand,
  lenCap = band.maxLen,
): CorridorProbe | null {
  const hard: Cell[] = [...RING_HARD_CELLS, ...upperBandCells(band.minTopLeftRow)]
  const path = findPath(tm, from, to, { hardBlockedCells: hard })
  if (!path) return null
  const geometry = pathGeometry(from, path)
  // Hard check: no cell may be a one-tile gap that a 2×2 tank cannot occupy.
  if (!validateCorridorPath(tm, from, path)) {
    return { band: band.name, path, geometry, reject: 'footprint', acceptable: false }
  }
  return classify(
    { band: band.name, path, geometry, reject: null, acceptable: false },
    { ...band, maxLen: lenCap },
  )
}

/**
 * Completion check AFTER a dig: has the lane actually opened?
 *
 * Distinct from evaluateCorridor on purpose. evaluateCorridor decides whether
 * to START digging (strict "short and direct" bar). Once the bricks are gone,
 * the lane still has to be OPEN, bottom-third and 2×2-passable, but its length
 * is dictated by terrain — Checkers' carved lane is 28 steps, above the strict
 * 26-step bar. Insisting on the strict bar here would make the tank march all
 * the way to P2 and time out instead of handing off as soon as it is open.
 */
export function carvedCorridorProbe(
  tm: TileMap,
  from: Cell,
  to: Cell,
  lenSlack = RELAXED_LEN_SLACK,
): CorridorProbe | null {
  const [hugBand, lowerBand] = corridorBands(from, to)
  for (const band of [hugBand, lowerBand]) {
    const probe = probeCorridor(tm, from, to, band, band.maxLen + lenSlack)
    if (probe?.acceptable) return probe
  }
  return null
}

export function corridorCarved(
  tm: TileMap,
  from: Cell,
  to: Cell,
  lenSlack = RELAXED_LEN_SLACK,
): boolean {
  return carvedCorridorProbe(tm, from, to, lenSlack) !== null
}

/**
 * Where to park the tank before handing back to the NN: the MIDDLE of the
 * lane we just opened. Geometry-driven, no stage IDs — the staging cell is
 * always on the bottom-third corridor, always 2×2-passable, and always away
 * from the spawn pocket. Used by the global "exit pocket" phase.
 */
export function corridorStagingCell(from: Cell, probe: CorridorProbe): Cell {
  const steps = Math.floor(probe.path.length / 2)
  let col = from.col
  let row = from.row
  for (let i = 0; i < steps; i++) {
    const di = dirIdx(probe.path[i])
    col += DIR_DX[di]
    row += DIR_DY[di]
  }
  return { col, row }
}

/**
 * P1↔P2 connectivity verdict. Hug band wins; bottom third is the fallback.
 * Both must be short and mostly glued to the ring wall. A long detour through
 * the north of the board is NOT "connected" — that is the whole point.
 */
export function evaluateCorridor(tm: TileMap, from: Cell, to: Cell): CorridorEvaluation {
  const [hugBand, lowerBand] = corridorBands(from, to)
  const hug = probeCorridor(tm, from, to, hugBand)
  const lower = probeCorridor(tm, from, to, lowerBand)
  if (hug?.acceptable) return { connected: true, band: 'hug', probe: hug, hug, lower }
  if (lower?.acceptable) return { connected: true, band: 'lower', probe: lower, hug, lower }
  return { connected: false, band: null, probe: null, hug, lower }
}

/**
 * Cheapest legal DIG that produces an acceptable corridor. Hug band is tried
 * first (and wins outright if it has any legal plan), then the bottom-third
 * band. Central bricks may only disappear inside ONE 2×2 window confined to
 * CORRIDOR_WINDOW_ROWS — i.e. the breach is always low, short and ring-adjacent.
 */
export function planCorridorDig(
  tm: TileMap,
  from: Cell,
  to: Cell,
  opts: CorridorDigOptions,
): CorridorDigPlan | null {
  const [hugBand, lowerBand] = corridorBands(from, to)
  const central = centralBrickCells(tm)
  const windows: (number | null)[] =
    opts.windowLock !== null
      ? [opts.windowLock]
      : [null, ...opts.windowRows.filter((r) => CORRIDOR_WINDOW_ROWS.includes(r))]
  const brickCost = GRID * GRID + 1

  const bestFor = (
    band: CorridorBand,
    lenCap: number,
    brickCap: number,
    relaxed: boolean,
  ): CorridorDigPlan | null => {
    const upper = upperBandCells(band.minTopLeftRow)
    let best: CorridorDigPlan | null = null
    for (const windowRow of windows) {
      const hard: Cell[] = [...RING_HARD_CELLS, ...upper]
      for (const c of central) {
        if (!windowAllowsCell(c.row, windowRow)) hard.push(c)
      }
      if (opts.extraHardCells) hard.push(...opts.extraHardCells)
      const path = findPath(tm, from, to, {
        breakBrick: true,
        hardBlockedCells: hard,
        brickCost,
      })
      if (!path) continue
      const geometry = pathGeometry(from, path)
      if (geometry.length > lenCap) continue
      const bricks = countPathBricks(tm, from, path)
      if (bricks > brickCap) continue
      const cand: CorridorDigPlan = {
        path,
        bricks,
        windowRow,
        band: band.name,
        geometry,
        relaxed,
      }
      if (
        !best ||
        cand.bricks < best.bricks ||
        (cand.bricks === best.bricks && cand.path.length < best.path.length) ||
        (cand.bricks === best.bricks &&
          cand.path.length === best.path.length &&
          cand.geometry.hugRatio > best.geometry.hugRatio) ||
        (cand.bricks === best.bricks &&
          cand.path.length === best.path.length &&
          cand.geometry.hugRatio === best.geometry.hugRatio &&
          (cand.windowRow ?? -1) < (best.windowRow ?? -1))
      ) {
        best = cand
      }
    }
    return best
  }

  // Stage 1 (strict): hug band wins outright; lower band is the fallback.
  const strict =
    bestFor(hugBand, hugBand.maxLen, opts.maxBricks, false) ??
    bestFor(lowerBand, lowerBand.maxLen, opts.maxBricks, false)
  if (strict || opts.allowRelaxed === false) return strict

  // Stage 2 (relaxed): Checkers-class maps. Still bottom third, still ONE low
  // central window, still no ring breach — only the length/brick budget grows.
  const maxBricks = opts.relaxedMaxBricks ?? RELAXED_MAX_BRICKS
  const slack = opts.relaxedLenSlack ?? RELAXED_LEN_SLACK
  return (
    bestFor(hugBand, hugBand.maxLen + slack, maxBricks, true) ??
    bestFor(lowerBand, lowerBand.maxLen + slack, maxBricks, true)
  )
}
