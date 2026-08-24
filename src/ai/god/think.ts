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
import type { Direction } from '../../constants'
import { BASE_POS, CELL, DIR_VECTORS, TANK } from '../../constants'
import { ALL_DIRS } from '../../utils/direction'
import { scanAheadImpl } from './FireControl'
import { runChain, ACTION_WEIGHTS, type Candidate } from './DecisionCore'
import { updateEnemyModel } from './EnemyModel'
import { superItemPressesImpl } from './SuperItems'

import { evalMidLaneHold } from './candidates/MidLaneHold'
import { evalCarvePath } from './candidates/CarvePath'
import { evalBaseConnectClear } from './candidates/BaseConnectClear'
import { evalFiringLane } from './candidates/FiringLane'
import { evalSurvive } from './candidates/Survive'
import { evalHunt } from './candidates/Hunt'
import { evalPickupLow } from './candidates/PickupLow'
import { evalEngage } from './candidates/Engage'
import { evalMidLaneDefense } from './candidates/MidLaneDefense'
import { evalDefenseIntercept } from './candidates/DefenseIntercept'
import { evalPickupMid } from './candidates/PickupMid'
import { evalClosePickup } from './candidates/ClosePickup'
import { evalAggro } from './candidates/Aggro'
import { evalPickupHigh } from './candidates/PickupHigh'
import { evalBaseLaneSentry } from './candidates/BaseLaneSentry'
import { evalUnifiedCandidates } from './candidates/UnifiedCandidates'
import { evalInterceptBase } from './candidates/InterceptBase'
import { evalDodge } from './candidates/Dodge'
import { evalSuicideReturn } from './candidates/SuicideReturn'

import { manhattan } from '../../utils/helpers'
import { findEnemyDirectionImpl } from './FireControl'

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
  evaluate: evalSuicideReturn,
}

/** dodge(1000) — survive first: reaction, M3 counter-fire, perpendicular dodge. */
const DODGE: Candidate = {
  id: 'dodge',
  weight: ACTION_WEIGHTS.dodge,
  evaluate: evalDodge,
}

/** interceptBase(900) — T8: stop an in-flight bullet aimed at the base. */
const INTERCEPT_BASE: Candidate = {
  id: 'interceptBase',
  weight: ACTION_WEIGHTS.interceptBase,
  evaluate: evalInterceptBase,
}

// ================================================================
// §X Base lane sentry — 基地车道哨兵
// ================================================================

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
 *   1. 拦截不动位 — 本候选不对齐时**主动导航到对齐站位**（evalBaseLaneSentry
 *      内的导航段）。
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
/**
 * M4 / 统一行动候选 (open-test protocol §7, 2026-08-16)。
 *
 * 基地受直接威胁(csb/cbr)时,不再让旧防守级联在"窗口已关"状态下提交,
 * 而是按 §7.1 度量比较四个固定候选(kill-current / intercept-base /
 * clear-lane / return-defense),§7.2 门控全过才提交:
 *   a. 安全 deadline slack > 0(站桩提交用 standing 击杀 slack — M3 S28s26:
 *      全行程 killAssessment 对"就差一枪"的场景系统性悲观);
 *   b. 有真实产出,站桩仅在 standing shot 赢 deadline 时合法;
 *   c. 第二威胁不可在完成前进入不可逆窗口;
 *   d. 射线不得穿过自家完好环砖(M3 S30s27 反例: 朝威胁开火打掉自家环
 *      反而引弹上身),clear-lane 永不打环砖。
 * 门控全不过 → return false,旧级联照旧(M3 主导机理就是窗口早已关闭,
 * 此时本层让路)。RNG 纪律: 仅提交时消耗 aimError roll,与其它候选一致。
 */
const UNIFIED_CANDIDATES: Candidate = {
  id: 'unifiedCandidates',
  weight: ACTION_WEIGHTS.unifiedCandidates,
  evaluate: evalUnifiedCandidates,
}

const BASE_LANE_SENTRY: Candidate = {
  id: 'baseLaneSentry',
  weight: ACTION_WEIGHTS.baseLaneSentry,
  evaluate: evalBaseLaneSentry,
}

/** pickupHigh(800) — §87/§88 HIGH-tier urgent pickup (bomb/freeze/fence ≤8格). */
const PICKUP_HIGH: Candidate = {
  id: 'pickupHigh',
  weight: ACTION_WEIGHTS.pickupHigh,
  evaluate: evalPickupHigh,
}

/** aggro(700) — S8/S9 freeze/shield window: stop-and-aim → power-up → navigate. */
const AGGRO: Candidate = {
  id: 'aggro',
  weight: ACTION_WEIGHTS.aggro,
  evaluate: evalAggro,
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
  evaluate: evalClosePickup,
}

/** pickupMid(600) — §88 MID-tier urgent pickup (star/tank/shield ≤4格). */
const PICKUP_MID: Candidate = {
  id: 'pickupMid',
  weight: ACTION_WEIGHTS.pickupMid,
  evaluate: evalPickupMid,
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
  evaluate: evalDefenseIntercept,
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
  evaluate: evalMidLaneDefense,
}

/** engage(500) — T2a: stop-and-aim when an enemy is in the line of fire. */
const ENGAGE: Candidate = {
  id: 'engage',
  weight: ACTION_WEIGHTS.engage,
  evaluate: evalEngage,
}

/** pickupLow(400) — S5: opportunistic power-up economy in normal mode. */
const PICKUP_LOW: Candidate = {
  id: 'pickupLow',
  weight: ACTION_WEIGHTS.pickupLow,
  evaluate: evalPickupLow,
}

/** hunt(200) — T2b: navigate towards the target (distance-adaptive). */
const HUNT: Candidate = {
  id: 'hunt',
  weight: ACTION_WEIGHTS.hunt,
  evaluate: evalHunt,
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
  evaluate: evalSurvive,
}

const FIRING_LANE: Candidate = {
  id: 'firingLane',
  weight: ACTION_WEIGHTS.firingLane,
  evaluate: evalFiringLane,
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
  evaluate: evalBaseConnectClear,
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
  evaluate: evalCarvePath,
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
  evaluate: evalMidLaneHold,
}

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
    self.branchCounts.dead++
    self._lastBranch = 'dead'
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
      self.branchCounts.hold++
      self._lastBranch = 'hold'
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
