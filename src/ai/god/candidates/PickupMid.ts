// candidates/PickupMid.ts — the pickupMid candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { commitPowerupTail,
  baseRingBreachedImpl, isDualCentralBreachHoldP1 } from '../candidates/shared'

export function evalPickupMid(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, pcx, pcy } = ctx
  // §178: dual central-breach P1 — pure defender, never diverts to power-ups.
  if (isDualCentralBreachHoldP1(self)) return false
  // Per the §88 rule-4 chain, MID-tier pickups outrank 据守咽喉要地. The HIGH
  // tier (bomb/freeze/fence) was already checked before the aggressive
  // section. Only runs when chokepointMode > 0; otherwise the single §87
  // branch above handled all tiers (byte-identical).
  // §146 C: MID tier is deliberately NOT gated by fieldRetreatPickupGate —
  // extending the gate to MID/LOW was A/B-measured net negative on chaos
  // (§147, see retreatGateBlocksPickup scope note).
  if (self.params.chokepointMode > 0 && !self.aggressive && self.params.pickupPriorityMode > 0) {
    // §225-B: 危局拾取抑制 — ring 已被击穿时 MID tier（star/tank/shield）
    // 让位：救不了基地，去向防守（sentry/intercept）。HIGH tier 豁免
    // （bomb/freeze/fence = 危局有效道具）。
    if (self.params.baseAlertPickupSuppress > 0 && self.hasBase && baseRingBreachedImpl(w)) {
      return false
    }
    // §152-W3: commit-persistent lookup (see PICKUP_HIGH) — the W3
    // oscillation was driven by this branch (the decoy at (21,14) sat
    // exactly at the mid-range boundary).
    const midTarget = self.findUrgentPowerUpTargetWithCommit(pcx, pcy, 'midlow')
    if (midTarget) {
      // §186: Skip when pixel-stuck — the powerup is unreachable.
      const puStuck =
        self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
      if (!puStuck) {
        return commitPowerupTail(self, ctx, midTarget)
      }
    }
  }
  return false
}
