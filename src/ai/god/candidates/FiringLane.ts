// candidates/FiringLane.ts — the firingLane candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { ALL_DIRS } from '../../../utils/direction'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { scanAheadImpl } from '../FireControl'
import { findFiringLaneCellImpl } from '../candidates/shared'

import { manhattan } from '../../../utils/helpers'

export function evalFiringLane(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy } = ctx
  const prm = self.params
  if (prm.firingLaneMode <= 0 || self.aggressive) return false
  // Live enemies present?
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  let enemyCount = 0
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (t.alive && t.spawnTimer <= 0) enemyCount++
  }
  if (enemyCount === 0) return false
  // Dead-zone: no enemy LOS in any direction (memoized scans — 4 calls,
  // one per-tick memo origin).
  let hasLoS = false
  for (let di = 0; di < ALL_DIRS.length; di++) {
    if (scanAheadImpl(self, pcx, pcy, ALL_DIRS[di]).enemy) {
      hasLoS = true
      break
    }
  }
  if (hasLoS) return false
  // All enemies beyond min-dist — a close enemy is faster chased directly.
  const pc = self.playerCell()
  // D5 (plan §D5): the deadzone redirect is confined to the BASE BOX
  // (rows >= firingLaneBoxRow). §139 failed because the trigger ran across
  // the whole maze — no LOS with distant enemies is the normal maze state,
  // so the player churned between lookout cells instead of pressing.
  // Inside the base box the same state is a genuine deadzone: the player
  // MUST be able to shoot the base rush (Battlement: parked fireless at
  // (11,24) while the right wing breaches the ring). 0 = OFF (byte-identical
  // to §139 mode=0).
  if (prm.firingLaneBoxRow > 0 && pc.row < prm.firingLaneBoxRow) return false
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    const d = manhattan(tc.col, tc.row, pc.col, pc.row)
    if (d <= prm.firingLaneMinEnemyDist) return false
  }
  // Throttled lookout-cell search (cache survives the replan window).
  const now = w.frame
  const arrived =
    self._firingLaneCell !== null &&
    self._firingLaneCell.col === pc.col &&
    self._firingLaneCell.row === pc.row
  if (
    self._firingLaneCell === null ||
    now - self._firingLaneTick >= prm.firingLaneReplanTicks ||
    arrived
  ) {
    self._firingLaneCell = findFiringLaneCellImpl(self, pc)
    self._firingLaneTick = now
  }
  const target = self._firingLaneCell
  if (!target) return false
  if (target.col === pc.col && target.row === pc.row) return false // arrived; next replan re-picks
  self._moveDir = self.navigateTowards(target)
  if (!self._moveDir) {
    // A* failed — unstick toward any passable direction (P2.2-style).
    for (let di = 0; di < ALL_DIRS.length; di++) {
      if (self.canMoveDir(p, ALL_DIRS[di])) {
        self._moveDir = ALL_DIRS[di]
        break
      }
    }
  }
  if (!self._moveDir) return false
  self.branchCounts.firingLane++
  self._lastBranch = 'firingLane'
  return true
}
