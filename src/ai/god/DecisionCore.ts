import type { GodAIInput } from '../GodAIInput'
import type { World } from '../../game/World'
import type { Tank, Bullet } from '../../types'
import type { Direction } from '../../constants'

/**
 * Pillar A (plan/God-AI-Redesign-v2 §3): decision-chain scoring framework.
 *
 * M1 (parity mode): candidates are verbatim extractions of the original
 * think() top-level chain. Selection = first candidate (in weight order)
 * whose `evaluate()` commits — the weights strictly mirror the chain order,
 * so this is exactly the original if-else with data-driven priority.
 *
 * M1 theorem (doc §3.3) — byte-parity by construction requires:
 *
 *   0. Only the committing candidate's body runs. Candidates are evaluated
 *      strictly in weight order with early-exit, so each candidate is only
 *      reached when all higher-priority candidates declined — exactly the
 *      original chain. A candidate's OWN on-entry state transitions (e.g.
 *      the T2a camp-tracking that runs before the campedTooLong
 *      fall-through, or the no-threat resets in the dodge section) are part
 *      of that branch's entry semantics and live inside its evaluate().
 *      No candidate ever pre-executes another candidate's body.
 *   1. evaluate() = the exact original branch return-condition (precondition
 *      ∧ internal decision, binary) AND the exact original branch body when
 *      the condition holds — a mechanical `return` → `return true`
 *      transcription of the original if-else. This is safer than a
 *      pass/body split for M1: several branch-entry computations write
 *      cross-tick caches (navigateTowards → _navCache, scanAhead → the
 *      shared _scanResult buffer), and evaluating them twice (once to score,
 *      once to apply) would desync parity. M2+ may add a separate score()
 *      phase on top of this skeleton for continuous value/urgency/risk.
 *   2. Weights = chain order (strictly decreasing → first commit wins).
 *   3. RNG stream unchanged: only committing bodies consume RNG, in the
 *      same order as the original chain (reaction/dodge consume none; the
 *      aimError rolls live in the aggro/engage bodies, which run only when
 *      those candidates commit). Parity holds under default params
 *      (survivalModeLives=0, γ=0 — M3 caveat from doc §3.3).
 */

/** Top-level decision actions — the candidates of the M1 chain. */
export type ActionId =
  | 'dodge'
  | 'interceptBase'
  | 'pickupHigh'
  | 'aggro'
  | 'pickupMid'
  | 'engage'
  | 'pickupLow'
  | 'hunt'
  // M3 (plan/God-AI-Redesign-v2 §3.2): survive 候选 — 无在飞子弹但处于交叉
  // 火力/包围位置时的主动换位（复用 findSafeMoveDir + findPathThreat）。
  // 默认权重 0 = 不激活（byte-identical）；M4 调优经 actionWeights.survive
  // 激活（通常置于 dodge 之下、interceptBase 之上）。
  | 'survive'
  // 自杀秒回 (suicide quick-return, user request 2026-08-04, §116): when all
  // 5 preconditions are met, the player embraces death to respawn at the
  // spawn point closer to a base-threatening enemy. Weight 1100 > dodge 1000
  // so it suppresses dodging when suiciding is the better base-saving trade.
  | 'suicideReturn'
  // §134 / 方向 D (user request 2026-08-05): 防守位停射拦截基地车道敌人。
  // Player near the base, an enemy already aligned with the base (canShootBase)
  // and aligned with the player → stop-and-aim intercept WITHOUT leaving the
  // defense position. Weight 550 > engage 500. Default 0 = OFF (byte-identical).
  | 'defenseIntercept'
  // §139 / 方向 A（进攻侧，2026-08-05）: 火力死区解除。玩家处于死区（四方向
  // 全无敌人 LOS）且敌人较远时，寻找附近有射界的瞭望格导航过去重新接战，
  // 而非原地待机（Battlement 34% 全 tick 钉在 (11,24) 死区是输出瓶颈主因）。
  // Weight 300 > hunt 200、< pickupLow 400。默认 0 = OFF（byte-identical）。
  | 'firingLane'
  // §158: 非冰冻期近距离道具拾取 — 无炮弹危险时捡取近处道具，随手开火。
  // Weight 540 > engage(500)、< defenseIntercept(550)。低于防守拦截，确保
  // 敌人接近基地车道时防守优先。默认通过 closePickupRange>0 激活。
  | 'closePickup'
  // §161 / 开路策略 (carve path): 出生点困在砖墙迷宫时，在下半区射击砖墙
  // 开出一条通途到驻守点（R1/R2）；驻守点无仗可打时开路到最可能威胁基地的
  // 敌人（R3）。Weight 250 > hunt(200)、< firingLane(300) — 覆盖 hunt 的
  // 盲走，但低于一切战斗/道具/瞭望格候选。默认 carvePathMode=0 OFF。
  | 'carvePath'
  // §163 / 中路防守 (mid-lane defense, user request 2026-08-06): 基地所在列
  // 无钢防时，玩家锚定基地列上方的防守点（开路挖过去），中路有威胁时停射
  // 向上对消炮弹/击杀凿墙敌人；中路无威胁时也保持距防守点 ≤ leash 格，随时
  // 回防。Weight 545 < defenseIntercept(550)（拦截优先）、> closePickup(540)。
  | 'midLaneDefense'
  // §164 / 中路列旁主动驻守 (proactive mid-lane flank hold, user request
  // 2026-08-06): 出袋后在顶部广场时优先驻守基地列旁的对消格（列无钢防且
  // 中路繁忙时），而非长期驻守边路出生点。Weight 220 > hunt(200)、< carvePath(250)
  // — 覆盖 hunt 的盲走，但低于一切战斗/道具/瞭望格/开路候选。默认 midLaneHold=0 OFF。
  | 'midLaneHold'

/**
 * M1 default weights — strictly mirror the original think() top-level chain
 * evaluation order (doc §3.2):
 *
 *   dodge(1000) > interceptBase(900) > pickupHigh(800) > aggro(700)
 *   > pickupMid(600) > engage(500) > pickupLow(400) > hunt(200)
 *
 * M3: survive defaults to 0 — the candidate exists but never commits at the
 * default. orderedCandidates sorts by effective weight; a 0-weight candidate
 * sorts below every active candidate, so it is only reachable when explicitly
 * promoted via `actionWeights` (M4 tuning surface).
 *
 * Data-driven priority: M2+ makes this a tunable per-difficulty vector
 * (`actionWeights`, L1). Changing a weight here IS a behavior change —
 * M1 keeps the chain order frozen (parity).
 */
export const ACTION_WEIGHTS: Record<ActionId, number> = {
  dodge: 1000,
  interceptBase: 900,
  pickupHigh: 800,
  aggro: 700,
  pickupMid: 600,
  engage: 500,
  pickupLow: 400,
  // §139 / 方向 A: 火力死区解除 — 覆盖 hunt(200) 的盲走，让死区玩家去有射界
  // 的瞭望格重新接战。默认 0（mode 门控），不激活时字节持平。
  firingLane: 300,
  // §161 / 开路策略: 低于 firingLane(300)、高于 hunt(200)。
  carvePath: 250,
  closePickup: 540,
  hunt: 200,
  survive: 0,
  suicideReturn: 1100,
  defenseIntercept: 550,
  // §163 / 中路防守: defenseIntercept(550) 之下、closePickup(540) 之上 —
  // 拦截候选优先（已上车道的敌人由拦截一枪解除），中路锚定次之。
  midLaneDefense: 545,
  // §164 / 中路列旁主动驻守: carvePath(250) 之下、hunt(200) 之上。
  midLaneHold: 220,
}

/**
 * Per-tick shared context — the values thinkImpl computed once in its common
 * prefix and every branch consumed. Built by the shell, read-only here.
 */
export interface DecisionContext {
  /** World (read-only; candidates observe, never mutate gameplay state). */
  w: World
  /** Player tank (alive, spawned — the dead case is handled before ctx). */
  p: Tank
  /** Player center px. */
  pcx: number
  /** Player center py. */
  pcy: number
  /** Cooldown-aware firing gate (M6): bulletCap reached OR timer not elapsed. */
  onCooldown: boolean
  /** Enemy in same row/col (T9 scan, global vision). */
  aimDir: Direction | null
  /** Most dangerous incoming bullet (null when shielded or none). */
  threat: Bullet | null
  /** Respawn shield active — skips dodge (S9). */
  shielded: boolean
}

/**
 * A decision candidate: one verbatim original branch. `evaluate()` runs the
 * branch's entry logic; returns true only when the branch commits (the
 * original would have `return`ed from here) — at which point the shell stops.
 */
export interface Candidate {
  id: ActionId
  weight: number
  evaluate(self: GodAIInput, ctx: DecisionContext): boolean
}

/**
 * Run the decision chain: evaluate candidates in weight order, first commit
 * wins (early-exit — same cost profile as the original if-else). Returns the
 * committing candidate, or null (only possible for a candidate-set bug: the
 * hunt candidate is unconditional, so the shell treats null as hunt).
 */
export function runChain(
  self: GodAIInput,
  ctx: DecisionContext,
  candidates: Candidate[],
): Candidate | null {
  for (let i = 0; i < candidates.length; i++) {
    const c = candidates[i]
    if (c.evaluate(self, ctx)) return c
  }
  return null
}

/**
 * M2 (plan/God-AI-Redesign-v2 §3.2/§3.3): build the effective candidate
 * evaluation order from the base chain and per-params weight overrides.
 *
 * With NO overrides (default), the result is exactly `base` — ACTION_WEIGHTS
 * is strictly decreasing, so the chain order is already the weight order
 * (M1 parity by construction). With overrides, candidates are stable-sorted
 * by effective weight (`overrides[id] ?? ACTION_WEIGHTS[id]`) descending;
 * the stable sort keeps the base chain order among ties.
 *
 * MUST be built once per params (GodAIInput.reset()), never per tick
 * (AGENTS §14.3/14.5 — no per-tick sort/allocations in hot paths).
 */
export function orderedCandidates(
  base: Candidate[],
  overrides: Partial<Record<ActionId, number>> | undefined,
): Candidate[] {
  if (!overrides) return base
  const eff = (c: Candidate): number => overrides[c.id] ?? c.weight
  return base.slice().sort((a, b) => eff(b) - eff(a))
}
