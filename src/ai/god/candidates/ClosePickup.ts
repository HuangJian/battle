// candidates/ClosePickup.ts — the closePickup candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type GodAIInput } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import {
  baseRingBreachedImpl,
  commitPowerupTail,
  isDualCentralBreachHoldP1,
} from '../candidates/shared'
import { findClosePickupTargetImpl } from '../StrategyPlanner'

export function evalClosePickup(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, pcx, pcy } = ctx
  // §178: dual central-breach P1 — pure defender, never diverts to power-ups.
  if (isDualCentralBreachHoldP1(self)) return false
  if (self.aggressive) return false
  if (self.params.closePickupRange <= 0) return false
  // Skip when base is under threat — defense outranks a nearby item.
  if (self.hasBase && self.isBaseUnderThreat()) return false
  // §225-B: 危局拾取抑制 — ring 已被击穿时非 HIGH 拾取让位（防"基地掉血还
  // 在捡 star"）；HIGH tier 豁免在 PICKUP_HIGH（bomb/freeze/fence 有效）。
  if (self.params.baseAlertPickupSuppress > 0 && self.hasBase && baseRingBreachedImpl(w))
    return false
  const target = findClosePickupTargetImpl(self, pcx, pcy)
  if (!target) return false
  // §186: Skip when pixel-stuck — the powerup is unreachable.
  if (self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks)
    return false
  return commitPowerupTail(self, ctx, target)
}


/** closePickup(540) — §158: non-freeze close-range power-up pickup.
 *
 * When NOT in freeze/shield mode, if a power-up is within `closePickupRange`
 * (default 4) cells and there is no immediate bullet threat (DODGE at weight
 * 1000 already declined — if it hadn't, this candidate would never run),
 * navigate to pick it up while firing at enemies in the move direction
 * (随手开火).
 *
 * Weight 540 < DEFENSE_INTERCEPT(550): defense intercept runs first when an
 * enemy is aligned with or approaching the base lane. This prevents the
 * seed-999 regression where the player diverted to a nearby power-up while
 * an enemy was breaking through to the base lane, arriving too late to
 * intercept. The isBaseUnderThreat guard still catches aligned enemies.
 *
 * Unlike PICKUP_HIGH/MID (which gate on nearby-enemy proximity and route
 * danger), this candidate has NO enemy gates — close items are worth
 * grabbing even with enemies nearby, as long as no bullet is currently
 * threatening the player AND no defense intercept is needed.
 *
 * Gated by closePickupRange (0 = OFF, byte-identical). */

export const CLOSE_PICKUP: Candidate = {
  id: 'closePickup',
  weight: ACTION_WEIGHTS.closePickup,
  evaluate: evalClosePickup,
}
