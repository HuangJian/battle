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
import type { Tank } from '../../types'
import type { World } from '../../game/World'
import type { Cell } from '../../utils/pathfind'
import type { Direction } from '../../constants'
import { BASE_POS, CELL, DIR_VECTORS, GRID, BULLET, TANK } from '../../constants'
import { ALL_DIRS } from '../../utils/helpers'
import {
  scanAheadImpl,
  shouldFireBreakThroughImpl,
  aimSurvivesTurnImpl,
  shotReachesBaseImpl,
  enemyInShotCorridorImpl,
  shouldFireInDirImpl,
  bulletPathSteelBlockedImpl,
} from './FireControl'
import {
  dodgeCounterFireDirImpl,
  findBulletThreatToBaseImpl,
  bulletLaneClearImpl,
  playerFasterThanImpl,
  findCloseEnemyImpl,
  safePerpDodgeImpl,
  countAlignedEnemiesImpl,
} from './ThreatAssessor'
import {
  controlledLives,
  hasLethalBulletWithinWindowImpl,
  findSuicideTargetImpl,
  anyThreatPointEnemyImpl,
} from './SuicideReturn'
import { runChain, ACTION_WEIGHTS, type Candidate, type DecisionContext } from './DecisionCore'
import { contractStandingHold, enemyBulletOnRay, ownBulletOnRay } from './ActionContract'
import { survivalPressure, updateEnemyModel } from './EnemyModel'
import {
  enemyCanShootBase,
  enemyCanBreachRing,
  enemyApproachingBaseLaneImpl,
} from './SmartThreatModel'
import { iceGlideAdjust } from './Navigator'
import { isFieldRetreatConditionImpl } from './StrategyPlanner'
import { superItemPressesImpl } from './SuperItems'
import {
  carvePostImpl,
  carveThreatEnemyImpl,
  carvePathInfoCached,
  carveFireAheadImpl,
  findCarveEscapeImpl,
  findLaneDefensePointImpl,
  laneThreatImpl,
  laneShellInColumnImpl,
  laneShellAboveImpl,
  laneColumnOpenToBaseImpl,
  findParryHoldCellImpl,
  enemyNearLaneImpl,
  digPathInfoCached,
} from './PathCarve'

// ===========================================================================
// Candidates — verbatim branch transcriptions. One object per action; the
// shell evaluates them strictly in weight order (chain order), first commit
// wins. Each evaluate() returns true exactly when the original branch would
// have `return`ed from the top-level chain.
// ===========================================================================

/**
 * §178 (autopsy hard-s34-base-l3-t25-seed2): dual central-breach P1 central-
 * hold gate. When true, this tank is P1 in a dual central-breach stage and
 * should behave as a PURE defender — hold the central anchor (§178), snipe the
 * col-12 spawn lane, and fire at enemies, but NEVER divert to grab power-ups
 * (P2 is the free tank that handles items: fence/bomb/star/shield). This is
 * the "sticky hold" that keeps P1 from wandering to top-row power-ups and
 * abandoning the base while it is dug out from below. Gated by spectateDual &&
 * centralBreachRisk && !isPlayer2 && dualCentralBreachP1HoldSticky>0 — single-
 * player and P2 stay byte-identical.
 */
function isDualCentralBreachHoldP1(self: GodAIInput): boolean {
  return (
    self.world.spectateDual &&
    self._centralBreachRisk &&
    !self.isPlayer2() &&
    self.params.dualCentralBreachP1HoldSticky > 0
  )
}

/** §152-W2: map-center escape target for the aggressive movement-stuck guard. */
const MAP_CENTER: Cell = { col: 12, row: 12 }

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

/**
 * §146 C: fieldRetreatPickupGate — HIGH-tier pickup gate. When the M13
 * field-pressure retreat condition holds (far from base + full enemy field,
 * base NOT under threat), the HIGH-tier urgent pickup (bomb/freeze/fence)
 * must NOT hijack the retreat: the pickup branch (800) evaluates before hunt
 * (200), so M13's return to the defense position never runs while an item
 * sits within divert range (S8: the player stayed in the dead-end pocket 43%
 * of loss-ending time while the base fell). Same predicate as
 * selectTargetUncached's M13 block (isFieldRetreatConditionImpl — single
 * source of truth). The item may still be picked on the way back (S5) or
 * after the retreat.
 *
 * SCOPE (A/B-measured 2026-08-05, §147): HIGH tier ONLY. Extending the gate
 * to MID (star/tank/shield) + LOW (S5 opportunistic) was measured NET NEGATIVE
 * — chaos aggregate −1.3pp (S12 −9pp / S4 −9pp / S15 −7pp / S34 −7pp) AND
 * hard per-stage dips to −11pp (S10 −11pp / S7 −7pp / S11 −6pp / S14 −6pp,
 * hard aggregate ~flat only because gains offset losses). MID items are the
 * permanent-DPS economy core and LOW pickups out-value the retreat under
 * pressure; suppressing them loses more than the retreat gains. HIGH tier
 * (bomb/freeze/fence) is the S8-measured hijack class and stays gated.
 * 0 = OFF (byte-identical).
 */
function retreatGateBlocksPickup(self: GodAIInput): boolean {
  if (self.params.fieldRetreatPickupGate <= 0) return false
  const pcC = self.playerCell()
  const distC = Math.abs(pcC.col - BASE_POS.col) + Math.abs(pcC.row - BASE_POS.row)
  return isFieldRetreatConditionImpl(self, self.isBaseUnderThreat(), distC, self._enemies.length)
}

// ================================================================
// §X Base lane sentry — 基地车道哨兵
// ================================================================

/** §X 原语: 8 个基地保护环格是否已有被击穿的洞（任一非砖/钢）。
 * 几何与 SimulationCombat.isBaseProtectionCell 一致（row 23 × cols 11-14，
 *  cols 11/14 × rows 24-25）。钢环（classic 某些关）= 永不击穿 → 哨兵自关。 */
function baseRingBreachedImpl(w: World): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const g = w.tileMap.grid
  const hole = (c: number, r: number) => g[r][c] !== 'brick' && g[r][c] !== 'steel'
  return (
    hole(bc - 1, br - 1) ||
    hole(bc, br - 1) ||
    hole(bc + 1, br - 1) ||
    hole(bc + 2, br - 1) ||
    hole(bc - 1, br) ||
    hole(bc - 1, br + 1) ||
    hole(bc + 2, br) ||
    hole(bc + 2, br + 1)
  )
}

/** §X 原语: 格对齐走廊检查 — (c,r)→(tc,tr) 必须同排或同列，两格之间逐格扫描。
 * 返回 0 = 走廊全通；>0 = 距 (c,r) 第 n 格（1 起）被非空地形挡住
 * （'base' 亦计 — 永不穿基地射击）。非对齐返回 -1。 */
function laneCorridorBlocked(w: World, c: number, r: number, tc: number, tr: number): number {
  const g = w.tileMap.grid
  if (c === tc) {
    if (r === tr) return 0
    if (r < 0 || r >= GRID || tr < 0 || tr >= GRID || c < 0 || c >= GRID) return 999
    const step = r < tr ? 1 : -1
    for (let rr = r + step; rr !== tr; rr += step) {
      if (g[rr][c] !== 'empty') return rr < r ? r - rr : rr - r
    }
    return 0
  }
  if (r === tr) {
    if (c === tc) return 0
    if (r < 0 || r >= GRID || c < 0 || c >= GRID || tc < 0 || tc >= GRID) return 999
    const step = c < tc ? 1 : -1
    for (let cc = c + step; cc !== tc; cc += step) {
      if (g[r][cc] !== 'empty') return cc < c ? c - cc : cc - c
    }
    return 0
  }
  return -1
}

/**
 * §X baseLaneSentry(850) — 基地车道哨兵: 基地危局下「走到车道、持位击杀」。
 *
 * 来源（Battlement hard seed 14 弹道级还原）：拆环 fast 在 (16,25)↔(15,25)
 * 口袋横走、hp 24（一枪线），玩家在 (16,21) 与其同列 40 ticks — 但唯一一枪
 * 从 (16,23) 砖格内发射被墙吃掉（双偏线像素扫描看见敌人边缘、真实子弹中线
 * 打墙），800ms 冷却后才恢复开火而敌人已转身逃离；随即 midLaneDefense(545)
 * 把玩家拖去中路横向火力送死（43hp 死于 t2255，重生空隙基地连受 3 发）。
 * 46/60 败局（base_destroyed 100%）同一 archetype 复现。
 *
 * 与 §134 defenseIntercept(550) 的本质区别：
 *   1. 拦截不动位 — 本候选不对齐时**主动导航到对齐站位**（pickSentryStandImpl）。
 *   2. 双偏线像点扫描的“可见”幻觉 — 本候选以**格对齐走廊**（laneCorridorBlocked，
 *      真实子弹中线）判定射界；单层砖挡则打砖开路（diggable，下一轮窗口生效）。
 *   3. 850 权重压掉 pickupHigh(800)/aggro(700)/midLane(545)/closePickup(540)/
 *      engage(500) — 威胁成立时整体锁定直到车道敌人被处理（dodge 1000 /
 *      interceptBase 900 仍在上方：生存与子弹拦截优先）。
 *
 * 门控：baseLaneSentryMode=0 短路（byte-identical）；无基地关；aggro 期让路。
 * 泛化：环格几何、csb/cbr 谓词、车道走廊全部只依赖 BASE_POS —— 任何带基地
 * 的地图通用；钢环/无拆环风险时哨兵永不激活（自关）。
 */
const BASE_LANE_SENTRY: Candidate = {
  id: 'baseLaneSentry',
  weight: ACTION_WEIGHTS.baseLaneSentry,
  evaluate(self, ctx) {
    const { w, p, pcx, pcy, onCooldown } = ctx
    const prm = self.params
    if (prm.baseLaneSentryMode <= 0 || !self.hasBase || self.aggressive) return false
    // 危局态触发: 环砖已被拆（通道洞开，口袋司机变为致命威胁）。
    const ringBreached = baseRingBreachedImpl(w)
    // 选目标: csb（下一发毁基地，地图任何位置都是第一优先）> cbr（下一发拆环，
    // 谓词自身即近基地带）> 口袋司机（环已破且敌人在 rows 23-25 / cols base±6 带）。
    const list = self._enemies.length > 0 ? self._enemies : w.tanks
    let best: Tank | null = null
    let bestScore = Infinity
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
      if (!t.alive || t.spawnTimer > 0) continue
      const toc = self.tankCell(t)
      const dc = Math.abs(toc.col - BASE_POS.col)
      const dr = Math.abs(toc.row - BASE_POS.row)
      const csb = enemyCanShootBase(self, t)
      const cbr = !csb && enemyCanBreachRing(self, t)
      const inBand = toc.row >= BASE_POS.row - 1 && dc <= 6
      if (!csb && !cbr && !(ringBreached && inBand)) continue
      const score = csb ? dc + dr : cbr ? 100 + dc + dr : 200 + dc + dr
      if (score < bestScore) {
        bestScore = score
        best = t
      }
    }
    if (!best) return false
    const tc = self.tankCell(best)
    const pc = self.playerCell()
    const ccol = pc.col
    const crow = pc.row
    const aligned = ccol === tc.col || crow === tc.row
    const blocked = laneCorridorBlocked(w, ccol, crow, tc.col, tc.row)
    const manhattan = Math.abs(ccol - tc.col) + Math.abs(crow - tc.row)
    const bestCsb = enemyCanShootBase(self, best)
    if (aligned && (blocked === 0 || (blocked === 1 && !bestCsb))) {
      // 对齐且中线畅通（或仅一层砖挡且敌人不是即刻杀手 — 原位打砖开路，
      // 下一轮车道窗口击杀；csb 敌人则绝不浪费弹药，交给导航换位）。
      const dir: Direction =
        ccol === tc.col ? (tc.row > crow ? 'down' : 'up') : tc.col > ccol ? 'right' : 'left'
      // 自射基地守卫（与 §134/ENGAGE 同源 — 绝不穿基地开火）。
      if (
        prm.selfFireBaseGuard > 0 &&
        shotReachesBaseImpl(self, pcx, pcy, dir) &&
        (prm.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, dir))
      ) {
        return false
      }
      if (blocked === 0) {
        // 中线畅通 — 持位射击: 立定向目标翻转 + 开火。仅在**能发射的 tick**
        // 接管（onCooldown 不 claim — 冷却期交给 midLane/navigate 正常流动，
        // 站位由流动保持或改善；冷却一过若仍对齐则哨兵再次接管开火）。
        if (manhattan > prm.baseLaneSentryRange) return false
        if (onCooldown) return false
        self._moveDir = p.dir === dir ? null : dir
        self._fire = !onCooldown && self.rng.next() >= self.params.aimError
        self.branchCounts.baseLaneSentry++
        self._lastBranch = 'baseLaneSentry'
        return true
      }
      // 单层砖挡（相邻格）且非即刻杀手 — 原位打砖开路。本 tick 打砖不可行
      // （shouldFireInDir 拒绝 — 如钢墙/自射守卫）则立刻让位，绝不空持死锁：
      // 正常流动（midLane/navigate）会移动玩家，几何改善后哨兵再接管。
      if (blocked === 1 && !bestCsb && shouldFireInDirImpl(self, pcx, pcy, dir)) {
        if (manhattan > prm.baseLaneSentryRange) return false
        // Phase 2 §6.1 行动有效性契约: 站立 + 冷却中的无产出站桩先过契约
        // （敌弹在射线 / 自己子弹在飞 / 站桩击杀 killSlack>0），否则让位。
        // mode=0 短路 → byte-identical。
        if (
          prm.actionContractMode > 0 &&
          p.dir === dir &&
          onCooldown &&
          !contractStandingHold({
            world: w,
            player: p,
            threat: best,
            enemyBulletOnRay: enemyBulletOnRay(w, p, ccol === tc.col),
            ownBulletOnRay: ownBulletOnRay(w, p, ccol === tc.col),
          }).valid
        ) {
          return false
        }
        self._moveDir = p.dir === dir ? null : dir
        self._fire = !onCooldown
        self.branchCounts.baseLaneSentry++
        self._lastBranch = 'baseLaneSentry'
        return true
      }
      return false
    }
    // §192 v6: 卫位导航（station-approach）— 非对齐或中线被挡时（含 dig 不可行、
    // csb 敌人不给挖等让位情形），走向相邻「清晰射击列」站位：站台 = 敌人列
    // ±1 列、玩家当前行、竖直到敌人行无砖、站台格可站。到达后由上方对齐开火
    // 接管（口袋 fast 横穿站台列时击杀）。仅限基地带内近距（manhattan ≤
    // range+1，敌列距 base ≤ 6）——绝不跨图劫持；威胁消失/敌人离开即让位。
    if (prm.baseLaneSentryStation > 0 && !bestCsb && manhattan <= prm.baseLaneSentryRange + 1) {
      const tdc = Math.abs(tc.col - BASE_POS.col)
      // 仅拦截「带外」敌人（row 20-22，即将进带）：敌人已入带（row ≥ 23）
      // 时基地受威胁，须下行堵口而非横向挪位（seed 51 实证：fast 已到
      // (7,23) 时站台步把下行回防劫持成左移，基地被打爆）。
      if (tdc <= 6 && tc.row >= BASE_POS.row - 4 && tc.row < BASE_POS.row - 1) {
        // §198 门槛 (5)（chaos S34 seed 7 铁证 t2052）：带内（row ≥ 23）
        // 已有敌人时站台让位 — 玩家应留在带内处理防线威胁（selectTarget
        // 正在击杀 (6,24) 低血 fast），横向挪去带外目标会放弃基地防线。
        // 玩家带内击杀进行中不得被站台导航劫持。
        // 注意：tankCellImpl 写入共享 _tankCellBuf — 循环内每次调用都会
        // 覆盖外层 tc 的引用内容，必须先快照（Navigator.ts 契约）。
        const bestCol = tc.col
        const bestRow = tc.row
        let inBandThreat = false
        for (let li = 0; li < list.length; li++) {
          const t = list[li]
          if (!t.alive || t.spawnTimer > 0 || t === best) continue
          const toc = self.tankCell(t)
          if (toc.row >= BASE_POS.row - 1 && Math.abs(toc.col - BASE_POS.col) <= 6) {
            inBandThreat = true
            break
          }
        }
        if (inBandThreat) return false
        // §198 门槛 (6)（chaos S34 seed 7 铁证 t2136）：玩家在目标下方
        // （crow > tc.row）且面向上行、row 差 ≤ 3 → 站台步多余 — 玩家
        // 两格即达目标行，横向挪位反而拖慢（该局殊途同归却因 14-tick
        // 延迟 RNG 蝴蝶翻局 stageclear→gameover）。
        if (crow > bestRow && crow - bestRow <= 3 && p.dir === 'up') return false
        // 玩家须已在带内（row ≥ 21）：带外下行回防中的玩家不得被拽横移
        // （seed 32 实证：玩家 (11,18) 下行被拽去 col 12，回防延迟致败）。
        if (crow < BASE_POS.row - 3) return false
        // 玩家列与目标列差 ≤ 1 → 玩家已在拦截列/目标即将入列，站台步
        // 纯属多余（seed 25 实证：idle 防守位被拖走导致败局）。
        const colGap = bestCol > ccol ? bestCol - ccol : ccol - bestCol
        if (colGap <= 1) return false
        // 目标横向移动（left/right）且与玩家同行 → 目标将横穿玩家所在行，
        // 守株待兔即可（seed 53 实证：fast 横穿玩家行时换列迎击错过窗口）。
        const targetDir = best ? best.dir : ''
        const rowGap = bestRow > crow ? bestRow - crow : crow - bestRow
        if ((targetDir === 'left' || targetDir === 'right') && rowGap <= 1) return false
        // 站台列 = 目标列 ±1（就近优先，距离限 2）：差 2 时玩家恰好能
        // 跨一列拦截（seed 17 实证：玩家 (13,21) 对目标 (11,20) 下行走
        // 近，换到 col 12 拦截即击杀）；差 ≥ 4 时距离限自然放弃（seed
        // 53 实证：fast 横移中远距换列纯属赶路）。不追踪敌列 ± 差≤1
        // 跳过共同防横向振荡（seed 32 实证）。
        const cands: number[] = []
        const d1 = bestCol - 1
        const d2 = bestCol + 1
        const dd1 = d1 > ccol ? d1 - ccol : ccol - d1
        const dd2 = d2 > ccol ? d2 - ccol : ccol - d2
        if (dd1 <= dd2) {
          cands.push(d1, d2)
        } else {
          cands.push(d2, d1)
        }
        for (let ci = 0; ci < cands.length; ci++) {
          const sc = cands[ci]
          if (sc < 0 || sc >= GRID || sc === ccol) continue
          if (sc > ccol ? sc - ccol > 2 : ccol - sc > 2) continue
          if (w.tileMap.get(sc, crow) !== 'empty') continue
          if (laneCorridorBlocked(w, sc, crow, sc, bestRow) !== 0) continue
          self._moveDir = sc > ccol ? 'right' : 'left'
          self._fire = false
          self.branchCounts.baseLaneSentry++
          self._lastBranch = 'baseLaneSentry'
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
            self.params.powerupStuckTicks > 0 &&
            self._digBlockTicks >= self.params.powerupStuckTicks
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
  },
}

/** aggro(700) — S8/S9 freeze/shield window: stop-and-aim → power-up → navigate. */
const AGGRO: Candidate = {
  id: 'aggro',
  weight: ACTION_WEIGHTS.aggro,
  evaluate(self, ctx) {
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
        const freezeTarget = self.findFreezePickupTarget(pcx, pcy)
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
      // §80: `aimSurvivesTurnImpl` MUST be evaluated BEFORE `scanAheadImpl`
      // below — both write into the shared `self._scanResult`, so running the
      // guard afterwards would clobber `aggScan`. The `&&` short-circuit gives
      // us that ordering for free. When the guard rejects the aim (the turn's
      // grid-snap would shove the tank off the firing line) we fall through to
      // the navigate path, which has real stall detection — this is what
      // breaks the period-2 freeze-window deadlock.
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
        self._aggCampSuppress <= 0 &&
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
        const aggFireBlocked =
          self.params.selfFireBaseGuard > 0 &&
          shotReachesBaseImpl(self, pcx, pcy, aimDir) &&
          (self.params.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, aimDir))
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
        // §186: Skip powerup when pixel-stuck — the A* path to the
        // powerup is blocked/unreachable, and returning true here blocks
        // the nav-stuck escape below (line 721).
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
        let escape152 = self._aggNavSuppress > 0
        if (!escape152) {
          const pc152 = self.playerCell()
          if (
            self._aggNavStuckCell &&
            Math.abs(self._aggNavStuckCell.col - pc152.col) <= 1 &&
            Math.abs(self._aggNavStuckCell.row - pc152.row) <= 1
          ) {
            self._aggNavStuckTicks++
            if (w.killCount !== self._aggNavKillsAtStart) {
              self._aggNavStuckTicks = 1
              self._aggNavKillsAtStart = w.killCount
            }
          } else {
            self._aggNavStuckCell = { col: pc152.col, row: pc152.row }
            self._aggNavStuckTicks = 1
            self._aggNavKillsAtStart = w.killCount
          }
          if (
            self._aggNavStuckTicks > self.params.aggNavStuckTicks &&
            w.killCount === self._aggNavKillsAtStart
          ) {
            self._aggNavStuckCell = null
            self._aggNavStuckTicks = 0
            self._aggNavSuppress = self.params.antiCampSuppressTicks
            escape152 = true
          }
        }
        if (escape152) {
          if (self._aggNavSuppress > 0) self._aggNavSuppress--
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
    if (self._aggCampCell) {
      self._aggCampCell = null
      self._aggCampTicks = 0
    }
    if (self._aggCampSuppress > 0) self._aggCampSuppress = 0
    return false
  },
}

/** closePickup(540) — §158: non-freeze close-range power-up pickup.
 *
 * When NOT in freeze/shield mode, if a power-up is within `closePickupRange`
 * (default 4) cells and there is no immediate bullet threat (DODGE at weight
 * 1000 already declined — if it hadn't, this candidate would never run),
 * navigate to pick it up while firing at enemies in the move direction
 * (随手开火).
 *
 * Weight 540 < DEFENSE_INTERCEPT(550): defense intercept runs first when an
 * enemy is aligned with or approaching the base lane. This prevents the
 * seed-999 regression where the player diverted to a nearby power-up while
 * an enemy was breaking through to the base lane, arriving too late to
 * intercept. The isBaseUnderThreat guard still catches aligned enemies.
 *
 * Unlike PICKUP_HIGH/MID (which gate on nearby-enemy proximity and route
 * danger), this candidate has NO enemy gates — close items are worth
 * grabbing even with enemies nearby, as long as no bullet is currently
 * threatening the player AND no defense intercept is needed.
 *
 * Gated by closePickupRange (0 = OFF, byte-identical). */
const CLOSE_PICKUP: Candidate = {
  id: 'closePickup',
  weight: ACTION_WEIGHTS.closePickup,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    // §178: dual central-breach P1 — pure defender, never diverts to power-ups.
    if (isDualCentralBreachHoldP1(self)) return false
    if (self.aggressive) return false
    if (self.params.closePickupRange <= 0) return false
    // Skip when base is under threat — defense outranks a nearby item.
    if (self.hasBase && self.isBaseUnderThreat()) return false
    const target = self.findClosePickupTarget(pcx, pcy)
    if (!target) return false
    // §186: Skip when pixel-stuck — the powerup is unreachable.
    if (self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks)
      return false
    self._moveDir = self.navigateTowards(target)
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    self.branchCounts.powerup++
    self._lastBranch = 'powerup'
    return true
  },
}

/** pickupMid(600) — §88 MID-tier urgent pickup (star/tank/shield ≤4格). */
const PICKUP_MID: Candidate = {
  id: 'pickupMid',
  weight: ACTION_WEIGHTS.pickupMid,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    // §178: dual central-breach P1 — pure defender, never diverts to power-ups.
    if (isDualCentralBreachHoldP1(self)) return false
    // Per the §88 rule-4 chain, MID-tier pickups outrank 据守咽喉要地. The HIGH
    // tier (bomb/freeze/fence) was already checked before the aggressive
    // section. Only runs when chokepointMode > 0; otherwise the single §87
    // branch above handled all tiers (byte-identical).
    // §146 C: MID tier is deliberately NOT gated by fieldRetreatPickupGate —
    // extending the gate to MID/LOW was A/B-measured net negative on chaos
    // (§147, see retreatGateBlocksPickup scope note).
    if (self.params.chokepointMode > 0 && !self.aggressive && self.params.pickupPriorityMode > 0) {
      // §152-W3: commit-persistent lookup (see PICKUP_HIGH) — the W3
      // oscillation was driven by this branch (the decoy at (21,14) sat
      // exactly at the mid-range boundary).
      const midTarget = self.findUrgentPowerUpTargetWithCommit(pcx, pcy, 'midlow')
      if (midTarget) {
        // §186: Skip when pixel-stuck — the powerup is unreachable.
        const puStuck =
          self.params.powerupStuckTicks > 0 && self._digBlockTicks >= self.params.powerupStuckTicks
        if (!puStuck) {
          self._moveDir = self.navigateTowards(midTarget)
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
        // Phase 2 §6.1 行动有效性契约: 站立（已面向）+ 冷却中的提交 = 无产出
        // 站桩（M0 台账 no_output_commit 60% 失败族）。mode>0 时先过契约 —
        // 敌弹在射线 / 自己子弹在飞 / 站桩击杀 killSlack>0 才允许原地等待，
        // 否则放弃本分支（fall through 到 engage/hunt/navigate 产生输出）。
        // 绝不否决有移动/有开火的提交（§199 弃守教训）。mode=0 短路 → byte-identical。
        if (
          prm.actionContractMode > 0 &&
          p.dir === dir &&
          onCooldown &&
          !contractStandingHold({
            world: w,
            player: p,
            threat: t,
            enemyBulletOnRay: enemyBulletOnRay(w, p, dCol === 0),
            ownBulletOnRay: ownBulletOnRay(w, p, dCol === 0),
          }).valid
        ) {
          continue
        }
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
        if (
          prm.actionContractMode > 0 &&
          p.dir === dir &&
          onCooldown &&
          !contractStandingHold({
            world: w,
            player: p,
            threat: t,
            enemyBulletOnRay: enemyBulletOnRay(w, p, dCol === 0),
            ownBulletOnRay: ownBulletOnRay(w, p, dCol === 0),
          }).valid
        ) {
          continue
        }
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

/**
 * midLaneDefense(545) — §163 / 中路防守 (user request 2026-08-06, replay
 * hard-s34-base-l2-t69-seed2050197249 Problem 2).
 *
 * 背景：基地所在列（BASE_POS.col..+1 正上方）多数地图没有钢铁防护，敌人只要
 * 进入该列就能沿列向下流弹凿穿砖墙直逼老鹰（回放：baseCol 20→0，28 秒凿穿），
 * 而玩家往往在边路/出生点游荡击杀。
 *
 * 行为（全参数门控，midLaneDefense=0 默认 OFF → byte-identical）：
 *   1. 锚定：玩家到基地列上方的可站防守点（findLaneDefensePointImpl，开路
 *      A* 挖过去——出生点被封时同 §162 carve-dig 挖通）。
 *   2. 持枪（midLaneHoldRange 内）：面向列上方停射——scan 到敌人/敌弹就开火
 *      对消（shouldFireInDir 含 T5 拦截），不再追击边路。
 *   3. 牵绳（midLaneMaxDist）：近基才锚定，超距即回撤，随时准备回防；
 *   4. 中路无威胁且玩家已在 leash 内 → return false（放行 hunt/engage）。
 *
 * 权重 545：defenseIntercept(550) 之下（已上车道的敌人由拦截一枪解除）、
 * closePickup(540) 之上（防守不被顺手拾取打断）。
 */
const MID_LANE_DEFENSE: Candidate = {
  id: 'midLaneDefense',
  weight: ACTION_WEIGHTS.midLaneDefense,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    const prm = self.params
    if (prm.midLaneDefense <= 0 || !self.hasBase || self.aggressive) return false
    const pc = self.playerCell()
    // The ONLY trigger is a real enemy bullet in the base column heading
    // down (bullet-bullet collision cancels it 对消). Enemy presence/facing
    // signals were A/B-measured catastrophic (14-35% of ticks on most maps
    // → player statue, 29/35 worse): enemies merely PASSING the base column
    // is not a threat. Bullets are the actual carve moment — rare, precise.
    //
    // §164: midLaneStickyTicks — a drill bullet dies on a brick after
    // 10-60 ticks, leaving 70-130 tick gaps where laneThreatImpl is false
    // and the player releases mid-walk, never reaching the lane point (S8
    // drill chain: ring bricks fall at t1305, the next bullet has a clear
    // 71-tick lane, player 6+ cells away loses the race). Once a lane shell
    // is seen, keep the candidate engaged for midLaneStickyTicks so the
    // player commits to the walk and holds through the whole drill.
    if (!laneThreatImpl(self)) {
      if (prm.midLaneStickyTicks <= 0 || self._midLaneStickyHold <= 0) return false
    } else if (prm.midLaneStickyTicks > 0) {
      self._midLaneStickyHold = prm.midLaneStickyTicks
    }

    // ---- 换持枪判定 (Tuning round 3): the hold does NOT require being at
    // the lane defense point. 对消 (bullet-bullet cancellation) needs the
    // player's UPWARD bullet to physically overlap the shell's line: both
    // bullets are 6px wide, so |bx − pcx| < 6 or they never meet. Standing
    // still in the 32px column matches a random shell only ~37% of the time
    // — so the player must WALK the column, searching for a shell line to
    // lock. When a cancellable shell is in the column (laneThreatImpl), the
    // player: (a) if aligned (|bx−pcx| < 6) → hold & fire UP to cancel;
    // (b) if a shell line is LEFT/RIGHT of the player within the column →
    // step sideways to acquire it, firing meanwhile; (c) else navigate to
    // the lane point and hold. This is the actual Battlement-independent
    // mechanism — the old point-only hold could never fire on maps where
    // the point sits inside a sealed fortress (Battlement (12,21)).
    // --- 换持枪判定 round 3: acquire the shell's bullet line. ---
    const shellOff = laneShellAboveImpl(self, pcx, pcy)
    if (shellOff !== null) {
      const absOff = Math.abs(shellOff)
      if (absOff < BULLET) {
        // Aligned with a coming shell — hold in place, face up, fire to
        // cancel. The shell is the target; fire whenever the gun is ready.
        const laneDir: Direction = 'up'
        self._moveDir = p.dir === laneDir ? null : laneDir
        self._fire = !onCooldown
        self.branchCounts.midLaneDefense++
        self._lastBranch = 'midLaneDefense'
        return true
      }
      if (absOff <= TANK) {
        // A shell line is one side-step away — acquire it: step toward the
        // offset. NOTE: _fire stays FALSE here — the engine fires along
        // tank.dir (= the step direction), so an up-lane suppression shot is
        // impossible while stepping sideways; firing would waste the bullet
        // cap on a horizontal shot. Pure reposition; the aligned-hold branch
        // above does the actual 对消. Keeps the player hunting the shell
        // line instead of standing at the point where a random shell matches
        // only ~37% of the time.
        const stepDir: Direction = shellOff > 0 ? 'right' : 'left'
        self._moveDir = stepDir
        self._fire = false
        self.branchCounts.midLaneDefense++
        self._lastBranch = 'midLaneDefense'
        return true
      }
      // Shell line too far sideways to acquire by stepping — fall through to
      // the lane point, which is at least inside the column.
    }

    const point = findLaneDefensePointImpl(self, pc)
    if (!point) return false
    const distToPoint = Math.abs(point.col - pc.col) + Math.abs(point.row - pc.row)
    const inHold = distToPoint <= prm.midLaneHoldRange

    // Leash: only engage when the player is NEAR the lane point. Pulling
    // from across the map is a cross-map tug-of-war with HUNT (§163 A/B:
    // Battlement pocket escape → lane point inside the sealed pocket is an
    // 8-step dig the player just escaped — dragging it back lost 20→16
    // kills). The user spec: 不能偏离中路防守点太远 (<3 cells).
    if (distToPoint > prm.midLaneMaxDist) return false

    // 2. In hold range → stand and fire up the lane. Fire when a shell is
    //    ANYWHERE in the base column (laneShellInColumnImpl — the 128px T5
    //    intercept range can't see shells 16 cells up the column), or when
    //    a normal enemy/target is in the line (shouldFireInDir).
    if (inHold) {
      const laneDir: Direction = 'up'
      self._moveDir = p.dir === laneDir ? null : laneDir
      self._fire =
        !onCooldown && (laneShellInColumnImpl(self) || self.shouldFireInDir(pcx, pcy, laneDir))
      self.branchCounts.midLaneDefense++
      self._lastBranch = 'midLaneDefense'
      return true
    }

    // 1/3. Out of hold range but within the leash → navigate to the point.
    //      Require the point be corridor-reachable or a SHORT dig (≤3
    //      cells) — a long dig through the sealed pocket is self-defeating
    //      (the player already escaped it); let HUNT fight the field and the
    //      §162 carve-dig handle pocket exits.
    const info = carvePathInfoCached(self, pc, point)
    const dig = info.path
    if (dig && dig.length > 0) {
      if (!info.corridor && dig.length > prm.midLaneMaxDigCells) return false
      const d = dig[0]
      self._moveDir = d
      if (self.canMoveDir(p, d)) {
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, d)
      } else if (carveFireAheadImpl(self, pcx, pcy, d)) {
        self._fire = !onCooldown
      } else {
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, d)
      }
      self.branchCounts.midLaneDefense++
      self._lastBranch = 'midLaneDefense'
      return true
    }
    // No carve path (rare — point unreachable) — plain navigation.
    self._moveDir = self.navigateTowards(point)
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    self.branchCounts.midLaneDefense++
    self._lastBranch = 'midLaneDefense'
    return true
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
    // §159: Override — don't skip when a CLOSE enemy is in the line of fire.
    // A close kill takes 1–2 shots (a few ticks) and directly helps defense.
    // Root cause: hard S20 Bastion, player 1 cell past the threshold with an
    // armor enemy 2 cells left → 160+ tick up/down oscillation, zero fire.
    let skipT2aForDefense =
      self.hasBase &&
      self.isBaseUnderThreat() &&
      Math.abs(self.playerCell().col - BASE_POS.col) +
        Math.abs(self.playerCell().row - BASE_POS.row) >
        self.params.maxPlayerDistFromBase

    // §6.3-D: Dual central breach — P2 is the free tank (flanker/pickup).
    // Don't force P2 to skip T2a when base is threatened — P1 holds the
    // anchor and handles base defense. P2 should be free to engage close
    // enemies it encounters while roaming. Gated by dualStrategyActive
    // (spectateDual || coop) && centralBreachRisk && isPlayer2 — P1 and
    // single-player byte-identical.
    if (skipT2aForDefense && self.dualStrategyActive && self.isPlayer2()) {
      skipT2aForDefense = false
    }

    if (skipT2aForDefense && aimDir && self.params.t2aDefenseOverrideRange > 0) {
      // Distance guard: only override when the player is slightly past the
      // threshold (within t2aDefenseOverrideRange cells past it). Far from
      // the base, even a quick kill takes too long — the base falls while
      // the player is engaged. This guard eliminates the Iron Curtain /
      // Quarry regressions (player 26+ cells from base stopped to engage).
      const overrideDist =
        Math.abs(self.playerCell().col - BASE_POS.col) +
        Math.abs(self.playerCell().row - BASE_POS.row)
      if (overrideDist <= self.params.maxPlayerDistFromBase + self.params.t2aDefenseOverrideRange) {
        // Reuses the per-tick scan memo (scanAheadImpl caches by origin+dir),
        // so the later scan in the engage body below is free.
        const defScan = scanAheadImpl(self, pcx, pcy, aimDir)
        if (defScan.enemy && defScan.enemyDist <= self.params.t2aDefenseOverrideRange) {
          skipT2aForDefense = false
        }
      }
    }

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
      // §152-W1: same as the aggressive branch — suppress the stop-and-aim
      // when the bullet's actual 6px path hits non-ring steel before the
      // enemy (the scan's offset lines can see the enemy while the center
      // line clips a steel column edge — hard S12 seed 934391936 W1).
      const steelPathBlocked152 =
        self.params.t2aSteelPathBlock > 0 &&
        bulletPathSteelBlockedImpl(self, pcx, pcy, aimDir, scan.enemyDist * CELL)
      if (
        scan.enemy &&
        !selfFireBlocked &&
        !steelPathBlocked152 &&
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
          // §165: T2a outnumbered retreat — when 2+ aligned enemies are within
          // t2aOutnumberedRange cells in the scan direction, a stationary T2a
          // duel is a losing trade (the 2nd enemy fires while the player is
          // locked aiming at the 1st). Fall through to navigate (which moves
          // to a safer angle or triggers P4.2 outnumbered retreat). 0 = OFF
          // (byte-identical). Only applies in pool model (classic instant
          // 1-HP has no grinding — a single shot kills, no trade gradient).
          const outgunned =
            self.params.t2aOutnumberedRetreat > 0 &&
            w.rules.combatModel === 'pool' &&
            countAlignedEnemiesImpl(self, pcx, pcy, aimDir, self.params.t2aOutnumberedRange) >=
              self.params.t2aOutnumberedCount
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

          if (!campedTooLong && !outgunned) {
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
          // §186: Skip when pixel-stuck — the powerup is unreachable.
          const puStuck =
            self.params.powerupStuckTicks > 0 &&
            self._digBlockTicks >= self.params.powerupStuckTicks
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
        navTarget = { col: 12, row: 12 }
      }
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

/**
 * §161 carve fire: when the next path step is blocked by a plain brick (the
 * dig path's current frontier), fire to break it — NEVER at steel (R5, even
 * when the player could pierce) and never at ring bricks (scanAhead's
 * baseWall flag, which under the hard default baseWallExactRing=1 is the
 * exact ring). Moving freely → fire at enemies in the facing direction
 * only (suppression, allowWallFire=false — side walls are not carved).
 */
function carveFire(self: GodAIInput, ctx: DecisionContext, dir: Direction | null): void {
  const { p, pcx, pcy, onCooldown } = ctx
  if (dir && !self.canMoveDir(p, dir)) {
    const bs = scanAheadImpl(self, pcx, pcy, dir)
    if (bs.wall && !bs.baseWall && !bs.baseSteel && !bs.steel) {
      self._fire = !onCooldown
      return
    }
  }
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir ?? p.dir, false)
}

/**
 * baseConnectClear(270) — §189 / 开局联通清墙 (user request 2026-08-11).
 *
 * At game start, the player observes the lower-half layout. If the player's
 * side of the base is NOT corridor-connected to the P2 spawn point (the
 * opposite side), the player proactively clears walls to reach it — going
 * AROUND the base ring, not through it. Once the corridor path is open,
 * the candidate stops and normal AI behavior takes over (出击/防守).
 *
 * The target is FIXED (P2 spawn point = 24 - P1 spawn col), not dynamic —
 * this ensures the player keeps carving toward the same goal regardless of
 * their current position, preventing the "reached base column → switched
 * target → went back" oscillation that the wing-anchor approach caused.
 *
 * Same hard constraints as §161 carve (R5/R6): never break steel, never
 * break base-ring bricks. The fire control (carveFire) already prevents
 * firing at ring/base walls. Reuses digPathInfoCached (findPath with
 * breakBrick + buildDigCosts) so A* routes AROUND the ring.
 * Runs only when baseConnectClearMode > 0 (default ON for hard/chaos).
 */
const BASE_CONNECT_CLEAR: Candidate = {
  id: 'baseConnectClear',
  weight: ACTION_WEIGHTS.baseConnectClear,
  evaluate(self, ctx) {
    const prm = self.params
    if (prm.baseConnectClearMode <= 0 || !self.hasBase) return false
    const pc = self.playerCell()
    // Lower-half gate — only clear walls in the lower half.
    if (pc.row < prm.baseConnectClearLowerRow) return false
    // Base under threat → defense candidates handle it. Do NOT reset the
    // travel flag — the player should resume traveling to the P2 spawn
    // after the threat is dealt with.
    if (self.isBaseUnderThreat()) return false

    // Fixed target: P2 spawn point (the opposite side of the base).
    const p2Col = self.world.player2SpawnPoint.col
    const p2Row = self.world.player2SpawnPoint.row
    // Arrived: player is within 2 cells of the P2 spawn. Reset the flag
    // and let normal AI take over (出击/防守).
    const distToP2 = Math.abs(pc.col - p2Col) + Math.abs(pc.row - p2Row)
    if (distToP2 <= 2) {
      self._baseConnectClearActive = false
      return false
    }

    const target: Cell = { col: p2Col, row: p2Row }
    const info = digPathInfoCached(self, pc, target)
    if (!info.path || info.path.length === 0) return false

    if (info.corridor) {
      // A corridor path exists. Only fire if we were previously carving
      // (travel mode) — the player needs to follow the opened corridor.
      // On stages where the corridor always existed, the flag is never set,
      // so the candidate never fires (no regression).
      if (!self._baseConnectClearActive) return false
    } else {
      // No corridor — carving needed. Activate travel mode.
      self._baseConnectClearActive = true
    }

    // Tick limit: bound the total active duration (carve + travel) so the
    // player eventually yields to combat even if they haven't reached P2.
    if (self._baseConnectClearActiveTicks >= prm.baseConnectClearMaxTicks) {
      self._baseConnectClearActive = false
      return false
    }
    self._baseConnectClearActiveTicks++

    // Follow the path toward the P2 spawn — carve walls if needed (dig path)
    // or navigate along the opened corridor.
    const dir = info.path[0]
    self._moveDir = dir
    carveFire(self, ctx, dir)
    self.branchCounts.baseConnectClear++
    self._lastBranch = 'baseConnectClear'
    return true
  },
}

/**
 * carvePath(250) — §161 / 开路策略 (carve path, user request 2026-08-06).
 *
 * R1/R2: when the spawn point is trapped in a brick maze and the standable
 * defense post (base guard anchor) is NOT smoothly reachable, shoot through
 * LOWER-HALF brick walls to carve a through-route to the post. R4: if a
 * smooth route exists, no carving. R5: never break steel (even when the
 * player could pierce it). R6: never break base-ring bricks; break at most
 * carveMaxBaseColumn bricks in the base's own columns when no alternative
 * exists. R3: once at the post with nothing fightable, carve toward the
 * enemy most likely to threaten the base. Data-driven — no stage names.
 * Runs only when carvePathMode > 0 (default OFF, byte-identical).
 */
const CARVE_PATH: Candidate = {
  id: 'carvePath',
  weight: ACTION_WEIGHTS.carvePath,
  evaluate(self, ctx) {
    const { w } = ctx
    const prm = self.params
    if (prm.carvePathMode <= 0 || self.aggressive || !self.hasBase) return false
    const pc = self.playerCell()
    // Lower-half gate (R1: 下半区开路).
    if (pc.row < prm.carveLowerRow) return false
    // Base under threat → the defense candidates / hunt's defense return
    // handle it; the carve is a calm-state reposition.
    if (self.isBaseUnderThreat()) return false

    const post = carvePostImpl(self)
    if (!post) return false
    const distToPost = Math.abs(pc.col - post.col) + Math.abs(pc.row - post.row)

    // ---- Mode B (R3): at the post, nothing fightable → dig toward the
    // most base-threatening enemy. ----
    if (distToPost <= prm.carveAtPostCells) {
      // Don't steal a close chase from hunt — a nearby enemy is faster
      // dealt with directly.
      const list = self._enemies.length > 0 ? self._enemies : w.tanks
      let closeEnemy = false
      for (let li = 0; li < list.length; li++) {
        const t = list[li]
        if (!t.alive || t.spawnTimer > 0) continue
        const tc = self.tankCell(t)
        if (Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row) <= prm.carveChaseCells) {
          closeEnemy = true
          break
        }
      }
      if (closeEnemy) return false
      const threat = carveThreatEnemyImpl(self)
      if (!threat) return false
      const info = carvePathInfoCached(self, pc, threat)
      if (!info.path || info.path.length === 0) return false
      const dir = info.path[0]
      self._moveDir = dir
      carveFire(self, ctx, dir)
      self.branchCounts.carvePath++
      self._lastBranch = 'carvePath'
      return true
    }

    // ---- Mode A (R1/R2/R4): no smooth route to the post → carve. ----
    const info = carvePathInfoCached(self, pc, post)
    if (!info.path || info.path.length === 0) {
      // The post is not carve-reachable from here — most often because the
      // spawn sits on the far side of the base ring from the defense post, so
      // every route to it would cross the ring (forbidden by R5/R6). R1/R2
      // still want the player to dig OUT of the sealed pocket: fall back to
      // carving toward the nearest carve-safe escape. This keeps §161 useful
      // on ring-fortified stages without ever breaking ring / base-column
      // bricks (the escape search is itself ring-safe — see findCarveEscapeImpl).
      const escape = findCarveEscapeImpl(self, pc)
      if (!escape) return false
      const einfo = carvePathInfoCached(self, pc, escape)
      // A smooth route to the escape means the player isn't boxed → let
      // navigate handle it (R4: no dig when a corridor exists).
      if (!einfo.path || einfo.path.length === 0 || einfo.corridor) return false
      const dir = einfo.path[0]
      self._moveDir = dir
      carveFire(self, ctx, dir)
      self.branchCounts.carvePath++
      self._lastBranch = 'carvePath'
      return true
    }
    if (info.corridor) return false // R4: 通畅路线 → 不打砖开路
    const dir = info.path[0]
    self._moveDir = dir
    carveFire(self, ctx, dir)
    self.branchCounts.carvePath++
    self._lastBranch = 'carvePath'
    return true
  },
}

/**
 * §164 中路列旁主动驻守 (proactive mid-lane flank hold) — 用户需求
 * 2026-08-06：§162 出袋后玩家优先走中路走廊（而非左侧），在列旁持枪对消。
 * 基地列无钢防时，顶部广场是基地凿穿弹的必经之路：本候选在玩家处于地图
 * 上半区（row ≤ midLaneHoldMaxRow）且中路繁忙（列内有敌弹，或敌人临近基地
 * 列）时，导航到/驻守基地列旁的对消格（pcx 在列 x 范围 ±BULLET 内、可站、
 * 走廊可达——findParryHoldCellImpl），面朝上开火对消。中路无威胁时
 * return false 放行 hunt/engage（击杀边路游荡敌人）；已在对消格且无威胁时
 * 同样放行（不钉子户）。
 *
 * 权重 220：carvePath(250) 之下、hunt(200) 之上 — 覆盖 hunt 的盲走，低于
 * 一切战斗/道具/瞭望格/开路候选；DODGE/ENGAGE/DEFENSE_INTERCEPT 全部高于
 * 它，危险时正常接战。默认 midLaneHold=0 OFF（byte-identical）。
 */
const MID_LANE_HOLD: Candidate = {
  id: 'midLaneHold',
  weight: ACTION_WEIGHTS.midLaneHold,
  evaluate(self, ctx) {
    const { p, pcx, pcy, onCooldown } = ctx
    const prm = self.params
    if (prm.midLaneHold <= 0 || !self.hasBase || self.aggressive) return false
    const pc = self.playerCell()
    // 上半区才锚定 — 永不在底部迷宫把玩家拽去中路（§162 出袋/§163 归档教训）。
    if (pc.row > prm.midLaneHoldMaxRow) return false
    // 列内有钢/水 → 敌弹到不了基地，对消无意义（S13 式钢防关直接跳过）。
    if (!laneColumnOpenToBaseImpl(self)) return false
    const hold = findParryHoldCellImpl(self, pc)
    if (!hold) return false
    const dist = Math.abs(hold.col - pc.col) + Math.abs(hold.row - pc.row)
    // 中路繁忙：列内有向下敌弹（§163 laneShellInColumnImpl — 真实凿穿信号），
    // 或敌人临近基地列（凿墙者即将到达）。两者皆无 = 中路无威胁 → 放行。
    const busy = laneShellInColumnImpl(self) || enemyNearLaneImpl(self, prm.midLaneHoldEnemyDist)
    if (dist === 0) {
      // 已在对消格上：无威胁则放行 hunt（击杀边路），有威胁则驻守向上对消。
      // 注意只允许 dist===0 — dist≤range(1) 时玩家还在邻格，强制面朝上会
      // 冻结在 (11,6) 永远进不了 (12,6)（§164 探针实测 stageclear→gameover）。
      if (!busy) return false
      const dir: Direction = 'up'
      self._moveDir = p.dir === dir ? null : dir
      self._fire =
        !onCooldown && (laneShellInColumnImpl(self) || self.shouldFireInDir(pcx, pcy, dir))
      self.branchCounts.midLaneHold++
      self._lastBranch = 'midLaneHold'
      return true
    }
    if (!busy) return false
    // 前往对消格（findParryHoldCellImpl 已保证走廊可达 — 顶部广场不打砖）。
    self._moveDir = self.navigateTowards(hold)
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
    self.branchCounts.midLaneHold++
    self._lastBranch = 'midLaneHold'
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
  // §X: 基地车道哨兵 — interceptBase(900) 之下、pickupHigh(800) 之上。
  BASE_LANE_SENTRY,
  PICKUP_HIGH,
  AGGRO,
  PICKUP_MID,
  DEFENSE_INTERCEPT,
  // §163: 中路防守 — defenseIntercept(550) 之下、closePickup(540) 之上。
  MID_LANE_DEFENSE,
  // §158: 非冰冻期近距离道具拾取 — defenseIntercept(550) 之下、engage(500) 之上。
  CLOSE_PICKUP,
  ENGAGE,
  PICKUP_LOW,
  FIRING_LANE,
  // §189: 开局联通清墙 — firingLane(300) 之下、carvePath(250) 之上.
  BASE_CONNECT_CLEAR,
  // §161: 开路策略 — firingLane(300) 之下、hunt(200) 之上.
  CARVE_PATH,
  // §164: 中路列旁主动驻守 — carvePath(250) 之下、hunt(200) 之上。
  MID_LANE_HOLD,
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
  let aimDir = self.findEnemyDirection(pcx, pcy)

  // §159: when the base is under threat and the player is past the defense
  // distance threshold, override aimDir to point at the CLOSEST enemy within
  // t2aDefenseOverrideRange cells (any direction) — but ONLY when the current
  // aimDir doesn't already have a close enemy. This ensures the engage branch
  // can fire at a close enemy even when findEnemyDirection picked a different
  // (higher-threat but farther) enemy, while avoiding unnecessary target
  // switching when the player is already aiming at a close enemy.
  // Root cause: hard S20 Bastion, aimDir flipped between 'left' (close armor)
  // and 'right' (far armor) due to sub-pixel alignment, preventing a stable
  // engage.
  if (self.params.t2aDefenseOverrideRange > 0 && self.hasBase && !frozen && !shielded) {
    // Only override when the player is past the defense threshold AND the
    // base is under threat (same condition as skipT2aForDefense).
    const pc159 = self.playerCell()
    const dist159 = Math.abs(pc159.col - BASE_POS.col) + Math.abs(pc159.row - BASE_POS.row)
    if (
      dist159 > self.params.maxPlayerDistFromBase &&
      dist159 <= self.params.maxPlayerDistFromBase + self.params.t2aDefenseOverrideRange &&
      self.isBaseUnderThreat()
    ) {
      // Check if the current aimDir already has a close enemy — if so, keep
      // it (avoid unnecessary target switching that caused regressions on
      // Iron Curtain / Quarry).
      let aimHasClose = false
      if (aimDir) {
        const aimScan = scanAheadImpl(self, pcx, pcy, aimDir)
        aimHasClose = aimScan.enemy && aimScan.enemyDist <= self.params.t2aDefenseOverrideRange
      }
      if (!aimHasClose) {
        let bestDir: Direction | null = null
        let bestDist = self.params.t2aDefenseOverrideRange + 1
        for (let di = 0; di < ALL_DIRS.length; di++) {
          const d = ALL_DIRS[di]
          const s = scanAheadImpl(self, pcx, pcy, d)
          if (s.enemy && s.enemyDist < bestDist) {
            bestDist = s.enemyDist
            bestDir = d
          }
        }
        if (bestDir) aimDir = bestDir
      }
    }
  }

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

  // §167 / B4: super-item press flags ride the same tick's decision context
  // (reactive gates only — never feed back into the chain above). The dead
  // branch returns early; endFrame() cleared the flags last tick, so they
  // stay false while dead.
  superItemPressesImpl(self, ctx)

  // ================================================================
  // 双玩家防堵车 (P1↔P2 yield) — 镜像守卫避让机制 (§159)。
  //
  // 当前进方向会被伙伴阻挡时, 尝试垂直避让; 无法避让则 P1 停止让 P2 先行
  // (避免死锁)。当 A* 无路径且伙伴极近时, P1 尝试任意可行方向脱困。
  // 纯 World 读取 — 无隐藏状态 (AGENTS §2.2)。
  // ================================================================
  if (self.hasLivingPartner()) {
    const partner = self.coopPartner()!
    if (self._moveDir !== null) {
      // Case 1: _moveDir is set — check if partner is in the forward cell
      const v = DIR_VECTORS[self._moveDir]
      const fx = p.x + v.dx * CELL
      const fy = p.y + v.dy * CELL
      if (
        fx < partner.x + partner.w &&
        fx + TANK > partner.x &&
        fy < partner.y + partner.h &&
        fy + TANK > partner.y
      ) {
        // Partner blocks forward — try perpendicular alternatives
        const perpA: Direction = self._moveDir === 'up' || self._moveDir === 'down' ? 'left' : 'up'
        const perpB: Direction =
          self._moveDir === 'up' || self._moveDir === 'down' ? 'right' : 'down'
        if (self.canMoveDir(p, perpA)) {
          self._moveDir = perpA
        } else if (self.canMoveDir(p, perpB)) {
          self._moveDir = perpB
        } else if (!self.isPlayer2()) {
          // P1 yields (stops); P2 keeps priority — prevents head-on deadlock
          self._moveDir = null
        }
      }
    } else if (!self.isPlayer2()) {
      // Case 2: _moveDir is null (A* failed) and partner is very close —
      // P1 tries any passable direction to break the deadlock
      const dist = Math.abs(p.x - partner.x) + Math.abs(p.y - partner.y)
      if (dist < TANK * 3) {
        for (let di = 0; di < ALL_DIRS.length; di++) {
          if (self.canMoveDir(p, ALL_DIRS[di])) {
            self._moveDir = ALL_DIRS[di]
            break
          }
        }
      }
    }
  }
}
