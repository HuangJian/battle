// candidates/Hunt.ts — the hunt candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BASE_POS, CELL } from '../../../constants'
import { ALL_DIRS } from '../../../utils/direction'
import { type Cell } from '../../../utils/grid-search'
import { type GodAIInput } from '../../GodAIInput'
import { travelFireDetourDir } from '../ActionCandidates'
import { type DecisionContext } from '../DecisionCore'
import { survivalPressure } from '../EnemyModel'
import { scanAheadImpl, shouldFireBreakThroughImpl } from '../FireControl'
import { iceGlideAdjust } from '../Navigator'
import { carveFireAheadImpl, carvePathInfoCached, findCarveEscapeImpl } from '../PathCarve'
import { enemyCanBreachRing, enemyCanShootBase } from '../SmartThreatModel'
import {
  bulletLaneClearImpl,
  findCloseEnemyImpl,
  playerFasterThanImpl,
  safePerpDodgeImpl,
} from '../ThreatAssessor'
import { MAP_CENTER, isDualCentralBreachHoldP1 } from '../candidates/shared'

import { manhattan } from '../../../utils/helpers'

export function evalHunt(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, shielded } = ctx
  // Far from target (>5 cells): A* pathfinding routes around walls via
  // corridors — essential for maze stages. A* finds the corridor, not the
  // direct path through walls.
  //
  // Close to target (≤5 cells): directMove chases the moving enemy
  // directly, adjusting every tick.
  //
  // P0.3: Navigate stuck escape — if the player has been at the same cell
  // in the navigate branch for too long (pursuit loop with a faster enemy),
  // override the target to the map center.
  const pc = self.playerCell()
  // §217 (open-test round 2): travel-phase fire-line detour — an aligned,
  // ray-clear, off-cooldown killable target (csb/cbr/base band) one turn
  // away beats continuing the nav plan: turn + fire this tick (cost: one
  // turn window, killSlack > 13 guarantees the kill wins the deadline).
  // Inside HUNT → dodge/interceptBase/aggro/pickup all evaluate above and
  // preempt it; pure geometry (no RNG perturbation), S30s27-safe (corridor
  // + fireRayBlocked). Mode 0 = OFF (byte-identical).
  if (self.params.fireLineDetourMode > 0 && !onCooldown) {
    const detourList = self._enemies.length > 0 ? self._enemies : w.tanks
    const detourDir = travelFireDetourDir(
      w,
      p,
      pc,
      detourList,
      self._lastSelectTargetId,
      (t) => {
        if (enemyCanShootBase(self, t) || enemyCanBreachRing(self, t)) return true
        // Scalar center-cell math (§14.1 — no per-tick object allocation in
        // the M5 callback; same center-floor semantics as tankCenterCell).
        const tcCol = Math.floor((t.x + t.w / 2) / CELL)
        const tcRow = Math.floor((t.y + t.h / 2) / CELL)
        return tcRow >= BASE_POS.row - 4 && Math.abs(tcCol - BASE_POS.col) <= 6
      },
      self.params.fireLineDetourMinSlack,
    )
    if (detourDir) {
      self._moveDir = detourDir
      self._fire = self.rng.next() >= self.params.aimError
      self.branchCounts.navigate++
      self._lastBranch = 'navigate'
      return true
    }
  }
  // §162: carve-dig START — the player is pixel-blocked (endFrame stuck
  // detector: moved < carveDigBlockThreshold px for carveDigBlockTicks
  // ticks, i.e. wall-blocked / sealed-pocket oscillation). The cell-level
  // navStuck counter can NOT detect this: playerCell() is the tank CENTER
  // and a pocket bounce of 128↔136px flips it 8↔9, resetting the counter
  // every few ticks. Runs whenever HUNT evaluates; only starts when a
  // NON-corridor carve-safe dig path to an escape target exists.
  const digStartStuck =
    self.params.navBreakStuck > 0 &&
    !self._carveDigActive &&
    self._digBlockTicks >= self.params.carveDigBlockTicks
  if (digStartStuck) {
    const escape = findCarveEscapeImpl(self, pc)
    if (escape) {
      const info = carvePathInfoCached(self, pc, escape)
      if (info.path && info.path.length > 0 && !info.corridor) {
        self._carveDigActive = true
        self._carveDigTicks = 0
        self._carveDigTarget = escape
        self._moveDir = info.path[0]
        self._fire = !onCooldown && carveFireAheadImpl(self, pcx, pcy, info.path[0])
        self.branchCounts.navigate++
        self._lastBranch = 'navigate'
        return true
      }
    }
  }
  // §162: active carve-dig session — persist across navStuck resets (a
  // fresh cell clears _navStuckTicks, which would otherwise kill a
  // multi-cell dig). Follow the exact-ring-safe carve path toward the
  // escape target until the pocket is exited (corridor opens / path
  // empties) or the session times out.
  if (self._carveDigActive && self.params.navBreakStuck > 0) {
    self._carveDigTicks++
    const target = self._carveDigTarget
    const info = target ? carvePathInfoCached(self, pc, target) : null
    const dig = info && info.path
    const done =
      !dig ||
      dig.length === 0 ||
      (info !== null && info.corridor) ||
      self._carveDigTicks > self.params.carveDigMaxTicks
    if (done) {
      // Dig complete (smooth route now open) / unreachable / timed out —
      // fall through to normal HUNT.
      self._carveDigActive = false
      self._carveDigTicks = 0
      self._carveDigTarget = null
      // §182: Reset pixel-stuck counter to prevent immediate carve-dig
      // re-start, giving the §182 face-enemy fallback a 90-tick window.
      self._digBlockTicks = 0
    } else {
      const d = dig[0]
      self._moveDir = d
      if (self.canMoveDir(p, d)) {
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, d)
      } else if (carveFireAheadImpl(self, pcx, pcy, d)) {
        // Wall ahead is carve-safe (exact ring R5/R6 re-verified) — fire
        // to break it. Bypasses shouldFireBreakThrough's wide-box gate.
        self._fire = !onCooldown
      } else {
        // Path step became unbreakable (terrain changed) — abandon the
        // dig and fall through to normal HUNT.
        self._carveDigActive = false
        self._carveDigTicks = 0
        self._carveDigTarget = null
        self._moveDir = null
        // §182: Reset pixel-stuck counter to prevent immediate carve-dig
        // re-start, giving the §182 face-enemy fallback a 90-tick window.
        self._digBlockTicks = 0
      }
      if (self._carveDigActive) {
        self.branchCounts.navigate++
        self._lastBranch = 'navigate'
        return true
      }
    }
  }
  // §168: navStuckZone — exact-cell comparison is defeated by sub-pixel
  // jitter: playerCell() is the tank CENTER and a 1px bounce across a
  // cell boundary flips it (e.g. S34 s8: y 87.02↔88.10 → center (4,5)↔
  // (4,6) every ~6 ticks), resetting the counter before it can ever
  // reach navStuckTicks. The ±1 zone check is the §152 aggNavStuckTicks
  // pattern — jitter stays inside the zone, real movement leaves it.
  const zone168 = self.params.navStuckZone > 0
  if (
    self._navStuckCell &&
    (zone168
      ? Math.abs(self._navStuckCell.col - pc.col) <= 1 &&
        Math.abs(self._navStuckCell.row - pc.row) <= 1
      : self._navStuckCell.col === pc.col && self._navStuckCell.row === pc.row)
  ) {
    self._navStuckTicks++
  } else {
    self._navStuckCell = { col: pc.col, row: pc.row }
    self._navStuckTicks = 1
  }

  // Reset stuck timer when a kill happens (player is making progress).
  if (self._navStuckTicks > 1 && w.killCount !== self._campKillsAtStart) {
    self._navStuckTicks = 1
    self._campKillsAtStart = w.killCount
  }

  let navStuck = self._navStuckTicks > self.params.navStuckTicks
  // §168: escape suppression window — triggering the escape once is not
  // enough: leaving the zone resets the counter, and the still-oscillating
  // target selection pulls the player straight back into the same spot
  // (S34 s8: escaped 3 cells up at t748, back in the pin by t800). After
  // a trigger, keep escaping for navStuckSuppressTicks HUNT evaluations
  // (the §152 window pattern) so the player actually clears the region.
  if (
    navStuck &&
    self.params.navStuckZone > 0 &&
    self.params.navStuckSuppressTicks > 0 &&
    self._navStuckSuppress <= 0
  ) {
    self._navStuckSuppress = self.params.navStuckSuppressTicks
    self._navStuckCell = null
    self._navStuckTicks = 0
  } else if (self._navStuckSuppress > 0) {
    self._navStuckSuppress--
    navStuck = true
  }

  // §161: When CARVE_PATH is enabled and the player is in the carve zone
  // (lower half), defer to CARVE_PATH — the center escape would pull the
  // player out of the pocket before CARVE_PATH can engage.
  if (navStuck && self.params.carvePathMode > 0 && pc.row >= self.params.carveLowerRow) {
    navStuck = false
    self._navStuckSuppress = 0
  }

  // §190: pixel-stuck fallback — when the player has been pixel-stuck for
  // >= pixelStuckDirectMoveTicks and no carve-dig is active, bypass A*
  // pathfinding and use directMove. With replanInterval=1 (default on hard),
  // A* recomputes every tick and target movement invalidates the replan
  // cache — the first step oscillates between directions, and the turn
  // cooldown creates a back-and-forth with zero net progress. directMove
  // picks a stable direction based on the target's relative position,
  // breaking the oscillation cycle.
  // Root cause: S35@seed10 (30.6s stuck at (1,25)), S2@seed13 (28s),
  // S17@seed12 (30.9s), S31@seed9 (11.6s).
  // GATED OFF BY DEFAULT (pixelStuckDirectMoveTicks: 0) since 2026-08-13:
  // paired A/B on --difficulty hard proved it is net-negative (suite
  // 0.5308 ON → 0.5363 OFF, p=0.0185) and failed to help its own target
  // seeds. Set the param > 0 to re-enable; threshold should sit above the
  // nav-stuck escape (180 ticks = 3s) and below the 10s alert threshold.
  if (
    self.params.pixelStuckDirectMoveTicks > 0 &&
    !self._carveDigActive &&
    self._digBlockTicks >= self.params.pixelStuckDirectMoveTicks
  ) {
    self._moveDir = self.directMove(pc)
    if (!self._moveDir) {
      // directMove failed — try any passable direction to get moving.
      for (let di = 0; di < ALL_DIRS.length; di++) {
        if (self.canMoveDir(p, ALL_DIRS[di])) {
          self._moveDir = ALL_DIRS[di]
          break
        }
      }
    }
    // Break-through fire if the chosen direction is blocked by terrain.
    if (self._moveDir && !self.canMoveDir(p, self._moveDir)) {
      const bs = scanAheadImpl(self, pcx, pcy, self._moveDir)
      const lvl = p.level ?? 0
      if (shouldFireBreakThroughImpl(bs, lvl, self.params.steelFireGate)) {
        self._fire = !onCooldown
      }
    } else {
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    }
    self.branchCounts.navigate++
    self._lastBranch = 'navigate'
    return true
  }

  let navTarget: Cell | null
  // P3.1: When nav-stuck triggers, only go to center if the player is
  // NOT already at/near center (target == current cell → deadlock, the S9
  // root cause). When already at center, chase the nearest enemy directly.
  const distToCenter = manhattan(pc.col, pc.row, MAP_CENTER.col, MAP_CENTER.row)
  const stuckAtCenter = distToCenter <= 2
  // M3 (survivalRiskWeight, P0-3 命数盲 fix): on the last lives (survival
  // pressure active), the HUNT candidate retreats to the defense position
  // instead of deep-hunting — the AI stops chasing far enemies it cannot
  // afford to die for. The defense position is the default defensive hold
  // (getDefaultDefensePosition: base column, defenseRowOffset above base).
  // Gated: only when the risk weight is > 0 AND the player is far from the
  // base (close to base, normal hunt/defense interplay is fine). 0 at
  // default ⇒ byte-identical to pre-M3.
  const survivalRetreat =
    self.params.survivalRiskWeight > 0 &&
    survivalPressure(self) > 0 &&
    manhattan(pc.col, pc.row, BASE_POS.col, BASE_POS.row) > self.params.baseRaceRangeCells
  if (navStuck && !stuckAtCenter) {
    // §179 (autopsy seed6 失误 B/C): when baseHp is critically low, the
    // navStuck escape must go to the DEFENSE POSITION, not map center.
    // The autopsy showed both tanks stuck at (18,6)/(21,6) for 18 seconds
    // while the base dropped 48→12→0 — the center escape pulled them to
    // (12,12) but target jitter sent them right back to the top-right.
    // Escaping to base defense breaks the oscillation cycle.
    if (
      self.params.emergencyBaseHpFrac > 0 &&
      self.hasBase &&
      w.spectateDual &&
      w.baseHp <= self.params.emergencyBaseHpFrac * w.baseMaxHp
    ) {
      navTarget = self.getDefaultDefensePosition()
    } else {
      navTarget = MAP_CENTER
    }
  } else if (survivalRetreat && self.hasBase) {
    navTarget = self.getDefaultDefensePosition()
  } else {
    navTarget = self.selectTarget(pc)
  }

  const navDist = navTarget ? manhattan(navTarget.col, navTarget.row, pc.col, pc.row) : Infinity

  if (navStuck && !stuckAtCenter) {
    // P2.2: Stuck too long — break the loop. Try A* to center first, then
    // fall back to any passable direction (not directMove, which would
    // re-select the enemy target and re-enter the stuck loop).
    self._moveDir = self.navigateTowards(navTarget!)
    if (!self._moveDir) {
      // A* failed (walled off) — try directions toward center first,
      // then any passable direction.
      const dx = navTarget!.col - pc.col
      const dy = navTarget!.row - pc.row
      // §233 (perf): the 2-element pref array was allocated per call (rare
      // navStuck path) — two locals, same order, byte-identical (AGENTS §14.1).
      let prefA: Direction | null
      let prefB: Direction | null
      if (Math.abs(dy) > Math.abs(dx)) {
        prefA = dy > 0 ? 'down' : 'up'
        prefB = dx > 0 ? 'right' : 'left'
      } else {
        prefA = dx > 0 ? 'right' : 'left'
        prefB = dy > 0 ? 'down' : 'up'
      }
      let moved = false
      if (prefA !== null && self.canMoveDir(p, prefA)) {
        self._moveDir = prefA
        moved = true
      } else if (prefB !== null && self.canMoveDir(p, prefB)) {
        self._moveDir = prefB
        moved = true
      }
      if (!moved) {
        // All preferred directions blocked — try any open direction.
        for (const d of ALL_DIRS) {
          if (self.canMoveDir(p, d)) {
            self._moveDir = d
            break
          }
        }
      }
      // §162: still fully walled in — try BREAKABLE directions (sealed spawn
      // pockets never get broken by the passable-only fallback; the break-
      // through fire below clears the wall once _moveDir faces it).
      if (!self._moveDir && self.params.navBreakStuck > 0) {
        for (const d of ALL_DIRS) {
          if (self.canMoveOrBreak(p, d)) {
            self._moveDir = d
            break
          }
        }
      }
    }
  } else if (navStuck && stuckAtCenter) {
    // P3.1: Stuck at/near center — chase nearest enemy directly instead
    // of re-targeting center. directMove breaks through brick walls.
    self._moveDir = self.directMove(pc)
    if (!self._moveDir) {
      // directMove also failed — try any passable direction to get moving.
      for (const d of ALL_DIRS) {
        if (self.canMoveDir(p, d)) {
          self._moveDir = d
          break
        }
      }
    }
  } else if (navDist <= 5) {
    // Close range — directMove (responsive, tracks moving enemies).
    self._moveDir = self.directMove(pc)
  } else if (
    // §177: Dual central breach — P2 navigates with directMove at ALL
    // ranges. A* routes AROUND walls through corridors, so P2 never ends
    // up on an enemy's row/column on open ground and the fire logic never
    // gets a clear shot (measured: P2 fire rate 0% for a full run, 0
    // kills). directMove closes the row gap first and breaks thin brick,
    // which is exactly the alignment shouldFireInDir needs. Gated by
    // dualStrategyActive (spectateDual || coop) && centralBreachRisk && isPlayer2 &&
    // dualCentralBreachP2DirectMove — single-player and P1 keep the A*
    // long-range branch (byte-identical).
    self.dualStrategyActive &&
    self.isPlayer2() &&
    self.params.dualCentralBreachP2DirectMove > 0
  ) {
    self._moveDir = self.directMove(pc)
    if (!self._moveDir) {
      // directMove found nothing (fully walled in / already on target) —
      // fall back to A*, the mirror of the default long-range order.
      self._moveDir = self.followPath()
    }
  } else if (
    // §181 (autopsy seed115): Dual central breach — P1 navigates with
    // directMove at ALL ranges, same rationale as P2 (§177). A* routes
    // around base-protection bricks, but the route changes as the player
    // moves, causing left↔right oscillation at spawn (P1 ping-pongs
    // 128↔136px for the entire game while enemies destroy the base).
    // directMove goes straight up toward the anchor, breaking thin
    // brick on the way. Gated by spectateDual && centralBreachRisk &&
    // !isPlayer2 && dualCentralBreachP1DirectMove — single-player and
    // P2 keep the A* long-range branch (byte-identical).
    self.world.spectateDual &&
    self._centralBreachRisk &&
    !self.isPlayer2() &&
    self.params.dualCentralBreachP1DirectMove > 0
  ) {
    self._moveDir = self.directMove(pc)
    if (!self._moveDir) {
      self._moveDir = self.followPath()
    }
  } else {
    // Long range — A* pathfinding (finds corridors in mazes).
    self._moveDir = self.followPath()
    if (!self._moveDir) {
      // A* failed or path exhausted — fall back to direct movement.
      self._moveDir = self.directMove(pc)
    }
  }
  // §182: When the player has been physically immobile for >= carveDigBlockTicks
  // (1.5s default) AND either (a) all movement options failed (_moveDir is
  // null) or (b) the movement direction is blocked by an enemy (not terrain),
  // turn to face the nearest enemy and fire at it. Without this, the player
  // faces a fixed direction and fires uselessly while adjacent enemies remain
  // untouched (S2@seed120: 150s stuck at defense position (9,25), gameover).
  // The _digBlockTicks gate ensures this only triggers on TRUE immobility,
  // not brief navigation pauses.
  if (
    self._enemies.length > 0 &&
    self.params.navBreakStuck > 0 &&
    self._digBlockTicks >= self.params.carveDigBlockTicks &&
    (!self._moveDir ||
      (!self.canMoveDir(p, self._moveDir) && !self.canMoveOrBreak(p, self._moveDir)))
  ) {
    let bestDir: Direction | null = null
    let bestDist = Infinity
    for (let ei = 0; ei < self._enemies.length; ei++) {
      const t = self._enemies[ei]
      const tc = self.tankCell(t)
      const dx = tc.col - pc.col
      const dy = tc.row - pc.row
      const d = Math.abs(dx) + Math.abs(dy)
      if (d < bestDist) {
        bestDist = d
        if (Math.abs(dy) >= Math.abs(dx)) {
          bestDir = dy > 0 ? 'down' : 'up'
        } else {
          bestDir = dx > 0 ? 'right' : 'left'
        }
      }
    }
    if (bestDir) {
      self._moveDir = bestDir
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, bestDir, false)
      self.branchCounts.navigate++
      self._lastBranch = 'navigate'
      return true
    }
  }
  // §85: Close-range enemy exposure check — don't turn your back on a
  // close enemy. If an enemy is within closeCombatDangerRange cells,
  // aligned with the player (same row/col), has no wall between them,
  // and the player's moveDir is NOT toward that enemy, cancel the move
  // and face the enemy to fire instead. This prevents the "turn and walk
  // away from a close enemy, get shot in the back" death pattern.
  if (!shielded && self.params.closeCombatDangerCheck > 0 && self._moveDir) {
    const dangerDir = self.closeCombatExposure(
      pcx,
      pcy,
      self._moveDir,
      self.params.closeCombatDangerRange,
    )
    if (dangerDir) {
      // §153-W2/§165: fire-rate-aware close combat. When the aligned close
      // enemy fires FASTER than the player, a stand-and-duel is a losing
      // trade; dodge perpendicular to a safe position instead.
      // §165 round 2: the multi-enemy count (2+ aligned = outgunned) was
      // A/B tested and found HARMFUL (-2.0pp) — the player MUST engage and
      // kill enemies to win; retreating from 2v1 gives enemies free rein
      // to approach the base. The DODGE candidate (weight 1000) handles
      // dodging specific bullets. Keep 1v1 fire-rate comparison only.
      if (self.params.closeCombatDuel > 0) {
        const enemyTank = findCloseEnemyImpl(
          self,
          pcx,
          pcy,
          dangerDir,
          self.params.closeCombatDangerRange,
        )
        const playerFaster = enemyTank ? playerFasterThanImpl(p, enemyTank) : true
        if (!playerFaster) {
          const dodgeDir = safePerpDodgeImpl(self, pcx, pcy, dangerDir)
          if (dodgeDir) {
            self._moveDir = dodgeDir
            self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dodgeDir)
            self.branchCounts.navigate++
            self._lastBranch = 'navigate'
            return true
          }
        }
      }
      // Cancel the move — face the enemy and fire.
      self._moveDir = p.dir === dangerDir ? null : dangerDir
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dangerDir)
      self.branchCounts.navigate++
      self._lastBranch = 'navigate'
      return true
    }
  }
  // M5/§165: 站位提前规避 — check the immediate next cell (1 cell ahead)
  // for a bullet that will arrive at the same time as the player. If a
  // threat is found, swap to a safe perpendicular direction. 1-cell
  // lookahead is precise (the bullet and player are both at the same
  // cell next tick); 3-cell lookahead was noisy (too many false positives).
  if (self.params.pathThreatAvoidance > 0 && self._moveDir) {
    const pathBullet = self.findPathThreat(pcx, pcy, self._moveDir, p.speed)
    if (pathBullet) {
      const safeDir = self.findSafeMoveDir(pcx, pcy, self._moveDir, p.speed)
      if (safeDir) {
        self._moveDir = safeDir
      }
    }
  }
  // §145: 冰上滑行控制 — 转弯/反向前先松键（null），滑行以 0.05 自然衰减，
  // 不倒退不过冲（冰上反向 = 真倒车 → 倒过头 → 格边界抖动 → 方向振荡，
  // S24 seed 23 t4506-4511 实测）。旋钮默认 0 → byte-identical。
  if (self.params.iceGlideControl > 0) {
    self._moveDir = iceGlideAdjust(
      self._moveDir,
      w.isTankOnIce(p),
      p.vx,
      p.vy,
      self.params.iceGlideMinSpeed,
    )
  }
  // §153-W1: wait-for-bullet — if the player's NEXT move would collide with
  // an enemy bullet (the predictive next-body check in bulletLaneClearImpl,
  // which includes the off-axis grid snap that drove the body into the
  // bullet's lane at hard S12 seed 3214953618 tick 1599), HOLD this tick
  // instead of driving/snapping into its path. §154: the original expanded-
  // body version held for perpendicular / passed bullets too and was net-
  // negative on hard (18 losing seeds) — the predictive check is exact.
  // §154 round 2: skip the hold while the turn is cooldown-deferred — the
  // player cannot snap into the lane this tick anyway (SimulationCombat
  // halts it), so the freeze is free; the check re-evaluates next tick and
  // releases exactly when the cooldown expires (S9-5's 5-tick freeze at
  // 480-484 was mostly this involuntary halt — the S12-1 over-wait family).
  // Bullets are ~4-6 px/tick, so any real hold clears in 1-3 ticks.
  if (self.params.bulletLaneWait > 0 && self._moveDir) {
    const turnCd = w.rules?.turnCooldownMs ?? 0
    const turnDeferred =
      turnCd > 0 &&
      p.dir !== self._moveDir &&
      w.frame * (1000 / 60) - (p.lastTurnMs ?? -9999) < turnCd
    if (!turnDeferred && !bulletLaneClearImpl(self, p, self._moveDir)) {
      self._moveDir = null
    }
  }
  // Fire control: when blocked by a breakable wall (verified by
  // canMoveOrBreak in directMove), fire immediately to break through.
  // Don't check shouldFireInDir here — it might fire at enemy bullets
  // (T5) instead of the wall, leaving the player stuck. When moving
  // freely, fire only at enemies (not walls) to save the bullet cap.
  // §179 (autopsy seed6 失误 A): when P1 is in the dual central breach hold
  // and the move direction is DOWN, skip break-through fire — P1 must not
  // carve through the base's central shield. Fall through to shouldFireInDir
  // (else branch), which still fires at enemies in the line of fire.
  const p1HoldNoDownFire = isDualCentralBreachHoldP1(self) && self._moveDir === 'down'
  if (self._moveDir && !self.canMoveDir(p, self._moveDir) && !p1HoldNoDownFire) {
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
    // §6.3-C: Dual central breach — P1 dig-while-moving. P1 fires at
    // brick walls while navigating toward the guard anchor, breaking
    // through corridors without waiting for the navStuck detector.
    // allowWallFire=true → shouldFireInDir fires at breakable walls
    // (T6/T11 guards still prevent firing at base brick/steel). Gated
    // by spectateDual && centralBreachRisk && !isPlayer2 — P2 and
    // single-player keep allowWallFire=false (byte-identical).
    const p1DigFire =
      self.world.spectateDual &&
      self._centralBreachRisk &&
      !self.isPlayer2() &&
      self.params.dualCentralBreachP1DigFire > 0
    // §179 (autopsy seed6 失误 A): P1 at the anchor (12,12) must NOT fire
    // DOWN at base-column bricks — that carved a 14-brick tunnel through
    // the base's central shield (rows 13-19, cols 12+13). P1's job is to
    // snipe UP the spawn lane, not dig toward the base. shouldFireInDir
    // still fires at enemies in the line of fire (enemy check runs before
    // the wall-fire check), so this only suppresses wall-fire, not combat.
    const fireDir = self._moveDir ?? p.dir
    const p1DigFireDir = p1DigFire && fireDir !== 'down'
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, fireDir, p1DigFireDir)
  }
  self.branchCounts.navigate++
  self._lastBranch = 'navigate'
  return true
}
