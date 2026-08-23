/**
 * ThreatBudget — pure, read-only threat-slack layer (plan Phase 1 §5).
 *
 * All ETA values are in TICKS (fixed 60 Hz). The layer derives every number
 * from the World as-is:
 *   - `turnCooldownMs` is READ from world.rules and never modified. 200ms and
 *     a future 500ms both change only the ETAs, never the fairness rule.
 *   - No World mutation, no RNG consumption, no caches, no module state.
 *   - Conservative, explainable bounds — the plan deliberately forbids
 *     simulating enemy RNG or treating A* routes as ground truth.
 *
 * Terminology (plan §5, open-test protocol §4):
 *   actionEta      = nextLegalTurnEta + movementEta + aimAlignmentEta
 *                    + fireCooldownEta + requiredShotsEta
 *                    (each cost billed exactly once — §4.1)
 *   killSlack(e)   = enemyDamageDeadline(e) - playerKillEta(e)
 *   interceptSlack(I) = (enemyArrivalLowerBound(I) - safetyMargin)
 *                    - playerArrivalAndAimEta(I)
 *
 * Only positive slack may justify deviating from the current target — the
 * caller (Phase 2 ActionContract) decides; this module only computes.
 *
 * Terrain mirrors: the ring-cell set and the clear-shot predicates are
 * verbatim mirrors of SimulationCombat.isBaseProtectionCell and the
 * SmartThreatModel geometry (canShootBaseFrom / canBreachRingFrom), kept
 * here so the model stays a pure function of (world, tank). A parity test
 * (tests/godai-threat-budget.test.ts) pins them to the AI's predicates.
 */

import { CELL, GRID, BASE_POS, TICK_MS, TURN_SENTINEL_MS, type Direction } from '../../constants'
import { manhattan } from '../../utils/helpers'
import { resolveProfile } from '../../config/combat'
import type { World } from '../../game/World'
import type { Tank } from '../../types'

/** The 8 cells of the base protection ring — mirror of isBaseProtectionCell. */
export const RING_CELLS: ReadonlyArray<{ col: number; row: number }> = (() => {
  const cells: Array<{ col: number; row: number }> = []
  const bc = BASE_POS.col
  const br = BASE_POS.row
  for (let col = bc - 1; col <= bc + 2; col++) cells.push({ col, row: br - 1 })
  for (let row = br; row <= br + 1; row++) {
    cells.push({ col: bc - 1, row })
    cells.push({ col: bc + 2, row })
  }
  return cells
})()

export interface ActionEta {
  /**
   * Wait until a turn is LEGAL (0 when no turn is needed at all — neither for
   * aim nor for a path axis change). Contains: nothing else. Counted exactly
   * ONCE in `total` (open-test protocol §4.1: never billed twice).
   */
  nextLegalTurnEta: number
  /** Manhattan movement along both axes. Contains: NO turn costs. */
  movementEta: number
  /**
   * ONE full turn window (cooldown duration + 1 tick) when either the aim
   * alignment or the path's perpendicular axis change requires a turn. The
   * aim turn and the axis-change turn are the SAME physical turn — charged
   * once, never once each.
   */
  aimAlignmentEta: number
  /** Wait until firing is legal (re-arm). */
  fireCooldownEta: number
  /** Re-arms for shots 2..N plus the final bullet's flight time. */
  requiredShotsEta: number
  /** The five fields above, each counted exactly once. */
  total: number
}

export interface EnemyDeadline {
  /**
   * OPTIMISTIC LOWER BOUND — earliest the enemy could physically reach the
   * ring: straight-line Manhattan at its current speed. Ignores turn
   * cooldowns, obstacles and detours, so it is valid for RELATIVE ORDERING
   * only — never as evidence that it is safe to leave the base (§4.2).
   */
  enemyArrivalLowerBound: number
  /**
   * LOWER BOUND — earliest tick a base-damaging enemy bullet could LAND
   * (fire readiness + brick-breach cycles when applicable + bullet flight).
   */
  enemyDamageEarliest: number
  /**
   * SAFE deadline for ALLOWING player deviation: `enemyDamageEarliest` minus
   * the enemy-ETA safety margin (one legal turn window — the geometric model
   * charges the enemy zero turn costs, so the margin restores honesty).
   * Only positive slack against THIS field may justify leaving the base or
   * holding without output. May be negative — that means the intervention
   * window has already closed.
   */
  enemyDamageDeadline: number
  /** Window from the first damaging shot until the base pool is exhausted. */
  enemyDamageWindow: number
  /** Composite 0..1+ urgency (base HP, ring integrity, shots required). */
  enemyUrgency: number
  /**
   * True when the enemy is ALREADY in a base-damage position (csb/cbr —
   * clear shot at the base, or a productive ring breach line). Only for
   * these is `enemyDamageDeadline` the real time of an imminent first
   * shot; for walk-branch enemies the deadline is an optimistic geometric
   * lower bound (no turn costs charged), so a negative killSlack there
   * does NOT mean the intervention is pointless — a late kill still
   * prevents the shots after the first.
   */
  directThreat: boolean
}

export interface KillAssessment {
  /** Player ETA to arrive AND aim at the enemy (ticks). */
  playerArrivalAndAimEta: number
  /** Player ETA to the first legal fire. */
  firstFireEta: number
  /** Player ETA to the killing shot landing (includes cadence + flight). */
  playerKillEta: number
  /** killSlack = damageDeadline - playerKillEta. */
  killSlack: number
  /** interceptSlack = enemyToRingEta - playerArrivalAndAimEta. */
  interceptSlack: number
  /** True when another enemy reaches a shoot position before the kill. */
  missesSecondThreat: boolean
}

const msToTicks = (ms: number): number => Math.max(0, ms / TICK_MS)

// §14.2 hot-path scratch: every object-returning helper below accepts an
// optional caller-owned `out` (reused across ticks — zero per-tick
// allocation). Module-level buffers are safe because the sim is single
// threaded and the helper contract is write-all-then-read (no partial
// reads of a shared buffer across nested calls).
const _CELL_A = { col: 0, row: 0 }
const _CELL_B = { col: 0, row: 0 }
const _DL: EnemyDeadline = {
  enemyArrivalLowerBound: 0,
  enemyDamageEarliest: 0,
  enemyDamageDeadline: 0,
  enemyDamageWindow: 0,
  enemyUrgency: 0,
  directThreat: false,
}
const _ETA: ActionEta = {
  nextLegalTurnEta: 0,
  movementEta: 0,
  aimAlignmentEta: 0,
  fireCooldownEta: 0,
  requiredShotsEta: 0,
  total: 0,
}

/** Ticks until the tank may legally turn (0 = a turn is allowed right now). */
export function ticksUntilLegalTurn(world: World, t: Tank): number {
  const turnCd = world.rules?.turnCooldownMs ?? 0
  if (turnCd <= 0) return 0
  const nowMs = world.frame * TICK_MS
  const elapsed = nowMs - (t.lastTurnMs ?? TURN_SENTINEL_MS)
  return elapsed >= turnCd ? 0 : msToTicks(turnCd - elapsed)
}

/** Ticks until the next shot is legal (0 = ready). Uses the frozen cadence. */
export function ticksUntilFire(world: World, t: Tank): number {
  const iv = t.nextFireInterval
  if (!(iv > 0)) return 0
  const nowMs = world.frame * TICK_MS
  const elapsed = nowMs - (t.lastFire ?? TURN_SENTINEL_MS)
  return elapsed >= iv ? 0 : msToTicks(iv - elapsed)
}

/** Ticks one legal turn consumes (the cooldown window itself). */
export function turnCostTicks(world: World): number {
  return msToTicks(world.rules?.turnCooldownMs ?? 0) + 1
}

/** Ticks per cell of movement at the tank's current speed (px/tick). */
const ticksPerCell = (t: Tank): number => (t.speed > 0 ? CELL / t.speed : 1e9)

/**
 * Plan §5.1 — actionEta for reaching (targetCol, targetRow) and firing at
 * aimDir. Open-test protocol §4.1: the five cost fields are MUTUALLY
 * EXCLUSIVE — `total` is their plain sum, and no cost appears in two fields:
 *
 *   total = movement + legalTurnWait + aimAlignment + fireCooldown
 *           + shotCadenceAndFlight
 *
 * - `nextLegalTurnEta` (the wait) is billed ONCE, in `total` — it is NOT
 *   repeated inside `aimAlignmentEta`.
 * - The aim-alignment turn and the path's perpendicular axis-change are the
 *   same physical turn: ONE turn window (`aimAlignmentEta`) covers both.
 *   When the tank already faces `aimDir` and the path is single-axis, no
 *   turn cost is charged at all.
 * - Movement is a conservative geometric Manhattan path (no A*).
 * - `shots` is the total shots needed (≥1).
 */
export function playerActionEta(
  world: World,
  p: Tank,
  targetCol: number,
  targetRow: number,
  aimDir: Direction,
  shots: number,
  out?: ActionEta,
): ActionEta {
  const tpc = ticksPerCell(p)
  const pc = tankCenterCell(p, _CELL_A)
  const cellsX = Math.abs(pc.col - targetCol)
  const cellsY = Math.abs(pc.row - targetRow)
  const needsAimTurn = p.dir !== aimDir
  const needsPathTurn = cellsX > 0 && cellsY > 0
  const needsAnyTurn = needsAimTurn || needsPathTurn
  const nextLegalTurnEta = needsAnyTurn ? ticksUntilLegalTurn(world, p) : 0
  const aimAlignmentEta = needsAnyTurn ? turnCostTicks(world) : 0
  const movementEta = (cellsX + cellsY) * tpc
  const fireCooldownEta = ticksUntilFire(world, p)
  // Shots after the first re-arm per base cadence; the final bullet must fly.
  const flightEta = manhattan(pc.col, pc.row, targetCol, targetRow) * ticksPerCellFire(p)
  const cadenceTicks =
    p.fireCooldown > 0 ? msToTicks(p.fireCooldown) : msToTicks(p.nextFireInterval)
  const requiredShotsEta = Math.max(0, shots - 1) * cadenceTicks + flightEta
  const total =
    nextLegalTurnEta + movementEta + aimAlignmentEta + fireCooldownEta + requiredShotsEta
  if (out) {
    out.nextLegalTurnEta = nextLegalTurnEta
    out.movementEta = movementEta
    out.aimAlignmentEta = aimAlignmentEta
    out.fireCooldownEta = fireCooldownEta
    out.requiredShotsEta = requiredShotsEta
    out.total = total
    return out
  }
  return {
    nextLegalTurnEta,
    movementEta,
    aimAlignmentEta,
    fireCooldownEta,
    requiredShotsEta,
    total,
  }
}

/** Player bullet flight per cell (px/tick speed). */
function ticksPerCellFire(p: Tank): number {
  const bs = p.bulletSpeed
  return bs > 0 ? CELL / bs : 0
}

/** Firepower (damage against the base pool) of a tank kind at its level. */
export function firePower(
  world: World,
  kind: 'player' | 'basic' | 'fast' | 'power' | 'armor',
): number {
  const level = kind === 'player' ? world.playerLevel : 0
  return resolveProfile(kind, level).firepower
}

/** Per-shot damage against tanks (pool model). */
export function bulletDamage(
  world: World,
  kind: 'player' | 'basic' | 'fast' | 'power' | 'armor',
): number {
  const level = kind === 'player' ? world.playerLevel : 0
  return Math.round(resolveProfile(kind, level).firepower * (kind === 'player' ? 1.05 : 1) * 2)
}

/** Hits to kill this enemy with player bullets (pool model). */
export function playerShotsToKill(world: World, e: Tank): number {
  const maxHp = e.maxHp > 0 ? e.maxHp : 1
  const dmg = bulletDamage(world, 'player')
  return Math.max(1, Math.ceil(maxHp / dmg))
}

/**
 * Mirror of canShootBaseFrom — does (col,row) have a clear shot at the base?
 * Static terrain read only (identical geometry to SmartThreatModel).
 */
export function canShootBaseLine(world: World, col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tm = world.tileMap
  if (col === bc) {
    const step = row < br ? 1 : -1
    for (let r = row + step; r !== br; r += step) {
      if (r < 0 || r >= GRID) return false
      const t = tm.get(col, r)
      if (t === 'brick' || t === 'steel') return false
    }
    const adjRow = br - step
    if (adjRow >= 0 && adjRow < GRID) {
      const t = tm.get(col, adjRow)
      if (t === 'brick' || t === 'steel') return false
    }
    return true
  }
  if (row === br || row === br + 1) {
    for (let c = Math.min(col, bc) + 1; c < Math.max(col, bc); c++) {
      if (c < 0 || c >= GRID) return false
      const t = tm.get(c, row)
      if (t === 'brick' || t === 'steel') return false
    }
    return true
  }
  return false
}

/**
 * Mirror of canBreachRingFrom — does (col,row) have a clear shot at an
 * intact ring brick (productive breach)? Mirrors SmartThreatModel verbatim.
 */
export function canBreachRingLine(world: World, col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tm = world.tileMap
  const clearShotAt = (rc: number, rr: number): boolean => {
    if (col === rc) {
      const step = row < rr ? 1 : -1
      for (let r = row + step; r !== rr; r += step) {
        if (r < 0 || r >= GRID) return false
        const t = tm.get(col, r)
        if (t === 'brick' || t === 'steel') return false
        if (t === 'base') return false
      }
    } else if (row === rr) {
      const step = col < rc ? 1 : -1
      for (let c = col + step; c !== rc; c += step) {
        if (c < 0 || c >= GRID) return false
        const t = tm.get(c, row)
        if (t === 'brick' || t === 'steel') return false
        if (t === 'base') return false
      }
    } else {
      return false
    }
    return tm.get(rc, rr) === 'brick'
  }
  for (let dc = -1; dc <= 2; dc++) {
    if (clearShotAt(bc + dc, br - 1)) return true
  }
  for (let dr = 0; dr <= 1; dr++) {
    if (clearShotAt(bc - 1, br + dr)) return true
    if (clearShotAt(bc + 2, br + dr)) return true
  }
  return false
}

/**
 * CENTER cell of a tank (protocol §4.3): floor of the tank's 32px center.
 * For a 32px tank on 16px cells this is ALWAYS the corner cell + 1 on both
 * axes — never compare it directly with CoveragePlanner's corner space.
 * (The AI-side Navigator uses Math.round(x/CELL) on the top-left corner —
 * a THIRD convention; conversions are pinned by tests, not assumed.)
 */
export function tankCenterCell(
  t: Tank,
  out?: { col: number; row: number },
): { col: number; row: number } {
  const col = Math.floor((t.x + t.w / 2) / CELL)
  const row = Math.floor((t.y + t.h / 2) / CELL)
  if (out) {
    out.col = col
    out.row = row
    return out
  }
  return { col, row }
}

/**
 * Safety margin subtracted from the optimistic geometric ETAs before they may
 * be used as PERMISSION to act (protocol §4.2). The geometric model charges
 * the enemy zero turn costs and assumes a straight Manhattan walk, so one
 * full legal-turn window (the fairness rule, read not modified) restores a
 * measure of honesty: 200ms rules → 12 ticks, a future 500ms rule → 30.
 */
export function enemyEtaSafetyMargin(world: World): number {
  return msToTicks(world.rules?.turnCooldownMs ?? 200)
}

/**
 * Plan §5.2 — enemy deadline, with every field's bound-ness stated
 * explicitly (protocol §4.2 — a Manhattan number is EITHER a relative-order
 * lower bound OR a safe permission deadline, never both by relabeling):
 *
 * - Already shooting the base (csb) → first damage = fire readiness + flight.
 * - Already breaching the ring (cbr) → first damage = fire readiness + one
 *   cadence+flight per blocking brick (bricksBetween INCLUDES the ring
 *   bricks on the line) + the final flight. The breach cost lives HERE and
 *   only here — the damage window does NOT re-charge it.
 * - Otherwise → optimistic straight-line walk to the ring + a 2-cycle ring
 *   breach when the ring still stands (no charge once it is gone) + flight
 *   (the walk leg ignores turn costs; the safety margin on
 *   enemyDamageDeadline compensates).
 *
 * No enemy RNG is simulated. `enemyDamageDeadline` may be ≤ 0 — that means
 * the safe intervention window has closed.
 */
export function enemyDeadline(world: World, e: Tank, out?: EnemyDeadline): EnemyDeadline {
  const ec = tankCenterCell(e, _CELL_A)
  const fp = firePower(world, e.kind === 'player' ? 'basic' : e.kind)
  const cadence = e.nextFireInterval > 0 ? msToTicks(e.nextFireInterval) : 0
  const flight = e.bulletSpeed > 0 ? (CELL * 2) / e.bulletSpeed : 0 // ring line → base ≈ 2 cells
  const tpc = ticksPerCell(e)

  let nearestRingDist = Infinity
  for (const rc of RING_CELLS) {
    const d = manhattan(ec.col, ec.row, rc.col, rc.row)
    if (d < nearestRingDist) nearestRingDist = d
  }

  const csb = canShootBaseLine(world, ec.col, ec.row)
  const cbr = csb ? false : canBreachRingLine(world, ec.col, ec.row)
  const fireReady = ticksUntilFire(world, e)
  const ringIntact = RING_CELLS.some((c) => {
    const t = world.tileMap.get(c.col, c.row)
    return t === 'brick' || t === 'steel'
  })

  let enemyDamageEarliest: number
  if (csb) {
    enemyDamageEarliest = fireReady + flight
  } else if (cbr) {
    // Breach cost (bricks on the line, ring bricks included) charged once.
    const blocks = bricksBetween(world, ec.col, ec.row)
    enemyDamageEarliest = fireReady + blocks * (cadence + flight) + flight
  } else {
    // Ring gone → nothing left to breach: walk + flight only.
    const breachCycles = ringIntact ? 2 : 0
    enemyDamageEarliest = nearestRingDist * tpc + breachCycles * (cadence + flight) + flight
  }

  const baseShots = Math.max(1, Math.ceil(world.baseHp / Math.max(1, fp)))
  const enemyDamageWindow = baseShots * (cadence + flight)

  const enemyUrgency =
    (1 - world.baseHp / Math.max(1, world.baseMaxHp)) * 0.5 +
    (1 - (ringIntact ? 1 : 0)) * 0.2 +
    Math.min(1, baseShots / 4) * 0.3

  const o = out ?? {
    enemyArrivalLowerBound: 0,
    enemyDamageEarliest: 0,
    enemyDamageDeadline: 0,
    enemyDamageWindow: 0,
    enemyUrgency: 0,
    directThreat: false,
  }
  o.enemyArrivalLowerBound = nearestRingDist * tpc
  o.enemyDamageEarliest = enemyDamageEarliest
  o.enemyDamageDeadline = enemyDamageEarliest - enemyEtaSafetyMargin(world)
  o.enemyDamageWindow = enemyDamageWindow
  o.enemyUrgency = enemyUrgency
  o.directThreat = csb || cbr
  return o
}

/** Count of brick cells between (col,row) and the base on the aligned axis. */
function bricksBetween(world: World, col: number, row: number): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tm = world.tileMap
  let n = 0
  if (col === bc) {
    const step = row < br ? 1 : -1
    for (let r = row + step; r !== br && r >= 0 && r < GRID; r += step) {
      if (tm.get(col, r) === 'brick') n++
    }
  } else if (row === br || row === br + 1) {
    for (let c = Math.min(col, bc) + 1; c < Math.max(col, bc); c++) {
      if (tm.get(c, row) === 'brick') n++
    }
  }
  return n
}

/**
 * Plan §5.3 — player kill/intercept slack for one enemy.
 *
 * killSlack is measured against `enemyDamageDeadline` (the SAFE deadline =
 * earliest damage minus safety margin), so a positive killSlack is a
 * permission with margin already baked in. interceptSlack compares the
 * player's arrival against the enemy's ARRIVAL LOWER BOUND minus the same
 * margin — the geometric bound alone never justifies leaving a lane.
 */
export function killAssessment(
  world: World,
  p: Tank,
  e: Tank,
  out?: KillAssessment,
): KillAssessment {
  const ec = tankCenterCell(e, _CELL_A)
  const pc = tankCenterCell(p, _CELL_B)
  const aimDir = aimDirTo(ec.col, ec.row, pc.col, pc.row) ?? p.dir
  const shots = playerShotsToKill(world, e)
  const eta = playerActionEta(world, p, ec.col, ec.row, aimDir, shots, _ETA)
  const dl = enemyDeadline(world, e, _DL)
  // Copy BEFORE the second-threat loop (it reuses _DL — nested calls would
  // otherwise clobber `dl` before the return reads it).
  const damageDeadline = dl.enemyDamageDeadline
  const arrivalLB = dl.enemyArrivalLowerBound
  const margin = enemyEtaSafetyMargin(world)

  // Second threat: any OTHER enemy with a safe deadline before our kill lands.
  let missesSecondThreat = false
  for (const t of world.tanks) {
    if (t.id === e.id || !t.alive || t.spawnTimer > 0) continue
    if (enemyDeadline(world, t, _DL).enemyDamageDeadline < eta.total) {
      missesSecondThreat = true
      break
    }
  }

  const o = out ?? {
    playerArrivalAndAimEta: 0,
    firstFireEta: 0,
    playerKillEta: 0,
    killSlack: 0,
    interceptSlack: 0,
    missesSecondThreat: false,
  }
  o.playerArrivalAndAimEta = eta.movementEta + eta.aimAlignmentEta + eta.nextLegalTurnEta
  o.firstFireEta =
    eta.movementEta + eta.aimAlignmentEta + eta.nextLegalTurnEta + eta.fireCooldownEta
  o.playerKillEta = eta.total
  o.killSlack = damageDeadline - eta.total
  o.interceptSlack = arrivalLB - margin - (eta.movementEta + eta.aimAlignmentEta)
  o.missesSecondThreat = missesSecondThreat
  return o
}

/**
 * Plan §6.2 — dynamic target value for engage/hunt target selection.
 *
 * targetValue(e) = expectedBaseDamagePrevented(e) / (reachEta(e) + killEta(e))
 *
 * expectedBaseDamagePrevented: the base damage e will deal between now and
 * the moment the player's killing shot lands, if e is left alone — fp × the
 * number of shots e lands inside that horizon (shots before its first
 * damaging shot could land don't count; enemyDamageEarliest is subtracted),
 * capped at baseHp. horizon = eta.total (arrival + aim + fire cooldown +
 * re-arms + final flight, each cost exactly once).
 *
 * The value therefore rises as e approaches a shoot position (smaller
 * enemyToShootEta), rises when the player is slow (more interim damage
 * accrues before the kill), and falls for cheap kills — the plan's "value
 * varies with enemy deadline, player distance, shot cost", not a static
 * bonusHuntBias. Callers (StrategyPlanner selectTarget) compare across
 * enemies and pick the max; near-ties fall back to the standard distance
 * ordering.
 */
export function targetValue(world: World, p: Tank, e: Tank): number {
  const dl = enemyDeadline(world, e, _DL)
  const fp = firePower(world, e.kind === 'player' ? 'basic' : e.kind)
  const cadence = e.nextFireInterval > 0 ? msToTicks(e.nextFireInterval) : 0
  const flight = e.bulletSpeed > 0 ? (CELL * 2) / e.bulletSpeed : 0
  const cycle = Math.max(1, cadence + flight)
  const ec = tankCenterCell(e, _CELL_A)
  const pc = tankCenterCell(p, _CELL_B)
  const aimDir = aimDirTo(ec.col, ec.row, pc.col, pc.row) ?? p.dir
  const shots = playerShotsToKill(world, e)
  const eta = playerActionEta(world, p, ec.col, ec.row, aimDir, shots, _ETA)
  // §4.1: eta.total already contains the reach leg — do NOT add it twice.
  const horizon = Math.max(1, eta.total)
  const interimShots = Math.max(0, Math.floor((horizon - dl.enemyDamageEarliest) / cycle))
  const damagePrevented = Math.min(world.baseHp, fp * interimShots)
  return damagePrevented / horizon
}

/** Direction from (fromCol,fromRow) toward (toCol,toRow); null when equal. */
export function aimDirTo(
  toCol: number,
  toRow: number,
  fromCol: number,
  fromRow: number,
): Direction | null {
  const dx = toCol - fromCol
  const dy = toRow - fromRow
  if (dx === 0 && dy === 0) return null
  if (Math.abs(dx) >= Math.abs(dy)) return dx > 0 ? 'right' : 'left'
  return dy > 0 ? 'down' : 'up'
}

export interface StandingAssessment {
  killEta: number
  killSlack: number
  deadline: number
}

/**
 * Standing-shot assessment (plan Phase 2 §6.1): the player HOLDS position —
 * already aligned with the threat (the caller guarantees the ray) — and fires
 * as soon as legal. killEta = fireCooldownEta + (shots−1) re-arms + last
 * bullet flight; NO movement component, because a hold is a hold.
 */
export function standingKillAssessment(
  world: World,
  p: Tank,
  e: Tank,
  out?: StandingAssessment,
): StandingAssessment {
  const ec = tankCenterCell(e, _CELL_A)
  const pc = tankCenterCell(p, _CELL_B)
  const flight = manhattan(pc.col, pc.row, ec.col, ec.row) * ticksPerCellFire(p)
  const shots = playerShotsToKill(world, e)
  const cadence = p.fireCooldown > 0 ? msToTicks(p.fireCooldown) : msToTicks(p.nextFireInterval)
  const killEta = ticksUntilFire(world, p) + Math.max(0, shots - 1) * cadence + flight
  const deadline = enemyDeadline(world, e, _DL).enemyDamageDeadline
  if (out) {
    out.killEta = killEta
    out.deadline = deadline
    out.killSlack = deadline - killEta
    return out
  }
  return { killEta, killSlack: deadline - killEta, deadline }
}
