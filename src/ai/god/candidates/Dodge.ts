// candidates/Dodge.ts — the dodge candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { CELL } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { dodgeCounterFireDirImpl } from '../ThreatAssessor'
import { COUNTER_FIRE_RANGE_CELLS } from '../constants'
import { dodgeDirectionImpl } from '../ThreatAssessor'

export function evalDodge(self: GodAIInput, ctx: DecisionContext): boolean {
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
      if (dist <= COUNTER_FIRE_RANGE_CELLS * CELL) {
        // 安全门控：检查是否有其他子弹在 5 格内
        const hasCrossfire = self.hasCrossFireBullet(pcx, pcy, threat.id, COUNTER_FIRE_RANGE_CELLS, 1)
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
    self._moveDir = dodgeDirectionImpl(self, threat, pcx, pcy)
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
}


/** dodge(1000) — survive first: reaction, M3 counter-fire, perpendicular dodge. */

export const DODGE: Candidate = {
  id: 'dodge',
  weight: ACTION_WEIGHTS.dodge,
  evaluate: evalDodge,
}
