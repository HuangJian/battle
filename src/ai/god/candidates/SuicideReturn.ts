// candidates/SuicideReturn.ts — the suicideReturn candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { BASE_POS } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import {
  anyThreatPointEnemyImpl,
  controlledLives,
  findSuicideTargetImpl,
  hasLethalBulletWithinWindowImpl,
} from '../SuicideReturn'
import { findBulletThreatToBaseImpl } from '../ThreatAssessor'

import { manhattan } from '../../../utils/helpers'

export function evalSuicideReturn(self: GodAIInput, ctx: DecisionContext): boolean {
  const mode = self.params.suicideReturnMode
  if (mode <= 0) return false
  const { w, p, pcx, pcy, shielded, onCooldown } = ctx
  const window = self.params.suicideReturnBulletTimeTicks

  // ---- Mid-trade: continue the suicide maneuver ----
  if (self._suicideStanding) {
    if (mode === 1) {
      // §116: keep standing ONLY while a lethal bullet is still approaching
      // within the window. Once the bullet is cancelled (no lethal bullet
      // left) or the player is shielded (post-respawn), clear the standing
      // state and resume normal play. Prevents the pathological per-tick
      // re-commit that froze the player standing forever (hard S30: 14/run).
      if (shielded || !hasLethalBulletWithinWindowImpl(w, p, pcx, pcy, window)) {
        self._suicideStanding = false
        return false
      }
      self._moveDir = null
      self._fire = false
      self.branchCounts.suicideReturn++
      self._lastBranch = 'suicideReturn'
      return true
    }

    // Modes 2/3 (condition-① trigger): exit the trade when the player is
    // shielded (post-respawn — the trade is over, it got its death) or the
    // base threat is gone (no bullet flying at the base, or no enemy at a
    // threat point anymore). Weak re-check only (①): a charge/stand is NOT
    // aborted just because the player has closed the distance.
    if (shielded || !findBulletThreatToBaseImpl(self)) {
      self._suicideStanding = false
      self._suicideStandTicks = 0
      return false
    }
    const target = anyThreatPointEnemyImpl(self)
    if (!target) {
      self._suicideStanding = false
      self._suicideStandTicks = 0
      return false
    }
    if (mode === 2) {
      // Stand still and wait to be killed — with a timeout so a player that
      // no enemy deigns to shoot resumes normal play instead of freezing
      // (the §116 S30 standing-freeze pathology, moved to the
      // healthy-player case). The abort arms a re-commit suppress: all
      // preconditions may still hold, so without it the candidate would
      // instantly re-commit and re-freeze the player (same trap as the
      // §116 standing-freeze, found by the mode-2 timeout unit test).
      self._suicideStandTicks++
      if (self._suicideStandTicks > self.params.suicideReturnStandMaxTicks) {
        self._suicideStanding = false
        self._suicideStandTicks = 0
        self._suicideStandSuppress = self.params.suicideReturnStandMaxTicks
        return false
      }
      self._moveDir = null
      self._fire = false
    } else {
      // Mode 3: charge the threat enemy at full speed, firing normally (the
      // §74/§70 base-wall guards inside shouldFireInDir keep it from
      // blowing up its own base). No dodging while the trade is active.
      const tc = self.tankCell(target)
      self._moveDir = self.navigateTowards(tc)
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    }
    self.branchCounts.suicideReturn++
    self._lastBranch = 'suicideReturn'
    return true
  }

  // ---- Not committed — check the preconditions ----
  // Post-timeout suppress (mode 2 only): after a STAND trade aborted via
  // the timeout, normal play resumes for a while instead of an instant
  // re-commit — standing longer cannot help if no enemy shot the player
  // during the whole previous window. Mode-gated: mode 1 never arms it
  // and must not inherit an unrelated cooldown.
  if (mode >= 2 && self._suicideStandSuppress > 0) return false

  // Condition 3: player has spare lives (库存命数 > 0).
  if (controlledLives(self) < self.params.suicideReturnMinLives) return false

  // Don't suicide during respawn shield — the player is invulnerable.
  if (shielded) return false

  // Condition 5 (mode 1 only): a lethal bullet will hit within the window
  // (the task: 可能多发 — scan ALL enemy bullets, not just the nearest; the
  // nearest may not be the lethal one). The bullet-bullet cancellation
  // caveat is handled by the standing mid-state above.
  if (mode === 1 && !hasLethalBulletWithinWindowImpl(w, p, pcx, pcy, window)) return false

  // GATE (root-cause fix, S23 seed-14 regression): the base must be under an
  // ACTIVE enemy-bullet threat — not merely an enemy sitting at a threat
  // point. A threat-point enemy alone is NOT proof the base is doomed: the
  // God AI's base walls + T8 interception usually handle it, so suiciding
  // then wastes a life (OFF arm dodged+survived+wrote, ON arm lost a life
  // for nothing). Only when a bullet is actually flying at the base does the
  // suicide-respawn-to-intercept trade become a genuine win.
  if (!findBulletThreatToBaseImpl(self)) return false

  // §118 strict-doom guard (modes 2/3 only): the §117 flip-loss root cause
  // was committing while the base was at FULL HP with the normal defense
  // still running — a single in-flight bullet is NOT proof the base will
  // fall (hard S35 seed-8: base 120/120, the OFF arm returned and cleared;
  // the ON arm abandoned the defense and the base fell). The trade now only
  // fires when the base is genuinely doomed AND the player cannot defend it:
  //   (a) suicideReturnBaseHpFrac > 0: baseHp must be at/below that fraction
  //       of baseMaxHp — a hit or two from falling;
  //   (b) suicideReturnDefendDistCells > 0: the player must be farther than
  //       that from the base — out of position, cannot return in time to
  //       intercept (killer-bullet travel to base ≈ 14-15 ticks ≈ 0.24s).
  // Both gated on param > 0 (default 0 ⇒ byte-identical to §117) and
  // mode >= 2 (mode 1 §116 keeps its own lethal-bullet evidence).
  if (mode >= 2 && self.params.suicideReturnBaseHpFrac > 0) {
    if (w.baseHp > self.params.suicideReturnBaseHpFrac * w.baseMaxHp) return false
  }
  if (mode >= 2 && self.params.suicideReturnDefendDistCells > 0) {
    const pcStrict = self.playerCell()
    const distBase = manhattan(pcStrict.col, pcStrict.row, BASE_POS.col, BASE_POS.row)
    if (distBase <= self.params.suicideReturnDefendDistCells) return false
  }

  // Conditions 1+2+4: a threat-point enemy the spawn can deal with, and the
  // player is too far to reach it in time (>5s at full speed).
  const target = findSuicideTargetImpl(self, pcx, pcy)
  if (!target) return false

  // All conditions met: begin the suicide trade. Modes 1/2 stand still to
  // embrace death (the player will respawn at the spawn point with a 3s
  // shield); mode 3 immediately charges the threat enemy instead.
  self._suicideStanding = true
  self._suicideStandTicks = 0
  if (mode === 3) {
    const tc = self.tankCell(target)
    self._moveDir = self.navigateTowards(tc)
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
  } else {
    self._moveDir = null
    self._fire = false
  }
  self.branchCounts.suicideReturn++
  self._lastBranch = 'suicideReturn'
  return true
}


// ===========================================================================
// Candidates — verbatim branch transcriptions. One object per action; the
// shell evaluates them strictly in weight order (chain order), first commit
// wins. Each evaluate() returns true exactly when the original branch would
// have `return`ed from the top-level chain.
// ===========================================================================

/** suicideReturn(1100) — 自杀秒回: embrace death to respawn at the spawn point
 * closer to a base-threatening enemy the player was too far to reach.
 * Suppresses dodging when the preconditions are met (see SuicideReturn.ts).
 *
 * Modes (suicideReturnMode):
 *   0 = OFF (byte-identical to pre-§116).
 *   1 = §116 original: trigger on condition ⑤ — a LETHAL bullet hits within
 *       1s (the player is about to die anyway, so the life-trade is nearly
 *       free). The player stands still and takes that bullet.
 *   2 = §117 condition-① variant, STAND: trigger when an enemy is at a threat
 *       point while a bullet is actively flying at the base (no lethal-bullet
 *       requirement); the player stands still waiting to be killed, with a
 *       suicideReturnStandMaxTicks timeout — if no death comes it resumes.
 *   3 = §117 condition-① variant, CHARGE: same trigger, but the player
 *       actively drives at the threat enemy (no dodging — this candidate
 *       outranks dodge) to die fast and respawn near the base, or to kill the
 *       enemy first; whichever happens first ends the trade.
 * All modes share the base-bullet GATE (S23 seed-14 fix). */

export const SUICIDE_RETURN: Candidate = {
  id: 'suicideReturn',
  weight: ACTION_WEIGHTS.suicideReturn,
  evaluate: evalSuicideReturn,
}
