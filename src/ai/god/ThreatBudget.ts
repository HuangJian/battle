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
 * Terminology (plan §5):
 *   actionEta      = nextLegalTurnEta + movementEta + aimAlignmentEta
 *                    + fireCooldownEta + requiredShotsEta
 *   killSlack(e)   = enemyDamageDeadline(e) - playerKillEta(e)
 *   interceptSlack(I) = enemyArrivalEta(I) - playerArrivalAndAimEta(I)
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

import { CELL, GRID, BASE_POS, type Direction } from '../../constants'
import { resolveProfile } from '../../config/combat'
import type { World } from '../../game/World'
import type { Tank } from '../../types'

const TICK_MS = 1000 / 60

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
  nextLegalTurnEta: number
  movementEta: number
  aimAlignmentEta: number
  fireCooldownEta: number
  requiredShotsEta: number
  total: number
}

export interface EnemyDeadline {
  /** ETA to reach the ring (0 when already breaching). Conservative lower bound. */
  enemyToRingEta: number
  /** ETA to the first shot that can hit the base or a productive ring brick. */
  enemyToShootEta: number
  /** Window from first productive shot until the base is destroyed. */
  enemyDamageWindow: number
  /** enemyToShootEta + enemyDamageWindow — hard deadline for the player. */
  damageDeadline: number
  /** Composite 0..1+ urgency (base HP, ring integrity, shots required). */
  enemyUrgency: number
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
const manhattan = (aCol: number, aRow: number, bCol: number, bRow: number): number =>
  Math.abs(aCol - bCol) + Math.abs(aRow - bRow)

/** Ticks until the tank may legally turn (0 = a turn is allowed right now). */
export function ticksUntilLegalTurn(world: World, t: Tank): number {
  const turnCd = world.rules?.turnCooldownMs ?? 0
  if (turnCd <= 0) return 0
  const nowMs = world.frame * TICK_MS
  const elapsed = nowMs - (t.lastTurnMs ?? -9999)
  return elapsed >= turnCd ? 0 : msToTicks(turnCd - elapsed)
}

/** Ticks until the next shot is legal (0 = ready). Uses the frozen cadence. */
export function ticksUntilFire(world: World, t: Tank): number {
  const iv = t.nextFireInterval
  if (!(iv > 0)) return 0
  const nowMs = world.frame * TICK_MS
  const elapsed = nowMs - (t.lastFire ?? -9999)
  return elapsed >= iv ? 0 : msToTicks(iv - elapsed)
}

/** Ticks one legal turn consumes (the cooldown window itself). */
function turnCostTicks(world: World): number {
  return msToTicks(world.rules?.turnCooldownMs ?? 0) + 1
}

/** Ticks per cell of movement at the tank's current speed (px/tick). */
const ticksPerCell = (t: Tank): number => (t.speed > 0 ? CELL / t.speed : 1e9)

/**
 * Plan §5.1 — actionEta for reaching (targetCol, targetRow) and firing at
 * aimDir: nextLegalTurnEta + movementEta + aimAlignmentEta + fireCooldownEta
 * + requiredShotsEta. Movement uses a conservative geometric Manhattan path
 * (no A*): axis distance plus one legal-turn cost for the required
 * perpendicular change. `shots` is the total shots needed (≥1).
 */
export function playerActionEta(
  world: World,
  p: Tank,
  targetCol: number,
  targetRow: number,
  aimDir: Direction,
  shots: number,
): ActionEta {
  const tpc = ticksPerCell(p)
  const pc = { col: Math.floor((p.x + p.w / 2) / CELL), row: Math.floor((p.y + p.h / 2) / CELL) }
  const cellsX = Math.abs(pc.col - targetCol)
  const cellsY = Math.abs(pc.row - targetRow)
  const needsTurn = p.dir !== aimDir
  const nextLegalTurnEta = needsTurn ? ticksUntilLegalTurn(world, p) : 0
  // A perpendicular path change costs one full turn (cooldown window) on top
  // of the axis distance; moving along a single axis costs nothing extra.
  const pathTurns = cellsX > 0 && cellsY > 0 ? 1 : 0
  const turnTicks = needsTurn || pathTurns > 0 ? turnCostTicks(world) : 0
  const movementEta = (cellsX + cellsY) * tpc
  const aimAlignmentEta = needsTurn ? nextLegalTurnEta + turnTicks : 0
  const fireCooldownEta = ticksUntilFire(world, p)
  // Shots after the first re-arm per base cadence; the final bullet must fly.
  const flightEta = manhattan(pc.col, pc.row, targetCol, targetRow) * ticksPerCellFire(p)
  const cadenceTicks = p.fireCooldown > 0 ? msToTicks(p.fireCooldown) : msToTicks(p.nextFireInterval)
  const requiredShotsEta = Math.max(0, shots - 1) * cadenceTicks + flightEta
  const total = nextLegalTurnEta + movementEta + aimAlignmentEta + fireCooldownEta + requiredShotsEta
  return { nextLegalTurnEta, movementEta, aimAlignmentEta, fireCooldownEta, requiredShotsEta, total }
}

/** Player bullet flight per cell (px/tick speed). */
function ticksPerCellFire(p: Tank): number {
  const bs = p.bulletSpeed
  return bs > 0 ? CELL / bs : 0
}

/** Firepower (damage against the base pool) of a tank kind at its level. */
export function firePower(world: World, kind: 'player' | 'basic' | 'fast' | 'power' | 'armor'): number {
  const level = kind === 'player' ? world.playerLevel : 0
  return resolveProfile(kind, level).firepower
}

/** Per-shot damage against tanks (pool model). */
export function bulletDamage(world: World, kind: 'player' | 'basic' | 'fast' | 'power' | 'armor'): number {
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

/** Enemy cell (center-based), mirroring tankCellImpl. */
export function tankCell(t: Tank): { col: number; row: number } {
  return { col: Math.floor((t.x + t.w / 2) / CELL), row: Math.floor((t.y + t.h / 2) / CELL) }
}

/**
 * Plan §5.2 — enemy deadline. Conservative, explainable bounds; no enemy RNG.
 * - Already shooting the base (csb) → shootEta 0.
 * - Already breaching the ring (cbr) → shootEta = time to clear the brick(s)
 *   on its line (geometric: bricks between enemy and base on the aligned
 *   axis, at the enemy's cadence) — the breach flips to csb when the last
 *   blocking brick falls.
 * - Otherwise → geometric walk to the nearest ring cell, then the ring
 *   breach is assumed immediate-but-conservative (the enemy walks in and
 *   shoots; ring bricks before it are charged one cadence each).
 * - Damage window: shots needed (baseHp / firepower) at the enemy cadence,
 *   each shot requiring one flight from the ring line to the base.
 */
export function enemyDeadline(world: World, e: Tank): EnemyDeadline {
  const ec = tankCell(e)
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

  let enemyToShootEta: number
  if (csb) {
    enemyToShootEta = 0
  } else if (cbr) {
    // Bricks between the enemy and the base line on its aligned axis.
    const blocks = bricksBetween(world, ec.col, ec.row)
    enemyToShootEta = blocks * (cadence + flight)
  } else {
    enemyToShootEta = nearestRingDist * tpc + 2 * (cadence + flight)
  }

  const ringIntact = RING_CELLS.some((c) => {
    const t = world.tileMap.get(c.col, c.row)
    return t === 'brick' || t === 'steel'
  })
  const baseShots = Math.max(1, Math.ceil(world.baseHp / Math.max(1, fp)))
  const enemyDamageWindow = baseShots * (cadence + flight) + (ringIntact && !csb ? 2 * (cadence + flight) : 0)

  const enemyUrgency =
    (1 - world.baseHp / Math.max(1, world.baseMaxHp)) * 0.5 +
    (1 - (ringIntact ? 1 : 0)) * 0.2 +
    Math.min(1, baseShots / 4) * 0.3

  return {
    enemyToRingEta: nearestRingDist * tpc,
    enemyToShootEta,
    enemyDamageWindow,
    damageDeadline: enemyToShootEta + enemyDamageWindow,
    enemyUrgency,
  }
}

/** Count of brick cells between (col,row) and the base on the aligned axis. */
function bricksBetween(world: World, col: number, row: number): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tm = world.tileMap
  let n = 0
  if (col === bc) {
    const step = row < br ? 1 : -1
    for (let r = row + step; r !== br; r += step) {
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
 */
export function killAssessment(world: World, p: Tank, e: Tank): KillAssessment {
  const ec = tankCell(e)
  const pc = tankCell(p)
  const aimDir = aimDirTo(ec.col, ec.row, pc.col, pc.row) ?? p.dir
  const shots = playerShotsToKill(world, e)
  const eta = playerActionEta(world, p, ec.col, ec.row, aimDir, shots)
  const dl = enemyDeadline(world, e)

  // Second threat: any OTHER enemy with a shoot deadline before our kill lands.
  let missesSecondThreat = false
  for (const t of world.tanks) {
    if (t.id === e.id || !t.alive || t.spawnTimer > 0) continue
    if (enemyDeadline(world, t).damageDeadline < eta.total) {
      missesSecondThreat = true
      break
    }
  }

  return {
    playerArrivalAndAimEta: eta.movementEta + eta.aimAlignmentEta + eta.nextLegalTurnEta,
    firstFireEta: eta.movementEta + eta.aimAlignmentEta + eta.nextLegalTurnEta + eta.fireCooldownEta,
    playerKillEta: eta.total,
    killSlack: dl.damageDeadline - eta.total,
    interceptSlack: dl.enemyToRingEta - (eta.movementEta + eta.aimAlignmentEta),
    missesSecondThreat,
  }
}

/**
 * Plan §6.2 — dynamic target value for engage/hunt target selection.
 *
 * targetValue(e) = expectedBaseDamagePrevented(e) / (reachEta(e) + killEta(e))
 *
 * expectedBaseDamagePrevented: the base damage e will deal between now and
 * the moment the player's killing shot lands, if e is left alone — fp × the
 * number of shots e lands inside that horizon (shots before its first
 * productive shot don't count; enemyToShootEta is subtracted), capped at
 * baseHp. horizon = arrival + aim + fire cooldown + re-arms + final flight.
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
  const dl = enemyDeadline(world, e)
  const fp = firePower(world, e.kind === 'player' ? 'basic' : e.kind)
  const cadence = e.nextFireInterval > 0 ? msToTicks(e.nextFireInterval) : 0
  const flight = e.bulletSpeed > 0 ? (CELL * 2) / e.bulletSpeed : 0
  const cycle = Math.max(1, cadence + flight)
  const ec = tankCell(e)
  const pc = tankCell(p)
  const aimDir = aimDirTo(ec.col, ec.row, pc.col, pc.row) ?? p.dir
  const shots = playerShotsToKill(world, e)
  const eta = playerActionEta(world, p, ec.col, ec.row, aimDir, shots)
  const reach = eta.movementEta + eta.aimAlignmentEta + eta.nextLegalTurnEta
  const horizon = Math.max(1, reach + eta.total)
  const interimShots = Math.max(0, Math.floor((horizon - dl.enemyToShootEta) / cycle))
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
): { killEta: number; killSlack: number; deadline: number } {
  const ec = tankCell(e)
  const pc = tankCell(p)
  const flight = manhattan(pc.col, pc.row, ec.col, ec.row) * ticksPerCellFire(p)
  const shots = playerShotsToKill(world, e)
  const cadence = p.fireCooldown > 0 ? msToTicks(p.fireCooldown) : msToTicks(p.nextFireInterval)
  const killEta = ticksUntilFire(world, p) + Math.max(0, shots - 1) * cadence + flight
  const deadline = enemyDeadline(world, e).damageDeadline
  return { killEta, killSlack: deadline - killEta, deadline }
}
