/**
 * NNOption.ts — P′-3 / E2′ 合成 `nn-option` candidate（plan/nn-hierarchical-options.plan.md §9.6）。
 *
 * 角色：把上层注入的 option（意图词表）展开成该意图的**候选子链**，在同一个
 * `thinkImpl → runChain → endFrame` 生命周期里执行——与 E1 的
 * `_candidateOverride` 子集走同一条路径（同一份 `candidateOverrideForIntent`
 * 白名单、同一个 `_orderedCandidates` 顺序、同一个 first-commit-wins）。
 *
 * 可达性：`CANDIDATES` **不含**本候选（默认链与 M1 parity 逐字节不变）；只有
 * `_candidateOverride` 含 `'nnOption'` 时 shell 才使用扩展池
 * （`_orderedCandidatesE2`）。`evaluate` 在未注入 `_optionCmd` 时恒 false。
 *
 * 委托失败（子链无人提交）⇒ 本候选返回 false，由 shell 统一回退 `HUNT`
 * （与 E1 的 fallback 完全同一条），本候选不吞 fallback。
 *
 * `targetKind/target/pathMode/param`：P′-3 第二步的注入面；本文件的委托层只
 * 解释 `option`，其余字段由后续执行层消费（先过 byte-identical 门）。
 */
import { runChain, type Candidate } from './DecisionCore'
import type { IntentId } from '../intent/vocab'

/** 目标格（顶点坐标口径，与 pathfind / goal-executor 一致）。 */
export interface NNOptionTarget {
  col: number
  row: number
}

/**
 * 一次 option 注入。缺省值（调用方必须显式给全）：
 *   targetKind='none' · target=null · pathMode='direct' · param=0
 */
export interface NNOptionCommand {
  option: IntentId
  targetKind: 'none' | 'enemy' | 'anchor' | 'powerup'
  target: NNOptionTarget | null
  pathMode: 'direct' | 'break' | 'detour'
  param: number
}

/**
 * E2′ `nn-option` candidate。纯委托：option → 预构建子链 → runChain。
 * 无 World 写入、无 RNG、无逐 tick 分配（子链在 reset 时构建）。
 */
export const NN_OPTION: Candidate = {
  id: 'nnOption',
  weight: 0,
  evaluate(self, ctx) {
    const cmd = self._optionCmd
    if (!cmd) return false
    const chain = self._optionChains.get(cmd.option)
    if (!chain || chain.length === 0) return false
    return runChain(self, ctx, chain) !== null
  },
}
