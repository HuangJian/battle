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
  hunt: 200,
  survive: 0,
  suicideReturn: 1100,
  defenseIntercept: 550,
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
