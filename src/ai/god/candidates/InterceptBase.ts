// candidates/InterceptBase.ts — the interceptBase candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { findBulletThreatToBaseImpl } from '../ThreatAssessor'
import { baseBulletInterceptCellImpl } from '../ThreatAssessor'

export function evalInterceptBase(self: GodAIInput, ctx: DecisionContext): boolean {
  const { p, pcx, pcy, onCooldown } = ctx
  // Check AFTER dodge (survive first) but BEFORE aggressive/T2a.
  // Skip only when enemies are frozen (aggressive hunt — no bullets to
  // intercept). When shielded, the player can still intercept bullets
  // headed for the base — the shield protects the player, not the base.
  // Gap B (plan §3): skip entirely when the stage has no base.
  if (!self.aggressive && self.hasBase) {
    const baseThreat = findBulletThreatToBaseImpl(self)
    if (baseThreat) {
      const interceptCell = baseBulletInterceptCellImpl(self, baseThreat)
      if (interceptCell) {
        self._moveDir = self.navigateTowards(interceptCell)
        // Fire to intercept the bullet (T5 extended to base defense).
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.t8++
        self._lastBranch = 't8'
        return true
      }
    }
  }
  return false
}
