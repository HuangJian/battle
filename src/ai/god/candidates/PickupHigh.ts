// candidates/PickupHigh.ts — the pickupHigh candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { CELL } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { isDualCentralBreachHoldP1, retreatGateBlocksPickup } from '../candidates/shared'

export function evalPickupHigh(self: GodAIInput, ctx: DecisionContext): boolean {
  const { p, pcx, pcy, onCooldown } = ctx
  // §180: Dual central breach — fence pickup by EITHER tank (previously
  // P2-only). Fence = steel walls for the base, the single most critical
  // powerup for preventing base destruction. When the fence spawns near
  // P1 while P2 is far away, P1 must grab it — the sticky-hold anchor is
  // less important than structural base defense. Runs BEFORE the P1
  // sticky-hold gate so P1 can pick up fence even in pure-defender mode.
  // The nearest tank to the fence wins (partner-dead / partner-far →
  // this tank takes it unconditionally). Gated by dualStrategyActive
  // (spectateDual || coop) && centralBreachRisk && dualCentralBreachP2FencePickup
  // — single-player is byte-identical (gate short-circuits).
  if (
    !self.aggressive &&
    self.dualStrategyActive &&
    self.params.dualCentralBreachP2FencePickup > 0
  ) {
    const fenceTarget = self.findDualFencePickup(pcx, pcy)
    if (fenceTarget) {
      const myCol = Math.floor(pcx / CELL)
      const myRow = Math.floor(pcy / CELL)
      const myDist = Math.abs(fenceTarget.col - myCol) + Math.abs(fenceTarget.row - myRow)
      let takeIt = true
      const partner = self.coopPartner()
      if (partner && partner.alive && partner.spawnTimer <= 0) {
        const pCol = Math.floor(partner.x / CELL)
        const pRow = Math.floor(partner.y / CELL)
        const partnerDist = Math.abs(fenceTarget.col - pCol) + Math.abs(fenceTarget.row - pRow)
        // Partner is significantly closer → let them handle it
        if (partnerDist < myDist - 2) takeIt = false
      }
      if (takeIt) {
        self._moveDir = self.navigateTowards(fenceTarget)
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.powerup++
        self._lastBranch = 'powerup'
        return true
      }
    }
  }
  // §178: dual central-breach P1 — pure defender, never diverts to power-ups
  // (P2 handles items). Sticky-hold so P1 keeps sniping the col-12 spawn
  // lane instead of wandering to top-row items while the base is dug out.
  // NOTE: fence is already handled above (§180) — the sticky hold now only
  // blocks non-fence powerups (star/tank/shield/bomb/freeze).
  if (isDualCentralBreachHoldP1(self)) return false
  // NORMAL mode only: during freeze the aggressive branch already grabs
  // power-ups when no enemy is aligned, and an aligned frozen enemy is a
  // free kill we must not interrupt. Gated by pickupPriorityMode.
  // §88 (chokepointMode>0): HIGH-tier outranks base defense and is checked
  // here; MID-tier (star/tank/shield) yields to base defense and is checked
  // after the aggressive section (see PICKUP_MID). When chokepointMode==0,
  // the original all-tiers-together order is kept (byte-identical to pre-§88).
  if (!self.aggressive) {
    // §146 C (extended): M13-condition gate — no pickup tier may hijack
    // the retreat. 0 = OFF (byte-identical).
    if (retreatGateBlocksPickup(self)) {
      return false
    }
    // E1 / 道具经济 (plan 反证判据): dire-state item pickup — when the base
    // is swarmed (enemies within direItemApproachCells + >= direItemMinEnemies)
    // or the ring is damaged (<= direItemRingLow), a nearby bomb/freeze/fence/
    // emp is worth a divert even with enemies nearby (the §87 gates block
    // under exactly this 4-enemy pressure). Runs before the normal §87 HIGH
    // tier, keeping the PICKUP_HIGH chain slot (weight 800 — above
    // engage/defenseIntercept, below dodge/interceptBase). 0 = OFF.
    const direTarget = self.params.direItemMode > 0 ? self.findDireItemTarget(pcx, pcy) : null
    if (direTarget) {
      self._moveDir = self.navigateTowards(direTarget)
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
      self.branchCounts.powerup++
      self._lastBranch = 'powerup'
      return true
    }
    if (self.params.pickupPriorityMode > 0) {
      // §152-W3: findUrgentPowerUpTargetWithCommit persists an active
      // pursuit across the transient dist>range flip (the W3 oscillation:
      // the item at the range boundary was abandoned the tick the player
      // stepped toward it). 0 = plain lookup (byte-identical).
      const urgentTarget =
        self.params.chokepointMode > 0
          ? self.findUrgentPowerUpTargetWithCommit(pcx, pcy, 'high')
          : self.findUrgentPowerUpTargetWithCommit(pcx, pcy)
      if (urgentTarget) {
        // §186: Skip when pixel-stuck — the powerup is unreachable.
        const puStuck =
          self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
        if (!puStuck) {
          self._moveDir = self.navigateTowards(urgentTarget)
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
