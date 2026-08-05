// Moved verbatim from GodAIInput.ts during the giant-file split — the core
// decision loop (private think()) relocated as thinkImpl(self) following the
// §0.5 `<name>Impl(self, ...)` convention.
//
// M1 (plan/God-AI-Redesign-v2 §3, DECISIONS §99): the top-level chain is now
// the decision-chain scoring shell (DecisionCore.ts). The common prefix stays
// here (dead check → Cluster C snapshots → cooldown → S8/S9 state → aimDir →
// threat), then the 8 candidates run in weight order with early-exit. Each
// candidate body is a VERBATIM transcription of the original branch — parity
// by construction (M1 theorem, doc §3.3). Weights strictly mirror the chain
// order, so behavior under default params is byte-identical to pre-M1.
import type { GodAIInput } from '../GodAIInput'
import type { Cell } from '../../utils/pathfind'
import type { Direction } from '../../constants'
import { BASE_POS, CELL, DIR_VECTORS, GRID } from '../../constants'
import { ALL_DIRS } from '../../utils/helpers'
import {
  scanAheadImpl,
  shouldFireBreakThroughImpl,
  aimSurvivesTurnImpl,
  shotReachesBaseImpl,
  enemyInShotCorridorImpl,
  shouldFireInDirImpl,
} from './FireControl'
import { dodgeCounterFireDirImpl, findBulletThreatToBaseImpl } from './ThreatAssessor'
import {
  controlledLives,
  hasLethalBulletWithinWindowImpl,
  findSuicideTargetImpl,
  anyThreatPointEnemyImpl,
} from './SuicideReturn'
import { runChain, ACTION_WEIGHTS, type Candidate } from './DecisionCore'
import { survivalPressure, updateEnemyModel } from './EnemyModel'
import { enemyCanShootBase, enemyApproachingBaseLaneImpl } from './SmartThreatModel'

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
const SUICIDE_RETURN: Candidate = {
  id: 'suicideReturn',
  weight: ACTION_WEIGHTS.suicideReturn,
  evaluate(self, ctx) {
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
      const distBase = Math.abs(pcStrict.col - BASE_POS.col) + Math.abs(pcStrict.row - BASE_POS.row)
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
  },
}

/** dodge(1000) — survive first: reaction, M3 counter-fire, perpendicular dodge. */
const DODGE: Candidate = {
  id: 'dodge',
  weight: ACTION_WEIGHTS.dodge,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown, threat } = ctx
    if (threat) {
      if (threat.id !== self.lastThreatId) {
        self.lastThreatId = threat.id
        self.reactionCounter = self.params.reactionDelay
      }

      if (self.reactionCounter > 0) {
        self.reactionCounter--
        // While reacting, keep navigating but fire only at targets in facing dir.
        self._moveDir = self.followPath()
        if (!self._moveDir) self._moveDir = self.directMove(self.playerCell())
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self._lastBranch = 'dodge'
        return true
      }

      // §M3-revisit round 3 (dodge quality, DECISIONS §98/§101): counter-fire
      // ONLY when the dodge is TERRAIN-pinned (isTerrainPinned: both
      // perpendicular directions impassable — corridor/corner). Facing the
      // bullet and firing to cancel it (bullet-bullet collision) is then the
      // only reliable survival move. Round 1 gated on distance alone and
      // counter-fired mid-maneuver during a VIABLE dodge (S25 seed 10 →
      // deterministic regression 5/20→1/20). Round 2 gated on timing-aware
      // infeasibility and gained +3.4pp chaos at 60-seed but regressed
      // crossfire stages (Twin Spires/Bastion/Final Redoubt): on open ground
      // a bullet too close to FULLY clear still benefits from a PARTIAL dodge
      // (keeps the player mobile), while standing to counter-fire became a
      // stationary death. Bullet coverage of a dodge cell never pins —
      // crossfire must keep the player moving. Not on ice (slippery turning
      // breaks 对枪, same guard as the T2a counter-fire). Default OFF
      // (0 = byte-identical to M0).
      if (self.params.dodgeCounterFire > 0 && !onCooldown && !w.isTankOnIce(p)) {
        if (self.isTerrainPinned(threat)) {
          const fireDir = dodgeCounterFireDirImpl(self, threat, pcx, pcy)
          if (fireDir) {
            self._moveDir = p.dir === fireDir ? null : fireDir
            self._fire = true
            // M3 diag: counter-fire trigger counter (pure observation, like
            // branchCounts — no RNG, no gameplay effect). Read by
            // tmp/probe-pinned-loss.ts to attribute crossfire-stage losses.
            self._counterFireTicks++
            // Keep the §86 dodge state consistent (fresh threat, no oscillation).
            self._lastDodgeThreatId = threat.id
            self._lastDodgeDir = self._moveDir
            self._dodgeFlipCount = 0
            self.branchCounts.dodge++
            self._lastBranch = 'dodge'
            return true
          }
        }
      }
      // M4 (plan/God-AI-Redesign-v2, DECISIONS §102): 紧急对枪 — 当子弹太近
      // (<5格) 且不在冷却中且无交叉火力时，放弃垂直闪避（数学上不可行），
      // 改为朝威胁方向移动并开火。子弹碰撞抵消（bullet-bullet collision）
      // 是近距离唯一可靠的生存手段。
      // 安全门控：`hasCrossFireBullet` 检查是否有其他子弹在 5 格内威胁玩家
      // — 交叉火力存在时保持垂直移动（部分闪避减少被击中概率），避免站定被
      // 另一颗子弹打死（§101 交叉火力关失败根因）。冰面跳过（滑移破坏对枪）。
      // 默认 OFF（dodgeCounterFire=0）⇒ byte-identical to M0。
      if (self.params.dodgeCounterFire > 0 && !onCooldown && !w.isTankOnIce(p)) {
        const vertical = threat.dir === 'up' || threat.dir === 'down'
        const dist = vertical
          ? Math.abs(threat.y + threat.w / 2 - pcy)
          : Math.abs(threat.x + threat.h / 2 - pcx)
        // 紧急对枪距离阈值：5格 = 80px。子弹 4px/tick，需 20 tick 到达；
        // 玩家垂直闪避需 18+ tick。5格内闪避数学上不可行（§M4 测量）。
        if (dist <= 5 * CELL) {
          // 安全门控：检查是否有其他子弹在 5 格内
          const hasCrossfire = self.hasCrossFireBullet(pcx, pcy, threat.id, 5, 1)
          if (!hasCrossfire) {
            const fireDir = dodgeCounterFireDirImpl(self, threat, pcx, pcy)
            if (fireDir) {
              self._moveDir = p.dir === fireDir ? null : fireDir
              self._fire = true
              self._counterFireTicks++
              self._lastDodgeThreatId = threat.id
              self._lastDodgeDir = self._moveDir
              self._dodgeFlipCount = 0
              self.branchCounts.dodge++
              self._lastBranch = 'dodge'
              return true
            }
          }
        }
      }

      // Dodge: move perpendicular to the bullet (M3: verify safety).
      self._moveDir = self.dodgeDirection(threat, pcx, pcy)
      // §86: Track dodge state for oscillation detection + persistence/hysteresis.
      // _lastDodgeThreatId is always set (needed by oscillation detection,
      // hysteresis, and persistence in ThreatAssessor). _lastDodgeDir is always
      // set (needed by oscillation detection to compare against next tick's dir).
      // _dodgeFlipCount tracks consecutive direction flips for the same threat.
      if (threat.id === self._lastDodgeThreatId && self._lastDodgeDir !== null) {
        // Same threat as last tick — check if direction flipped.
        if (self._moveDir !== null && self._moveDir !== self._lastDodgeDir) {
          self._dodgeFlipCount++
        } else {
          // Direction stable or null — reset flip counter.
          self._dodgeFlipCount = 0
        }
      } else {
        // New threat — reset flip counter.
        self._dodgeFlipCount = 0
      }
      self._lastDodgeThreatId = threat.id
      self._lastDodgeDir = self._moveDir
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
      self.branchCounts.dodge++
      self._lastBranch = 'dodge'
      return true
    }

    // No threat — reset reaction state (the dodge section's no-threat resets).
    self.reactionCounter = 0
    self.lastThreatId = -1
    // §86: reset dodge state when no threat is active.
    self._lastDodgeThreatId = -1
    self._lastDodgeDir = null
    self._dodgeFlipCount = 0
    return false
  },
}

/** interceptBase(900) — T8: stop an in-flight bullet aimed at the base. */
const INTERCEPT_BASE: Candidate = {
  id: 'interceptBase',
  weight: ACTION_WEIGHTS.interceptBase,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    // Check AFTER dodge (survive first) but BEFORE aggressive/T2a.
    // Skip only when enemies are frozen (aggressive hunt — no bullets to
    // intercept). When shielded, the player can still intercept bullets
    // headed for the base — the shield protects the player, not the base.
    // Gap B (plan §3): skip entirely when the stage has no base.
    if (!self.aggressive && self.hasBase) {
      const baseThreat = self.findBulletThreatToBase()
      if (baseThreat) {
        const interceptCell = self.baseBulletInterceptCell(baseThreat)
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
  },
}

/** pickupHigh(800) — §87/§88 HIGH-tier urgent pickup (bomb/freeze/fence ≤8格). */
const PICKUP_HIGH: Candidate = {
  id: 'pickupHigh',
  weight: ACTION_WEIGHTS.pickupHigh,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    // NORMAL mode only: during freeze the aggressive branch already grabs
    // power-ups when no enemy is aligned, and an aligned frozen enemy is a
    // free kill we must not interrupt. Gated by pickupPriorityMode.
    // §88 (chokepointMode>0): HIGH-tier outranks base defense and is checked
    // here; MID-tier (star/tank/shield) yields to base defense and is checked
    // after the aggressive section (see PICKUP_MID). When chokepointMode==0,
    // the original all-tiers-together order is kept (byte-identical to pre-§88).
    if (!self.aggressive) {
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
        const urgentTarget =
          self.params.chokepointMode > 0
            ? self.findUrgentPowerUpTarget(pcx, pcy, 'high')
            : self.findUrgentPowerUpTarget(pcx, pcy)
        if (urgentTarget) {
          self._moveDir = self.navigateTowards(urgentTarget)
          self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
          self.branchCounts.powerup++
          self._lastBranch = 'powerup'
          return true
        }
      }
    }
    return false
  },
}

/** aggro(700) — S8/S9 freeze/shield window: stop-and-aim → power-up → navigate. */
const AGGRO: Candidate = {
  id: 'aggro',
  weight: ACTION_WEIGHTS.aggro,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
    if (self.aggressive) {
      // Skip defense, go straight for the nearest enemy or power-up.
      //
      // §80: `aimSurvivesTurnImpl` MUST be evaluated BEFORE `scanAheadImpl`
      // below — both write into the shared `self._scanResult`, so running the
      // guard afterwards would clobber `aggScan`. The `&&` short-circuit gives
      // us that ordering for free. When the guard rejects the aim (the turn's
      // grid-snap would shove the tank off the firing line) we fall through to
      // the navigate path, which has real stall detection — this is what
      // breaks the period-2 freeze-window deadlock.
      if (aimDir && self._aggCampSuppress <= 0 && aimSurvivesTurnImpl(self, p, aimDir)) {
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
        const aggFireBlocked =
          self.params.selfFireBaseGuard > 0 &&
          shotReachesBaseImpl(self, pcx, pcy, aimDir) &&
          (self.params.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, aimDir))
        if (aggFireBlocked) self._selfFireGuardBlocks++
        // §74: Don't fire when a base-protection wall is on the other offset
        // line, or is closer than (or at the same distance as) the enemy — the
        // 6px bullet spans both offset columns and would hit the wall first.
        if (
          aggScan.enemy &&
          !aggFireBlocked &&
          !(aggScan.baseWall && aggScan.baseWallDist <= aggScan.enemyDist) &&
          !(aggScan.baseSteel && (p.level ?? 0) >= 3)
        ) {
          // §84: Aggressive stall detection — the aggressive branch has NO
          // anti-stall guard (unlike T2a's _campTicks and navigate's
          // _navStuckTicks). Without this, the player can sit at one cell
          // firing at an enemy whose body is slightly offset from the bullet
          // path for the ENTIRE freeze window. When camping exceeds
          // aggCampTimeoutTicks with no kills, fall through to navigate.
          if (self.params.aggCampTimeoutTicks > 0) {
            const pc84 = self.playerCell()
            if (
              self._aggCampCell &&
              Math.abs(self._aggCampCell.col - pc84.col) <= 1 &&
              Math.abs(self._aggCampCell.row - pc84.row) <= 1
            ) {
              self._aggCampTicks++
              if (w.killCount !== self._aggCampKillsAtStart) {
                self._aggCampTicks = 1
                self._aggCampKillsAtStart = w.killCount
              }
            } else {
              self._aggCampCell = { col: pc84.col, row: pc84.row }
              self._aggCampTicks = 1
              self._aggCampKillsAtStart = w.killCount
            }

            if (
              self._aggCampTicks > self.params.aggCampTimeoutTicks &&
              w.killCount === self._aggCampKillsAtStart
            ) {
              // Camped too long with no kills — suppress aggressive
              // stop-and-aim for a while and fall through to navigate.
              self._aggCampCell = null
              self._aggCampTicks = 0
              self._aggCampSuppress = self.params.antiCampSuppressTicks
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
        self._moveDir = self.navigateTowards(puTarget)
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self._lastBranch = 'aggressive'
        return true
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
    if (self._aggCampCell) {
      self._aggCampCell = null
      self._aggCampTicks = 0
    }
    if (self._aggCampSuppress > 0) self._aggCampSuppress = 0
    return false
  },
}

/** pickupMid(600) — §88 MID-tier urgent pickup (star/tank/shield ≤4格). */
const PICKUP_MID: Candidate = {
  id: 'pickupMid',
  weight: ACTION_WEIGHTS.pickupMid,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    // Per the §88 rule-4 chain, MID-tier pickups outrank 据守咽喉要地. The HIGH
    // tier (bomb/freeze/fence) was already checked before the aggressive
    // section. Only runs when chokepointMode > 0; otherwise the single §87
    // branch above handled all tiers (byte-identical).
    if (self.params.chokepointMode > 0 && !self.aggressive && self.params.pickupPriorityMode > 0) {
      const midTarget = self.findUrgentPowerUpTarget(pcx, pcy, 'midlow')
      if (midTarget) {
        self._moveDir = self.navigateTowards(midTarget)
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
        self.branchCounts.powerup++
        self._lastBranch = 'powerup'
        return true
      }
    }
    return false
  },
}

/**
 * defenseIntercept(550) — §134/方向 D: 防守位停射拦截基地车道敌人。
 *
 * 与 §132（selectTarget 威胁重排，追快车）的本质区别：本候选**不离开防守位**。
 * 玩家在基地附近（distToBase ≤ defenseInterceptMaxDist）时，若某存活敌人
 * 已经与基地对齐且无遮挡（enemyCanShootBase——下一发子弹就能毁基地），同时
 * 该敌人与玩家同排/同列（玩家能从防守位直接命中），则停射拦截它——turn to
 * face + fire，像 T2a 但目标不是 aimDir 选中的最近敌人，而是基地车道上的敌人。
 *
 * 背景（hard 35×120 取证，§131-§133）：Battlement 基地被毁 117/120、凶手 59%
 * fast。三个方向先后证伪——T8 拦子弹（已离膛）、威胁重排（fast 4.5cps 追不上
 * 1★ 玩家 4.19cps）、距离收紧（早回防=把中场让给敌人）。存活下来的思路是
 * 「在车道口把敌人打掉」：敌人与 base 对齐的瞬间（它破砖进入 row 23-25 或 base
 * 列的走廊）正是它最脆弱也最危险的时刻，玩家在防守位（base 列上方）与它同列
 * 的概率最高，一枪命中即解除威胁。
 *
 * 门控（全部默认 OFF → byte-identical）：defenseInterceptMode=0 短路；
 * aggressive（freeze 窗口由 aggro 处理）；无基地关；玩家太远（不出防位追）。
 * 复用 ENGAGE 的 self-fire base guard（shotReachesBaseImpl）——绝不朝基地方向
 * 开火穿过基地打敌人。
 */
const DEFENSE_INTERCEPT: Candidate = {
  id: 'defenseIntercept',
  weight: ACTION_WEIGHTS.defenseIntercept,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown } = ctx
    const prm = self.params
    if (prm.defenseInterceptMode <= 0 || !self.hasBase || self.aggressive) return false
    const pc = self.playerCell()
    const playerDistToBase = Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row)
    if (playerDistToBase > prm.defenseInterceptMaxDist) return false

    // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
    const list = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
      if (!t.alive || t.spawnTimer > 0) continue
      // §134: enemy is ON the base's firing lanes RIGHT NOW (aligned + clear
      // LOS — it can destroy the base with its next bullet). §135 (predict):
      // OR it is about to enter a lane — shares the base's column/row, FACES
      // the base, within defenseInterceptPredictCells (0 = OFF, byte-identical
      // to §134 SHIPPED). Intercept BEFORE it reaches the ring and fires.
      // §135: predict flag is reused by the §136 dig branch below.
      const approaching =
        prm.defenseInterceptPredictCells > 0 &&
        enemyApproachingBaseLaneImpl(self, t, prm.defenseInterceptPredictCells)
      if (!enemyCanShootBase(self, t) && !approaching) continue
      const tc = self.tankCell(t)
      const dCol = tc.col - pc.col
      const dRow = tc.row - pc.row
      // Player must share the enemy's row or column (interceptable shot).
      if (dCol !== 0 && dRow !== 0) continue
      const distCells = Math.abs(dCol) + Math.abs(dRow)
      if (distCells === 0 || distCells > prm.defenseInterceptRangeCells) continue
      const dir: Direction = dCol !== 0 ? (dCol > 0 ? 'right' : 'left') : dRow > 0 ? 'down' : 'up'
      // Self-fire base guard (same as ENGAGE/aggressive §121): never shoot
      // THROUGH the base at an enemy on the far side.
      if (
        prm.selfFireBaseGuard > 0 &&
        shotReachesBaseImpl(self, pcx, pcy, dir) &&
        (prm.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, dir))
      ) {
        continue
      }
      // Confirm a live enemy is actually on the line within range — the
      // scan may hit a nearer enemy, which is equally worth shooting.
      const scan = scanAheadImpl(self, pcx, pcy, dir)
      if (scan.enemy && scan.enemyDist <= distCells * CELL + CELL) {
        self._moveDir = p.dir === dir ? null : dir
        self._fire = !onCooldown && self.rng.next() >= self.params.aimError
        self.branchCounts.defenseIntercept++
        self._lastBranch = 'defenseIntercept'
        return true
      }
      // §136 / 方向 D 破砖版: 预测命中（enemyApproachingBaseLaneImpl）但
      // 弹道被砖挡 → 打砖开路，为即将进车道的敌人建立射界。复用
      // shouldFireInDirImpl（默认 allowWallFire=true）——其内部对
      // baseWall（基地保护环）与钢墙（level<3）一律禁止，子弹只打场景砖。
      // 第一发破砖，敌人走进射界时后续子弹直接命中。天然自终止：砖打光后
      // scan.wall=false → 本分支不再成立（fall through 到正常拦截/走位）。
      if (
        approaching &&
        prm.defenseInterceptDigBricks > 0 &&
        scan.wall &&
        !scan.baseWall &&
        !scan.steel
      ) {
        self._moveDir = p.dir === dir ? null : dir
        self._fire = !onCooldown && shouldFireInDirImpl(self, pcx, pcy, dir)
        self.branchCounts.defenseIntercept++
        self._lastBranch = 'defenseIntercept'
        return true
      }
    }
    return false
  },
}

/** engage(500) — T2a: stop-and-aim when an enemy is in the line of fire. */
const ENGAGE: Candidate = {
  id: 'engage',
  weight: ACTION_WEIGHTS.engage,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
    // P0.2: Only camp when there's a REAL enemy in the line of fire
    // (scan.enemy == true). The old code also camped when there was just a
    // wall (scan.wall && !scan.baseWall), which caused the T2a deadlock:
    // the player would stop and fire at a wall endlessly, never advancing.
    // Now the player only stops to aim when there's an actual enemy to shoot.
    // When the enemy is behind a wall, the player falls through to navigate,
    // which moves toward the enemy and breaks walls via directMove/canMoveOrBreak.
    //
    // P0.1: Anti-camp escape — track how long the player has been at the
    // same cell in T2a. If camping exceeds campTimeoutTicks with no kills,
    // fall through to navigate and hunt the enemy directly.
    //
    // P1: Skip T2a when the base is under threat and the player is too far
    // from the base. Camping far from the base while enemies approach it
    // was the #1 cause of base_destroyed gameovers.
    const skipT2aForDefense =
      self.hasBase &&
      self.isBaseUnderThreat() &&
      Math.abs(self.playerCell().col - BASE_POS.col) +
        Math.abs(self.playerCell().row - BASE_POS.row) >
        self.params.maxPlayerDistFromBase

    if (aimDir && self._antiCampSuppress <= 0 && !skipT2aForDefense) {
      // Inline scanAheadImpl (perf §66, see aggressive branch above).
      const scan = scanAheadImpl(self, pcx, pcy, aimDir)

      // §121: T2a self-fire base guard (default OFF, selfFireBaseGuard=0 →
      // byte-identical). Root cause of the §120 32-run self-kill corpus
      // (t2a 81%): the scan's dual ±8px offset lines catch an enemy up to
      // ~25px off the bullet's 6px center path and report scan.enemy CLOSER
      // than the base eagle — the §74 guard below then allows fire, but the
      // bullet misses the off-line enemy and continues into the base (hard
      // S6 s43: killer shot x=200, enemy body x∈[206,238], bullet [197,203]
      // passed beside it into the eagle). Walk the bullet's real center
      // line: if it reaches the base, don't stop-and-aim here — fall
      // through to navigate (which repositions off the base line).
      // Mode 2 (lenient): keep the shot when an enemy body truly overlaps
      // the 6px corridor (point-blank overlap kill — bullet hits enemy first).
      const selfFireBlocked =
        self.params.selfFireBaseGuard > 0 &&
        shotReachesBaseImpl(self, pcx, pcy, aimDir) &&
        (self.params.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, aimDir))
      if (selfFireBlocked) self._selfFireGuardBlocks++

      // §74: Don't enter T2a when a base-protection wall is closer than
      // (or at the same distance as) the enemy on the other offset line.
      // Fall through to navigate when blocked by a closer base wall.
      if (
        scan.enemy &&
        !selfFireBlocked &&
        !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) &&
        !(scan.baseSteel && (p.level ?? 0) >= 3)
      ) {
        // §56: dynamic T2a range based on enemy kind.
        // For non-armor enemies (basic/fast/power): use t2aMaxRange (15) —
        // one shot kills at any distance, no DPS penalty for range.
        // For armor (4 hitsToKill): use t2aHighHpMaxRange (2) — close combat.
        // M3 (dodgeRateShrinksT2a): shrink the 1-HP range by the EnemyModel's
        // perceived turn discipline — enemies that dodge/redirect a lot make
        // long-range shots wasteful, so engage point-blank (shorter bullet
        // travel → fewer dodged shots). 0 at default ⇒ byte-identical.
        let effectiveRange =
          scan.enemyKind === 'armor' ? self.params.t2aHighHpMaxRange : self.params.t2aMaxRange
        if (self.params.dodgeRateShrinksT2a > 0 && scan.enemyKind !== 'armor') {
          const m = self._enemyModel
          if (m && m.active) {
            const shrink = self.params.dodgeRateShrinksT2a * m.discipline
            if (shrink > 0) effectiveRange = Math.max(1, effectiveRange * (1 - shrink))
          }
        }
        if (scan.enemyDist <= effectiveRange) {
          // Track camping duration in a ZONE (±1 cell), not exact cell.
          // P2.1fix: the old exact-cell check was defeated by sub-cell
          // oscillation — the player bounces between two adjacent cells
          // (e.g., x=32→40→32) at the TANK/CELL boundary, resetting the
          // camp cell each time the boundary is crossed. This prevented
          // the anti-camp escape from EVER firing, causing the Stage 3/4
          // deadlocks (player stuck at one spot for 17000+ ticks). The
          // zone fix accumulates camp time across nearby cells, so the
          // escape triggers even if the player wiggles between two cells.
          const pc = self.playerCell()
          if (
            self._campCell &&
            Math.abs(self._campCell.col - pc.col) <= 1 &&
            Math.abs(self._campCell.row - pc.row) <= 1
          ) {
            self._campTicks++
            // If a kill happened since camping started, reset the camp timer.
            // The player is being productive — let it continue camping.
            if (w.killCount !== self._campKillsAtStart) {
              self._campTicks = 1
              self._campKillsAtStart = w.killCount
            }
          } else {
            // Moved outside the camp zone — start fresh camp tracking.
            self._campCell = { col: pc.col, row: pc.row }
            self._campTicks = 1
            self._campKillsAtStart = w.killCount
          }

          // Anti-camp: if too long at this cell with no kills, break out.
          const campedTooLong =
            self._campTicks > self.params.campTimeoutTicks && w.killCount === self._campKillsAtStart

          if (!campedTooLong) {
            // ---- §49: 炮口相向分场景策略 ----
            // When an enemy faces the player, adapt per enemy type: ice skips,
            // 1HP enemies fight normally (counter-fire still applies — it is a
            // firing action, not movement dodge), armor uses counter-fire +
            // keep-alignment. 对枪抵消 applies to ALL kinds: when an enemy
            // bullet is already in the line, firing to cancel is safer than
            // trading hits. 120-seed validation: +5 wins all kinds.
            // §49-revisit: parameterized for A/B.
            const facing =
              self.params.counterFire > 0 ? self.findEnemyFacingPlayer(pcx, pcy, aimDir) : null
            const onIce = w.isTankOnIce(p)

            if (facing && !onIce && facing.dist <= self.params.counterFireMaxRange * CELL) {
              // ---- 对枪抵消逻辑（适用于所有敌人类型）----
              const enemyBulletInLine = self.hasEnemyBulletInLine(pcx, pcy, aimDir)

              if (enemyBulletInLine && !onCooldown) {
                // 对枪：敌方子弹已在直线上 → 开火抵消
                if (p.dir === aimDir) {
                  self._moveDir = null
                } else {
                  self._moveDir = aimDir
                }
                self._fire = true
                self.branchCounts.t2a++
                self._lastBranch = 't2a'
                return true
              }

              // 先手开火 / 冷却中等待：保持对齐以备对枪
              // 不横移——横移会脱离防守位，在密集关卡导致更多死亡
              if (p.dir === aimDir) {
                self._moveDir = null
              } else {
                self._moveDir = aimDir
              }
              self._fire = !onCooldown && self.rng.next() >= self.params.aimError
              self.branchCounts.t2a++
              self._lastBranch = 't2a'
              return true
            }

            // ---- 正常 T2a（非炮口相向 / 1HP / 冰面）----
            if (p.dir === aimDir) {
              self._moveDir = null // Already facing — stop and shoot
            } else {
              self._moveDir = aimDir // Turn to face enemy
            }
            self._fire = !onCooldown && self.rng.next() >= self.params.aimError
            self.branchCounts.t2a++
            self._lastBranch = 't2a'
            return true
          }

          // Camped too long with no kills — suppress T2a and fall through
          // to navigate, which will move the player toward the enemy.
          self._campCell = null
          self._campTicks = 0
          self._antiCampSuppress = self.params.antiCampSuppressTicks
        }
        // Enemy in line of fire but beyond effective range — fall through
        // to navigate (close the distance for high-HP enemies).
      }
      // No real enemy in line of fire (wall-only or clear) — fall through.
    } else if (self._campCell) {
      // Not in T2a (suppressed or no aimDir) — reset camp tracking.
      self._campCell = null
      self._campTicks = 0
    }
    return false
  },
}

/** pickupLow(400) — S5: opportunistic power-up economy in normal mode. */
const PICKUP_LOW: Candidate = {
  id: 'pickupLow',
  weight: ACTION_WEIGHTS.pickupLow,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
    // Check for power-ups when no enemy is in line of fire. Previously this
    // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
    // Now the AI opportunistically grabs power-ups when it's safe to divert.
    // P1: Skip power-ups when the base is under threat — defense first.
    // P3.2: Also skip when there are enemies within 5 cells of the player —
    // chasing power-ups while enemies are nearby was a major cause of
    // defense-collapse gameovers on S6/S26/S32.
    if ((!aimDir || onCooldown) && !(self.hasBase && self.isBaseUnderThreat())) {
      // P3.2: Don't divert to power-ups when enemies are close.
      const pc2 = self.playerCell()
      let nearbyEnemy = false
      // Cluster C: reuse the per-tick enemy snapshot.
      const nearbyScan = self._enemies.length > 0 ? self._enemies : w.tanks
      for (let ni = 0; ni < nearbyScan.length; ni++) {
        const t = nearbyScan[ni]
        if (!t.alive || t.spawnTimer > 0) continue
        const tc = self.tankCell(t)
        if (Math.abs(tc.col - pc2.col) + Math.abs(tc.row - pc2.row) <= 5) {
          nearbyEnemy = true
          break
        }
      }
      if (!nearbyEnemy) {
        const puTarget = self.findPowerUpTarget(pcx, pcy)
        if (puTarget) {
          self._moveDir = self.navigateTowards(puTarget)
          self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
          self.branchCounts.powerup++
          self._lastBranch = 'powerup'
          return true
        }
      }
    }
    return false
  },
}

/** hunt(200) — T2b: navigate towards the target (distance-adaptive). */
const HUNT: Candidate = {
  id: 'hunt',
  weight: ACTION_WEIGHTS.hunt,
  evaluate(self, ctx) {
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
    if (
      self._navStuckCell &&
      self._navStuckCell.col === pc.col &&
      self._navStuckCell.row === pc.row
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

    const navStuck = self._navStuckTicks > self.params.navStuckTicks

    let navTarget: Cell | null
    // P3.1: When nav-stuck triggers, only go to center if the player is
    // NOT already at/near center (target == current cell → deadlock, the S9
    // root cause). When already at center, chase the nearest enemy directly.
    const distToCenter = Math.abs(pc.col - 12) + Math.abs(pc.row - 12)
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
      Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row) >
        self.params.baseRaceRangeCells
    if (navStuck && !stuckAtCenter) {
      navTarget = { col: 12, row: 12 }
    } else if (survivalRetreat && self.hasBase) {
      navTarget = self.getDefaultDefensePosition()
    } else {
      navTarget = self.selectTarget(pc)
    }

    const navDist = navTarget
      ? Math.abs(navTarget.col - pc.col) + Math.abs(navTarget.row - pc.row)
      : Infinity

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
        const pref: Direction[] = []
        if (Math.abs(dy) > Math.abs(dx)) {
          pref.push(dy > 0 ? 'down' : 'up')
          pref.push(dx > 0 ? 'right' : 'left')
        } else {
          pref.push(dx > 0 ? 'right' : 'left')
          pref.push(dy > 0 ? 'down' : 'up')
        }
        let moved = false
        for (const d of pref) {
          if (self.canMoveDir(p, d)) {
            self._moveDir = d
            moved = true
            break
          }
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
    } else {
      // Long range — A* pathfinding (finds corridors in mazes).
      self._moveDir = self.followPath()
      if (!self._moveDir) {
        // A* failed or path exhausted — fall back to direct movement.
        self._moveDir = self.directMove(pc)
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
        // Cancel the move — face the enemy and fire.
        self._moveDir = p.dir === dangerDir ? null : dangerDir
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dangerDir)
        self.branchCounts.navigate++
        self._lastBranch = 'navigate'
        return true
      }
    }
    // M5 (plan/God-AI-Redesign-v2 §3.2, DECISIONS §103): 站位提前规避 —
    // when the path ahead (up to 3 cells) is crossed by an in-flight enemy
    // bullet that the reactive dodge branch cannot see yet (the bullet is
    // NOT aligned with the player's CURRENT cell), swap the immediate next
    // step to a safe alternative via findSafeMoveDir instead of walking
    // into the crossfire. Distinction from the retired §68-v2 diversion
    // (DECISIONS §73): this swaps only cell-1 and re-evaluates every tick
    // — no A* path commitment, no premature perpendicular diversion at
    // 12-23 tick lead times. 0 at default ⇒ byte-identical to M0.
    if (self.params.pathThreatAvoidance > 0 && self._moveDir) {
      const pathBullet = self.findPathThreat(pcx, pcy, self._moveDir, p.speed)
      if (pathBullet) {
        const safeDir = self.findSafeMoveDir(pcx, pcy, self._moveDir, p.speed)
        if (safeDir) {
          // Step aside — the reactive dodge (next tick if the bullet
          // becomes aligned) will handle the rest.
          self._moveDir = safeDir
        }
      }
    }
    // Fire control: when blocked by a breakable wall (verified by
    // canMoveOrBreak in directMove), fire immediately to break through.
    // Don't check shouldFireInDir here — it might fire at enemy bullets
    // (T5) instead of the wall, leaving the player stuck. When moving
    // freely, fire only at enemies (not walls) to save the bullet cap.
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
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir, false)
    }
    self.branchCounts.navigate++
    self._lastBranch = 'navigate'
    return true
  },
}

/**
 * survive (M3, plan/God-AI-Redesign-v2 §3.2, P1-3 生存优先) — 主动换位.
 *
 * Default weight 0 ⇒ never reached (orderedCandidates sorts it below every
 * active candidate; hunt is unconditional so the chain always terminates
 * before it). Promoted via `actionWeights.survive` (M4 tuning surface), it
 * runs when NO bullet is in flight (dodge declined — the immediate threat is
 * gone) but the player is in a positional dead-end: surrounded by enemies in
 * a low-exit cell. The player actively repositions to a safer cell instead
 * of continuing the current navigate/hunt path into the crossfire.
 *
 * Design (plan §4.4 整合: trapAvoidance 族的"包围风险"输入): a cell with
 * ≤ 2 passable exits is a corridor/corner/dead-end (the §48-revisit surround
 * heuristic); with `surviveMinEnemies` live enemies within
 * `surviveEnemyRadiusCells`, that dead-end is a kill box. The candidate picks
 * the open direction whose next cell has the MOST exits (tie-break toward the
 * base), strictly better than the current cell — never trades one dead-end
 * for another. Fire stays gated on the move direction (normal fire control).
 *
 * Gated additionally by survival pressure: only when `survivalPressure(self) > 0`
 * (last lives / high accuracy / surrounded) does the AI spend ticks on
 * repositioning — otherwise the regular hunt/engage chain is the better play.
 */
const SURVIVE: Candidate = {
  id: 'survive',
  weight: ACTION_WEIGHTS.survive,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
    if (self.aggressive) return false
    // Only when there is no immediate bullet threat (dodge already declined)
    // AND survival pressure is active (P1-3: preserve the last lives).
    if (self.params.surviveMinEnemies <= 0) return false
    if (survivalPressure(self) <= 0) return false
    // When an enemy is ALREADY aligned in the line of fire (aimDir set), the
    // T2a counter-fire / stop-and-aim tactic is the right call — survive is
    // for MULTI-DIRECTION crossfire (no single shootable enemy, plan §3.2
    // "无在飞子弹但处于交叉火力/包围位置"), where standing to fire at one
    // of several threats is death. An aligned target stays engage's job.
    if (aimDir) return false
    // The current cell must be a positional dead-end (≤ 2 passable exits).
    const pc = self.playerCell()
    let exits = 0
    for (let di = 0; di < ALL_DIRS.length; di++) {
      if (self.canMoveDir(p, ALL_DIRS[di])) exits++
    }
    if (exits > 2) return false
    // Enemies must be surrounding the dead-end.
    const radius = self.params.surviveEnemyRadiusCells
    const need = self.params.surviveMinEnemies
    let nearby = 0
    const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
    for (let i = 0; i < enemies.length; i++) {
      const t = enemies[i]
      if (!t.alive || t.spawnTimer > 0) continue
      const ec = self.tankCell(t)
      const d = Math.abs(ec.col - pc.col) + Math.abs(ec.row - pc.row)
      if (d <= radius) {
        if (++nearby >= need) break
      }
    }
    if (nearby < need) return false
    // Pick the open direction whose next cell has the most passable exits,
    // strictly more than the current cell, tie-broken toward the base.
    const baseCol = BASE_POS.col + 1
    const baseRow = BASE_POS.row + 1
    let bestDir: Direction | null = null
    let bestExits = exits
    let bestBaseDist = Infinity
    for (let di = 0; di < ALL_DIRS.length; di++) {
      const d = ALL_DIRS[di]
      if (!self.canMoveDir(p, d)) continue
      const dv = DIR_VECTORS[d]
      const cx = pc.col + dv.dx
      const cy = pc.row + dv.dy
      if (cx < 0 || cx >= 26 || cy < 0 || cy >= 26) continue
      let dExits = 0
      for (let dj = 0; dj < ALL_DIRS.length; dj++) {
        const v2 = DIR_VECTORS[ALL_DIRS[dj]]
        const c2 = cx + v2.dx
        const r2 = cy + v2.dy
        if (c2 < 0 || c2 >= 26 || r2 < 0 || r2 >= 26) continue
        if (!w.isCellBlocked(c2, r2)) dExits++
      }
      const baseDist = Math.abs(cx - baseCol) + Math.abs(cy - baseRow)
      if (dExits > bestExits || (dExits === bestExits && baseDist < bestBaseDist)) {
        bestDir = d
        bestExits = dExits
        bestBaseDist = baseDist
      }
    }
    if (bestDir === null) return false
    // Strictly-more-open guarantee: never trade a dead-end for a dead-end.
    if (bestExits <= exits) return false
    self._moveDir = bestDir
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, bestDir)
    self.branchCounts.survive++
    self._lastBranch = 'survive'
    // Update the last-aim so the engine sees a coherent turn (same as T2b).
    if (aimDir) void aimDir
    return true
  },
}

/**
 * §139 / 方向 A（进攻侧）: 火力死区解除 (firing-lane re-engage).
 *
 * Battlement 击杀效率分析（2026-08-05）: 命中率 23.7% 正常，瓶颈是射击量——
 * 玩家 51% 时间静止、34% 全 tick 钉在 (11,24) 火力死区（四方向无敌人 LOS），
 * 射击量 24.9 发/局只有 S32 的 37%（67.7），击杀 5.9/20 局基地即失守。
 *
 * 本候选：玩家处于死区（四方向 scan 全无敌人）且所有敌人较远（>=
 * firingLaneMinEnemyDist，无法直接追到）时，不再原地待机——在半径
 * firingLaneRadius 内找可站、能看到 ≥1 个敌人（同排/列 + 无遮挡）的瞭望格，
 * 导航过去重新接战（到了之后由 engage/aggressive 接管开火）。与 §137/§138
 * （去守位格「站着防守」）本质区别：这是「解卡 + 保持移动找射界」，不驻守。
 *
 * 门控：firingLaneMode=0 短路（byte-identical）；freeze/aggressive 跳过；
 * 已有敌人 LOS 跳过（engage/aggressive 接管）；敌人近在咫尺跳过（hunt
 * 直接追更快）。瞭望格搜索带 tick 节流（firingLaneReplanTicks）。纯函数：
 * 无 RNG、不改 World；分支计数仅观察。
 */
function findFiringLaneCellImpl(self: GodAIInput, pc: Cell): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const prm = self.params
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  // Enemy cells + base distance (bounded allocation — throttled rare path,
  // same discipline as the §88 chokepoint replan, not per-tick).
  const ecols: number[] = []
  const erows: number[] = []
  const ebd: number[] = []
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    ecols.push(tc.col)
    erows.push(tc.row)
    ebd.push(Math.abs(tc.col - BASE_POS.col) + Math.abs(tc.row - BASE_POS.row))
  }
  const r = prm.firingLaneRadius
  let best: Cell | null = null
  let bestScore = -Infinity
  for (let rr = pc.row - r; rr <= pc.row + r; rr++) {
    for (let cc = pc.col - r; cc <= pc.col + r; cc++) {
      if (cc < 0 || cc >= GRID || rr < 0 || rr >= GRID) continue
      const t = tm.get(cc, rr)
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') continue
      // Count enemies visible from (cc,rr): same row/col with clear bullet line.
      let vis = 0
      let score = 0
      for (let ei = 0; ei < ecols.length; ei++) {
        if (cc === ecols[ei]) {
          const step = erows[ei] < rr ? -1 : 1
          let clear = true
          for (let y = rr + step; y !== erows[ei]; y += step) {
            const ty = tm.get(cc, y)
            if (ty === 'brick' || ty === 'steel' || ty === 'base') {
              clear = false
              break
            }
          }
          if (clear) {
            vis++
            // Base-adjacent enemies are urgent (intercept bias under pressure).
            score += ebd[ei] <= prm.threatRangeCells ? 2 : 1
          }
        } else if (rr === erows[ei]) {
          let clear = true
          for (let x = Math.min(cc, ecols[ei]) + 1; x < Math.max(cc, ecols[ei]); x++) {
            const tx = tm.get(x, rr)
            if (tx === 'brick' || tx === 'steel' || tx === 'base') {
              clear = false
              break
            }
          }
          if (clear) {
            vis++
            score += ebd[ei] <= prm.threatRangeCells ? 2 : 1
          }
        }
      }
      if (vis === 0) continue
      const dist = Math.abs(cc - pc.col) + Math.abs(rr - pc.row)
      const s = score * 10 - dist
      if (s > bestScore) {
        bestScore = s
        best = { col: cc, row: rr }
      }
    }
  }
  return best
}

const FIRING_LANE: Candidate = {
  id: 'firingLane',
  weight: ACTION_WEIGHTS.firingLane,
  evaluate(self, ctx) {
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
      const d = Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)
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
  },
}

/** The M1 chain — weight order strictly mirrors the original top-level order.
 * Exported for the M1 invariant test (tests/decision-core.test.ts): a reorder
 * without a matching ACTION_WEIGHTS update is a behavior change. */
export const CANDIDATES: Candidate[] = [
  SUICIDE_RETURN,
  DODGE,
  INTERCEPT_BASE,
  PICKUP_HIGH,
  AGGRO,
  PICKUP_MID,
  DEFENSE_INTERCEPT,
  ENGAGE,
  PICKUP_LOW,
  FIRING_LANE,
  HUNT,
  SURVIVE,
]

// ===========================================================================
// Shell — common prefix + decision chain
// ===========================================================================

export function thinkImpl(self: GodAIInput): void {
  if (self._thought) return
  self._thought = true

  const w = self.world
  const p = self.controlledTank(w)
  if (!p || !p.alive || p.spawnTimer > 0) {
    self._moveDir = null
    self._fire = false
    self.branchCounts.dead++
    self._lastBranch = 'dead'
    return
  }

  // ---- Cluster C: per-tick snapshots (built once, reused across modules) ----
  // These mirror the exact filters the god/* sub-modules used to run on every
  // call, in the same iteration order, so no decision (incl. enemies[0]
  // tie-breaks) changes. Pure recomputation elimination, not a behavior change.
  const tanks = w.tanks
  self._enemies.length = 0
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && t.spawnTimer <= 0) self._enemies.push(t)
  }
  const all = w.allTanks
  self._otherTanks.length = 0
  for (let i = 0; i < all.length; i++) {
    const o = all[i]
    if (o.alive) self._otherTanks.push(o)
  }

  // M3 (Pillar B, plan/God-AI-Redesign-v2 §4.2b): per-tick EnemyModel update.
  // Gated on enemyModelMode > 0 && window > 0 — OFF at default ⇒ the hook is
  // byte-inert and the gates stay byte-identical to M0. Pure World observation
  // (no RNG, no difficultyKey reads) — deterministic, replay-safe.
  if (self.params.enemyModelMode > 0 && self.params.enemyModelWindowTicks > 0) {
    updateEnemyModel(self)
  }

  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const now = w.frame * (1000 / 60)

  // P0.1: Decrement anti-camp suppression every tick the player is alive.
  if (self._antiCampSuppress > 0) self._antiCampSuppress--
  // §84: Decrement aggressive camp suppression every tick.
  if (self._aggCampSuppress > 0) self._aggCampSuppress--
  // §117: Decrement the mode-2 post-timeout re-commit suppress every tick.
  if (self._suicideStandSuppress > 0) self._suicideStandSuppress--

  // ---- M6: Cooldown-aware firing ----
  // In 'bulletCap' mode (classic FC), the engine gates fire by on-screen
  // bullet count, NOT by a time cooldown. The AI must mirror this:
  // "on cooldown" means the player's bullet is still in flight (cap
  // reached), not that a timer hasn't elapsed. Using the time check here
  // would suppress fire for ~1.3s after each shot even though the engine
  // allows refire the instant the previous bullet resolves — this was the
  // #1 root cause of the AI's abysmal kill count (1-3 kills/game) in classic.
  let onCooldown: boolean
  if (w.rules.fireModel === 'bulletCap') {
    const cap =
      (w.rules.maxBullets['player'] ?? 1) +
      ((p.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
    let inFlight = 0
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (b.alive && b.ownerId === p.id) {
        if (++inFlight >= cap) break // early exit — cap reached
      }
    }
    onCooldown = inFlight >= cap
  } else {
    onCooldown = now - p.lastFire < p.nextFireInterval
  }

  // ---- S8: Freeze window — aggressive hunt mode ----
  const frozen = w.freezeTimer > 0

  // ---- S9: Shield — skip dodge but DON'T abandon defense ----
  // The 3-second respawn shield makes the player invulnerable, so dodge
  // is unnecessary. But the player must STILL defend the base — chasing
  // enemies across the map during the shield window leaves the base
  // undefended (the #1 cause of 330-tick base losses in classic).
  const shielded = (p.shieldTimer ?? 0) > 0

  // ---- S8: Set aggressive mode (freeze only, NOT shield) ----
  self.aggressive = frozen

  // ---- Scan for enemy targets (global vision, T9 priority) ----
  const aimDir = self.findEnemyDirection(pcx, pcy)

  // ---- Threat assessment (dodge incoming bullets) ----
  // Dodge FIRST: survive before defending the base.
  const threat = shielded ? null : self.findMostDangerousBullet(pcx, pcy)

  // M1 shell: reuse a per-self ctx buffer (AGENTS §14.2 — no per-tick
  // allocation). Built lazily on the first think of the first tick; fields
  // overwritten each tick. Candidates read it synchronously and never retain
  // it, so reuse is safe.
  let ctx = self._decisionCtx
  if (ctx) {
    ctx.w = w
    ctx.p = p
    ctx.pcx = pcx
    ctx.pcy = pcy
    ctx.onCooldown = onCooldown
    ctx.aimDir = aimDir
    ctx.threat = threat
    ctx.shielded = shielded
  } else {
    ctx = self._decisionCtx = { w, p, pcx, pcy, onCooldown, aimDir, threat, shielded }
  }
  // First commit wins. hunt is unconditional (always commits), so a null
  // return is impossible — but a defensive fallback keeps _moveDir/_fire/
  // _lastBranch from going stale if a candidate-set bug ever made every
  // candidate decline (DecisionCore.runChain doc).
  // M2: the chain runs in effective-weight order (pre-built per reset in
  // GodAIInput._orderedCandidates; default = the M1 chain order).
  if (!runChain(self, ctx, self._orderedCandidates)) HUNT.evaluate(self, ctx)
}
