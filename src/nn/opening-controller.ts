/**
 * opening-controller.ts — deterministic opening phase for the hybrid policy.
 *
 * Scope (plan/g4-opening-hybrid.plan.md §3):
 *   1. P1↔P2 spawn corridor (mandatory, long-term defense lane on EVERY map);
 *   2. if no open A* lane to any enemy, dig the cheapest legal lane to the
 *      southernmost reachable enemy.
 * Once both are done (or an abort fires) the controller hands the rest of the
 * game to the NN and never acts again.
 *
 * P1↔P2 connectivity is NOT plain reachability (§9.6/§11): the lane must be
 * open, short and direct, stay in the bottom third, and preferably hug the
 * base-ring wall. Geometry lives in `opening-corridor.ts`; a long detour
 * through the north of the board counts as NOT connected, so the controller
 * carves the low lane instead. No stage IDs, no map-specific rules.
 *
 * Purity: read-only World access, no Math.random, no module-level mutable
 * state. The only shared mutable state touched is `pathfind.ts`'s A* buffers —
 * `step()` never re-enters findPath (it plans sequentially), and callers must
 * invoke it at most once per simulation tick (same contract as InputLike).
 *
 * Hard constraints (§2.2): base-ring cells are always hard-blocked (even when
 * `breakBrick=true`); central-column bricks are hard-blocked unless the single
 * 2×2 window is enabled AND locked. Ring identity comes from `RING_CELLS`
 * only — never `isBaseProtectionBrick` (5-cell wide box).
 */
import { BASE_POS, CELL, GRID, TICK_MS, TURN_SENTINEL_MS, type Direction } from '../constants'
import type { World } from '../game/World'
import type { Tank } from '../types'
import { DIR_DX, DIR_DY, dirIdx } from '../utils/direction'
import { computePlayer2SpawnCol } from '../utils/helpers'
import { RING_CELLS } from '../utils/base-ring'
import { findPath, type Cell } from '../utils/pathfind'
import {
  evaluateCorridor,
  carvedCorridorProbe,
  corridorStagingCell,
  planCorridorDig,
  countPathBricks,
  upperBandCells,
  CORRIDOR_WINDOW_ROWS,
  LOWER_THIRD_ROW,
  type CorridorBandName,
  type CorridorEvaluation,
  type CorridorProbe,
} from './opening-corridor'

export type OpeningPhase = 'p1p2' | 'exit' | 'enemy' | 'done'

export type OpeningAbortReason =
  | 'player_dead'
  | 'player_low_hp'
  | 'base_damaged'
  | 'ring_breach'
  | 'central_breach'
  | 'enemy_close'
  | 'no_progress'
  | 'timeout'
  | 'no_path'
  /** No short lower-third P1↔P2 lane can be carved (steel/water seal or brick budget). */
  | 'no_corridor'

export interface OpeningAction {
  move: Direction | null
  fire: boolean
}

export interface OpeningStep {
  action: OpeningAction
  phase: OpeningPhase
  done: boolean
  abortReason: OpeningAbortReason | null
  targetId: number | null
  bricksPlanned: number
  planLength: number
}

export interface OpeningTraceEvent {
  kind: 'central_gone'
  col: number
  row: number
  /** true = 该砖在 controller 自己计划路径的 footprint 里（自己拆的）；
   *  false = 不在（只能是对手/环境拆的）。 */
  planned: boolean
}

export interface OpeningSnapshot {
  phase: OpeningPhase
  done: boolean
  abortReason: OpeningAbortReason | null
  targetId: number | null
  bricksPlanned: number
  planLength: number
  centralWindowRow: number | null
  p1p2Connected: boolean
  enemyConnected: boolean
  /** Band of the accepted/attempted P1↔P2 lane (diagnostic). */
  corridorBand: CorridorBandName | null
  corridorLength: number
  corridorHugRatio: number
  /** Bricks the active corridor dig plan needs. */
  corridorBricks: number
  /** true = the strict dig failed and the relaxed bottom-third lane was used. */
  corridorRelaxed: boolean
  /** Effective opening tick budget (raised for brick-heavy lanes). */
  openingMaxTicks: number
  /** Global "exit pocket" phase diagnostics. */
  exitPocketTicks: number
  exitPocketTarget: { col: number; row: number } | null
  exitPocketDone: boolean
  elapsedTicks: number
  noProgressTicks: number
}

export interface OpeningControllerOptions {
  /** Enable the single 2×2 central-column window. Default false (all
   *  central bricks hard-blocked) — opening the base-side lane is a feel
   *  change that must be earned by A/B, not assumed. */
  allowCentralWindow?: boolean
  /** Replan cadence in controller ticks (default 6). */
  replanEveryTicks?: number
  /** Hard cap on opening length (default 2400; 0★ fire interval ≈ 74 ticks). */
  maxTicks?: number
  /** No "brick destroyed / cell moved" progress for this long → abort. */
  noProgressTicks?: number
  /** Abort when an aligned enemy is within this Chebyshev distance. */
  abortEnemyDist?: number
  /** Abort when player HP fraction drops below this. */
  abortHp?: number
  /**
   * Allow ONE 2×2 central window, confined to the corridor band (rows 20/21),
   * to carve the P1↔P2 lane. Default true: this is the only legal central
   * breach and it is always low, short and ring-adjacent. Turn off only for
   * the ablation that forbids touching central bricks at all.
   */
  carveCorridorWindow?: boolean
  /** Refuse a corridor dig that needs more than this many bricks (default 8). */
  maxCorridorBricks?: number
  /**
   * If the strict dig has no legal lane, allow a longer/brick-heavy lane that
   * still stays in the bottom third (Checkers needs 28 steps / 35 bricks).
   * Default true — this is the user-approved corridor shape.
   */
  allowRelaxedCorridor?: boolean
  /** Brick budget for that fallback (default 40). */
  relaxedMaxBricks?: number
  /** Extra steps over the band cap for that fallback (default 4). */
  relaxedLenSlack?: number
  /**
   * After the lane is OPEN, push the tank out of the spawn pocket to the
   * middle of that lane before handing back to the NN. Applies to EVERY map.
   * Default false on the bare controller (keeps the A/B control arm); the
   * eval/rollout deployment path turns it on. This is a public feel change:
   * every stage's opening gets ~10–30 extra scripted ticks.
   */
  exitPocket?: boolean
  /** Max controller ticks spent in the exit-pocket phase (default 240). */
  exitMaxTicks?: number
  /** 取证回调（默认关）：central_breach 判定时报告消失的砖 + 是否自己拆的。 */
  onTrace?: (e: OpeningTraceEvent) => void
}

interface DigPlan {
  path: Direction[]
  bricks: number
  /** null = no central window (all central bricks avoided). */
  windowRow: number | null
}

const EMPTY_ACTION: OpeningAction = { move: null, fire: false }

/**
 * A relaxed corridor dig can need dozens of bricks (Checkers: 35). The 0★
 * fire interval is ~74 ticks and a cell of march is ~23 ticks, so the opening
 * budget must be derived from the plan instead of a flat 2400 — otherwise the
 * controller would abort with `timeout` halfway through the lane it was told
 * to carve. Only ever raises the cap, never lowers a caller's explicit one.
 */
const FIRE_INTERVAL_TICKS = 74
const MARCH_TICKS_PER_CELL = 23
const OPENING_MARGIN_TICKS = 300
const MAX_OPENING_TICKS_CAP = 6000

export function estimateOpeningTicks(bricks: number, pathLength: number): number {
  return Math.min(
    MAX_OPENING_TICKS_CAP,
    bricks * FIRE_INTERVAL_TICKS + pathLength * MARCH_TICKS_PER_CELL + OPENING_MARGIN_TICKS,
  )
}

/** Terrain types a tank can never enter, even in dig mode. */
function isSolid(type: string): boolean {
  return type === 'brick' || type === 'steel' || type === 'base'
}

/** Ring keys in `row * GRID + col` form (static, allocation-free lookups). */
const RING_KEYS: ReadonlySet<number> = new Set(RING_CELLS.map((c) => c.row * GRID + c.col))

function cellKey(col: number, row: number): number {
  return row * GRID + col
}

// Tank x/y is the top-left pixel, and SimulationCombat snaps the perpendicular
// coordinate to the NEAREST cell boundary when moving. Use round (not floor) so
// a tank stopped mid-cell is planned as the cell it will snap into; floor would
// plan from the cell above and issue a move the collision solver then blocks.
function playerCell(world: World): Cell {
  const p = world.player
  return p ? { col: Math.round(p.x / CELL), row: Math.round(p.y / CELL) } : { col: 0, row: 0 }
}

function enemyCell(t: Tank): Cell {
  return { col: Math.round(t.x / CELL), row: Math.round(t.y / CELL) }
}

/** Direction between two orthogonally adjacent cells, or null. */
function stepDirection(from: Cell, to: Cell): Direction | null {
  const dc = to.col - from.col
  const dr = to.row - from.row
  if (dc === 1 && dr === 0) return 'right'
  if (dc === -1 && dr === 0) return 'left'
  if (dc === 0 && dr === 1) return 'down'
  if (dc === 0 && dr === -1) return 'up'
  return null
}

function footprintHasBrick(world: World, cell: Cell): boolean {
  const g = world.tileMap.grid
  if (cell.col < 0 || cell.row < 0 || cell.col + 1 >= GRID || cell.row + 1 >= GRID) return false
  return (
    g[cell.row][cell.col] === 'brick' ||
    g[cell.row][cell.col + 1] === 'brick' ||
    g[cell.row + 1][cell.col] === 'brick' ||
    g[cell.row + 1][cell.col + 1] === 'brick'
  )
}

function footprintBlocked(world: World, cell: Cell): boolean {
  const g = world.tileMap.grid
  if (cell.col < 0 || cell.row < 0 || cell.col + 1 >= GRID || cell.row + 1 >= GRID) return true
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      const t = g[cell.row + dr][cell.col + dc]
      if (t === 'steel' || t === 'base' || t === 'water') return true
    }
  }
  return false
}

export class OpeningController {
  private readonly allowCentralWindow: boolean
  private readonly replanEveryTicks: number
  private readonly baseMaxTicks: number
  private effectiveMaxTicks: number
  private readonly noProgressLimit: number
  private readonly abortEnemyDist: number
  private readonly abortHp: number
  private readonly carveCorridorWindow: boolean
  private readonly maxCorridorBricks: number
  private readonly ringCells: Cell[]

  private phase: OpeningPhase = 'p1p2'
  private abortReason: OpeningAbortReason | null = null

  private initialized = false
  private ticks = 0
  private lastProgressTick = 0
  private lastCell: Cell = { col: 0, row: 0 }
  private lastRevision = -1

  private initialBaseHp = 0
  private readonly initialRingSolid: boolean[] = []
  private readonly initialCentralBricks = new Set<number>()

  /** Locked central window row (undefined = none chosen yet). At most one. */
  private windowLock: number | undefined

  private plan: Direction[] | null = null
  private planFrom: Cell | null = null
  private planBricks = 0
  private lastPlanTick = -1
  private targetId: number | null = null
  /** Direction we are waiting to commit because the turn cooldown is active. */
  private pendingTurn: Direction | null = null
  private pendingFrom: Cell | null = null

  private p1p2Connected = false
  private enemyConnected = false
  private corridorBand: CorridorBandName | null = null
  private corridorLength = 0
  private corridorHugRatio = 0
  private corridorBricks = 0
  private corridorRelaxed = false
  /** Terrain revision at the last connectivity probe — real-time re-probe. */
  private lastProbeRevision = -1
  private lastProbeTick = -999
  private readonly onTrace?: (e: OpeningTraceEvent) => void
  /** 本局所有计划路径 footprint 覆盖过的中央砖（row*GRID+col）。 */
  private readonly plannedCentral = new Set<number>()
  /** 取证去重：同一块中央砖只 trace 一次（否则每 tick 刷屏）。 */
  private readonly tracedCentral = new Set<number>()
  private readonly exitPocketEnabled: boolean
  private readonly exitMaxTicks: number
  private exitTicks = 0
  private exitTarget: Cell | null = null
  private exitDone = false
  private readonly allowRelaxedCorridor: boolean
  private readonly relaxedMaxBricks: number
  private readonly relaxedLenSlack: number

  constructor(opts: OpeningControllerOptions = {}) {
    this.allowCentralWindow = opts.allowCentralWindow ?? false
    this.replanEveryTicks = Math.max(1, opts.replanEveryTicks ?? 6)
    this.baseMaxTicks = Math.max(1, opts.maxTicks ?? 2400)
    this.effectiveMaxTicks = this.baseMaxTicks
    this.noProgressLimit = Math.max(1, opts.noProgressTicks ?? 180)
    this.abortEnemyDist = Math.max(0, opts.abortEnemyDist ?? 4)
    this.abortHp = opts.abortHp ?? 0.35
    this.carveCorridorWindow = opts.carveCorridorWindow ?? true
    this.maxCorridorBricks = Math.max(0, opts.maxCorridorBricks ?? 8)
    this.allowRelaxedCorridor = opts.allowRelaxedCorridor ?? true
    this.relaxedMaxBricks = opts.relaxedMaxBricks ?? 40
    this.relaxedLenSlack = opts.relaxedLenSlack ?? 4
    this.onTrace = opts.onTrace
    this.exitPocketEnabled = opts.exitPocket ?? false
    this.exitMaxTicks = Math.max(1, opts.exitMaxTicks ?? 240)
    // Copy the ring once: findPath takes a plain readonly array, and a stable
    // copy keeps the planning input deterministic across calls.
    this.ringCells = RING_CELLS.map((c) => ({ col: c.col, row: c.row }))
  }

  /** New game: clear phase / target / window / counters. */
  reset(): void {
    this.phase = 'p1p2'
    this.abortReason = null
    this.initialized = false
    this.ticks = 0
    this.lastProgressTick = 0
    this.lastCell = { col: 0, row: 0 }
    this.lastRevision = -1
    this.initialBaseHp = 0
    this.initialRingSolid.length = 0
    this.initialCentralBricks.clear()
    this.windowLock = undefined
    this.plan = null
    this.planFrom = null
    this.planBricks = 0
    this.lastPlanTick = -1
    this.targetId = null
    this.pendingTurn = null
    this.pendingFrom = null
    this.p1p2Connected = false
    this.enemyConnected = false
    this.corridorBand = null
    this.corridorLength = 0
    this.corridorHugRatio = 0
    this.corridorBricks = 0
    this.corridorRelaxed = false
    this.lastProbeRevision = -1
    this.lastProbeTick = -999
    this.exitTicks = 0
    this.exitTarget = null
    this.exitDone = false
    this.plannedCentral.clear()
    this.tracedCentral.clear()
    this.effectiveMaxTicks = this.baseMaxTicks
  }

  snapshot(): OpeningSnapshot {
    return {
      phase: this.phase,
      done: this.phase === 'done',
      abortReason: this.abortReason,
      targetId: this.targetId,
      bricksPlanned: this.planBricks,
      planLength: this.plan ? this.plan.length : 0,
      centralWindowRow: this.windowLock ?? null,
      p1p2Connected: this.p1p2Connected,
      enemyConnected: this.enemyConnected,
      corridorBand: this.corridorBand,
      corridorLength: this.corridorLength,
      corridorHugRatio: this.corridorHugRatio,
      corridorBricks: this.corridorBricks,
      corridorRelaxed: this.corridorRelaxed,
      openingMaxTicks: this.effectiveMaxTicks,
      exitPocketTicks: this.exitTicks,
      exitPocketTarget: this.exitTarget
        ? { col: this.exitTarget.col, row: this.exitTarget.row }
        : null,
      exitPocketDone: this.exitDone,
      elapsedTicks: this.ticks,
      noProgressTicks: Math.max(0, this.ticks - this.lastProgressTick),
    }
  }

  /** One tick. `done=true` means the NN owns the rest of the game. */
  step(world: World): OpeningStep {
    if (this.phase === 'done') return this.result(EMPTY_ACTION)

    if (!this.initialized) this.initialize(world)
    this.ticks++

    const cell = playerCell(world)
    if (
      cell.col !== this.lastCell.col ||
      cell.row !== this.lastCell.row ||
      world.tileMap.revision !== this.lastRevision
    ) {
      this.lastProgressTick = this.ticks
      this.lastCell = cell
      this.lastRevision = world.tileMap.revision
    }

    const abort = this.checkAbort(world)
    if (abort) {
      this.abort(abort)
      return this.result(EMPTY_ACTION)
    }

    // Expire a stale plan before the phase functions get to use it. While a
    // turn is pending we keep the plan: it is the only record of which facing
    // we are waiting to commit, and swapping it mid-cooldown causes the
    // right/up/right oscillation that halts movement.
    if (this.plan && !this.pendingTurn && this.ticks - this.lastPlanTick >= this.replanEveryTicks) {
      this.clearPlan()
    }

    if (this.phase === 'p1p2') this.stepP1P2(world)
    if (this.phase === 'exit') this.stepExit(world)
    if (this.phase === 'enemy') this.stepEnemy(world)
    if (this.isFinished()) return this.result(EMPTY_ACTION)

    return this.result(this.executePlan(world))
  }

  // ── initialization ──────────────────────────────────────────────────────

  private initialize(world: World): void {
    this.initialized = true
    this.initialBaseHp = world.baseHp
    this.lastCell = playerCell(world)
    this.lastRevision = world.tileMap.revision
    this.lastProgressTick = 0
    this.ticks = 0
    this.initialRingSolid.length = 0
    for (let i = 0; i < this.ringCells.length; i++) {
      const c = this.ringCells[i]
      this.initialRingSolid.push(isSolid(world.tileMap.get(c.col, c.row)))
    }
    this.initialCentralBricks.clear()
    for (let row = 0; row < GRID; row++) {
      for (let col = BASE_POS.col; col <= BASE_POS.col + 1; col++) {
        if (RING_KEYS.has(cellKey(col, row))) continue
        if (world.tileMap.get(col, row) === 'brick') {
          this.initialCentralBricks.add(cellKey(col, row))
        }
      }
    }
  }

  // ── abort checks ────────────────────────────────────────────────────────

  private checkAbort(world: World): OpeningAbortReason | null {
    const p = world.player
    if (!p || p.hp <= 0) return 'player_dead'
    if (p.maxHp > 0 && p.hp / p.maxHp < this.abortHp) return 'player_low_hp'
    if (world.baseHp < this.initialBaseHp) return 'base_damaged'
    if (this.baseRingBreached(world)) return 'ring_breach'
    if (this.centralBreached(world)) return 'central_breach'
    if (this.enemyCloseAndAligned(world, p)) return 'enemy_close'
    if (this.ticks >= this.effectiveMaxTicks) return 'timeout'
    if (this.ticks - this.lastProgressTick >= this.noProgressLimit) return 'no_progress'
    return null
  }

  private baseRingBreached(world: World): boolean {
    for (let i = 0; i < this.ringCells.length; i++) {
      if (!this.initialRingSolid[i]) continue
      const c = this.ringCells[i]
      if (!isSolid(world.tileMap.get(c.col, c.row))) return true
    }
    return false
  }

  /**
   * Central bricks may only disappear inside the single locked 2×2 window —
   * BUT only the bricks the controller itself planned to shoot count. Enemies
   * routinely punch holes in the centre columns from above; blaming the
   * controller for those aborts the opening halfway and hands a half-carved
   * lane back to the NN (2026-11-15 实测：5/5 central_breach 都是 col=12,row=4
   * 的敌人流弹，planned=false). External damage is traced, never fatal.
   */
  private centralBreached(world: World): boolean {
    if (this.initialCentralBricks.size === 0) return false
    const wr = this.windowLock
    for (const key of this.initialCentralBricks) {
      if (world.tileMap.grid[Math.floor(key / GRID)][key % GRID] === 'brick') continue
      const row = Math.floor(key / GRID)
      const col = key % GRID
      if (wr !== undefined && (row === wr || row === wr + 1)) continue
      const planned = this.plannedCentral.has(key)
      if (!this.tracedCentral.has(key)) {
        this.tracedCentral.add(key)
        this.onTrace?.({ kind: 'central_gone', col, row, planned })
      }
      if (planned) return true
    }
    return false
  }

  private enemyCloseAndAligned(world: World, p: Tank): boolean {
    const pRow = Math.floor(p.y / CELL)
    const pCol = Math.floor(p.x / CELL)
    for (let i = 0; i < world.tanks.length; i++) {
      const e = world.tanks[i]
      const ec = enemyCell(e)
      const dist = Math.max(Math.abs(ec.col - pCol), Math.abs(ec.row - pRow))
      if (dist > this.abortEnemyDist) continue
      if (ec.col === pCol || ec.row === pRow) return true
    }
    return false
  }

  // ── phases ──────────────────────────────────────────────────────────────

  private p1p2Goal(world: World): Cell {
    const spawn = world.playerSpawnPoint
    return { col: computePlayer2SpawnCol(spawn.col), row: spawn.row }
  }

  /** Lane endpoints are the SPAWNS, not the tank's current cell: the corridor
   *  is a property of the map, so a player that has wandered off must not make
   *  a broken spawn-to-spawn lane look "connected" (or vice versa). */
  private p1p2From(world: World): Cell {
    const spawn = world.playerSpawnPoint
    return { col: spawn.col, row: spawn.row }
  }

  private recordCorridor(ev: CorridorEvaluation): void {
    const geometry = ev.probe?.geometry ?? ev.hug?.geometry ?? ev.lower?.geometry ?? null
    this.corridorBand = ev.band ?? ev.hug?.band ?? ev.lower?.band ?? null
    this.corridorLength = geometry?.length ?? 0
    this.corridorHugRatio = geometry?.hugRatio ?? 0
  }

  private stepP1P2(world: World): void {
    const goal = this.p1p2Goal(world)
    // REAL-TIME: the lane is a property of the CURRENT terrain, not of this
    // controller's memory. In co-op both tanks may be clearing bricks, so
    // re-probe as soon as tileMap.revision changes (plus the normal replan
    // cadence). No per-tick A* storm: revision bumps only when a brick falls.
    const revision = world.tileMap.revision
    const probeDue =
      !this.plan ||
      revision !== this.lastProbeRevision ||
      this.ticks - this.lastProbeTick >= this.replanEveryTicks
    if (!probeDue) return
    this.lastProbeRevision = revision
    this.lastProbeTick = this.ticks

    const ev = evaluateCorridor(world.tileMap, this.p1p2From(world), goal)
    this.recordCorridor(ev)
    if (ev.connected) {
      this.corridorBricks = 0
      this.completeP1P2(world, ev.probe)
      return
    }

    // Completion = "the lane is OPEN", not "the tank reached P2". Checked
    // unconditionally (not only after our own dig): another tank may have
    // cleared the other half. The strict "short and direct" length bar is NOT
    // re-applied here — terrain dictates the carved lane's length.
    const carved = carvedCorridorProbe(world.tileMap, this.p1p2From(world), goal)
    if (carved) {
      this.completeP1P2(world, carved)
      return
    }

    // Terrain changed but the lane is still closed: keep the current plan for
    // this tick; the replan cadence will refresh it.
    if (this.plan) return

    const dig = planCorridorDig(world.tileMap, playerCell(world), goal, {
      windowLock: this.windowLock ?? null,
      windowRows: this.carveCorridorWindow ? CORRIDOR_WINDOW_ROWS : [],
      maxBricks: this.maxCorridorBricks,
      allowRelaxed: this.allowRelaxedCorridor,
      relaxedMaxBricks: this.relaxedMaxBricks,
      relaxedLenSlack: this.relaxedLenSlack,
    })
    if (!dig) {
      // Steel/water seal or the brick budget is exhausted: hand off instead of
      // carving a long northern detour. No map-specific exceptions.
      this.abort('no_corridor')
      return
    }
    if (dig.windowRow !== null) this.windowLock = dig.windowRow
    this.corridorBand = dig.band
    this.corridorLength = dig.geometry.length
    this.corridorHugRatio = dig.geometry.hugRatio
    this.corridorBricks = dig.bricks
    this.corridorRelaxed = dig.relaxed
    // 35 bricks × ~74 ticks + 28 cells × ~23 ticks ⇒ the flat 2400 cap would
    // abort halfway through. Derive the budget from the plan (never lower it).
    this.effectiveMaxTicks = Math.max(
      this.effectiveMaxTicks,
      estimateOpeningTicks(dig.bricks, dig.path.length),
    )
    this.commitPlan(
      { path: dig.path, bricks: dig.bricks, windowRow: dig.windowRow },
      playerCell(world),
      world,
    )
  }

  private stepEnemy(world: World): void {
    // Target death invalidates the plan even if its old cell is still walkable.
    if (this.targetId !== null) {
      let alive = false
      for (let i = 0; i < world.tanks.length; i++) {
        if (world.tanks[i].id === this.targetId) {
          alive = true
          break
        }
      }
      if (!alive) {
        this.targetId = null
        this.clearPlan()
      }
    }
    if (!this.plan) {
      if (this.anyEnemyOpen(world)) {
        this.enemyConnected = true
        this.finish()
        return
      }
      this.chooseEnemy(world)
      if (this.phase === 'done') return
    }
  }

  /** P1↔P2 done: either hand off straight away, or first push the tank out
   *  of the spawn pocket (global "exit pocket" phase, deployment default). */
  private completeP1P2(world: World, probe: CorridorProbe | null): void {
    this.p1p2Connected = true
    if (!this.exitPocketEnabled) {
      this.beginEnemyPhase()
      return
    }
    const target = probe ? corridorStagingCell(this.p1p2From(world), probe) : null
    this.exitTarget = target
    this.exitTicks = 0
    this.exitDone = false
    if (!target) {
      this.beginEnemyPhase()
      return
    }
    this.phase = 'exit'
    this.clearPlan()
    this.targetId = null
  }

  /**
   * Walk to the MIDDLE of the just-opened lane before handing back to the NN.
   * Applies to every map with no stage IDs: the staging cell is the path
   * midpoint, always bottom-third, always 2×2-passable, always outside the
   * spawn pocket. A bounded budget means a blocked route still hands off.
   */
  private stepExit(world: World): void {
    this.exitTicks++
    const target = this.exitTarget
    if (!target || this.exitTicks > this.exitMaxTicks) {
      this.beginEnemyPhase()
      return
    }
    const cell = playerCell(world)
    if (cell.col === target.col && cell.row === target.row) {
      this.exitDone = true
      this.beginEnemyPhase()
      return
    }
    if (this.plan) return
    const hard: Cell[] = [...this.ringCells, ...upperBandCells(LOWER_THIRD_ROW)]
    const path = findPath(world.tileMap, cell, target, { hardBlockedCells: hard })
    if (!path || path.length === 0) {
      this.beginEnemyPhase()
      return
    }
    this.commitPlan({ path, bricks: 0, windowRow: null }, cell, world)
  }

  private beginEnemyPhase(): void {
    this.phase = 'enemy'
    this.clearPlan()
    this.targetId = null
  }

  private finish(): void {
    this.phase = 'done'
    this.abortReason = null
    this.clearPlan()
  }

  private abort(reason: OpeningAbortReason): void {
    this.phase = 'done'
    this.abortReason = reason
    this.clearPlan()
  }

  // ── enemy selection ─────────────────────────────────────────────────────

  private anyEnemyOpen(world: World): boolean {
    const from = playerCell(world)
    for (let i = 0; i < world.tanks.length; i++) {
      const to = enemyCell(world.tanks[i])
      if (findPath(world.tileMap, from, to, { hardBlockedCells: this.ringCells })) {
        this.enemyConnected = true
        return true
      }
    }
    return false
  }

  /** Southernmost enemy with a legal dig path; row desc, then col asc, id asc. */
  private chooseEnemy(world: World): void {
    if (world.tanks.length === 0) {
      // No enemy has spawned yet: nothing to dig toward. Hand off rather than
      // burn the opening budget on a target that does not exist.
      this.finish()
      return
    }
    const sorted = world.tanks.slice().sort((a, b) => {
      const ac = enemyCell(a)
      const bc = enemyCell(b)
      if (ac.row !== bc.row) return bc.row - ac.row
      if (ac.col !== bc.col) return ac.col - bc.col
      return a.id - b.id
    })
    const from = playerCell(world)
    for (let i = 0; i < sorted.length; i++) {
      const e = sorted[i]
      const plan = this.planDig(world, from, enemyCell(e))
      if (!plan) continue
      this.targetId = e.id
      this.commitPlan(plan, from, world)
      return
    }
    this.abort('no_path')
  }

  // ── planning ────────────────────────────────────────────────────────────

  /** 记录计划路径 footprint 覆盖的中央砖：只有这些砖可能是 controller 自己拆的。 */
  private recordPlannedCentral(world: World, from: Cell, path: readonly Direction[]): void {
    let col = from.col
    let row = from.row
    for (let i = 0; i < path.length; i++) {
      const di = dirIdx(path[i])
      col += DIR_DX[di]
      row += DIR_DY[di]
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = 0; dc <= 1; dc++) {
          const c = col + dc
          const r = row + dr
          if (c !== BASE_POS.col && c !== BASE_POS.col + 1) continue
          if (world.tileMap.get(c, r) !== 'brick') continue
          this.plannedCentral.add(r * GRID + c)
        }
      }
    }
  }

  private commitPlan(plan: DigPlan, from: Cell, world: World): void {
    this.recordPlannedCentral(world, from, plan.path)
    this.plan = plan.path.slice()
    this.planFrom = { col: from.col, row: from.row }
    this.planBricks = plan.bricks
    this.lastPlanTick = this.ticks
    if (plan.windowRow !== null) this.windowLock = plan.windowRow
  }

  /**
   * Best legal dig plan, evaluated as a lexicographic objective:
   *   bricks asc → path length asc → no-window first → window row asc.
   * Central bricks are hard-blocked outside the (single) allowed window.
   */
  private planDig(world: World, from: Cell, to: Cell): DigPlan | null {
    let best: DigPlan | null = null
    const consider = (windowRow: number | null): void => {
      const hard = this.hardCellsFor(windowRow, world)
      const path = findPath(world.tileMap, from, to, {
        breakBrick: true,
        hardBlockedCells: hard,
        // One brick must outweigh every possible path-length tie-break, so the
        // objective is truly "fewest bricks, then shortest".
        brickCost: GRID * GRID + 1,
      })
      if (!path) return
      const bricks = countPathBricks(world.tileMap, from, path)
      const cand: DigPlan = { path, bricks, windowRow }
      if (!best || comparePlans(cand, best) < 0) best = cand
    }

    if (this.allowCentralWindow) {
      if (this.windowLock !== undefined) {
        consider(this.windowLock)
      } else {
        consider(null)
        for (let row = 0; row <= GRID - 2; row++) consider(row)
      }
    } else {
      consider(null)
    }
    return best
  }

  /** Ring + every central brick except the allowed 2×2 window. */
  private hardCellsFor(windowRow: number | null, world: World): Cell[] {
    const hard: Cell[] = this.ringCells.slice()
    for (let row = 0; row < GRID; row++) {
      if (windowRow !== null && (row === windowRow || row === windowRow + 1)) continue
      for (let col = BASE_POS.col; col <= BASE_POS.col + 1; col++) {
        if (RING_KEYS.has(cellKey(col, row))) continue
        if (world.tileMap.get(col, row) === 'brick') hard.push({ col, row })
      }
    }
    return hard
  }

  // ── execution ───────────────────────────────────────────────────────────

  private executePlan(world: World): OpeningAction {
    if (!this.plan || this.plan.length === 0) {
      this.clearPlan()
      return EMPTY_ACTION
    }

    const cell = playerCell(world)
    if (this.pendingTurn) {
      if (
        !this.pendingFrom ||
        cell.col !== this.pendingFrom.col ||
        cell.row !== this.pendingFrom.row
      ) {
        this.pendingTurn = null
        this.pendingFrom = null
      } else if (!this.turnReady(world, this.pendingTurn)) {
        return EMPTY_ACTION
      } else {
        const pending = this.pendingTurn
        this.pendingTurn = null
        this.pendingFrom = null
        if (this.plan[0] !== pending) {
          this.clearPlan()
          return EMPTY_ACTION
        }
      }
    }
    if (this.planFrom && (cell.col !== this.planFrom.col || cell.row !== this.planFrom.row)) {
      const moved = stepDirection(this.planFrom, cell)
      if (moved && this.plan[0] === moved) {
        this.plan.shift()
        this.planFrom = cell
      } else {
        this.clearPlan()
        return EMPTY_ACTION
      }
    }
    if (!this.plan || this.plan.length === 0) {
      this.clearPlan()
      return EMPTY_ACTION
    }

    const dir = this.plan[0]
    // Respect the simulation's turn cooldown: issuing a new facing while the
    // cooldown is active makes updateMovement revert + halt, which looks like
    // "stuck at a wall" even though the plan is valid. Remember the pending
    // facing and keep requesting it until the cooldown clears.
    if (!this.turnReady(world, dir)) {
      this.pendingTurn = dir
      this.pendingFrom = { col: cell.col, row: cell.row }
      return EMPTY_ACTION
    }
    const di = dirIdx(dir)
    const next: Cell = { col: cell.col + DIR_DX[di], row: cell.row + DIR_DY[di] }
    if (footprintBlocked(world, next)) {
      this.clearPlan()
      return EMPTY_ACTION
    }
    if (footprintHasBrick(world, next)) {
      // Turn toward the brick and fire; `move` is the facing request, the
      // collision solver keeps the tank in place while the brick is solid.
      // The fire cooldown (≈74 ticks at 0★) gates the rate.
      return { move: dir, fire: true }
    }
    return { move: dir, fire: false }
  }

  private clearPlan(): void {
    this.plan = null
    this.planFrom = null
    this.planBricks = 0
    this.pendingTurn = null
    this.pendingFrom = null
  }

  /** Mirror SimulationCombat.applyTurnCooldown for the upcoming tick. */
  private turnReady(world: World, dir: Direction): boolean {
    const p = world.player
    if (!p) return true
    const prev = p.prevMoveDir ?? p.dir
    if (dir === prev) return true
    const turnCd = world.rules?.turnCooldownMs ?? 0
    if (turnCd <= 0) return true
    // Simulation increments frame at tick start before updateMovement runs.
    const nowMs = (world.frame + 1) * TICK_MS
    const lastTurn = p.lastTurnMs ?? TURN_SENTINEL_MS
    return nowMs - lastTurn >= turnCd
  }

  private isFinished(): boolean {
    return this.phase === 'done'
  }

  private result(action: OpeningAction): OpeningStep {
    return {
      action,
      phase: this.phase,
      done: this.phase === 'done',
      abortReason: this.abortReason,
      targetId: this.targetId,
      bricksPlanned: this.planBricks,
      planLength: this.plan ? this.plan.length : 0,
    }
  }
}

/** Plan comparator: bricks asc → length asc → no-window first → row asc. */
function comparePlans(a: DigPlan, b: DigPlan): number {
  if (a.bricks !== b.bricks) return a.bricks - b.bricks
  if (a.path.length !== b.path.length) return a.path.length - b.path.length
  const ar = a.windowRow === null ? -1 : a.windowRow
  const br = b.windowRow === null ? -1 : b.windowRow
  return ar - br
}
