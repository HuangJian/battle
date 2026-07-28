import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { Cell } from '../../utils/pathfind'
import { CELL, BASE_POS, POWERUP_TIMEOUT_MS } from '../../constants'
import { pxToCell } from '../../utils/pathfind'
import { POWERUP_PRIORITY, KIND_THREAT_WEIGHT } from './constants'

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
  if (w.powerUps.length === 0) return null

  let bestPu: { cell: Cell; score: number } | null = null

  for (const pu of w.powerUps) {
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)

    // S5d: if about to expire and too far, skip.
    const lifeRemaining = POWERUP_TIMEOUT_MS - pu.lifeTimer
    if (lifeRemaining < 3000 && dist > 5) continue

    // S5a: base priority by type.
    const priority = POWERUP_PRIORITY[pu.type] ?? 5

    // NEW Requirement 3: Calculate route danger
    const dangerLevel = self.calculateRouteDanger(pcx, pcy, cx, cy)

    // Fix Bug 2: Score formula was reversed — priority * 1000 gave bomb
    // (priority=0) a base score of 0 and boat (priority=6) a base of 6000.
    // Now (6 - priority) * 1000 gives bomb the highest base score.
    let score = (6 - priority) * 1000 - dist * 10 - dangerLevel * 500

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

    if (!bestPu || score > bestPu.score) {
      bestPu = { cell: pxToCell(pu.x, pu.y), score }
    }
  }

  return bestPu?.cell ?? null
}

/**
 * NEW Requirement 3: Calculate how dangerous a route is.
 * Returns a danger level from 0 (safe) to N (many enemies on the path).
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

  // Simple heuristic: count enemies that are closer to the target than we are
  const targetCell = pxToCell(toX, toY)
  const playerCell = pxToCell(fromX, fromY)
  const playerDistToTarget =
    Math.abs(targetCell.col - playerCell.col) + Math.abs(targetCell.row - playerCell.row)

  for (const t of w.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue

    const enemyCell = pxToCell(t.x, t.y)
    const enemyDistToTarget =
      Math.abs(targetCell.col - enemyCell.col) + Math.abs(targetCell.row - enemyCell.row)

    // If enemy is closer to target than player, and on the path, add danger
    if (enemyDistToTarget < playerDistToTarget) {
      // Check if enemy is roughly between player and target
      const dx = enemyCell.col - playerCell.col
      const dy = enemyCell.row - playerCell.row
      const tx = targetCell.col - playerCell.col
      const ty = targetCell.row - playerCell.row

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
 * Default defense position: centered above the base at the defense row.
 * This is the fallback when no enemies are present.
 * Gap B: when the stage has no base, returns the player's current cell
 * (stay put — there's nothing to defend).
 */
export function getDefaultDefensePositionImpl(self: GodAIInput): Cell {
  if (!self.hasBase) return self.playerCell()
  return { col: BASE_POS.col, row: BASE_POS.row - self.params.defenseRowOffset }
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
export function selectTargetImpl(self: GodAIInput, playerCell: Cell): Cell | null {
  const w = self.world
  const p = w.player
  if (!p) return null

  const baseCol = BASE_POS.col
  const baseRow = BASE_POS.row
  const defenseRow = baseRow - self.params.defenseRowOffset

  const enemies = w.tanks.filter((t) => t.alive && t.spawnTimer <= 0)
  if (enemies.length === 0) return self.getDefaultDefensePosition()

  // ---- Gap B: no-base fast path (plan/God-AI-Curriculum §3) ----
  // When the stage has no base, the AI is a pure hunter: always chase the
  // nearest enemy. No defense positioning, no base-threat checks, no
  // distance-from-base constraint. This is the correct behavior for
  // curriculum stages 1-4 (no-base) and also for real stages where the
  // base has already been destroyed (the AI should still try to clear).
  if (!self.hasBase) {
    let best = enemies[0]
    let bestDist = Infinity
    for (const t of enemies) {
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // ---- S6: Determine strategy mode ----
  // Emergency defense: any enemy within 3 cols of the base and at or
  // below row 20 (within 4 rows of the base) — these are immediate
  // threats that need direct interception. The old check (row >=
  // defenseRow = 23) triggered too late — the enemy was already adjacent
  // to the base and the player couldn't get back in time.
  const baseUnderThreat = enemies.some((t) => {
    const tc = self.tankCell(t)
    return Math.abs(tc.col - baseCol) <= 3 && tc.row >= 20
  })

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

  // If the player is too far from the base, return to defense position.
  // In hunt mode, the player can roam freely.
  // When base is under threat, use strict defense distance.
  // When base is NOT under threat, allow free hunting — the AI checks
  // for threats every tick and will immediately return to defense if
  // an enemy approaches the base. This prevents the "0-kill turtle"
  // where the AI sits at the defense position while enemies roam the
  // top of the map, unable to reach them.
  const playerDistToBase = Math.abs(playerCell.col - baseCol) + Math.abs(playerCell.row - baseRow)
  if (!canHunt && baseUnderThreat && playerDistToBase > self.params.maxPlayerDistFromBase) {
    return self.getDefaultDefensePosition()
  }

  // Aggressive mode (freeze): enemies can't move — chase nearest directly.
  if (self.aggressive) {
    let best = enemies[0]
    let bestDist = Infinity
    for (const t of enemies) {
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      if (d < bestDist) {
        bestDist = d
        best = t
      }
    }
    return self.tankCell(best)
  }

  // ---- S6: Aggressive hunt mode ----
  // When few enemies remain, go directly for the nearest enemy.
  // This replaces the old endgame check (which was too restrictive:
  // enemiesRemaining <= 1 && enemies.length <= 1).
  if (canHunt) {
    let best = enemies[0]
    let bestDist = Infinity
    for (const t of enemies) {
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      // Prefer bonus enemies (they drop power-ups) when distances are close.
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // When the base is NOT under threat, behave like the no-base case:
  // chase the nearest enemy to the player. This prevents the AI from
  // chasing enemies near the base while ignoring closer enemies,
  // which was the #1 cause of low kill counts in maze stages.
  // The baseUnderThreat check runs every tick, so the AI immediately
  // switches to defense mode when an enemy approaches the base.
  if (!baseUnderThreat) {
    let best = enemies[0]
    let bestDist = Infinity
    for (const t of enemies) {
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
      const adjustedDist = d - (t.bonus ? 2 : 0)
      if (adjustedDist < bestDist) {
        bestDist = adjustedDist
        best = t
      }
    }
    return self.tankCell(best)
  }

  // Base is under threat — find the enemy closest to the base.
  let bestEnemy: Tank | null = null
  let bestScore = -Infinity
  for (const t of enemies) {
    const tc = self.tankCell(t)
    const distToBase = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
    if (distToBase > self.params.threatRangeCells) continue

    const threatWeight = KIND_THREAT_WEIGHT[t.kind] ?? 1
    const bonusWeight = t.bonus ? 3 : 0
    // HUGE bonus for enemies at or below the defense row — critical threat.
    // Fix Bug 1: urgencyBonus was reversed — (baseRow - tc.row) gave 0 at
    // baseRow and negative below. Now (tc.row - defenseRow + 1) gives higher
    // bonus the closer the enemy is to the base.
    const urgencyBonus = tc.row >= defenseRow ? (tc.row - defenseRow + 1) * 100 : 0
    // Small bonus for enemies in the base row region (rows 20-23)
    const proximityBonus = tc.row >= 20 ? 50 : 0
    const score =
      -distToBase * 10 + (threatWeight + bonusWeight) * 30 + urgencyBonus + proximityBonus
    if (score > bestScore) {
      bestScore = score
      bestEnemy = t
    }
  }

  if (!bestEnemy) {
    // No enemy within threat range — go after the nearest enemy anyway.
    // Sitting idle at the defense position while enemies roam the top of
    // the map means the player never engages and never clears the stage.
    let nearest = enemies[0]
    let nearestDist = Infinity
    for (const t of enemies) {
      const tc = self.tankCell(t)
      const d = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
      if (d < nearestDist) {
        nearestDist = d
        nearest = t
      }
    }
    return self.tankCell(nearest)
  }

  // Go directly toward the best enemy. With the bulletCap-aware onCooldown
  // fix, the player fires frequently and can kill enemies while pursuing.
  // The interception-point strategy was abandoned because wandering enemies
  // rarely cross the fixed interception column, leaving the player idle.
  return self.tankCell(bestEnemy)
}
