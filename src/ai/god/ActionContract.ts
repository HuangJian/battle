/**
 * ActionContract — plan Phase 2 §6.1 "行动有效性" gate (default OFF).
 *
 * The M0 ledger's #1 failure family is no_output_commit: a defense branch
 * (defenseIntercept / baseLaneSentry) submits a standing hold
 * (`_moveDir = null, _fire = false`) while on cooldown — the player becomes
 * a statue and the base dies. §6.1 forbids committing such a branch "just
 * because an enemy was detected"; it may commit only when the hold has valid
 * waiting value:
 *
 *   1. a base-bound bullet is on the held ray (interception imminent), or
 *   2. the player's own bullet is in flight on the threat line (the kill is
 *      already resolving), or
 *   3. the standing shot beats the enemy's damage deadline
 *      (standingKillAssessment.killSlack > 0 — the next legal fire + flight
 *      lands before the base dies).
 *
 * §199 lesson (7 consecutive escape-param falsifications): do NOT blindly
 * fall through and abandon the firing position. This contract only vetoes
 * PRODUCTION-FREE standing commits (no move, no fire); every submission with
 * movement or fire output passes unconditionally — so a rejection sends the
 * decision chain to engage/hunt/navigate, which produce output, never into a
 * dodge-escape that abandons the lane.
 *
 * Pure functions of (world, tank) — no RNG, no World mutation, no state.
 */

import { CELL, BASE_POS } from '../../constants'
import type { World } from '../../game/World'
import type { Tank } from '../../types'
import { standingKillAssessment } from './ThreatBudget'

export interface ContractInputs {
  world: World
  player: Tank
  /** The enemy the branch is holding against (null when holding a lane only). */
  threat: Tank | null
  /** An enemy bullet is on the held ray, heading toward the base. */
  enemyBulletOnRay: boolean
  /** The player's own bullet is in flight on the threat line. */
  ownBulletOnRay: boolean
}

export interface HoldVerdict {
  valid: boolean
  reason: string | null
}

/** Verdict for a standing (no move, no fire) hold submission. */
export function contractStandingHold(c: ContractInputs): HoldVerdict {
  if (c.enemyBulletOnRay) {
    return { valid: true, reason: 'enemy bullet on held ray — interception imminent' }
  }
  if (c.ownBulletOnRay && c.threat) {
    return { valid: true, reason: 'own bullet in flight on the threat line' }
  }
  if (c.threat) {
    const s = standingKillAssessment(c.world, c.player, c.threat)
    if (s.killSlack > 0) {
      return {
        valid: true,
        reason: `standing shot beats deadline (killSlack=${s.killSlack.toFixed(1)}, deadline=${s.deadline.toFixed(0)}, killEta=${s.killEta.toFixed(0)})`,
      }
    }
    return {
      valid: false,
      reason: `standing shot cannot beat deadline (killSlack=${s.killSlack.toFixed(1)}, deadline=${s.deadline.toFixed(0)}, killEta=${s.killEta.toFixed(0)})`,
    }
  }
  return { valid: false, reason: 'no threat — standing hold has no valid waiting value' }
}

/**
 * Is any ENEMY bullet on the ray through the player (vertical = same column,
 * horizontal = same row), heading toward the base zone? Bullets are ~6px
 * wide, so the overlap window is half the bullet width + 3px on each side.
 */
export function enemyBulletOnRay(world: World, player: Tank, vertical: boolean): boolean {
  const bpx = player.x + player.w / 2
  const bpy = player.y + player.h / 2
  const baseTop = BASE_POS.row * CELL
  const baseBottom = (BASE_POS.row + 2) * CELL
  const baseLeft = BASE_POS.col * CELL
  const baseRight = (BASE_POS.col + 2) * CELL
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    if (vertical) {
      if (Math.abs(bx - bpx) >= (b.w + 6) / 2) continue
      if (b.dir === 'down' && by < baseTop) return true
      if (b.dir === 'up' && by > baseBottom) return true
    } else {
      if (Math.abs(by - bpy) >= (b.w + 6) / 2) continue
      if (b.dir === 'right' && bx < baseLeft) return true
      if (b.dir === 'left' && bx > baseRight) return true
    }
  }
  return false
}

/** Is the player's own bullet in flight on the (vertical|horizontal) ray? */
export function ownBulletOnRay(world: World, player: Tank, vertical: boolean): boolean {
  const bpx = player.x + player.w / 2
  const bpy = player.y + player.h / 2
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.ownerId !== player.id) continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    if (vertical ? Math.abs(bx - bpx) < (b.w + 6) / 2 : Math.abs(by - bpy) < (b.w + 6) / 2) {
      return true
    }
  }
  return false
}
