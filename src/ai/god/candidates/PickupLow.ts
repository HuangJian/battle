// candidates/PickupLow.ts — the pickupLow candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { baseRingBreachedImpl, isDualCentralBreachHoldP1 } from '../candidates/shared'

import { manhattan } from '../../../utils/helpers'

export function evalPickupLow(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
  // §178: dual central-breach P1 — pure defender, never diverts to power-ups.
  if (isDualCentralBreachHoldP1(self)) return false
  // §146 C: LOW tier is deliberately NOT gated by fieldRetreatPickupGate —
  // extending the gate to MID/LOW was A/B-measured net negative on chaos
  // (§147, see retreatGateBlocksPickup scope note).
  // Check for power-ups when no enemy is in line of fire. Previously this
  // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
  // Now the AI opportunistically grabs power-ups when it's safe to divert.
  // P1: Skip power-ups when the base is under threat — defense first.
  // P3.2: Also skip when there are enemies within 5 cells of the player —
  // chasing power-ups while enemies are nearby was a major cause of
  // defense-collapse gameovers on S6/S26/S32.
  if ((!aimDir || onCooldown) && !(self.hasBase && self.isBaseUnderThreat())) {
    // §225-B: 危局拾取抑制 — ring 破时 LOW tier 机会拾取同样让位（HIGH tier
    // 目标已在 pickupHigh(800) 先行处理, 到这里只剩非 HIGH 道具）。
    if (self.params.baseAlertPickupSuppress > 0 && self.hasBase && baseRingBreachedImpl(w))
      return false
    // P3.2: Don't divert to power-ups when enemies are close.
    const pc2 = self.playerCell()
    let nearbyEnemy = false
    // Cluster C: reuse the per-tick enemy snapshot.
    const nearbyScan = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let ni = 0; ni < nearbyScan.length; ni++) {
      const t = nearbyScan[ni]
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = self.tankCell(t)
      if (manhattan(tc.col, tc.row, pc2.col, pc2.row) <= 5) {
        nearbyEnemy = true
        break
      }
    }
    if (!nearbyEnemy) {
      const puTarget = self.findPowerUpTarget(pcx, pcy)
      if (puTarget) {
        // §186: Skip when pixel-stuck — the powerup is unreachable.
        const puStuck =
          self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
        if (!puStuck) {
          self._moveDir = self.navigateTowards(puTarget)
          self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
          self.branchCounts.powerup++
          self._lastBranch = 'powerup'
          return true
        }
      }
    }
  }
  return false
}
