// candidates/Dodge.ts — the dodge candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { CELL } from '../../../constants'
import { type GodAIInput, markBranch, recordBranch } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { dodgeCounterFireDirImpl } from '../ThreatAssessor'
import { scanAheadImpl } from '../FireControl'
import { COUNTER_FIRE_RANGE_CELLS } from '../constants'
import { dodgeDirectionImpl } from '../ThreatAssessor'
import { crossfireEscapeDirImpl } from '../crossfire'
import { muzzleEscapeDirImpl } from '../muzzle-threat'

export function evalDodge(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, threat } = ctx
  // 2026-11-01 「用命换输出」：非致命命中**只扣 HP**（SimulationCombat 的
  // 命中分支明确保证不改 identity/profile/任何派生属性，无 stun、无击退），
  // 所以吃一发非致命弹的唯一代价就是 HP —— 而 hard 有 3 条命，败局平均只用
  // 0.5 条（lives=1→3 是 +35pp，3→5 是 0.0pp：第 3 条命以后完全没人用）。
  // 血量富余时躲弹 = 白送输出窗口。`dodgeHpArmorMode > 0` 让 AI 在
  // 「hp > damage × dodgeHpArmorHits」时无视这一发，把 dodge 的 tick 让给
  // 进攻候选。0 = OFF（byte-identical）。
  //
  // mode 2（2026-11-01 追加）：再要求**还剩 ≥2 条命**。全局无门版实测 −2.38pp
  // （Steel Web −14 / Bastion −10 / Riverbed −9 —— 那几关的败局本来就是
  // lives_exhausted，3.0 死/局），而受益的正是「败局几乎不死」的关
  // （Ice Palace +6 / Checkers +5 / Star Fort +5）。命数门把「换」限制在
  // 「还有本钱」的阶段：用掉第 1 条换输出，但绝不把最后一条也赌进去。
  // mode 5（2026-11-02 用户裁定）：「激进不是浪更不是送死」—— 只在**打得着**的
  // 时候换血，三个条件缺一不可：
  //   ① **>1 命**（`w.lives > 1`）—— 还输得起；
  //   ② **非致命**（`p.hp > threat.damage`）—— 这一发打不死我；
  //   ③ **能对消 或 能换伤**：
  //      a) 对消：来袭弹与我对齐且车道干净（`dodgeCounterFireDirImpl`）⇒ 迎面
  //         一炮把它打掉，**不掉血、只花冷却**；
  //      b) 换伤：枪是热的且当前朝向上确实有敌人（`scanAheadImpl(p.dir).enemy`）
  //         ⇒ 站住打这一发，挨的那一下当交换。
  // 与 mode 1–4 的区别：那四档只看「血量够」就放弃躲，等于无条件挨打（实测
  // 全负）；本档只在**这一拍有产出**时才换，没有产出照旧躲。
  if (self.params.dodgeHpArmorMode >= 5 && threat) {
    const dmg = threat.damage > 0 ? threat.damage : 1
    if (w.lives > 1 && p.hp > dmg) {
      if (!onCooldown) {
        const faceDir = dodgeCounterFireDirImpl(self, threat, pcx, pcy)
        if (faceDir) {
          self._moveDir = faceDir
          self._fire = true
          markBranch(self, 'dodgeTrade')
          return true
        }
        // mode 6 = 只对消（不做换伤）；5/7 才走换伤分支。
        if (self.params.dodgeHpArmorMode !== 6 && scanAheadImpl(self, pcx, pcy, p.dir).enemy) {
          // mode 5：站住打这一发；mode 7：**保持朝向继续走**并开火（不转身 ⇒
          // 子弹仍沿已提交的 p.dir 飞，A2 不变量不被破坏；同时不放弃机动）。
          self._moveDir = self.params.dodgeHpArmorMode >= 7 ? p.dir : null
          self._fire = true
          markBranch(self, 'dodgeTrade')
          return true
        }
      }
    }
  }
  // mode 1–4 是「血量门」家族（只看血量就放弃躲，已全部否证）；5 以上走上面的
  // 「换血」家族，两个家族互斥 —— 否则 mode 6 会在对消失败后落进血量门，名不副实。
  if (self.params.dodgeHpArmorMode > 0 && self.params.dodgeHpArmorMode < 5 && threat) {
    const hits = self.params.dodgeHpArmorHits
    const dmg = threat.damage > 0 ? threat.damage : 1
    const livesOk =
      self.params.dodgeHpArmorMode < 2 ||
      (self.params.dodgeHpArmorMode === 4 ? w.lives >= 3 : w.lives >= 2)
    // mode 3：只在「贴脸」（最近敌人在 ≤dodgeHpArmorMeleeCells 格）时换 ——
    // 那个距离上躲弹的成功率本来就低，而一换一能立刻把场上的敌人打少一个。
    let meleeOk = true
    if (self.params.dodgeHpArmorMode >= 3) {
      meleeOk = false
      const lim = self.params.dodgeHpArmorMeleeCells
      const list = self._enemies.length > 0 ? self._enemies : w.tanks
      for (let i = 0; i < list.length; i++) {
        const e = list[i]
        if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') continue
        const ec = self.tankCell(e)
        if (
          Math.abs(ec.col - Math.floor(pcx / CELL)) + Math.abs(ec.row - Math.floor(pcy / CELL)) <=
          lim
        ) {
          meleeOk = true
          break
        }
      }
    }
    if (livesOk && meleeOk && p.hp > dmg * hits) return false
  }
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
      markBranch(self, 'dodge')
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
          recordBranch(self, 'dodge')
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
        const hasCrossfire = self.hasCrossFireBullet(
          pcx,
          pcy,
          threat.id,
          COUNTER_FIRE_RANGE_CELLS,
          1,
        )
        if (!hasCrossfire) {
          const fireDir = dodgeCounterFireDirImpl(self, threat, pcx, pcy)
          if (fireDir) {
            self._moveDir = p.dir === fireDir ? null : fireDir
            self._fire = true
            self._counterFireTicks++
            self._lastDodgeThreatId = threat.id
            self._lastDodgeDir = self._moveDir
            self._dodgeFlipCount = 0
            recordBranch(self, 'dodge')
            return true
          }
        }
      }
    }

    // A12 (2026-10-23): MULTI-SECTOR CROSSFIRE ESCAPE. s14 autopsy: 75% of the
    // fatal ticks had ≥2 shells in flight and the single-threat dodge steps
    // out of one lane straight into the other. Take the step that leaves the
    // FEWEST lanes covering us, when that strictly beats the normal dodge.
    if (self.params.crossfireSectorMode > 0) {
      const esc = crossfireEscapeDirImpl(self, pcx, pcy)
      if (esc !== null) {
        self._moveDir = esc
        if (threat.id === self._lastDodgeThreatId && self._lastDodgeDir !== null) {
          if (self._moveDir !== self._lastDodgeDir) self._dodgeFlipCount++
          else self._dodgeFlipCount = 0
        } else {
          self._dodgeFlipCount = 0
        }
        self._lastDodgeThreatId = threat.id
        self._lastDodgeDir = self._moveDir
        self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, esc)
        recordBranch(self, 'dodge')
        return true
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
    recordBranch(self, 'dodge')
    return true
  }

  // A14 (2026-10-25): MUZZLE threat — no shell in flight, but a barrel is
  // already trained down a clear lane at us and the lane cannot be left in
  // time once the shell exists (19px of travel + 12-tick turn cooldown).
  // Leave the lane now, before the shot.
  if (self.params.muzzleEscapeMode > 0 && !self.aggressive && p.alive) {
    const dir = muzzleEscapeDirImpl(self, pcx, pcy)
    if (dir !== null) {
      self._moveDir = dir
      self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir)
      recordBranch(self, 'muzzleEscape')
      return true
    }
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
