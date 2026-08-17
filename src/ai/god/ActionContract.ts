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
import {
  standingKillAssessment,
  ticksUntilLegalTurn,
  ticksUntilFire,
  turnCostTicks,
} from './ThreatBudget'

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
 * Is any ENEMY bullet on the held ray such that holding this ray can still
 * intercept it under the fairness rules? (protocol §5.1 — an interception
 * SEGMENT, not just "same line".) TRUE requires ALL of:
 *
 *   1. lateral overlap with the ray (bullets are ~6px wide);
 *   2. the bullet is heading toward the base zone;
 *   3. the bullet is IN FRONT of the held muzzle (`aimDir`), not behind the
 *      player — a bullet that already passed the player cannot be met by
 *      this ray;
 *   4. the bullet has not already passed the base's near edge (a bullet at
 *      the base is this tick's damage, not an interception opportunity);
 *   5. interception is FEASIBLE at current speeds after the player's real
 *      readiness delay (legal-turn wait when not yet facing `aimDir`, plus
 *      fire re-arm): head-on shots must still meet in front of the muzzle;
 *      chase shots (bullet between player and base, moving away) need the
 *      player's bullet to be faster AND to catch it before the base edge.
 *
 * Counterexamples that must stay false (§5.1 tests): same column but already
 * past the base; same column but behind the player; wrong direction; ray
 * overlap but no feasible catch. The horizontal case is symmetric.
 */
export function enemyBulletOnRay(
  world: World,
  player: Tank,
  aimDir: 'up' | 'down' | 'left' | 'right',
): boolean {
  const vertical = aimDir === 'up' || aimDir === 'down'
  const sigma = aimDir === 'right' || aimDir === 'down' ? 1 : -1
  const bpx = player.x + player.w / 2
  const bpy = player.y + player.h / 2
  const baseTop = BASE_POS.row * CELL
  const baseLeft = BASE_POS.col * CELL
  const baseRight = (BASE_POS.col + 2) * CELL
  // Readiness delay (ticks) before the player's bullet can depart this ray.
  const turnWait =
    player.dir !== aimDir ? ticksUntilLegalTurn(world, player) + turnCostTicks(world) : 0
  const ready = turnWait + ticksUntilFire(world, player)
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.allegiance !== 'enemy' || b.speed <= 0) continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const pos = vertical ? by : bx
    const playerPos = vertical ? bpy : bpx
    // (1) lateral overlap with the ray.
    if (vertical ? Math.abs(bx - bpx) >= (b.w + 6) / 2 : Math.abs(by - bpy) >= (b.w + 6) / 2) {
      continue
    }
    // (2) heading toward the base along this axis; (4) not at/past the base.
    // The bullet travels in direction v and first meets the base at
    // `nearEdge`; "at or past" means it has REACHED that edge in its own
    // direction: v·(pos − nearEdge) ≥ 0. (Down-moving bullets approach the
    // base from ABOVE — pos < baseTop — so the check must NOT be a bare
    // pos ≤ baseTop comparison.)
    let v: number // bullet velocity sign along the axis
    let nearEdge: number // first base edge the bullet would reach
    if (vertical) {
      if (b.dir !== 'down') continue // base sits at the bottom rows
      v = 1
      nearEdge = baseTop
    } else {
      if (b.dir === 'left') {
        v = -1
        nearEdge = baseRight
      } else if (b.dir === 'right') {
        v = 1
        nearEdge = baseLeft
      } else {
        continue
      }
    }
    if (v * (pos - nearEdge) >= 0) continue
    // (NEW, review P1) The bullet must START on the player's side of the base.
    // If the base sits between player and bullet, the player's shot dies on
    // the base before it can reach the bullet — no interception exists.
    const baseBottom = (BASE_POS.row + 2) * CELL
    const baseInFront = vertical
      ? sigma === 1
        ? playerPos < baseTop
        : playerPos > baseBottom
      : sigma === 1
        ? playerPos < baseLeft
        : playerPos > baseRight
    const playerEdge = vertical ? (sigma === 1 ? baseTop : baseBottom) : sigma === 1 ? baseLeft : baseRight
    if (baseInFront && sigma * (pos - playerEdge) >= 0) continue
    // Advance the bullet through the readiness delay, then require (3) it is
    // still in front of the muzzle.
    const bulletPos = pos + v * b.speed * ready
    if (sigma * (bulletPos - playerPos) <= 0) continue
    // (5) feasibility. Head-on: the bullets close at vp+ve — they always
    // meet in front of the muzzle; only the base near-edge can void it.
    // Chase: the player's bullet must be faster and catch before the edge.
    const meetOrCatch: number =
      v === -sigma
        ? bulletPos + v * ((sigma * (bulletPos - playerPos)) / (player.bulletSpeed + b.speed)) * b.speed
        : player.bulletSpeed > b.speed
          ? bulletPos +
            v * ((sigma * (bulletPos - playerPos)) / (player.bulletSpeed - b.speed)) * b.speed
          : Number.NaN
    if (!Number.isFinite(meetOrCatch)) continue
    // The interception must resolve while the bullet is still on its approach
    // side of the near edge — i.e. BEFORE it has reached the base.
    if (v * (meetOrCatch - nearEdge) >= 0) continue
    // (NEW, review P1) The meeting point must also lie on the player's side
    // of the base — before the PLAYER's own bullet would hit the base. The
    // bullet's near edge and the player's limiting edge differ for head-on
    // bullets approaching from the far side (base between player and bullet);
    // the old near-edge check alone let those through (e.g. player right of
    // the base firing left at a shell crossing from the left).
    if (baseInFront && sigma * (meetOrCatch - playerEdge) >= 0) continue
    return true
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
