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
import { type GodAIInput, recordBranch } from '../GodAIInput'
import type { Direction } from '../../constants'
import { BASE_POS, CELL, DIR_VECTORS, TANK } from '../../constants'
import { ALL_DIRS } from '../../utils/direction'
import { scanAheadImpl } from './FireControl'
import { runChain, type Candidate } from './DecisionCore'
import { updateEnemyModel } from './EnemyModel'
import { superItemPressesImpl } from './SuperItems'

import { MID_LANE_HOLD } from './candidates/MidLaneHold'
import { CARVE_PATH } from './candidates/CarvePath'
import { BASE_CONNECT_CLEAR } from './candidates/BaseConnectClear'
import { FIRING_LANE } from './candidates/FiringLane'
import { SURVIVE } from './candidates/Survive'
import { HUNT } from './candidates/Hunt'
import { PICKUP_LOW } from './candidates/PickupLow'
import { ENGAGE } from './candidates/Engage'
import { MID_LANE_DEFENSE } from './candidates/MidLaneDefense'
import { DEFENSE_INTERCEPT } from './candidates/DefenseIntercept'
import { PICKUP_MID } from './candidates/PickupMid'
import { CLOSE_PICKUP } from './candidates/ClosePickup'
import { AGGRO } from './candidates/Aggro'
import { PICKUP_HIGH } from './candidates/PickupHigh'
import { BASE_LANE_SENTRY } from './candidates/BaseLaneSentry'
import { UNIFIED_CANDIDATES } from './candidates/UnifiedCandidates'
import { INTERCEPT_BASE } from './candidates/InterceptBase'
import { DODGE } from './candidates/Dodge'
import { SUICIDE_RETURN } from './candidates/SuicideReturn'

import { manhattan } from '../../utils/helpers'
import { findEnemyDirectionImpl } from './FireControl'
import type { GodAIParams } from './params.interface'

/**
 * ─── 候选存活状态清单 — 已数据化为下方 CANDIDATE_SURVIVAL（唯一事实源）──
 * plan/God-AI-Organization.md §6 C1（2026-08-26）：tests/godai-archived-knobs.test.ts
 * 消费该常量做 L1 守卫（OFF 候选门控必须 === 0、ON 候选门控必须非 0、与
 * params.interface 的 ARCHIVED_KNOB_GROUPS 交叉一致）。
 *
 * 移出决策链的条件（refactor.trae.md §1.2-2）：DECISIONS=阴性/reject + DEFAULT=0
 *   + 零引用 + 无 1 态 A/B 测试调用，四者同时成立。四条件核查（2026-08-25）：
 * 当前所有 OFF 候选均不满足「零引用 + 无 1 态测试」，按 AGENTS §5.1 一律留档标注、
 * 不移出数组、不删文件（逐候选引用证据见 git 历史 / plan/refactor.trae.md §7）。
 * 完整 A/B 数据见 docs/god-ai-tuning.progress.md；§ 编号见 DECISIONS.md。
 */
export interface CandidateSurvivalRow {
  /** 候选名（CANDIDATES 数组成员的语义 id）。 */
  candidate: string
  /** 门控字段；'(always)' 行为 null；actionWeights.survive 用点路径字符串。 */
  gate: keyof GodAIParams | 'actionWeights.survive' | null
  /** 是否在默认决策链中可达。 */
  on: boolean
  /** 决策号 / 备注。 */
  note: string
}

export const CANDIDATE_SURVIVAL: readonly CandidateSurvivalRow[] = [
  {
    candidate: 'suicideReturn',
    gate: 'suicideReturnMode',
    on: false,
    note: '§116/§117 阴性, 保留可重开 A/B',
  },
  { candidate: 'dodge', gate: null, on: true, note: '生存优先, 顶层' },
  { candidate: 'interceptBase', gate: null, on: true, note: 'T8 拦子弹' },
  {
    candidate: 'unifiedCandidates',
    gate: 'candidateMode',
    on: false,
    note: '§221 reject, 保留实验资产',
  },
  {
    candidate: 'baseLaneSentry',
    gate: 'baseLaneSentryMode',
    on: true,
    note: '§198 SHIPPED (classic restore 0)',
  },
  { candidate: 'pickupHigh', gate: 'pickupPriorityMode', on: true, note: '§87/§88 SHIPPED' },
  { candidate: 'aggro', gate: null, on: true, note: '(always, freeze/shield) S8/S9' },
  { candidate: 'pickupMid', gate: 'pickupPriorityMode', on: true, note: '§88 SHIPPED' },
  {
    candidate: 'defenseIntercept',
    gate: 'defenseInterceptMode',
    on: true,
    note: '§134 SHIPPED (classic restore 0)',
  },
  { candidate: 'midLaneDefense', gate: 'midLaneDefense', on: true, note: '§163/§165 SHIPPED' },
  {
    candidate: 'closePickup',
    gate: 'closePickupRange',
    on: true,
    note: '§158 SHIPPED (range 2 ≠ 0 即 ON)',
  },
  { candidate: 'engage', gate: null, on: true, note: 'T2a' },
  { candidate: 'pickupLow', gate: 'pickupPriorityMode', on: true, note: 'S5' },
  {
    candidate: 'firingLane',
    gate: 'firingLaneMode',
    on: false,
    note: '§139 灾难性阴性, 保留实验资产',
  },
  {
    candidate: 'baseConnectClear',
    gate: 'baseConnectClearMode',
    on: true,
    note: '§189 SHIPPED (classic restore 0)',
  },
  { candidate: 'carvePath', gate: 'carvePathMode', on: false, note: '§161 诚实阴性, 保留实验资产' },
  {
    candidate: 'midLaneHold',
    gate: 'midLaneHold',
    on: false,
    note: '§164 灾难性阴性, 保留实验资产',
  },
  { candidate: 'hunt', gate: null, on: true, note: 'T2b' },
  {
    candidate: 'survive',
    gate: 'actionWeights.survive',
    on: false,
    note: 'M3, weight 0 → 链中永不可达, 保留实验资产',
  },
]
/** The M1 chain — weight order strictly mirrors the original top-level order.
 * Authoritative weight-order contract: DecisionCore.ACTION_WEIGHTS (locked by
 * tests/decision-core.test.ts); this array must stay in the same order.
 * Exported for the M1 invariant test: a reorder without a matching
 * ACTION_WEIGHTS update is a behavior change. */
export const CANDIDATES: Candidate[] = [
  SUICIDE_RETURN,
  DODGE,
  INTERCEPT_BASE,
  // M4 / 统一行动候选 — interceptBase(900) 之下、baseLaneSentry(850) 之上:
  // 基地受直接威胁时四候选(kill-current/intercept-base/clear-lane/
  // return-defense)按协议 §7.2 门控择一;门控不过则旧级联照旧。
  UNIFIED_CANDIDATES,
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
    recordBranch(self, 'dead')
    return
  }

  // §233 (perf): decision-chain throttle. thinkInterval > 1 runs the full
  // candidate chain every Nth tick and HOLDS the previous _moveDir/_fire on
  // off-ticks — pure decision latency, no World mutation, no RNG consumed on
  // off-ticks. 1 tick = 16.7ms is far below every reaction horizon (bullets
  // cross in 100+ ticks; fire cooldown ~13 ticks; followPath consumption is
  // cell-gated ~23 ticks/cell). A/B validated on hard (DECISIONS §233); the
  // gate truth was re-baselined for the shipped hard/chaos value of 2.
  if (self.params.thinkInterval > 1) {
    self._thinkCounter++
    if (self._thinkCounter % self.params.thinkInterval !== 0) {
      // Hold the last committed decision. The player keeps moving/firing in
      // the previous branch's direction — movement is cell-gated and bullets
      // are long-horizon, so the 1-tick hold is imperceptible. Pure
      // observation counters only.
      recordBranch(self, 'hold')
      return
    }
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
  if (self._aggCampTrack.suppress > 0) self._aggCampTrack.suppress--
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
  let aimDir = findEnemyDirectionImpl(self, pcx, pcy)

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
    const dist159 = manhattan(pc159.col, pc159.row, BASE_POS.col, BASE_POS.row)
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
      const dist = manhattan(p.x, p.y, partner.x, partner.y)
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
