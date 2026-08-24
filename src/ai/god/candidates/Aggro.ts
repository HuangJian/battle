// candidates/Aggro.ts — the aggro candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { CELL } from '../../../constants'
import { ALL_DIRS } from '../../../utils/direction'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { aimSurvivesTurnImpl, bulletPathSteelBlockedImpl, scanAheadImpl, shouldFireBreakThroughImpl } from '../FireControl'
import { MAP_CENTER, isDualCentralBreachHoldP1, selfFireBaseGuardBlocks } from '../candidates/shared'
import { updateStuckTrack } from '../stuck-track'
import { STEEL_PIERCE_PLAYER_LEVEL } from '../../../config/combat'
import { findFreezePickupTargetImpl } from '../StrategyPlanner'

export function evalAggro(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
  if (self.aggressive) {
    // §156: freeze-window close-range power-up pickup — BEFORE stop-and-aim.
    // During freeze, enemies can't move or fire. A power-up within
    // freezePickupRange cells should be grabbed first; the frozen enemy
    // will still be there 2-3 ticks later. DODGE (weight 1000 > 700) already
    // handled any in-flight bullet threat before we reach here.
    // Only during freeze (not shield): shield makes aggressive=true too, but
    // enemies are NOT frozen during shield.
    // §178: dual central-breach P1 — even in freeze, never divert to items;
    // it holds the center and fires (P2 handles freeze pickups).
    if (
      w.freezeTimer > 0 &&
      self.params.freezePickupRange > 0 &&
      !isDualCentralBreachHoldP1(self)
    ) {
      const freezeTarget = findFreezePickupTargetImpl(self, pcx, pcy)
      if (freezeTarget) {
        self._moveDir = self.navigateTowards(freezeTarget)
        // §185: When navigateTowards returns null (no path to the pickup),
        // fall through to aggressive branch instead of returning true with
        // move=null — the player would be stuck indefinitely in the powerup
        // branch with no escape (HUNT/nav-stuck never runs). Root cause:
        // S20@seed27 stuck 22.8s in powerup branch, move=null, pathLen=3,
        // gameover. The §184 _digBlockTicks gate only fires after 1.5s, but
        // between triggers the branch re-enters and returns true.
        if (self._moveDir) {
          // §184: When the player has been physically stuck for >= 1.5s
          // during freeze pickup, fall through to AGGRO's stop-and-aim /
          // navigate sub-branches to kill the blocking enemy first.
          // The freeze pickup will resume next tick once the enemy is dead
          // or the path opens. Without this, the player navigates toward
          // the powerup but can't actually move (blocked by frozen enemy),
          // and fires uselessly for the entire freeze window (S31@seed14:
          // 19.6s stuck, 0 fire ticks). The _digBlockTicks gate ensures
          // this only triggers on TRUE immobility, not brief pauses.
          if (
            self._enemies.length > 0 &&
            self.params.navBreakStuck > 0 &&
            self._digBlockTicks >= self.params.carveDigBlockTicks
          ) {
            // Don't commit — let stop-and-aim / navigate handle the enemy
          } else {
            self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir)
            self.branchCounts.powerup++
            self._lastBranch = 'powerup'
            return true
          }
        }
      }
    }
    // Skip defense, go straight for the nearest enemy or power-up.
    //
    // §80 (ordering constraint REMOVED in §3.2): `aimSurvivesTurnImpl` used
    // to share `_scanResults[dirIdx]` with the scan below, forcing it to be
    // evaluated FIRST. It now writes to its own dedicated buffer
    // (`self._turnSnapScan`), so evaluation order no longer matters — the
    // guard stays inside the same `&&` for short-circuit efficiency only.
    // When the guard rejects the aim (the turn's grid-snap would shove the
    // tank off the firing line) we fall through to the navigate path, which
    // has real stall detection — this is what breaks the period-2
    // freeze-window deadlock.
    // §186: When pixel-stuck for >= powerupStuckTicks, skip T2a stop-and-
    // aim — the player has been firing without moving or killing for too
    // long. Fall through to nav-stuck escape, which increments every tick
    // (instead of only during camp-suppress) and triggers faster.
    // Root cause: S19@seed37 18.6s, S31@seed71 18.0s, S33@seed83 17.5s —
    // player camps in T2a firing at far enemies (15 cells) with 0 kills.
    const t2aSkipStuck =
      self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
    if (
      aimDir &&
      self._aggCampTrack.suppress <= 0 &&
      !t2aSkipStuck &&
      aimSurvivesTurnImpl(self, p, aimDir)
    ) {
      // T2a: stop-and-aim — check if enemy is visible (no steel blocking).
      // Inline scanAheadImpl directly (perf §66): the thin scanAhead
      // wrapper adds ~14ms (2.8%) of function-call overhead across 30 games.
      const aggScan = scanAheadImpl(self, pcx, pcy, aimDir)
      // §121: aggressive stop-and-aim self-fire base guard (default OFF,
      // selfFireBaseGuard=0 → byte-identical). The scan's ±8px offset lines
      // can be screened by an enemy off the bullet's 6px center path — the
      // §120 enemy-screen self-kill. Suppress the fire when the bullet's
      // actual center line reaches the base (mode 1 strict; mode 2 lenient
      // keeps it when an enemy body truly overlaps the corridor).
      const aggFireBlocked = selfFireBaseGuardBlocks(self, pcx, pcy, aimDir)
      if (aggFireBlocked) self._selfFireGuardBlocks++
      // §74: Don't fire when a base-protection wall is on the other offset
      // line, or is closer than (or at the same distance as) the enemy — the
      // 6px bullet spans both offset columns and would hit the wall first.
      // §152-W1: also don't fire when the bullet's ACTUAL 6px path hits
      // non-ring steel before the enemy (the scan's offset lines can see the
      // enemy while the center-line bullet clips a steel column edge — hard
      // S12 seed 934391936 W1). Precise center-line walk, NOT the scan-steel
      // gate (which over-suppresses the §74 dual-offset case).
      const steelPathBlocked152 =
        self.params.t2aSteelPathBlock > 0 &&
        bulletPathSteelBlockedImpl(self, pcx, pcy, aimDir, aggScan.enemyDist * CELL)
      if (
        aggScan.enemy &&
        !aggFireBlocked &&
        !steelPathBlocked152 &&
        !(aggScan.baseWall && aggScan.baseWallDist <= aggScan.enemyDist) &&
        !(aggScan.baseSteel && (p.level ?? 0) >= STEEL_PIERCE_PLAYER_LEVEL)
      ) {
        // §84: Aggressive stall detection — the aggressive branch has NO
        // anti-stall guard (unlike T2a's _campTicks and navigate's
        // _navStuckTicks). Without this, the player can sit at one cell
        // firing at an enemy whose body is slightly offset from the bullet
        // path for the ENTIRE freeze window. When camping exceeds
        // aggCampTimeoutTicks with no kills, fall through to navigate.
        if (self.params.aggCampTimeoutTicks > 0) {
          const pc84 = self.playerCell()
          // §3.4: shared zone-stuck tracker (god/stuck-track.ts).
          const campTimedOut = updateStuckTrack(
            self._aggCampTrack,
            w,
            pc84,
            self.params.aggCampTimeoutTicks,
            1,
          )
          if (campTimedOut) {
            // Camped too long with no kills — suppress aggressive
            // stop-and-aim for a while and fall through to navigate.
            const st = self._aggCampTrack
            st.cell = null
            st.ticks = 0
            st.suppress = self.params.antiCampSuppressTicks
            // Fall through to power-up / navigate below.
          } else {
            if (p.dir === aimDir) {
              self._moveDir = null
            } else {
              self._moveDir = aimDir
            }
            self._fire = !onCooldown && self.rng.next() >= self.params.aimError
            self._lastBranch = 'aggressive'
            return true
          }
        } else {
          if (p.dir === aimDir) {
            self._moveDir = null
          } else {
            self._moveDir = aimDir
          }
          self._fire = !onCooldown && self.rng.next() >= self.params.aimError
          self._lastBranch = 'aggressive'
          return true
        }
      }
      // Enemy behind obstacle — fall through to navigate toward it.
    }
    // No enemy in row/col — check for power-up (S5).
    const puTarget = self.findPowerUpTarget(pcx, pcy)
    if (puTarget) {
      // §186: Skip powerup when pixel-stuck — the A* path to the
      // powerup is blocked/unreachable, and returning true here blocks
      // the nav-stuck escape below (evalAggro 的 nav-stuck 阶梯段).
      // Root cause: S20@seed27 22.9s stuck cycling camp→suppress→
      // powerup-stuck→camp; S35@seed52 19.1s stuck in powerup during
      // freeze; S33@seed35 16.1s; S25@seed6 18.1s; S9@seed69 18.9s.
      const puStuck =
        self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
      if (!puStuck) {
        self._moveDir = self.navigateTowards(puTarget)
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self._lastBranch = 'aggressive'
        return true
      }
    }
    // §152-W2: aggressive MOVEMENT stuck guard — the freeze window burns
    // entirely if the A* path ping-pongs between two adjacent cells (path
    // first step blocked by a frozen enemy's body / water → followPath's
    // fallback moves back to the previous cell → the replan replays the
    // same dead path). Zone-based (±1 cell, same as the T2a camp zone): a
    // kill resets the counter. After aggNavStuckTicks without progress, a
    // navigate-to-center escape runs for the antiCampSuppressTicks window
    // (A* routes around the blocking tank/water — in a dead-end corridor
    // the only open direction leads OUT).
    if (self.params.aggNavStuckTicks > 0) {
      // §3.4: shared zone-stuck tracker (god/stuck-track.ts).
      let escape152 = self._aggNavTrack.suppress > 0
      if (!escape152) {
        const pc152 = self.playerCell()
        if (
          updateStuckTrack(self._aggNavTrack, w, pc152, self.params.aggNavStuckTicks, 1)
        ) {
          const st = self._aggNavTrack
          st.cell = null
          st.ticks = 0
          st.suppress = self.params.antiCampSuppressTicks
          escape152 = true
        }
      }
      if (escape152) {
        if (self._aggNavTrack.suppress > 0) self._aggNavTrack.suppress--
        self._moveDir = self.navigateTowards(MAP_CENTER)
        if (!self._moveDir) {
          for (let di = 0; di < ALL_DIRS.length; di++) {
            const d = ALL_DIRS[di]
            if (self.canMoveDir(p, d)) {
              self._moveDir = d
              break
            }
          }
        }
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.aggressive++
        self._lastBranch = 'aggressive'
        return true
      }
    }
    // Navigate to nearest enemy.
    self._moveDir = self.followPath()
    if (!self._moveDir) self._moveDir = self.directMove(self.playerCell())
    // Proactive fire — but ALWAYS check shouldFireInDir to avoid shooting
    // the player's own base (T6). In classic instant combat the base has
    // 1 HP, so a single self-inflicted bullet destroys it.
    if (self._moveDir && !self.canMoveDir(p, self._moveDir)) {
      // §70/§74: break-through fire — never fire through base brick/steel
      // (§70) or at steel the player can't pierce (§74). Both guards live
      // in shouldFireBreakThroughImpl, which also drops the old `bs.enemy ||
      // ...` short-circuit that fired through the base wall on dual-offset
      // scans (DECISIONS §75 / commit 54600f9 — 4 S32 player suicides).
      const bs = scanAheadImpl(self, pcx, pcy, self._moveDir)
      const lvl = p.level ?? 0
      if (shouldFireBreakThroughImpl(bs, lvl, self.params.steelFireGate)) {
        self._fire = !onCooldown
      }
    } else {
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    }
    self.branchCounts.aggressive++
    self._lastBranch = 'aggressive'
    return true
  }

  // §84: Reset aggressive camp tracking when not in aggressive mode.
  const st = self._aggCampTrack
  if (st.cell) {
    st.cell = null
    st.ticks = 0
  }
  if (st.suppress > 0) st.suppress = 0
  return false
}
