// candidates/LeadFire.ts — A10.5: AIM-FIRST lead fire (the human technique).
//
// The earlier A10 gate only fired along the CURRENT facing, which is the wrong
// half of the technique: a human picks the lane the enemy is about to cross,
// TURNS to face it, and fires so the shell arrives as the enemy crosses. The
// gates therefore only ever fired in the lane where "wait 1–2 ticks and it
// aligns by itself" was strictly better (mode 2 lost −4.14pp for exactly that
// reason).
//
// This candidate does the full three steps:
//   1. for each of the four lanes, ask the shared arrival predicate whether a
//      shell fired NOW would land (`leadHitForDir`), with `waitWindowTicks` so a
//      lane whose enemy aligns within the cooldown window is REJECTED — waiting
//      is a 100% hit and costs the same cooldown;
//   2. if the best lane is not the committed facing, turn toward it (the engine
//      turn cooldown is 12 ticks, so the intent must be set early);
//   3. once facing it, fire.
import type { Direction } from '../../../constants'
import { ALL_DIRS } from '../../../utils/direction'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { leadHitForDir } from '../lead-fire'

/** Ticks a lane must stay un-aligned before we commit to a lead shot. Longer
 * than the 12-tick turn cooldown: if the enemy aligns inside that window the
 * aligned shot (100%) is strictly better than the predicted one. */
const LEAD_WAIT_WINDOW = 14

export function evalLeadFire(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.leadFireMode < 5 || !self.hasBase) return false
  const { p } = ctx
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2

  let bestDir: Direction | null = null
  // Prefer the committed facing (a free shot, no turn needed).
  for (let i = 0; i < ALL_DIRS.length; i++) {
    const d = ALL_DIRS[i]
    if (leadHitForDir(self.world, d, pcx, pcy, { waitWindowTicks: LEAD_WAIT_WINDOW }) > 0) {
      bestDir = d
      if (d === p.dir) break
    }
  }
  if (bestDir === null) return false

  if (bestDir === p.dir) {
    // Facing the lane: take the shot, hold position so the muzzle stays put.
    self._moveDir = null
    self._fire = true
    recordBranch(self, 'leadFire')
    return true
  }
  // Turn toward the lane; the shot follows on a later tick once the committed
  // facing matches (A1: bullets leave along the COMMITTED facing).
  self._moveDir = bestDir
  self._fire = false
  recordBranch(self, 'leadFire')
  return true
}

/** leadFire(505) — above engage(500), below closePickup(540). */
export const LEAD_FIRE: Candidate = {
  id: 'leadFire',
  weight: ACTION_WEIGHTS.leadFire,
  evaluate: evalLeadFire,
}
