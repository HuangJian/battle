// candidates/UnifiedCandidates.ts — the unifiedCandidates candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { BASE_POS } from '../../../constants'
import { type Tank } from '../../../types'
import { type GodAIInput } from '../../GodAIInput'
import { clearLaneFireDir, evaluateUnifiedCandidates, fireRayBlocked } from '../ActionCandidates'
import { type DecisionContext } from '../DecisionCore'
import { shouldFireInDirImpl } from '../FireControl'

export function evalUnifiedCandidates(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.candidateMode <= 0 || !self.hasBase || self.aggressive) return false
  const { w, p, pcx, pcy, onCooldown } = ctx
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  // kill-current target: the last selectTarget pick while still alive,
  // else the nearest enemy (the mode-0 hunt rule proxy). No selectTarget
  // call from here — it would write intent state as a side effect.
  let hunt: Tank | null = null
  let nearest: Tank | null = null
  let nearestD = Infinity
  const pcxCell = self.playerCell()
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    if (t.id === self._lastSelectTargetId) hunt = t
    const tc = self.tankCell(t)
    const d = Math.abs(tc.col - pcxCell.col) + Math.abs(tc.row - pcxCell.row)
    if (d < nearestD) {
      nearestD = d
      nearest = t
    }
  }
  const anchorCol = BASE_POS.col
  const anchorRow = Math.max(2, BASE_POS.row - 1 - prm.defenseRowOffset)
  const v = evaluateUnifiedCandidates(
    w,
    p,
    list,
    hunt ?? nearest,
    anchorCol,
    anchorRow,
    self._candVerdict,
  )
  if (!v.kind) return false
  const roll = (): boolean => self.rng.next() >= prm.aimError
  if (v.kind === 'returnDefense') {
    self._moveDir = self.navigateTowards({ col: anchorCol, row: anchorRow })
    self._fire = false
    self.branchCounts.unifiedCandidates++
    self._lastBranch = 'candidateReturn'
    return true
  }
  // All fight candidates address one threat tank (verdict.threatId).
  let target: Tank | null = null
  for (let i = 0; i < list.length; i++) {
    if (list[i].id === v.threatId) {
      target = list[i]
      break
    }
  }
  if (!target || !target.alive) return false
  const tc = self.tankCell(target)
  const aligned = tc.col === pcxCell.col || tc.row === pcxCell.row
  const facing =
    p.dir ===
    (aligned
      ? tc.col === pcxCell.col
        ? tc.row > pcxCell.row
          ? 'down'
          : 'up'
        : tc.col > pcxCell.col
          ? 'right'
          : 'left'
      : p.dir)
  if (v.kind === 'clearLane') {
    const dir = clearLaneFireDir(w, p, target)
    if (!dir) return false
    self._moveDir = p.dir === dir ? null : dir
    self._fire = p.dir === dir && !onCooldown && shouldFireInDirImpl(self, pcx, pcy, dir)
    self.branchCounts.unifiedCandidates++
    self._lastBranch = 'candidateClear'
    return true
  }
  // killCurrent / interceptBase: standing hold only when the VERDICT came
  // from the standing assessment (standingShot — the M1 standing shot that
  // wins the deadline). Never re-derive standing from firstOutputTick ===
  // 0: an aligned approach has zero arrival cost yet may carry a blocked
  // ray. Approach commits always move; fire en route only when the CURRENT
  // ray is ring/base-clear (S30s27 — the commit-time verdict predates the
  // player's alignment).
  if (v.standingShot) {
    self._moveDir = null
    self._fire = v.fireClear && !onCooldown && roll()
  } else {
    self._moveDir = self.navigateTowards(tc)
    self._fire = aligned && facing && !fireRayBlocked(w, p, target) && !onCooldown && roll()
  }
  self.branchCounts.unifiedCandidates++
  self._lastBranch = v.kind === 'killCurrent' ? 'candidateKill' : 'candidateIntercept'
  return true
}
