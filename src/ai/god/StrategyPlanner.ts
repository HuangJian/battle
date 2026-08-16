import type { GodAIInput } from '../GodAIInput'
import type { Tank, PowerUpType } from '../../types'
import type { World } from '../../game/World'
import { findPath, type Cell } from '../../utils/pathfind'
import { CELL, BASE_POS, POWERUP_TIMEOUT_MS, GRID } from '../../constants'
import { BALANCED_ENEMY_CPS, BASE_SPEED_CPS } from '../../config/speed'
import { POWERUP_PRIORITY, kindThreatWeight } from './constants'
import type { GodAIParams } from './params'
import { enemyCanShootBase, enemyCanBreachRing } from './SmartThreatModel'
import { targetValue, enemyDeadline, killAssessment } from './ThreatBudget'
import { coveragePlanImpl } from './CoveragePlanner'
import type { ActionId } from './DecisionCore'
import { blocksBullet } from './Chokepoint'

// ---- Phase 2 §6.3 / open-test protocol §5.3: short-term action intent ----
// A hunt/engage target is locked only for a lease; revalidation releases on
// target death/unreachability, lease expiry, player stall (no move + no fire
// for the window), slack collapse past the committed slack, a clearly worse
// new threat, or the committed action no longer beating the safe deadline.
// This is NOT a longer huntCommitTicks — expiry, progress and threat
// constraints all bind. The defense cascade runs above selectTarget, so an
// intent never delays threat response. intentMode 0 = never read/written
// (byte-identical).
export interface ActionIntent {
  kind: ActionId
  targetId: number
  expiresTick: number
  /**
   * TRUE slack at commit time (protocol §5.3): the enemy's SAFE damage
   * deadline minus the player's kill ETA for that target. Never a raw
   * deadline value wearing a slack name.
   */
  committedSlack: number
  expectedProgress: number
  lastMoveFrame: number
  lastPlayerCol: number
  lastPlayerRow: number
}
/**
 * New-threat release: another enemy's deadline must be this many ticks
 * tighter to count as "clearly more urgent" (§5.3). One cell of enemy
 * approach ≈ 15.5 ticks — a mere 1-cell-closer harmless enemy must NOT
 * release the intent, so the bar sits at ~2 cells of urgency.
 */
const INTENT_THREAT_DELTA = 30
/** Slack release: committed deadline tightened past minSlack − relax. */
const INTENT_SLACK_RELAX = 10
/** Flight release: distance grew past expectedProgress + this (cells). */
const INTENT_PROGRESS_DEGRADE = 2
/** Revalidation cadence for the expensive deadline scan (plan band 6~15). */
const INTENT_REVALIDATE_EVERY = 10

/**
 * Validate the active intent and return the committed target cell, or null
 * when any release condition holds (caller falls through to a fresh pick,
 * which re-commits via intentWrite). Cheap conditions (lease, alive, stall,
 * flight) run every call; the deadline scans run on the INTENT_REVALIDATE_EVERY
 * frame grid (deterministic — frame-based, no RNG).
 */
function intentRead(
  self: GodAIInput,
  w: World,
  p: Tank,
  playerCell: Cell,
  enemies: Tank[],
): Cell | null {
  const it = self._intent
  if (!it) return null
  if (w.frame >= it.expiresTick) return null // lease expired
  let ti = -1
  for (let i = 0; i < enemies.length; i++) {
    if (enemies[i].id === it.targetId) {
      ti = i
      break
    }
  }
  if (ti < 0) return null // target dead or unreachable
  const t = enemies[ti]
  // Track movement for the stall check (also refreshes after player respawn).
  if (it.lastPlayerCol !== playerCell.col || it.lastPlayerRow !== playerCell.row) {
    it.lastMoveFrame = w.frame
    it.lastPlayerCol = playerCell.col
    it.lastPlayerRow = playerCell.row
  }
  // Stall: unmoved and not recently firing for the window → release. Firing
  // means the player is still engaging (hold-fire) — fireCooldown > 0 marks a
  // recent shot (cadence ≥ 99, so the window cannot outlive the marker).
  if (w.frame - it.lastMoveFrame >= self.params.intentProgressWindowTicks && p.fireCooldown <= 0) {
    return null
  }
  // Flight: target farther than at commit (+ degrade) → the approach sunk.
  const tc = self.tankCell(t)
  const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
  if (d > it.expectedProgress + INTENT_PROGRESS_DEGRADE && p.fireCooldown <= 0) return null
  // Threat revalidation (throttled): the CURRENT slack (safe deadline minus
  // current kill ETA, legal-turn waits included) must not have collapsed
  // past the committed slack, and no other enemy's deadline may be clearly
  // more urgent than the committed target's.
  if (w.frame % INTENT_REVALIDATE_EVERY === 0) {
    const committedDeadline = enemyDeadline(w, t).enemyDamageDeadline
    const currentSlack = committedDeadline - killAssessment(w, p, t).playerKillEta
    if (currentSlack < 0) return null // the committed action now misses the deadline
    if (currentSlack < it.committedSlack - INTENT_SLACK_RELAX) return null
    for (let i = 0; i < enemies.length; i++) {
      if (enemies[i].id === it.targetId) continue
      if (enemyDeadline(w, enemies[i]).enemyDamageDeadline < committedDeadline - INTENT_THREAT_DELTA)
        return null
    }
  }
  return tc
}

/** Commit the freshly picked target as a short-term intent. */
function intentWrite(self: GodAIInput, w: World, playerCell: Cell, best: Tank) {
  const tc = self.tankCell(best)
  const ka = killAssessment(w, w.player!, best)
  self._intent = {
    kind: 'hunt',
    targetId: best.id,
    expiresTick: w.frame + self.params.intentLeaseTicks,
    committedSlack: ka.killSlack,
    expectedProgress: Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row),
    lastMoveFrame: w.frame,
    lastPlayerCol: playerCell.col,
    lastPlayerRow: playerCell.row,
  }
}

// ============================================================
// StrategyPlanner — target selection (S6 attack-defense), power-up
// economy (S5), and default defense positioning.
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * S5a/S5c/NEW-Requirement-3: Find a power-up worth collecting.
 * Returns the target cell if a power-up is available and worth the risk,
 * null otherwise.
 *
 * NEW: Dynamic priority based on:
 *   - Power-up effect (bomb > star > freeze > fence > tank > shield/helmet > boat)
 *   - Travel distance (cost in time/opportunity)
 *   - Route danger (how many enemies are between player and power-up)
 */
export function findPowerUpTargetImpl(self: GodAIInput, pcx: number, pcy: number): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null

  // Reusable best-cell fields (perf §65): avoid allocating `{cell, score}`
  // per upgrade — track the best cell's (col,row) + score as scalars, and
  // return a single fresh Cell at the end. findPowerUpTarget is called up
  // to ~2× per think (normal + aggressive branches); each call used to
  // allocate up to N (1 per power-up) intermediate `{cell, score}` objects.
  let bestCol = 0
  let bestRow = 0
  let bestScore = -Infinity
  let hasBest = false

  // BONUS TIME (DECISIONS §84): the stage is cleared (last enemy destroyed)
  // and the pickup window is running — the player is in the "grab the bonus"
  // grace period (POWERUP_PICKUP_WINDOW_MS = 10s) before the stage auto-ends.
  // With nothing left to fight or defend, the normal divert-distance /
  // route-danger caps are lifted: the WHOLE field is fair game, and the only
  // scarce resource is TIME. Items are scored by despawn urgency first (an
  // item about to expire must be grabbed NOW), then nearest-first to maximize
  // how many items are collected inside the window, with a small priority
  // tie-break. Without this, an item farther than `powerupMaxDivertDistance`
  // cells was never targeted and the AI stood at the defense position while
  // the window expired (督战 spectate: the God player never collected the
  // bonus loot).
  const bonusWindow = w.pickupWindowEntered && w.pickupWindowTimer > 0

  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)

    // S5d: if about to expire and too far, skip.
    const lifeRemaining = POWERUP_TIMEOUT_MS - pu.lifeTimer
    if (!bonusWindow && lifeRemaining < 3000 && dist > 5) continue

    // S5a: base priority by type.
    const priority = POWERUP_PRIORITY[pu.type] ?? 5

    let score: number
    // The cell the tank must drive to — the item's own cell normally, or an
    // overlapping passable neighbour when the item sits on blocking terrain.
    let collectCol = Math.floor(pu.x / CELL)
    let collectRow = Math.floor(pu.y / CELL)
    // §187: skip powerups overlapping with a live enemy — kill enemy first.
    if (powerupHasEnemyOverlap(self, collectCol, collectRow)) continue
    if (bonusWindow) {
      // §84-revisit (DECISIONS §85): skip items the tank can never reach
      // (steel/water-enclosed pockets). Chasing an unreachable item every
      // tick burns the whole window — `navigateTowards` always answers null
      // for it. Uses the exact same A* the navigator drives (corridors, then
      // dig-through-brick), so this matches what the tank can actually drive.
      // Pure function of World state — no RNG, no cache mutation.
      const collect = powerUpCollectCell(self, collectCol, collectRow)
      if (!collect) continue
      collectCol = collect.col
      collectRow = collect.row
      // Time-boxed loot run: an item with <5s of life left is in imminent
      // despawn danger — its urgency boost (up to 2000) outweighs any
      // distance/priority difference. Otherwise nearest-first (dist*10)
      // sweeps the field, with a mild priority tie-break ((6-priority)*10).
      const urgency = lifeRemaining < 5000 ? (5000 - lifeRemaining) / 2.5 : 0
      score = urgency - dist * 10 + (6 - priority) * 10
    } else {
      // NEW Requirement 3: Calculate route danger
      const dangerLevel = self.calculateRouteDanger(pcx, pcy, cx, cy)

      // Fix Bug 2: Score formula was reversed — priority * 1000 gave bomb
      // (priority=0) a base score of 0 and boat (priority=6) a base of 6000.
      // Now (6 - priority) * 1000 gives bomb the highest base score.
      score = (6 - priority) * 1000 - dist * 10 - dangerLevel * 500

      // Extra bonus for bomb (it clears the whole screen, worth high risk)
      if (pu.type === 'bomb') {
        score += 2000
      }

      // Extra bonus for star (permanent upgrade)
      if (pu.type === 'star') {
        score += 1000
      }

      // Penalty for boat (only situationally useful)
      if (pu.type === 'boat') {
        score -= 500
      }

      // Fix Bug 3: maxDist logic was reversed — high-value power-ups (bomb/star)
      // should allow LONGER diversion distance, not shorter.
      const maxDist = priority <= 1 ? 8 : self.params.powerupMaxDivertDistance
      if (dist > maxDist) continue

      // NEW: Don't collect if route is too dangerous unless it's a bomb/star
      if (dangerLevel > 3 && priority > 2) continue // Too dangerous for low-value power-ups
      if (dangerLevel > 5 && pu.type !== 'bomb') continue // Bomb is worth almost any risk
    }

    if (score > bestScore) {
      bestScore = score
      bestCol = collectCol
      bestRow = collectRow
      hasBest = true
    }
  }

  return hasBest ? { col: bestCol, row: bestRow } : null
}

/**
 * §87 (user request 2026-08-02): urgent power-up pickup — a CLOSE power-up
 * with a SAFE PATH outranks base defense (回防) and enemy-kill (杀敌)
 * targets.
 *
 * Categories (distance gates, tunable):
 *   HIGH — bomb/freeze/fence:   pickupPriorityHighRange cells (target 8)
 *   MID  — star/tank/shield:    pickupPriorityMidRange cells (target 4)
 *   LOW  — boat:                pickupPriorityLowRange cells (target 2)
 *
 * "Path safe" = route danger (enemies between the player and the item,
 * calculateRouteDanger) <= pickupPriorityMaxDanger (target 0) AND the item
 * is reachable via A* (powerUpCollectCell — the same corridor/dig path the
 * navigator drives; steel/water-enclosed pockets are skipped, never chased).
 *
 * Nearest-first, ties broken toward the higher-value item (lower
 * POWERUP_PRIORITY). Returns the collect cell (possibly an overlapping
 * passable neighbour) or null. Gated by pickupPriorityMode — the caller
 * (think) only invokes this when > 0, so OFF is byte-identical.
 */
/** §88 tier filter: HIGH = bomb/freeze/fence (+ modern emp/guard); MID/LOW = the rest. */
function urgentTier(type: PowerUpType): 'high' | 'midlow' {
  switch (type) {
    case 'bomb':
    case 'freeze':
    case 'fence':
    case 'emp':
    case 'guard':
      return 'high'
    default:
      return 'midlow'
  }
}

export function findUrgentPowerUpTargetImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  tier: 'all' | 'high' | 'midlow' = 'all',
): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null
  const p = self.params
  if (p.pickupPriorityMode <= 0) return null

  // Nearby-enemy gate (per-seed tick-diff finding, Lattice s2 / Battlement
  // s3): "path safe" must also mean no enemy is breathing down the player's
  // neck. The route-danger check below only counts enemies BETWEEN the player
  // and the item; an enemy 5 cells away (or an active firefight) was still
  // abandoned while the player walked to the item, then the player stalled
  // or stopped firing and died. Same radius as the S5 P3.2 gate (5 cells).
  // D5 (plan §D5): demoted from an early return to a per-item flag — star/
  // tank items inside the base box (row >= pickupStarBoxRow) are exempt in
  // the loop below. 0 = pre-D5 (every item blocked → null, byte-identical).
  let nearbyEnemy = false
  if (p.pickupPriorityMinEnemyDist > 0) {
    const playerCol = Math.floor(pcx / CELL)
    const playerRow = Math.floor(pcy / CELL)
    // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
    const nearbyScan = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let ni = 0; ni < nearbyScan.length; ni++) {
      const t = nearbyScan[ni]
      if (!t.alive || t.spawnTimer > 0) continue
      const eCol = Math.floor(t.x / CELL)
      const eRow = Math.floor(t.y / CELL)
      if (Math.abs(eCol - playerCol) + Math.abs(eRow - playerRow) <= p.pickupPriorityMinEnemyDist) {
        nearbyEnemy = true
        break
      }
    }
  }

  // §166 / B1: star rush — below starRushMaxLevel the first star is a
  // permanent-DPS upgrade worth a field-wide errand (M6/M11: each star ~
  // +9pp; hard 35x120 forensics: 75% of base-death losses never picked a
  // star). Computed once per scan; the level cannot change mid-scan
  // (pickups resolve in the Simulation, not here).
  let starRush = false
  if (p.starRushMode > 0) {
    const ct = self.controlledTank(w)
    starRush = (ct?.level ?? 0) < p.starRushMaxLevel
  }
  const liftGates = starRush && p.starRushLiftGates > 0

  let bestCol = 0
  let bestRow = 0
  let bestDist = Infinity
  let bestPriority = Infinity
  let hasBest = false

  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    // D5 (plan §D5): star/tank in the base box bypass the §87 nearby-enemy
    // gate AND the route-danger gate — with 4 enemies on field both gates
    // block forever, starving the player at 1★ (Battlement star 0.07/run).
    // A base-box star/tank is a permanent-DPS upgrade worth the risk.
    // 0 = pre-D5 byte-identical.
    const starInBox =
      p.pickupStarBoxRow > 0 &&
      (pu.type === 'star' || pu.type === 'tank') &&
      Math.floor(pu.y / CELL) >= p.pickupStarBoxRow
    // §166 / B1: rush stars share the D5 exemption shape (field-wide).
    const rushStar = starRush && pu.type === 'star'
    if (nearbyEnemy && !starInBox && !(rushStar && liftGates)) continue
    // §88 tier gate: only consider items of the requested tier (default 'all'
    // = pre-§88 behavior, byte-identical).
    if (tier !== 'all' && urgentTier(pu.type) !== tier) continue
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)
    let range = urgentPickupRange(pu.type, p)
    // §166 / B1: extended urgent range for rush stars (4 -> starRushRangeCells).
    if (rushStar && p.starRushRangeCells > range) range = p.starRushRangeCells
    // Distance gate by category — the core of the tuning sweep.
    if (range <= 0 || dist > range) continue

    // 双玩家协作: 伙伴明显更近的道具跳过, 让伙伴拾取 (避免争抢)
    if (self.hasLivingPartner()) {
      const partner = self.coopPartner()!
      const pcx2 = partner.x + partner.w / 2
      const pcy2 = partner.y + partner.h / 2
      const partnerDist = Math.round((Math.abs(cx - pcx2) + Math.abs(cy - pcy2)) / CELL)
      if (partnerDist < dist - 3) continue
    }

    // Enemy spawn-zone gate (per-seed tick-diff finding, Lattice s2/s32):
    // items in the enemy spawn band (classic spawns at row 0) are traps —
    // the player dives in and meets fresh spawns. Never treat the band as
    // an urgent errand; S5 navigation can still fetch far items there.
    if (p.pickupPrioritySpawnRowMax > 0 && Math.floor(pu.y / CELL) <= p.pickupPrioritySpawnRowMax) {
      continue
    }

    // Path safety gate: no enemy between the player and the item (D5: the
    // base-box star/tank exemption above also lifts this gate — both §87
    // gates starve under 4-enemy field pressure).
    if (
      self.calculateRouteDanger(pcx, pcy, cx, cy) > p.pickupPriorityMaxDanger &&
      !starInBox &&
      !(rushStar && liftGates)
    )
      continue

    // Reachability gate: the tank must be able to drive to a cell that
    // overlaps the item (same A* as the bonus window). Without this the
    // player would chase a steel/water-enclosed pocket every tick.
    const collect = powerUpCollectCell(self, Math.floor(pu.x / CELL), Math.floor(pu.y / CELL))
    if (!collect) continue

    const priority = POWERUP_PRIORITY[pu.type] ?? 5
    if (dist < bestDist || (dist === bestDist && priority < bestPriority)) {
      bestDist = dist
      bestPriority = priority
      bestCol = collect.col
      bestRow = collect.row
      hasBest = true
    }
  }

  return hasBest ? { col: bestCol, row: bestRow } : null
}

/** §152-W3: is an alive power-up at (col,row)? Pure World read (no RNG). */
function powerUpAliveAt(self: GodAIInput, col: number, row: number): boolean {
  const powerUps = self.world.powerUps
  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    if (Math.floor(pu.x / CELL) === col && Math.floor(pu.y / CELL) === row) return true
  }
  return false
}

/**
 * §187: Does a live enemy tank overlap the powerup at (col,row)?
 * Both tanks and powerups occupy a 2×2 sub-block footprint, so the overlap
 * check is |ec - col| <= 1 && |er - row| <= 1. When true, the player should
 * kill the enemy first (via hunt/aggro) before navigating to the powerup.
 * Pure World read (no RNG).
 */
function powerupHasEnemyOverlap(self: GodAIInput, col: number, row: number): boolean {
  if (self.params.powerupEnemyOverlapSkip <= 0) return false
  const tanks = self.world.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    const tc = self.tankCell(t)
    if (Math.abs(tc.col - col) <= 1 && Math.abs(tc.row - row) <= 1) return true
  }
  return false
}

/**
 * §152-W3: the ITEM cell of the alive power-up whose collect cell equals
 * `target` (the cell findUrgentPowerUpTargetImpl returned), or null. The
 * collect cell can differ from the item cell when the item sits on blocking
 * terrain — the commit must re-verify the ITEM (existence) while navigating
 * to the COLLECT cell. Uses the same memoized reachability as the lookup,
 * so repeated per-tick re-verification is cheap.
 */
function itemCellForCollect(self: GodAIInput, target: Cell): Cell | null {
  const powerUps = self.world.powerUps
  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const ic = Math.floor(pu.x / CELL)
    const ir = Math.floor(pu.y / CELL)
    const collect = powerUpCollectCell(self, ic, ir)
    if (!collect) continue
    if (collect.col === target.col && collect.row === target.row) {
      return { col: ic, row: ir }
    }
  }
  return null
}

/**
 * §152-W3: urgent power-up target WITH commit persistence.
 *
 * Same as findUrgentPowerUpTargetImpl, but once a pursuit commits to an item,
 * it keeps returning that item's collect cell until the item is collected /
 * despawned or the pickupCommitTicks window expires — the transient
 * "dist > range" exclusion (the player MOVING toward the item pushed its
 * manhattan distance past the category range) must not cancel an active
 * pursuit, or the player oscillates between the item and the nav target
 * forever (hard S12 Lattice seed 934391936 W3: ~800 ticks at
 * (21,18)↔(22,18), zero kills, while enemies swarmed the base).
 *
 * The commit only continues when the committed item is still alive — a
 * collected/despawned item ends the pursuit immediately. Higher-weight
 * candidates (dodge/interceptBase) naturally preempt the pickup on threat
 * ticks; the commit state survives those preemptions and resumes after.
 * 0 = OFF (pickupCommitTicks <= 0 → byte-identical to the plain lookup).
 */
export function findUrgentPowerUpTargetWithCommitImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  tier: 'all' | 'high' | 'midlow' = 'all',
): Cell | null {
  const p = self.params
  if (p.pickupCommitTicks <= 0) return findUrgentPowerUpTargetImpl(self, pcx, pcy, tier)

  // ---- Active commit: continue while the item lives within the window ----
  if (self._pickupCommitActive) {
    self._pickupCommitTicks++
    const itemAlive = powerUpAliveAt(self, self._pickupCommitItemCol, self._pickupCommitItemRow)
    if (!itemAlive || self._pickupCommitTicks > p.pickupCommitTicks) {
      self._pickupCommitActive = false
      self._pickupCommitTicks = 0
    } else {
      return { col: self._pickupCommitCol, row: self._pickupCommitRow }
    }
  }

  // ---- No active commit — fresh lookup, then arm the commit ----
  const target = findUrgentPowerUpTargetImpl(self, pcx, pcy, tier)
  if (target) {
    const itemCell = itemCellForCollect(self, target)
    if (itemCell) {
      self._pickupCommitActive = true
      self._pickupCommitTicks = 0
      self._pickupCommitCol = target.col
      self._pickupCommitRow = target.row
      self._pickupCommitItemCol = itemCell.col
      self._pickupCommitItemRow = itemCell.row
    }
    return target
  }
  return null
}

/**
 * §156/§158: shared logic — find the nearest reachable power-up within
 * `range` cells (Manhattan). Skips enemy/danger gates — the caller is
 * responsible for ensuring safety (freeze = enemies can't move; close =
 * DODGE already handled bullet threats). Checks A* reachability so the
 * player doesn't chase unreachable items (steel/water-enclosed pockets).
 */
function findNearestReachablePowerUp(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  range: number,
): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null

  let bestCol = 0
  let bestRow = 0
  let bestDist = Infinity
  let bestPriority = Infinity
  let hasBest = false

  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)
    if (dist > range) continue

    // Reachability gate: same A* as the normal pickup — don't chase
    // steel/water-enclosed pockets.
    const collect = powerUpCollectCell(self, Math.floor(pu.x / CELL), Math.floor(pu.y / CELL))
    if (!collect) continue

    // §187: skip powerups overlapping with a live enemy — kill enemy first.
    if (powerupHasEnemyOverlap(self, collect.col, collect.row)) continue

    const priority = POWERUP_PRIORITY[pu.type] ?? 5
    if (dist < bestDist || (dist === bestDist && priority < bestPriority)) {
      bestDist = dist
      bestPriority = priority
      bestCol = collect.col
      bestRow = collect.row
      hasBest = true
    }
  }

  return hasBest ? { col: bestCol, row: bestRow } : null
}

/**
 * §156: freeze-window power-up pickup (unlimited range). During freeze,
 * enemies are frozen and cannot move or fire. The only threat is in-flight
 * bullets, which DODGE (weight 1000 > AGGRO 700) already handles. So ANY
 * reachable power-up should be picked up BEFORE stop-and-aim at a frozen
 * enemy — the frozen enemy will still be there later.
 *
 * §156-v2: range changed from 2 to 999 (effectively unlimited). During
 * freeze the player should traverse the map to grab any reachable item.
 *
 * Gated by freezePickupRange (0 = OFF, byte-identical). The caller (AGGRO
 * candidate) only invokes this when self.aggressive && w.freezeTimer > 0.
 */
export function findFreezePickupTargetImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Cell | null {
  const range = self.params.freezePickupRange
  if (range <= 0) return null
  return findNearestReachablePowerUp(self, pcx, pcy, range)
}

/**
 * §158: non-freeze close-range power-up pickup. When NOT in freeze/shield
 * mode, a power-up within `closePickupRange` (default 4) cells is worth
 * grabbing if there is no immediate bullet threat (DODGE at weight 1000
 * already declined — if it hadn't, this candidate would never run).
 *
 * Unlike findUrgentPowerUpTargetImpl, this skips the nearby-enemy gate and
 * the route-danger gate — close items are worth grabbing even with enemies
 * nearby, as long as no bullet is currently threatening the player. The
 * player fires at enemies in the move direction while navigating (随手开火).
 *
 * Gated by closePickupRange (0 = OFF, byte-identical).
 */
export function findClosePickupTargetImpl(self: GodAIInput, pcx: number, pcy: number): Cell | null {
  const range = self.params.closePickupRange
  if (range <= 0) return null
  return findNearestReachablePowerUp(self, pcx, pcy, range)
}

/** §87: distance gate (cells) for a power-up type. 0 = never urgent. */
function urgentPickupRange(type: PowerUpType, p: GodAIParams): number {
  switch (type) {
    case 'bomb':
    case 'freeze':
    case 'fence':
    case 'emp': // modern: crowd-control, freeze-like
    case 'guard': // modern: base protection, fence-like
      return p.pickupPriorityHighRange
    case 'boat':
      return p.pickupPriorityLowRange
    default:
      // star / tank / shield (+ modern extras rewind/repair/decoy/mine/…)
      return p.pickupPriorityMidRange
  }
}

/**
 * E1 / 道具经济 (plan/Battlement-Hard-Exploration 反证判据): 危急道具拾取 —
 * when the base is in a DIRE state (enemies swarming within
 * direItemApproachCells OR the base ring damaged at/below direItemRingLow), a
 * nearby bomb/freeze/fence/emp is worth a divert even with enemies nearby.
 * The item's ACTIVE effect resolves the dire state directly (bomb clears the
 * staging field, freeze buys a kill window, fence reinforces the breached
 * ring), unlike star (passive — D5(b) measured flat). Bypasses the §87
 * nearby-enemy + route-danger gates (which block under exactly this 4-enemy
 * field pressure); reachability + spawn-band gates still apply.
 *
 * Probe-verified scope (2026-08-05, 7 seeds): only the high-kill losses
 * (2/7) had uncollected HIGH items within 10 cells of the player in the
 * final window — the other 5/7 are kill-starved upstream (zero drops), so
 * this knob is bounded by ~2/7 of losses. Pure World-state reads (findPath
 * draws no RNG) — gated by direItemMode (0 = OFF, byte-identical).
 */
export function findDireItemTargetImpl(self: GodAIInput, pcx: number, pcy: number): Cell | null {
  const p = self.params
  if (p.direItemMode <= 0) return null
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null
  const bc = BASE_POS.col
  const br = BASE_POS.row

  // ---- Dire triggers ----
  // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  let liveEnemies = 0
  let anyApproaching = false
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    liveEnemies++
    if (!anyApproaching) {
      const tc = self.tankCell(t)
      if (Math.abs(tc.col - bc) + Math.abs(tc.row - br) <= p.direItemApproachCells) {
        anyApproaching = true
      }
    }
  }
  // Trigger A (清环前带): swarm converging on the base.
  const swarm = liveEnemies >= p.direItemMinEnemies && anyApproaching
  // Trigger B (补环): the base ring is damaged — fence reinforcement urgent.
  let ringIntact = 8
  if (p.direItemRingLow > 0) {
    ringIntact = 0
    const tm = w.tileMap
    for (let dc = -1; dc <= 2; dc++) if (tm.get(bc + dc, br - 1) === 'brick') ringIntact++
    for (let dr = 0; dr <= 1; dr++) {
      if (tm.get(bc - 1, br + dr) === 'brick') ringIntact++
      if (tm.get(bc + 2, br + dr) === 'brick') ringIntact++
    }
  }
  const ringLow = ringIntact <= p.direItemRingLow
  if (!swarm && !ringLow) return null

  let best: Cell | null = null
  let bestScore = -Infinity
  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (!pu.alive) continue
    const t = pu.type
    if (t !== 'bomb' && t !== 'freeze' && t !== 'fence' && t !== 'emp') continue
    const cellRow = Math.floor(pu.y / CELL)
    // Spawn-band gate (same as §87 — a fresh-enemy trap).
    if (p.pickupPrioritySpawnRowMax > 0 && cellRow <= p.pickupPrioritySpawnRowMax) continue
    const dist = Math.round(
      (Math.abs(pu.x + pu.w / 2 - pcx) + Math.abs(pu.y + pu.h / 2 - pcy)) / CELL,
    )
    if (dist > p.direItemRangeCells) continue
    // Reachability (same A* as the navigator — powerUpCollectCell).
    const collect = powerUpCollectCell(self, Math.floor(pu.x / CELL), cellRow)
    if (!collect) continue
    // Value: ring-low prefers fence (补环); swarm prefers bomb/freeze/emp.
    let score = 1000 - dist * 10
    if (ringLow && t === 'fence') score += 500
    if (swarm && (t === 'bomb' || t === 'freeze' || t === 'emp')) score += 500
    if (score > bestScore) {
      bestScore = score
      best = collect
    }
  }
  return best
}

/**
 * §84-revisit (DECISIONS §85): the tank-position cell from which a power-up
 * at (col,row) can be collected, or null when it is genuinely unreachable
 * (steel/water-enclosed pocket). The tank collects by OVERLAPPING the item's
 * TANK-sized rect, so the item's own cell works normally; when the item sits
 * on blocking terrain (a deferred drop materialized on another stage's
 * layout, fence steel placed over a drop), the tank parks on the nearest
 * overlapping passable neighbour instead — and THAT cell is what the caller
 * must navigate to (targeting the item's own impassable cell would re-create
 * the stuck state). Uses the exact same A* the navigator drives (corridor
 * paths, then dig-through-brick), so this matches what the tank can actually
 * drive. Pure function of World state: no RNG draws, no cache mutation.
 */
function powerUpCollectCell(self: GodAIInput, col: number, row: number): Cell | null {
  const pc = self.playerCell()
  if (col === pc.col && row === pc.row) return { col, row }
  // Item's own cell first — drops always land on passable ground, so this is
  // the overwhelmingly common case (1-2 A* runs per item).
  if (powerUpCellReachable(self, col, row)) return { col, row }
  // Wall/base-adjacent edge case: overlap the item from a neighbouring
  // passable tank position; prefer the one closest to the player.
  let bestCol = -1
  let bestRow = -1
  let bestDist = Infinity
  for (let dr = -1; dr <= 1; dr++) {
    for (let dc = -1; dc <= 1; dc++) {
      if (dr === 0 && dc === 0) continue
      const nc = col + dc
      const nr = row + dr
      if (nc === pc.col && nr === pc.row) return { col: nc, row: nr }
      if (!powerUpCellReachable(self, nc, nr)) continue
      const d = Math.abs(nc - pc.col) + Math.abs(nr - pc.row)
      if (d < bestDist) {
        bestDist = d
        bestCol = nc
        bestRow = nr
      }
    }
  }
  return bestCol >= 0 ? { col: bestCol, row: bestRow } : null
}

/** A* reachability from the player's cell to one specific tank-position cell.
 *
 * (perf §129) Two byte-identical mechanisms (both a pure memo of World
 * state — findPath draws no RNG and reads only tileMap/from/to/constraints):
 *   1. DIG-ONLY. The old corridor-first call is redundant: `breakBrick`'s
 *      search space strictly contains the corridor space (brick becomes
 *      passable at cost 5; steel/water/base still block), so a corridor path
 *      exists ⟺ a dig path exists — the boolean answer never differs. The
 *      corridor-fail case (full reachable-component exploration) was the
 *      expensive one; dig-only short-circuits it.
 *   2. CROSS-TICK MEMO on (playerCell, target) + tileMap.revision, the same
 *      strict-pure-memo discipline as the §127 replan cache. The player
 *      crosses a cell boundary only every ~8-23 ticks, and pickup queries
 *      re-evaluate the same items every think (urgent + bonus-window paths),
 *      so repeats dominate. 8 direct-mapped slots; terrain mutations bump
 *      revision and make stale slots miss the same tick.
 * Gate: params.pickupReachCache (0 = byte-identical pre-§129 corridor+dig,
 * uncached — the A/B arm B).
 */
function powerUpCellReachable(self: GodAIInput, col: number, row: number): boolean {
  const pc = self.playerCell()
  if (self.params.pickupReachCache <= 0) {
    const target: Cell = { col, row }
    const corridor = findPath(self.world.tileMap, pc, target)
    if (corridor && corridor.length > 0) return true
    const dig = findPath(self.world.tileMap, pc, target, { breakBrick: true })
    return !!dig && dig.length > 0
  }
  const rev = self.world.tileMap.revision
  const slots = self._pickupReachSlots
  // 60-tick defence-in-depth timer (same discipline as §127's _replanTimer):
  // a pure memo recomputes the identical value, so this only bounds staleness
  // if findPath ever gains an input outside the key — never changes results.
  self._pickupReachTimer--
  if (self._pickupReachTimer <= 0) {
    for (let i = 0; i < slots.length; i++) slots[i].valid = false
    self._pickupReachTimer = self._pickupReachMax
  }
  const s = slots[(col * 13 + row * 7) & 7]
  if (
    s.valid &&
    s.pcCol === pc.col &&
    s.pcRow === pc.row &&
    s.col === col &&
    s.row === row &&
    s.rev === rev
  ) {
    return s.reachable
  }
  const dig = findPath(self.world.tileMap, pc, { col, row }, { breakBrick: true })
  const reachable = !!dig && dig.length > 0
  s.valid = true
  s.pcCol = pc.col
  s.pcRow = pc.row
  s.col = col
  s.row = row
  s.rev = rev
  s.reachable = reachable
  return reachable
}

/**
 * Dual central breach (plan/dual-central-breach-strategy.md §6.3-A):
 * Find the nearest fence powerup for P2 — bypasses all gates (nearby-enemy,
 * route-danger, divert-distance). Fence = steel walls for the base,
 * structurally solving the central breach. P1 handles center defense, so
 * P2 is free to grab fence regardless of enemy pressure.
 * Returns the fence powerup cell, or null if no fence exists.
 * Pure World read — no RNG, no mutation.
 */
export function findDualFencePickupImpl(self: GodAIInput, pcx: number, pcy: number): Cell | null {
  const w = self.world
  const powerUps = w.powerUps
  if (powerUps.length === 0) return null
  let bestCol = 0
  let bestRow = 0
  let bestDist = Infinity
  let hasBest = false
  const playerCol = Math.floor(pcx / CELL)
  const playerRow = Math.floor(pcy / CELL)
  for (let pi = 0; pi < powerUps.length; pi++) {
    const pu = powerUps[pi]
    if (pu.type !== 'fence') continue
    const puCol = Math.floor(pu.x / CELL)
    const puRow = Math.floor(pu.y / CELL)
    const d = Math.abs(puCol - playerCol) + Math.abs(puRow - playerRow)
    if (d < bestDist) {
      bestDist = d
      bestCol = puCol
      bestRow = puRow
      hasBest = true
    }
  }
  return hasBest ? { col: bestCol, row: bestRow } : null
}

/**
 * §177 (plan/dual-central-breach-strategy.md §6.5 follow-up): P2's enemy-
 * spawn-point patrol target.
 *
 * P2's problem on a central-breach stage is not target *priority*, it is that
 * it holds a static flank cell and therefore almost never shares a row or
 * column with an enemy — so `shouldFireInDir` never has a shot and P2 scores
 * zero kills. Enemies all descend from the spawn columns, so walking toward
 * the nearest spawn point puts P2 on the lane traffic uses.
 *
 * Returns null (⇒ caller falls through to the normal nearest-enemy hunt) when
 * an enemy is already within `dualCentralBreachP2PatrolEnemyDist`, or when P2
 * is standing on the spawn point — in both cases hunting/firing beats walking.
 *
 * Pure World read — no RNG, no mutation. Uses a module-scope buffer (AGENTS
 * §14.1); the only caller copies the value into `_selTargetBuf` immediately.
 */
const _dualPatrolCell: Cell = { col: 0, row: 0 }
export function findDualPatrolTargetImpl(
  self: GodAIInput,
  playerCell: Cell,
  enemies: Tank[],
): Cell | null {
  // Mode 2: hold P2's own guard post (the §175 shift −2 flank cell) instead
  // of sweeping — the P2 mirror of P1's unconditional anchor hold. No
  // enemy-distance gate: the whole point is that P2 stops leaving the base.
  if (self.params.dualCentralBreachP2Patrol === 2) {
    const post = self.getDefaultDefensePosition()
    if (post.col === playerCell.col && post.row === playerCell.row) return null
    _dualPatrolCell.col = post.col
    _dualPatrolCell.row = post.row
    return _dualPatrolCell
  }
  const pts = self.world.enemySpawnPoints
  if (pts.length === 0) return null
  const engageDist = self.params.dualCentralBreachP2PatrolEnemyDist
  for (let ti = 0; ti < enemies.length; ti++) {
    const t = enemies[ti]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    if (Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row) <= engageDist) {
      return null
    }
  }
  // The patrol row is a knob, not the spawn row: sweeping all the way to
  // row 0 pulls P2 off the board while four enemies converge on the base
  // (every measured Battlement loss is base_destroyed, never a timeout), so
  // the useful position is the spawn COLUMN at a row that still covers the
  // approach. `dualCentralBreachP2PatrolRow` = 0 reproduces the literal
  // spawn-point sweep.
  const patrolRow = Math.min(GRID - 1, Math.max(0, self.params.dualCentralBreachP2PatrolRow))
  let bestCol = 0
  let bestDist = Infinity
  for (let pi = 0; pi < pts.length; pi++) {
    const col = Math.floor(pts[pi].x / CELL)
    const d = Math.abs(col - playerCell.col) + Math.abs(patrolRow - playerCell.row)
    if (d < bestDist) {
      bestDist = d
      bestCol = col
    }
  }
  if (bestDist === 0) return null
  _dualPatrolCell.col = bestCol
  _dualPatrolCell.row = patrolRow
  return _dualPatrolCell
}

/**
 * NEW Requirement 3: Calculate how dangerous a route is.
 * Returns a danger level from 0 (safe) to N (many enemies on the path).
 *
 * (perf §65): eliminated the 3× `pxToCell` Cell allocations per call
 * (target/player/enemy cells). calculateRouteDanger is called once per
 * power-up candidate per think → with N power-ups and M enemies this was
 * up to N×M Cell allocations per think, ~3M allocs over a 30-game batch.
 * Scalar col/row locals are byte-identical (same Math.floor division).
 */
export function calculateRouteDangerImpl(
  self: GodAIInput,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
): number {
  const w = self.world
  let danger = 0

  // Simple heuristic: count enemies that are closer to the target than we are.
  // Inline pxToCell as scalar col/row — no Cell allocation.
  const targetCol = Math.floor(toX / CELL)
  const targetRow = Math.floor(toY / CELL)
  const playerCol = Math.floor(fromX / CELL)
  const playerRow = Math.floor(fromY / CELL)
  const playerDistToTarget = Math.abs(targetCol - playerCol) + Math.abs(targetRow - playerRow)

  // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
  const dangerScan = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let ti = 0; ti < dangerScan.length; ti++) {
    const t = dangerScan[ti]
    if (!t.alive || t.spawnTimer > 0) continue

    // Inline pxToCell(t.x, t.y) — scalar col/row, no Cell allocation.
    const enemyCol = Math.floor(t.x / CELL)
    const enemyRow = Math.floor(t.y / CELL)
    const enemyDistToTarget = Math.abs(targetCol - enemyCol) + Math.abs(targetRow - enemyRow)

    // If enemy is closer to target than player, and on the path, add danger
    if (enemyDistToTarget < playerDistToTarget) {
      // Check if enemy is roughly between player and target
      const dx = enemyCol - playerCol
      const dy = enemyRow - playerRow
      const tx = targetCol - playerCol
      const ty = targetRow - playerRow

      // Simple projection check
      if (Math.sign(dx) === Math.sign(tx) && Math.sign(dy) === Math.sign(ty)) {
        danger += 1
        // Extra danger for power/armor tanks
        if (t.kind === 'power' || t.kind === 'armor') {
          danger += 1
        }
      }
    }
  }

  return danger
}

/**
 * §137 / 基地守位格 (base guard anchor): compute the best STANDABLE defense
 * anchor near the base.
 *
 * The default defense position (BASE_POS.col, baseRow − defenseRowOffset) sits
 * on the base protection ring — a brick on ALL 35 stages — so navigate can
 * never reach it and the AI has no defensive hold point (Battlement exposes
 * this: enemies breach the ring while the player roams or parks in the left
 * rubble gap). When enabled, pick the best standable cell in the base box
 * (cols bc−2..bc+3 × rows br−3..br+1) by:
 *   ringCover     — how many of the 8 base-ring cells can be DEFENDED from
 *     here (the player shoots the ring's approach BEFORE it is breached; a
 *     ring cell is defended when it shares the candidate's row/col with clear
 *     LOS),
 *   approachCover — D1 (plan §D1): how many cells of the enemy STAGING band
 *     beyond the ring (cols bc+2..bc+5 ∪ bc−3..bc−1 × rows br−1..br+1, the
 *     right/left wings where the base rush launches) the candidate can SHOOT
 *     (same row/col, clear LOS). §137's pick (Battlement: (12,22)) stared at
 *     the ring and could not shoot the right-wing breachers at all — this
 *     term makes an antechamber/right-wing cell ((14-15,22-23)/(15,24)) win,
 *     so the player holds a cell WITH a firing lane onto the attack band,
 *   laneCover     — open bullet range along the candidate's row+column
 *     (intercept value for enemies crossing the approach band),
 *   cover         — solid neighbours (brick/steel/base) shielding the
 *     candidate,
 *   distance      — Manhattan to the base (faster response; small penalty).
 *
 * Pure terrain function of World state — no RNG, no per-tick cost (cached on
 * GodAIInput, recomputed on stage reset). Data-driven — DECISIONS §81 forbids
 * stage-name overrides; this is stage-characteristic-driven and therefore
 * applies wherever the default defense position is unreachable.
 */
export function computeBaseGuardAnchorImpl(self: GodAIInput): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const bc = BASE_POS.col
  const br = BASE_POS.row
  // The 8 base-ring cells (border of the 4×4 box around the 2×2 base).
  const ring: Array<[number, number]> = []
  for (let r = br - 1; r <= br + 1; r++) {
    for (let c = bc - 1; c <= bc + 2; c++) {
      const isBase = c >= bc && c <= bc + 1 && r >= br && r <= br + 1
      if (!isBase && r >= 0 && r < GRID && c >= 0 && c < GRID) ring.push([c, r])
    }
  }
  const standable = (c: number, r: number): boolean => {
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    const t = tm.get(c, r)
    return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
  }
  // D1 (plan §D1): the enemy staging band beyond the ring — standable cells
  // the base rush launches from (right wing cols bc+2..bc+5, left wing cols
  // bc-3..bc-1, attack rows br-1..br+1).
  const band: Array<[number, number]> = []
  for (let br2 = br - 1; br2 <= br + 1; br2++) {
    for (let bc2 = bc + 2; bc2 <= bc + 5; bc2++) {
      if (bc2 >= 0 && bc2 < GRID && br2 >= 0 && br2 < GRID && standable(bc2, br2)) {
        band.push([bc2, br2])
      }
    }
    for (let bc2 = bc - 3; bc2 <= bc - 1; bc2++) {
      if (bc2 >= 0 && bc2 < GRID && br2 >= 0 && br2 < GRID && standable(bc2, br2)) {
        band.push([bc2, br2])
      }
    }
  }
  const bulletOpen = (c: number, r: number): boolean => {
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    const t = tm.get(c, r)
    return t !== 'brick' && t !== 'steel' && t !== 'base'
  }
  const DIRS: Array<[number, number]> = [
    [-1, 0],
    [1, 0],
    [0, -1],
    [0, 1],
  ]
  let best: Cell | null = null
  let bestScore = -1
  for (let r = br - 3; r <= br + 1; r++) {
    for (let c = bc - 2; c <= bc + 3; c++) {
      if (!standable(c, r)) continue
      // Clear bullet line from the candidate (c,r) to (tc,tr): same row or
      // column with no brick/steel/base between (water lets bullets pass).
      const lineClearTo = (tc: number, tr: number): boolean => {
        if (tc === c) {
          const step = tr < r ? -1 : 1
          for (let y = r + step; y !== tr; y += step) {
            const t = tm.get(c, y)
            if (t === 'brick' || t === 'steel' || t === 'base') return false
          }
          return true
        }
        if (tr === r) {
          for (let x = Math.min(c, tc) + 1; x < Math.max(c, tc); x++) {
            const t = tm.get(x, r)
            if (t === 'brick' || t === 'steel' || t === 'base') return false
          }
          return true
        }
        return false
      }
      // ringCover: ring cells sharing row/col with clear LOS from (c,r).
      let ringCover = 0
      for (let ri = 0; ri < ring.length; ri++) {
        if (lineClearTo(ring[ri][0], ring[ri][1])) ringCover++
      }
      // D1: approach-band LOS coverage — staging cells the candidate can
      // SHOOT (equal weight to ringCover). This is the term that makes a
      // right-wing/antechamber cell ((14-15,22-23)/(15,24)) win over the
      // §137 pick (12,22), which stares at the ring and cannot shoot the
      // breachers on the band.
      let approachCover = 0
      for (let bi = 0; bi < band.length; bi++) {
        if (lineClearTo(band[bi][0], band[bi][1])) approachCover++
      }
      // laneCover: open bullet range along row + col (up to 6 cells each way).
      let laneCover = 0
      for (let di = 0; di < DIRS.length; di++) {
        let x = c + DIRS[di][0]
        let y = r + DIRS[di][1]
        let steps = 0
        while (steps < 6 && bulletOpen(x, y)) {
          steps++
          x += DIRS[di][0]
          y += DIRS[di][1]
        }
        laneCover += steps
      }
      // cover: solid orthogonal neighbours.
      let cover = 0
      for (let di = 0; di < DIRS.length; di++) {
        const x = c + DIRS[di][0]
        const y = r + DIRS[di][1]
        if (x >= 0 && x < GRID && y >= 0 && y < GRID) {
          const t = tm.get(x, y)
          if (t === 'brick' || t === 'steel' || t === 'base') cover++
        }
      }
      const dist = Math.abs(c - bc) + Math.abs(r - br)
      const score = ringCover * 60 + approachCover * 60 + laneCover * 4 + cover * 15 - dist * 6
      if (score > bestScore) {
        bestScore = score
        best = { col: c, row: r }
      }
    }
  }
  return best
}

/**
 * §178 (autopsy hard-s34-base-l3-t25-seed2): dual central-breach P1 central
 * hold — a standable cell near the top-center that can shoot UP the col-12
 * spawn lane, intercepting the central-breacher as it drives down (the user
 * expectation "开墙抵达中路驻守点"). The cell is chosen to maximize clear
 * vertical LOS up col 12 (so it can snipe the spawn) and centrality, so P1
 * digs up out of its spawn pocket and holds the center. Pure World read — no
 * RNG. Returns null when no suitable cell exists (caller falls back to the
 * flank anchor).
 */
export function findDualCentralHoldImpl(self: GodAIInput): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const standable = (c: number, r: number): boolean => {
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    const t = tm.get(c, r)
    return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
  }
  const prm = self.params
  // Per-stage override takes precedence when both knobs are valid.
  if (prm.dualCentralBreachP1AnchorCol >= 0 && prm.dualCentralBreachP1AnchorRow >= 0) {
    const c = prm.dualCentralBreachP1AnchorCol
    const r = prm.dualCentralBreachP1AnchorRow
    if (standable(c, r)) return { col: c, row: r }
  }
  // Otherwise pick the standable cell in the upper-center band with the longest
  // clear upward LOS in col 12 (snipe the spawn lane), tie-broken toward row 3
  // and column 12.
  let best: Cell | null = null
  let bestScore = -Infinity
  for (let r = 1; r <= 12; r++) {
    for (let c = 10; c <= 14; c++) {
      if (!standable(c, r)) continue
      // Clear upward LOS in col 12 from this row to the top (the spawn lane).
      let los = 0
      let ok = true
      for (let y = r; y >= 0; y--) {
        if (c === 12) {
          if (tm.get(12, y) !== 'empty') {
            ok = false
            break
          }
        } else {
          // Off-col-12 hold: must have a clear horizontal shot INTO col 12,
          // then clear vertical col 12 above the junction.
          if (y === r) {
            if (tm.get(12, r) !== 'empty') {
              ok = false
              break
            }
          } else if (tm.get(12, y) !== 'empty') {
            ok = false
            break
          }
        }
        los++
      }
      if (!ok) continue
      const score = los * 10 - Math.abs(c - 12) * 2 - Math.abs(r - 3)
      if (score > bestScore) {
        bestScore = score
        best = { col: c, row: r }
      }
    }
  }
  return best
}

/**
 * Default defense position: centered above the base at the defense row.
 * This is the fallback when no enemies are present.
 * Gap B: when the stage has no base, returns the player's current cell
 * (stay put — there's nothing to defend).
 */
export function getDefaultDefensePositionImpl(self: GodAIInput): Cell {
  if (!self.hasBase) return self.playerCell()
  // When guard anchor mode is on, use the standable anchor as the base
  // defense position. The default (12, 23) is always a ring brick — A*
  // can't route there, so P1 gets stuck on directMove when returning to
  // defense. The anchor (e.g. (12, 22) on Battlement) is standable and
  // covers the approach band. Only active when baseGuardAnchorMode > 0
  // (default 0 = OFF = byte-identical).
  const def = { col: BASE_POS.col, row: BASE_POS.row - self.params.defenseRowOffset }
  if (self.params.baseGuardAnchorMode > 0) {
    const anchor = self.getBaseGuardAnchor()
    if (anchor) {
      def.col = anchor.col
      def.row = anchor.row
    }
  }
  // 双玩家协作防守分路: P1 守左翼, P2 守右翼。当伙伴存活时,
  // 各自偏移防守列以覆盖基地两侧, 避免两个 AI 挤在同一位置。
  // 纯 World 读取 (coopPartner 只读 world.player/player2) — 无隐藏状态。
  if (self.hasLivingPartner()) {
    // §178 (autopsy seed2): dual central breach — P1 holds a CENTRAL position
    // (dig up and intercept the col-12 spawn lane) instead of the right-wing
    // anchor. Matches the user expectation "开墙抵达中路驻守点". P2 keeps the
    // flank/hunting split (§177). Gated by spectateDual && centralBreachRisk
    // && !isPlayer2 — single-player and P2 unchanged (byte-identical).
    if (
      self.world.spectateDual &&
      self._centralBreachRisk &&
      !self.isPlayer2() &&
      self.params.dualCentralBreachP1Anchor > 0
    ) {
      const hold = findDualCentralHoldImpl(self)
      if (hold) {
        def.col = hold.col
        def.row = hold.row
        return def
      }
    }
    // Dual central breach strategy (plan/dual-central-breach-strategy.md §B):
    // P1 holds the center guard position (shift 0) to intercept col-12
    // breachers; P2 takes the left flank (shift -2) to cover col 0/6 spawns
    // and avoid §159 yield at center. Gated by dualStrategyActive
    // (spectateDual || coop) && centralBreachRisk — single-player keeps the
    // existing P1-left/P2-right split (byte-identical).
    let shift: number
    if (self.dualStrategyActive) {
      shift = self.isPlayer2() ? -2 : 0
    } else {
      shift = self.isPlayer2() ? 2 : -2
    }
    def.col = BASE_POS.col + shift
    // 确保偏移后的防守位在网格内且可站立; 否则回退到中心位
    const bc = BASE_POS.col
    if (def.col < 0 || def.col >= GRID) {
      def.col = bc
    } else {
      const t = self.world.tileMap.get(def.col, def.row)
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') {
        // 偏移位不可站 — 尝试偏移量减半
        def.col = bc + (shift > 0 ? 1 : -1)
        if (def.col >= 0 && def.col < GRID) {
          const t2 = self.world.tileMap.get(def.col, def.row)
          if (t2 === 'brick' || t2 === 'steel' || t2 === 'water' || t2 === 'base') {
            def.col = bc
          }
        } else {
          def.col = bc
        }
      }
    }
  }
  // §146 B: defensePosStandable — 集合点可达性修复。默认防守位 (12, 24-offset)
  // 在全部 35 关上都是环砖格（§137 注释承认），A*（corridor 与 breakBrick）到
  // 砖格目标均返回空路径 → 紧急回防/§113/§88 的回防路由全部失效，玩家只能靠
  // directMove 盲目破砖（S8 实测：pocket→(12,23) corridor=0 breakBrick=0）。
  // 旋钮开启时：若默认点不可站，在基地周边小盒（rows br-6..br+1, cols
  // bc-3..bc+4）内扫最近可站格作为集合点——保证回防目标永远可达。
  // 0 默认 OFF → byte-identical。
  if (self.params.defensePosStandable > 0) {
    // 仅远位（> defensePosStandableMinDist）触发：近基 idle 时保持旧行为
    // （directMove 盲走到环砖前），爆炸半径最小化；S8 口袋回防（dist 25-32）
    // 正是远位场景。playerCell 是 per-tick 缓存，无 RNG —— 安全。
    const pc = self.playerCell()
    const far =
      Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row) >
      self.params.defensePosStandableMinDist
    if (!far) return def
    const t = self.world.tileMap.get(def.col, def.row)
    if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') {
      const bc = BASE_POS.col
      const br = BASE_POS.row
      let best: Cell | null = null
      let bestDist = Infinity
      for (let r = br - 6; r <= br + 1; r++) {
        for (let c = bc - 3; c <= bc + 4; c++) {
          if (c < 0 || c >= GRID || r < 0 || r >= GRID) continue
          const t2 = self.world.tileMap.get(c, r)
          if (t2 === 'brick' || t2 === 'steel' || t2 === 'water' || t2 === 'base') continue
          const d = Math.abs(c - bc) + Math.abs(r - br)
          if (d < bestDist) {
            bestDist = d
            best = { col: c, row: r }
          }
        }
      }
      if (best) return best
    }
  }
  // §137: the default sits on the base ring (brick on all 35 stages) — when
  // the guard-anchor mechanism is ON, hold the computed guard cell instead.
  if (self.params.baseGuardAnchorMode > 0) {
    const t = self.world.tileMap.get(def.col, def.row)
    const standable = t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
    if (!standable) {
      const anchor = self.getBaseGuardAnchor()
      if (anchor) return anchor
    }
  }
  return def
}

/**
 * Select the best target cell for the player to navigate toward.
 *
 * S6 Attack-defense switching (core strategy):
 *   - Emergency defense: enemies close to base → strict defense position
 *   - Aggressive hunt: few enemies remaining → chase directly, relax distance
 *   - Normal defense: intercept at defense row (default)
 *
 * Priority:
 *   1. Aggressive mode (freeze/shield) → chase nearest enemy directly
 *   2. S6 hunt mode (few enemies remaining) → chase nearest enemy directly
 *   3. S6 emergency defense → return to defense position, intercept close enemies
 *   4. Normal: enemy closest to base → intercept at defense row
 *   5. No enemies → default defense position
 */
/**
 * §88 A/B round 3: can a tank parked at `choke` shoot the imminent enemy
 * (cell `enemy`) or its nearest threat point? Same row or column with clear
 * bullet LOS (brick/steel/base block). Used to decide chase-vs-hold: when the
 * chokepoint covers the enemy's approach, holding is strictly better than a
 * direct chase (the player shoots the enemy as it crosses); when it does NOT
 * cover the approach, the player must chase the enemy directly (S32 seed 23:
 * chokepoint (15,18) could not shoot a fast at (24,22) heading for the base).
 *
 * This is an ENDPOINT proxy for path coverage: the real threat path is the
 * A* corridor from the enemy to its nearest threat point, and a path can
 * pass through a cell sharing the chokepoint's row/col even when neither
 * endpoint does. The endpoints are a sound approximation for the short
 * (<= chaseMaxDist 3 cells) imminent corridors that drive this decision —
 * do NOT "fix" this into a per-path A* re-walk without A/B evidence.
 */
export function chokepointCoversEnemy(self: GodAIInput, choke: Cell, enemy: Cell): boolean {
  const w = self.world
  const tm = w.tileMap
  const plan = self._chokepointPlan
  // Enemy's nearest threat point (reuse the throttled plan's cached set).
  let tpCol = -1
  let tpRow = -1
  let tpDist = Infinity
  if (plan) {
    for (let ti = 0; ti < plan.threatPoints.length; ti++) {
      const t = plan.threatPoints[ti]
      const d = Math.abs(t.col - enemy.col) + Math.abs(t.row - enemy.row)
      if (d < tpDist) {
        tpDist = d
        tpCol = t.col
        tpRow = t.row
      }
    }
  }

  // LOS check from the chokepoint to a target cell: same row or column with
  // no brick/steel/base between. (Water lets bullets pass — blocksBullet.)
  const clear = (c: number, r: number): boolean => {
    // Same cell — an enemy standing ON the chokepoint is trivially covered
    // (point-blank). The zero-length walk below would step off-grid forever
    // (crash: grid[-1] undefined) — the 60-seed chaos A/B exposed this latent
    // §88 bug via seeds > 20 (DECISIONS §101).
    if (c === choke.col && r === choke.row) return true
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
    if (choke.col === c) {
      const step = choke.row < r ? 1 : -1
      for (let rr = choke.row + step; rr !== r; rr += step) {
        if (rr < 0 || rr >= GRID) return false
        if (blocksBullet(tm.grid[rr][c])) return false
      }
      return true
    }
    if (choke.row === r) {
      const step = choke.col < c ? 1 : -1
      for (let cc = choke.col + step; cc !== c; cc += step) {
        if (cc < 0 || cc >= GRID) return false
        if (blocksBullet(tm.grid[r][cc])) return false
      }
      return true
    }
    return false
  }
  if (clear(enemy.col, enemy.row)) return true
  return tpCol >= 0 && clear(tpCol, tpRow)
}

/**
 * §146 C: M13 field-pressure retreat predicate — the SINGLE source of truth
 * for "should the player stop what it's doing and return to the defense
 * position". Extracted from the M13 block below so PICKUP_HIGH (§146 C knob
 * fieldRetreatPickupGate) can suppress the power-up divert under exactly the
 * same conditions: a HIGH-tier item was hijacking the retreat (S8: pickup
 * branch 800 > hunt 200, so M13 never got to run while a power-up sat within
 * divert range). Pure read of params + cached per-tick state — no RNG, no
 * mutation. `enemiesAlive` is the field-wide live count (Cluster C
 * snapshot), not the nearby count.
 */
export function isFieldRetreatConditionImpl(
  self: GodAIInput,
  baseUnderThreat: boolean,
  playerDistToBase: number,
  enemiesAlive: number,
): boolean {
  return (
    self.params.outnumberedFieldRetreat > 0 &&
    self.world.rules.combatModel === 'pool' &&
    !baseUnderThreat &&
    !self.aggressive &&
    playerDistToBase > self.params.outnumberedFieldDistCells &&
    enemiesAlive >= self.params.outnumberedFieldEnemies
  )
}

export function selectTargetImpl(self: GodAIInput, playerCell: Cell): Cell | null {
  // (perf §68 Round 9) NOTE: CROSS-tick caching was evaluated and REJECTED.
  // A 30-tick (0.5s) cache caused S6 Iron Curtain win rate to drop from
  // 72% to 40% — the stage has heavy steel walls forcing frequent target
  // switches, and 0.5s staleness leaves the player stuck behind walls too
  // long. Responsiveness must stay at tick granularity.
  //
  // (perf §125) A WITHIN-tick memo is a different animal and IS safe. HUNT
  // computes `navTarget = selectTarget(pc)` and then routes through
  // followPath → replan → selectTarget(playerCell) (or directMove →
  // selectTarget), so the same query runs 2-3× per tick with an identical
  // playerCell. Direct callers (tests / tools/diag) must keep playerCell
  // unchanged between calls or call `endFrame()`/`reset()` to invalidate. `selectTargetUncached` reads only World state and params —
  // it draws no RNG — and nothing mutates the World during think(), so the
  // repeats are provably redundant. Zero staleness: the memo dies every tick
  // in endFrame(), which is exactly the granularity §68 demanded.
  //
  // The result is copied into a dedicated buffer rather than passed through,
  // because the uncached path may return the shared `_tankCellBuf` (which the
  // next tankCell() call clobbers) or a freshly allocated defense cell. One
  // stable buffer removes that aliasing hazard and the allocation.
  //
  // Telemetry note: `branchCounts.chokepoint` now counts once per tick
  // instead of once per redundant query. That counter is pure observation
  // (tools/diag), never gameplay.
  if (
    self._selTargetValid &&
    self._selTargetKeyCol === playerCell.col &&
    self._selTargetKeyRow === playerCell.row
  ) {
    return self._selTargetNull ? null : self._selTargetBuf
  }

  const res = selectTargetUncached(self, playerCell)
  self._selTargetValid = true
  self._selTargetKeyCol = playerCell.col
  self._selTargetKeyRow = playerCell.row
  if (res === null) {
    self._selTargetNull = true
    return null
  }
  self._selTargetNull = false
  self._selTargetBuf.col = res.col
  self._selTargetBuf.row = res.row
  return self._selTargetBuf
}

// §171: travel-cost model for pathTargetMode. A dig-only route means the
// player must fire through brick first — real cost far exceeds step count,
// so it is priced above any plausible corridor length (grid diameter ≤ 48).
// The penalty matches the probe's dig cost (1000): the override fires only
// when the Manhattan pick's real cost explodes, not for short 1-2 brick
// digs that are legitimately worth taking. A fully unreachable enemy keeps
// Manhattan ordering under a large penalty so it is only picked when
// nothing better exists.
const PATH_TARGET_DIG_PENALTY = 1000
const PATH_TARGET_UNREACHABLE_PENALTY = 2000
// Phase 2 §6.2: targetValue near-tie window — values within this gap are
// equal, and the standard distance ordering (bonus/coop included) breaks
// the tie. Typical values are ~0.1-1.0, so 0.05 is a small but meaningful
// window: close-enough targets keep their historical preferences.
const TARGET_VALUE_TIE_EPS = 0.05

// §171: reusable Cell buffers for huntPathCost's findPath calls — avoids
// per-query allocation (AGENTS §14.1). findPath is synchronous and never
// reentrant within think(), same discipline as pathfind.ts's own buffers.
const _huntFrom: Cell = { col: 0, row: 0 }
const _huntTo: Cell = { col: 0, row: 0 }

/**
 * §171: true travel cost playerCell → enemy cell for pathTargetMode,
 * memoized on (playerCell, enemyId, enemyCell, tileMap.revision) in the
 * fixed 8-slot table on GodAIInput. findPath draws no RNG, so the memo is
 * byte-identical. Returns the corridor length when a pure corridor
 * connects, dig length + penalty when only brick-breaking works, and
 * Manhattan + large penalty otherwise.
 */
function huntPathCost(
  self: GodAIInput,
  playerCell: Cell,
  enemyId: number,
  ecCol: number,
  ecRow: number,
): number {
  const pKey = playerCell.row * GRID + playerCell.col
  const eKey = ecRow * GRID + ecCol
  const rev = self.world.tileMap.revision
  const eId = self._pathCostEId
  for (let s = 0; s < 8; s++) {
    if (
      eId[s] === enemyId &&
      self._pathCostPKey[s] === pKey &&
      self._pathCostEKey[s] === eKey &&
      self._pathCostRev[s] === rev
    ) {
      return self._pathCostVal[s]
    }
  }
  _huntFrom.col = playerCell.col
  _huntFrom.row = playerCell.row
  _huntTo.col = ecCol
  _huntTo.row = ecRow
  const tm = self.world.tileMap
  let cost: number
  const corridor = findPath(tm, _huntFrom, _huntTo)
  if (corridor) {
    cost = corridor.length
  } else {
    const dig = findPath(tm, _huntFrom, _huntTo, { breakBrick: true })
    cost = dig
      ? PATH_TARGET_DIG_PENALTY + dig.length
      : PATH_TARGET_UNREACHABLE_PENALTY +
        Math.abs(ecCol - playerCell.col) +
        Math.abs(ecRow - playerCell.row)
  }
  const slot = self._pathCostNext
  self._pathCostNext = (slot + 1) & 7
  eId[slot] = enemyId
  self._pathCostPKey[slot] = pKey
  self._pathCostEKey[slot] = eKey
  self._pathCostRev[slot] = rev
  self._pathCostVal[slot] = cost
  return cost
}

function selectTargetUncached(self: GodAIInput, playerCell: Cell): Cell | null {
  const w = self.world
  // §79: controlled tank, not `w.player`. In co-op the God AI drives P2, so
  // gating target selection on P1's existence would blank P2's target list
  // whenever P1 is permanently dead.
  const p = self.controlledTank(w)
  if (!p) return null

  const baseCol = BASE_POS.col
  const baseRow = BASE_POS.row
  const defenseRow = baseRow - self.params.defenseRowOffset

  // 双玩家协作: 获取伙伴信息用于目标去冲突。当伙伴存活时,
  // 各自优先攻击自己一侧的敌人, 避免两个 AI 追同一个目标。
  // 纯 World 读取 — 无隐藏状态 (AGENTS §2.2)。
  const coopActive = self.hasLivingPartner()
  const partnerCell = coopActive ? self.tankCell(self.coopPartner()!) : null

  // Cluster C: reuse the per-tick enemy snapshot (built in think()) instead
  // of allocating a filtered array on every call (AGENTS §14.1).
  // Falls back to a fresh scan only if think() hasn't populated it yet.
  let enemies =
    self._enemies.length > 0 ? self._enemies : w.tanks.filter((t) => t.alive && t.spawnTimer <= 0)

  // §187: target blacklist — skip the blacklisted enemy (temporarily
  // removed from the target pool due to unreachability).
  self._lastSelectTargetId = -1
  if (self.params.targetBlacklistStuckTicks > 0 && self._blacklistEnemyId >= 0) {
    if (w.frame >= self._blacklistExpiryFrame) {
      self._blacklistEnemyId = -1 // expired
    } else if (enemies.length > 1) {
      // Only filter if there are other enemies to target
      enemies = enemies.filter((t) => t.id !== self._blacklistEnemyId)
    }
  }

  if (enemies.length === 0) return self.getDefaultDefensePosition()

  // ---- §179: emergency base defense (autopsy seed6 失误 B/C) ----
  // When baseHp is at/below the emergency fraction, ALL tanks return to
  // the defense position regardless of their current target. The autopsy
  // showed both tanks oscillating in the top-right corner for 18 seconds
  // while the base dropped 48→12→0 — no emergency override existed.
  // Gated by emergencyBaseHpFrac (0 = OFF, byte-identical) AND spectateDual
  // (SP byte-identical — the SP regression on seeds 11/13 showed the
  // general threshold is too aggressive for single-tank defense). Runs
  // after the no-enemies check (no enemies ⇒ defense position anyway) and
  // before the no-base fast path (no base ⇒ no emergency to respond to).
  if (
    self.params.emergencyBaseHpFrac > 0 &&
    self.hasBase &&
    (w.spectateDual || w.coop) &&
    w.baseHp <= self.params.emergencyBaseHpFrac * w.baseMaxHp
  ) {
    return self.getDefaultDefensePosition()
  }

  // ---- Gap B: no-base fast path (plan/God-AI-Curriculum §3) ----
  // When the stage has no base, the AI is a pure hunter: always chase the
  // nearest enemy. No defense positioning, no base-threat checks, no
  // distance-from-base constraint. This is the correct behavior for
  // curriculum stages 1-4 (no-base) and also for real stages where the
  // base has already been destroyed (the AI should still try to clear).
  if (!self.hasBase) {
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      let adjustedDist = d - (t.bonus ? 2 : 0)
      // 协作: 伙伴更近的敌人降优先级
      if (coopActive && partnerCell) {
        const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
        if (pd < d - 3) adjustedDist += 5
      }
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // ---- S6: Determine strategy mode ----
  // Emergency defense: delegated to isBaseUnderThreat() so target selection
  // and the T2a/power-up defense skips share ONE threat model. This includes
  // the static ±3-col box AND the P4 race-to-base check (flanking runners
  // that would beat the player back to the base).
  const baseUnderThreat = self.isBaseUnderThreat()
  // D1 (plan §D1): the §137 guard-anchor knob also drives the D1 objective
  // (approach-band LOS term in computeBaseGuardAnchorImpl) and the D1 hooks
  // below — flag once, reuse in both the base-threat hold and the normal
  // selection hold.
  const anchorModeOn = self.params.baseGuardAnchorMode > 0

  // S6 Aggressive hunt (§5.3): few enemies on field AND few remaining in
  // queue. Both conditions must hold — requiring only one sent the player
  // chasing across the map between spawns (enemies.length dips to 0-1
  // during the 1.8s spawn gap), leaving the base undefended.
  //
  // §5.3 parameterization: the old hardcoded 2/3 thresholds were the root
  // cause of 0% win rate — the AI only hunted when ≤3 enemies remained in
  // the queue, spending 85% of the game turtling. Now reads from params:
  //   huntAllyCount (default 4 = MAX_ENEMIES_ALIVE) — field count gate
  //   endgameEnemyThreshold (default 6) — queue count gate
  const canHunt =
    enemies.length <= self.params.huntAllyCount &&
    w.enemiesRemaining <= self.params.endgameEnemyThreshold

  // If the player is too far from the base when it's under threat, return
  // to defense position. This applies regardless of canHunt — even in the
  // endgame, base defense takes priority over hunting when the player is
  // too far away to intercept in time.
  const playerDistToBase = Math.abs(playerCell.col - baseCol) + Math.abs(playerCell.row - baseRow)
  if (baseUnderThreat && playerDistToBase > self.params.maxPlayerDistFromBase) {
    return self.getDefaultDefensePosition()
  }

  // ---- P4.2: Outnumbered retreat (S18 crossfire family) ----
  // When several enemies converge on the player away from the base, pressing
  // the attack trades 1-for-1 at best (three tanks can fire from three
  // directions; the player has one barrel). Fall back toward the defense
  // position: corridors funnel pursuers into single file, and the base
  // gains a defender. Skipped when the base is already under threat (the
  // defense logic below handles that) and in aggressive/freeze mode.
  if (!baseUnderThreat && !self.aggressive) {
    let nearby = 0
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      if (d <= self.params.outnumberedRadiusCells) nearby++
    }
    if (nearby >= self.params.outnumberedEnemyCount && playerDistToBase > 6) {
      return self.getDefaultDefensePosition()
    }
  }

  // ---- M13: field-wide pressure retreat (SHIPPED, DECISIONS §113) ----
  // P4.2 only fires when 3+ enemies CONVERGE within outnumberedRadiusCells.
  // The M13 probe showed 70% of hard/chaos deaths happen with the FULL
  // enemy field alive (4/4) and 39% at >20 cells from the base — the player
  // deep-hunts while the field is at max pressure, and 1★ single-bullet
  // firepower cannot out-race 3-4 enemies (grinding death, §111). When the
  // FIELD count (not just nearby) is at/over outnumberedFieldEnemies and the
  // player is beyond outnumberedFieldDistCells, return to the defense
  // position — stop over-extending into a full battlefield. `enemies` here
  // is the Cluster C field-wide live snapshot, not the nearby count.
  // A/B (official 口径): 20-seed hard +2.7pp / chaos +2.6pp; 60-seed hard
  // +2.3pp / chaos +0.6pp — the FIRST mechanism without a chaos downside
  // (base losses and deaths down in BOTH difficulties; every dodge/horizon
  // mechanism before had the hard+/chaos- signature). The winning tuning
  // retreats at 3+ alive (attrition starts at 3, not 4 — 1★ can't out-race
  // 3 enemies either) beyond 15 cells; ON4@10 was measured HARMFUL
  // (too passive, base falls: hard -5.3pp). Pool-model only: classic
  // 'instant' has no grinding deaths (1-shot kills, 91% gate byte-locked).
  // NOTE (endgame interplay): this block runs BEFORE the S6 aggressive-hunt
  // below, so with 3+ enemies alive in the endgame (queue <= 6) the player
  // retreats instead of hunting. The 60-seed A/B empirically validated this
  // as net positive (hard +2.3pp / chaos +0.6pp) — do not "fix" it into a
  // regression by reordering the blocks.
  if (isFieldRetreatConditionImpl(self, baseUnderThreat, playerDistToBase, enemies.length)) {
    return self.getDefaultDefensePosition()
  }

  // Aggressive mode (freeze): enemies can't move — chase nearest directly.
  // §179 (autopsy seed6 失误 D): when freezeBasePriority > 0, pick the enemy
  // nearest to the BASE instead of nearest to the player. The autopsy showed
  // a 20-second freeze window completely wasted — both tanks hunted enemies
  // in the top-right while an enemy sat at (7,24), 5 cells from the base.
  // Frozen enemies can't move, so distance-to-player only matters for travel
  // time; distance-to-base determines threat priority. Gated by
  // freezeBasePriority (0 = OFF, byte-identical).
  if (self.aggressive) {
    const freezeBaseFirst = self.params.freezeBasePriority > 0 && self.hasBase && w.spectateDual
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = freezeBaseFirst
        ? Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
        : Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      if (d < bestDist) {
        bestDist = d
        best = t
      }
    }
    self._lastSelectTargetId = best.id
    return self.tankCell(best)
  }

  // ---- §88: 据守咽喉要地 (chokepoint holding, user request 2026-08-02) ----
  // Rule 2: when the base is NOT under threat, hold the chokepoint (咽喉要地)
  // while swarmed — enemies on field > chokepointHoldThreshold — and chase the
  // enemy nearest a threat point otherwise (<= threshold). The chokepoint is
  // the lower-half cell that can shoot the most threat paths (see
  // Chokepoint.ts); navigating there and holding lets the player intercept
  // base-bound enemies instead of roaming. Gated by chokepointMode (0 = OFF,
  // byte-identical to pre-§88). Falls through to the normal target selection
  // below when no chokepoint/coverage exists (no threat points, no enemies
  // heading for the base, steel-sealed base, etc.).
  //
  // A/B round 2 (per-seed tick-diff): the hold arm ALSO requires a live
  // imminent threat (threatChaseTarget non-null — some enemy within
  // chokepointChaseMaxDist of a threat point). Without it the player walked
  // to the (30-tick cached) chokepoint, found the enemies had turned away,
  // and idled there while the base fell from another side (S19 seed 23:
  // player oscillated at (4,20) for ~1200 ticks). Once at the hold cell with
  // no imminent threat, fall through to the normal nearest-enemy chase.
  if (self.params.chokepointMode > 0 && self.hasBase && !baseUnderThreat) {
    // ---- Rule 1 (imminent enemy) outranks rule 2 (hold) ----
    // An enemy within chokepointChaseMaxDist of a threat point, facing the
    // base, is about to attack it — 优先击杀这些敌人. Chase it directly
    // UNLESS the chokepoint already covers its approach (same row/col with
    // clear LOS to the enemy or its nearest threat point): then holding lets
    // the player shoot it as it crosses — strictly better than a chase.
    // A/B round 3 (S32 seed 23): without this, the hold arm (enemies > 2)
    // marched the player to a chokepoint that could NOT shoot the imminent
    // fast tank's lane, and the fast broke through while A chased it.
    // (chase computed once — the hold arm reuses it, same-tick identical.)
    const chase = self.threatChaseTarget()
    const choke = self.chokepointCell()
    if (chase && (!choke || !chokepointCoversEnemy(self, choke, chase))) {
      self.branchCounts.chokepoint++
      return chase
    }
    if (enemies.length > self.params.chokepointHoldThreshold && choke) {
      // Hold only when an enemy is still approaching a threat point (the
      // imminence gate) AND the hold cell is close enough to march to — a
      // far hold cell is pure march time the enemy turns during (S26 seed
      // 12). Otherwise fall through to the normal hunt below.
      const holdDist = Math.abs(choke.col - playerCell.col) + Math.abs(choke.row - playerCell.row)
      if (
        chase &&
        (self.params.chokepointHoldMaxDist <= 0 || holdDist <= self.params.chokepointHoldMaxDist)
      ) {
        self.branchCounts.chokepoint++
        return choke
      }
    }
  }

  // ---- D1 (plan §D1): approach-band anchor hold in NORMAL selection ----
  // §137 v2 only held the anchor inside the base-threat branch. When an
  // enemy has entered the base approach band (rows >= 20 near the base
  // column) but the base is NOT yet under threat, hold the anchor instead
  // of chasing the nearest enemy away from the base — the D1 objective now
  // places the anchor WITH clear LOS to the staging band, so holding shoots
  // the rush before it reaches the ring. Only when the player is already
  // close (no march time — same gate as §137 v2) and enough enemies are on
  // field (2+ — a lone straggler is better hunted down than waited for).
  const anchorHold = self.getBaseGuardAnchor()
  if (anchorModeOn && anchorHold && !baseUnderThreat && !self.aggressive) {
    let approaching = false
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      if (tc.row >= 20 && Math.abs(tc.col - baseCol) <= 6) {
        approaching = true
        break
      }
    }
    if (
      enemies.length >= 2 &&
      approaching &&
      Math.abs(anchorHold.col - playerCell.col) + Math.abs(anchorHold.row - playerCell.row) <=
        self.params.baseGuardAnchorHoldRange
    ) {
      return anchorHold
    }
  }

  // ---- S6: Aggressive hunt mode ----
  // When few enemies remain, go directly for the nearest enemy.
  // This replaces the old endgame check (which was too restrictive:
  // enemiesRemaining <= 1 && enemies.length <= 1).
  if (canHunt) {
    // Phase 2 §6.3: intent read — all defense/override branches above have
    // returned already, so a valid intent never delays threat response.
    if (self.params.intentMode > 0) {
      const it = intentRead(self, w, p, playerCell, enemies)
      if (it) return it
    }
    // Phase 2 §6.2: targetValueMode — dynamic target value instead of pure
    // distance (targetValue in ThreatBudget: expected base damage prevented
    // over the player's reach+kill horizon). Near-ties (value within
    // TARGET_VALUE_TIE_EPS) fall back to the standard distance ordering, so
    // bonus/coop preferences keep working as tiebreaks. Mode 0 = the loop
    // below verbatim (byte-identical).
    if (self.params.targetValueMode > 0) {
      let best = enemies[0]
      let bestV = -Infinity
      let bestD = Infinity
      for (let ti = 0; ti < enemies.length; ti++) {
        const t = enemies[ti]
        const tc = self.tankCell(t)
        const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
        let adjustedDist = d - (t.bonus ? 2 : 0)
        if (coopActive && partnerCell) {
          const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
          if (pd < d - 3) adjustedDist += 5
        }
        const v = targetValue(self.world, p, t)
        if (v > bestV + TARGET_VALUE_TIE_EPS || (Math.abs(v - bestV) <= TARGET_VALUE_TIE_EPS && adjustedDist < bestD)) {
          bestV = v
          bestD = adjustedDist
          best = t
        }
      }
      self._lastSelectTargetId = best.id
      if (self.params.intentMode > 0) intentWrite(self, w, playerCell, best)
      return self.tankCell(best)
    }
    let best = enemies[0]
    let bestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      // Prefer bonus enemies (they drop power-ups) when distances are close.
      let adjustedDist = d - (t.bonus ? 2 : 0)
      // 协作: 伙伴更近的敌人降优先级
      if (coopActive && partnerCell) {
        const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
        if (pd < d - 3) adjustedDist += 5
      }
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    self._lastSelectTargetId = best.id
    if (self.params.intentMode > 0) intentWrite(self, w, playerCell, best)
    return self.tankCell(best)
  }

  // M0.5 退役（2026-08-03）: D1/D2 guardBand + damagedArmor 空块已移除
  // （否决，移入 experimental.ts 归档）。

  // ---- Normal target selection ----
  // When the base is NOT under threat, behave like the no-base case:
  // chase the nearest enemy to the player. This prevents the AI from
  // chasing enemies near the base while ignoring closer enemies,
  // which was the #1 cause of low kill counts in maze stages.
  // The baseUnderThreat check runs every tick, so the AI immediately
  // switches to defense mode when an enemy approaches the base.
  if (!baseUnderThreat) {
    // Dual central breach (plan/dual-central-breach-strategy.md §B): P1
    // guards the anchor full-time — its job is to intercept col-12 breachers,
    // not hunt across the map. P2 handles flanks/hunting. P1 still fires at
    // enemies in range via T2a/engage (the candidate chain handles firing
    // independent of the navigation target). Gated by spectateDual &&
    // centralBreachRisk && !isPlayer2 — single-player and P2 unaffected.
    //
    // §180 (autopsy seed34): when NO enemy is in the center lane (cols 11–13)
    // but enemies are gathering on the RIGHT side (cols ≥ 15, rows ≥ 10)
    // — typically breaking through the right-flank walls toward the base —
    // P1 temporarily leaves the anchor to intercept the nearest right-side
    // enemy. This prevents the right-flank blind spot where E2/E26 carve
    // through the right walls unopposed while P1 stares at an empty center.
    // P1 returns to the anchor as soon as a center enemy reappears.
    if (self.world.spectateDual && self._centralBreachRisk && !self.isPlayer2()) {
      const anchor = self.getBaseGuardAnchor()
      if (anchor) return anchor
    }
    // §177: the P2 half of the same split — sweep the enemy spawn points
    // instead of drifting toward a static flank cell. Yields to the normal
    // nearest-enemy hunt as soon as an enemy is close enough to engage (see
    // findDualPatrolTargetImpl). Gated by dualStrategyActive (spectateDual || coop)
    // && centralBreachRisk && isPlayer2 — single-player and P1 unaffected (byte-identical).
    if (self.dualStrategyActive && self.isPlayer2() && self.params.dualCentralBreachP2Patrol > 0) {
      const patrol = findDualPatrolTargetImpl(self, playerCell, enemies)
      if (patrol) return patrol
    }
    // Phase 3 (plan §7): dynamic attack coverage point. After every defense
    // and override branch — a coverage hold never delays threat response.
    // Falls through to the normal hunt when no better point exists (规划失败
    // 时回到原有 hunt/engage 行为).
    if (self.params.coverageMode > 0) {
      const cov = coveragePlanImpl(self, w, p, playerCell, enemies)
      if (cov) return cov
    }
    // Phase 2 §6.3: intent read (same position as the §170 commit — after
    // every defense/override branch, so a valid intent never delays threat
    // response). intentMode wins over the plain §170 commit when both are on.
    if (self.params.intentMode > 0) {
      const it = intentRead(self, w, p, playerCell, enemies)
      if (it) return it
    }
    // §170 hunt commit: while the commit window is open and the committed
    // enemy is still alive, keep chasing it — the per-tick nearest-pick
    // re-routes the mid-approach player whenever the nearest identity
    // flips, sinking the approach cost (DECISIONS §170). Defense cascade
    // runs above, so a commit never delays threat response; expiry simply
    // falls through to the free nearest-selection below (which re-commits
    // if the same enemy is still the pick). Disabled when intentMode > 0.
    if (
      self.params.intentMode <= 0 &&
      self.params.huntCommitTicks > 0 &&
      self._huntCommitId >= 0 &&
      w.frame < self._huntCommitUntil
    ) {
      for (let ti = 0; ti < enemies.length; ti++) {
        if (enemies[ti].id === self._huntCommitId) return self.tankCell(enemies[ti])
      }
    }
    let best = enemies[0]
    let bestDist = Infinity
    if (self.params.targetValueMode > 0) {
      // Phase 2 §6.2 (same contract as the canHunt variant above; also keeps
      // the §171 pathTargetMode flavor in the tiebreak when enabled).
      let bestV = -Infinity
      let bestD = Infinity
      for (let ti = 0; ti < enemies.length; ti++) {
        const t = enemies[ti]
        const tc = self.tankCell(t)
        const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
        let adjustedDist: number
        if (self.params.pathTargetMode > 0) {
          adjustedDist =
            huntPathCost(self, playerCell, t.id, tc.col, tc.row) -
            (t.bonus ? self.params.bonusHuntBias : 0)
          if (coopActive && partnerCell) {
            const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
            if (pd < d - 3) adjustedDist += 5
          }
        } else {
          adjustedDist = d - (t.bonus ? self.params.bonusHuntBias : 0)
          if (coopActive && partnerCell) {
            const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
            if (pd < d - 3) adjustedDist += 5
          }
        }
        const v = targetValue(self.world, p, t)
        if (v > bestV + TARGET_VALUE_TIE_EPS || (Math.abs(v - bestV) <= TARGET_VALUE_TIE_EPS && adjustedDist < bestD)) {
          bestV = v
          bestD = adjustedDist
          best = t
        }
      }
      if (self.params.intentMode > 0) intentWrite(self, w, playerCell, best)
      else if (self.params.huntCommitTicks > 0) {
        self._huntCommitId = best.id
        self._huntCommitUntil = w.frame + self.params.huntCommitTicks
      }
      self._lastSelectTargetId = best.id
      return self.tankCell(best)
    }
    if (self.params.pathTargetMode > 0) {
      // §171: score enemies by TRUE travel cost instead of Manhattan —
      // maze stages put wall-separated enemies "near" in Manhattan while the
      // real approach cost is several times larger (DECISIONS §171). The
      // bonusHuntBias adjustment (§172) still applies; per-tick reselection
      // is kept.
      for (let ti = 0; ti < enemies.length; ti++) {
        const t = enemies[ti]
        const tc = self.tankCell(t)
        let adjustedDist =
          huntPathCost(self, playerCell, t.id, tc.col, tc.row) -
          (t.bonus ? self.params.bonusHuntBias : 0)
        // 协作: 伙伴更近的敌人降优先级
        if (coopActive && partnerCell) {
          const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
          if (pd < Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row) - 3)
            adjustedDist += 5
        }
        if (adjustedDist < bestDist) {
          bestDist = adjustedDist
          best = t
        }
      }
    } else {
      for (let ti = 0; ti < enemies.length; ti++) {
        const t = enemies[ti]
        const tc = self.tankCell(t)
        const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
        // §172: bonus preference is the bonusHuntBias knob (default 2 =
        // the historical constant, byte-identical).
        let adjustedDist = d - (t.bonus ? self.params.bonusHuntBias : 0)
        // 协作: 伙伴更近的敌人降优先级
        if (coopActive && partnerCell) {
          const pd = Math.abs(tc.col - partnerCell.col) + Math.abs(tc.row - partnerCell.row)
          if (pd < d - 3) adjustedDist += 5
        }
        if (adjustedDist < bestDist) {
          bestDist = adjustedDist
          best = t
        }
      }
    }
    if (self.params.intentMode > 0) intentWrite(self, w, playerCell, best)
    else if (self.params.huntCommitTicks > 0) {
      self._huntCommitId = best.id
      self._huntCommitUntil = w.frame + self.params.huntCommitTicks
    }
    self._lastSelectTargetId = best.id
    return self.tankCell(best)
  }

  // Base is under threat — find the most threatening enemy. Enemies with a
  // clear shot at the base (aligned + no walls in between) get a huge bonus
  // (defenseClearShotBonus, §59) — they can destroy the base with their next
  // bullet and must be prioritized. Others use kindThreatWeight scoring.
  let bestEnemy: Tank | null = null
  let bestScore = -Infinity
  // §177: dual central breach P2 defense de-confliction. The defense score
  // below is a function of the ENEMY and the BASE only — the player's own
  // position never enters it — so P1 and P2 compute an identical ranking and
  // both drive at the same tank while the other three keep digging the ring.
  // (The coop de-confliction above only covers the hunt branches.) When the
  // knob is on, the gated P2 takes the RUNNER-UP instead, so the two tanks
  // cover the two most dangerous lanes. Deterministic and symmetric-free:
  // the split is by player index, not by who happens to be closer.
  const p2DefenseSecond =
    self.params.dualCentralBreachP2DefenseSecond > 0 &&
    self.dualStrategyActive &&
    self.isPlayer2() &&
    coopActive
  let secondEnemy: Tank | null = null
  let secondScore = -Infinity
  // §137 v2: any enemy with a clear shot at the base right now? If so the
  // player MUST chase (the anchor hold cannot cover every lane); otherwise
  // holding the guard anchor is safe and intercepts the approach band.
  let anyClearShot = false
  // D2 / 拆环威胁: count intact ring bricks ONCE per call (only when the knob
  // is on). The breach bonus grows as the ring weakens — ×1 at full ring →
  // ×1.875 at one brick left — so the scorer reacts EARLY (breacher shooting
  // the intact ring) and the urgency rises as the breach completes.
  const breachOn = self.params.defenseBreachBonus > 0
  // D1: the ring-breach predicate also gates the anchor hold — do NOT hold
  // the anchor while a breach is active (the anchor may stare at the wrong
  // lane; chase the breacher instead). Runs only when D2 or D1 is on
  // (default both 0 → zero cost, byte-identical).
  const breachCheckOn = breachOn || anchorModeOn
  let anyBreacher = false
  let ringIntact = 8
  if (breachOn) {
    ringIntact = 0
    const tm = w.tileMap
    for (let dc = -1; dc <= 2; dc++) {
      if (tm.get(baseCol + dc, baseRow - 1) === 'brick') ringIntact++
    }
    for (let dr = 0; dr <= 1; dr++) {
      if (tm.get(baseCol - 1, baseRow + dr) === 'brick') ringIntact++
      if (tm.get(baseCol + 2, baseRow + dr) === 'brick') ringIntact++
    }
  }
  for (let ti = 0; ti < enemies.length; ti++) {
    const t = enemies[ti]
    const tc = self.tankCell(t)
    const distToBase = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
    // §59: an enemy with a clear shot at the base is always considered,
    // even beyond threatRangeCells — it can destroy the base NOW from any
    // distance. Other enemies are filtered by threatRangeCells as before.
    const hasClearShot = self.params.defenseClearShotBonus > 0 && enemyCanShootBase(self, t)
    if (hasClearShot) anyClearShot = true
    if (distToBase > self.params.threatRangeCells && !hasClearShot) continue

    // M0.5 退役: smartThreatModel 的 defense-priority kind weights 已移除
    // （Phase A 否决）——始终使用 kindThreatWeight（原 OFF 路径，字节相同）。
    const defenseKindWeight = kindThreatWeight(t.kind)
    const bonusWeight = t.bonus ? 3 : 0
    const urgencyBonus = tc.row >= defenseRow ? (tc.row - defenseRow + 1) * 100 : 0
    const proximityBonus = tc.row >= 20 ? 50 : 0
    // §59: canShootBaseFrom bonus — enemy has a clear shot at the base. This
    // is the highest-priority target: it can destroy the base NOW. Controlled
    // by defenseClearShotBonus (default 500, 0 = OFF = byte-identical to pre-§59).
    const clearShotBonus = hasClearShot ? self.params.defenseClearShotBonus : 0
    // D2 / 拆环威胁: enemy whose next bullet destroys an intact ring brick —
    // §59's static clear-shot is false until the ring falls (too late, the
    // fatal bullet is already flying). Fires EARLY, scoring-only; the term
    // grows as the ring weakens ((8 − ringIntact) destroyed bricks × 0.125).
    // Can't co-fire with clearShotBonus (mutually exclusive by construction).
    const isBreacher = breachCheckOn && !hasClearShot && enemyCanBreachRing(self, t)
    if (isBreacher && anchorModeOn) anyBreacher = true
    const breachBonus =
      breachOn && isBreacher ? self.params.defenseBreachBonus * (1 + (8 - ringIntact) * 0.125) : 0
    // §132 / 方向 B (fast × base-proximity): a fast tank closing on the base
    // is a bigger threat than a basic tank the same distance out (it reaches
    // the base in ~5/6 the time and keeps firing as it moves). The static
    // defenseKindWeight (fast=2 vs basic=1) + linear -distToBase*10 cannot
    // express that — both score identically at equal distance. Add
    //   weight × speedRatio(kind) × clamp01((range − distToBase) / range)
    // where speedRatio = BASE_SPEED_CPS[kind] / BALANCED_ENEMY_CPS (fast
    // 1.2, basic 1.0, power 0.95, armor 0.85) and the approach factor ramps
    // 1 at the base ring → 0 at fastBaseApproachRangeCells. weight = 0
    // (default) short-circuits to 0 — byte-identical to pre-§132.
    const speedApproachBonus =
      self.params.fastBaseApproachWeight > 0 && distToBase < self.params.fastBaseApproachRangeCells
        ? self.params.fastBaseApproachWeight *
          (BASE_SPEED_CPS[t.kind] / BALANCED_ENEMY_CPS) *
          (1 - distToBase / self.params.fastBaseApproachRangeCells)
        : 0
    const score =
      -distToBase * 10 +
      (defenseKindWeight + bonusWeight) * 30 +
      urgencyBonus +
      proximityBonus +
      clearShotBonus +
      breachBonus +
      speedApproachBonus
    if (score > bestScore) {
      if (p2DefenseSecond) {
        secondScore = bestScore
        secondEnemy = bestEnemy
      }
      bestScore = score
      bestEnemy = t
    } else if (p2DefenseSecond && score > secondScore) {
      secondScore = score
      secondEnemy = t
    }
  }

  // §177: hand P2 the runner-up threat (P1 keeps the top one). Only when a
  // runner-up exists — with a single threat on the field both tanks should
  // still converge on it.
  //
  // §180 (autopsy seed34): proximity override. The defense score is a
  // function of (enemy, base) only — the player's position never enters it
  // — so P1 and P2 compute identical rankings. With defenseSecond, P2 is
  // always sent after the runner-up. But when P2 is MUCH closer to the top
  // threat than P1 (e.g., P2 is in the right-bottom quadrant and the base
  // killer is right there, while P1 is at the center anchor), P2 should
  // take the top threat instead. Without this override, P2 was sent after
  // a runner-up on the far side of the map while a `fast` tank at (24,21)
  // was 4-shotting the base unopposed.
  if (p2DefenseSecond && secondEnemy && bestEnemy) {
    const topCell = self.tankCell(bestEnemy)
    const myDistToTop =
      Math.abs(topCell.col - playerCell.col) + Math.abs(topCell.row - playerCell.row)
    const partner = self.coopPartner()
    let takeTop = false
    if (partner && partner.alive && partner.spawnTimer <= 0) {
      const pCell = self.tankCell(partner)
      const partnerDistToTop = Math.abs(topCell.col - pCell.col) + Math.abs(topCell.row - pCell.row)
      // P2 takes the top threat when it's > 5 cells closer than P1
      if (myDistToTop < partnerDistToTop - 5) takeTop = true
    }
    if (!takeTop) bestEnemy = secondEnemy
  }

  if (!bestEnemy) {
    // No enemy within threat range — go after the nearest enemy anyway.
    // Sitting idle at the defense position while enemies roam the top of
    // the map means the player never engages and never clears the stage.
    let nearest = enemies[0]
    let nearestDist = Infinity
    for (let ti = 0; ti < enemies.length; ti++) {
      const t = enemies[ti]
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
      if (d < nearestDist) {
        nearestDist = d
        nearest = t
      }
    }
    return self.tankCell(nearest)
  }

  // §137 v2: hold the guard anchor (funnel mouth) while the base is under
  // threat but no enemy can shoot it YET. From the anchor the §134
  // lane-intercept + t2a fire at enemies crossing the approach band BEFORE
  // they reach the ring (Battlement: the row-22 antechamber). Chase directly
  // when an enemy already has a clear shot (must kill it NOW, the anchor may
  // not cover its lane) or the player is too far from the anchor to make
  // holding worthwhile (marching across the map while the base is threatened
  // loses more than it gains). Only active when baseGuardAnchorMode > 0
  // (getBaseGuardAnchor returns null otherwise — byte-identical).
  // §177: the guard anchor is base-relative, so P1 and P2 hold the SAME cell
  // and §159 yield makes one of them shuffle uselessly. Split it: arm 1 sends
  // the gated P2 to its own shifted defense post, arm 2 makes P2 skip the
  // hold entirely and drive at its runner-up threat. 0 = OFF (shared anchor,
  // byte-identical).
  const p2AnchorSplit =
    self.params.dualCentralBreachP2AnchorSplit > 0 &&
    self.dualStrategyActive &&
    self.isPlayer2() &&
    coopActive
  const guardAnchor = p2AnchorSplit ? null : self.getBaseGuardAnchor()
  if (p2AnchorSplit && self.params.dualCentralBreachP2AnchorSplit === 1) {
    const post = self.getDefaultDefensePosition()
    if (
      !anyClearShot &&
      !anyBreacher &&
      Math.abs(post.col - playerCell.col) + Math.abs(post.row - playerCell.row) <=
        self.params.baseGuardAnchorHoldRange
    ) {
      return post
    }
  }
  if (
    guardAnchor &&
    !anyClearShot &&
    !anyBreacher &&
    Math.abs(guardAnchor.col - playerCell.col) + Math.abs(guardAnchor.row - playerCell.row) <=
      self.params.baseGuardAnchorHoldRange
  ) {
    return guardAnchor
  }

  // Go directly toward the best enemy. With the bulletCap-aware onCooldown
  // fix, the player fires frequently and can kill enemies while pursuing.
  // The interception-point strategy was abandoned because wandering enemies
  // rarely cross the fixed interception column, leaving the player idle.
  return self.tankCell(bestEnemy)
}
