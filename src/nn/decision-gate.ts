/**
 * decision-gate.ts —— NN 决策门的**唯一实现**（plan/new-era-stop.plan.md §6 R2，x2 事件 rung）。
 *
 * 背景（x1 现状 = 均匀 K）：5 处「什么时候跑一次前向」的门各写一份 `t % K === 0`
 * （`policy-input.ts:131` / `export-rl-rollout.ts:825` / `export-eval-game.ts:512` /
 * `export-nn-replays.ts:117` / `record-games-video.ts:198` + `divergence-probe.ts:182` 的
 * `t % K === 0 || t === 0`）。每多一处就是一次「训练说 A、判决说 B」的机会——本模块把它们
 * 收敛成一个谓词，并加上 x2 rung 的**事件补充**：
 *
 *   due(t) = 均匀边界 (t % k === 0)  ∪  threat-ONSET 沿 (`!prevThreat && inThreatLane(world)`)
 *
 * 三条硬约束（各有出处，改动前先读）：
 *   ① **只用沿（onset），禁电平**（hy P0-3）：电平在威胁持续期每 tick 成立 ⇒ Δt=1 ⇒
 *      部署侧 6Hz→60Hz（MAdds 预算失控）+ 训练侧视界塌缩（γ=0.995 ⇒ 200 步）。
 *      翻沿检测要求**每 tick 调用本函数**（调用方必须逐 tick 喂，不能只在候选 tick 上判）。
 *   ② **最小间隔闸**（R2.3）：两次决策（含均匀）之间的 Δt 恒 ≥ `minGap`（默认 3 tick）。
 *      晚出现的 `t % k === 0` 边界若落在事件后 < minGap 内会被跳过（罕见；事件恰好发生在
 *      K 边界前 1–2 tick 时才发生），其余均匀边界逐 tick 不变 ⇒ 「无事件局与旧版逐字节一致」。
 *      这同时是部署侧前向频率的**上界**（hy P0-3 要求补的第二维）：最坏 1/3 tick = 20 Hz
 *      （均匀 = 6 Hz）；实测 c20 四局只 +0.5–2% 决策步（avg-Δt 9.79–9.94）。
 *   ③ **均匀子集是默认**：`events=false` 时本谓词退化为 `t % k === 0` 的精确实现（不读
 *      inThreatLane、不设最小间隔闸），x1 首刀与所有旧调用逐字节不变。事件 rung 必须显式
 *      打开（`--decision-events` / `NNInputOptions.decisionEvents`），且**判决链与部署链同值**
 *      （tools/sim/eval-game-parity.test.ts 钉住）。
 *
 * 读数（R2.3 的 avg-Δt）：状态里自带计数器（decisions / eventDecisions / dtSum / minDt /
 * maxDt），由 `decisionReadout()` 汇总成落盘对象；消费方（rollout manifest / eval 报告）不要
 * 另算一份——两处算法必分叉。
 *
 * `minDt` 是「Δt≥minGap 恒成立」这条断言的**实测值**（每次决策时更新）：门自身保证下限，
 * 但读数落盘让运行期证据也在（测试之外仍可查）。
 *
 * 纯函数 + 极小的状态对象；不读 rng、不写 World、每 tick 零分配（AGENTS §2.3/§14.1–14.2）。
 * 状态由调用方持有（不进单例/模块变量，AGENTS §2.2）。
 */
import type { World } from '../game/World'
import { inThreatLane } from './danger-metrics'

/** 均匀决策周期（与旧 `K` 同值；单一来源，勿在别处再写 10）。 */
export const DEFAULT_DECISION_K = 10
/** 最小决策间隔（tick）。hy P0-3 的「(如 ≥3 tick)」；同时守住部署侧前向频率上界。 */
export const DEFAULT_DECISION_MIN_GAP = 3

export interface DecisionGateConfig {
  /** 均匀边界周期 K（x1 首刀 = 10）。 */
  k: number
  /** 是否启用 threat-ONSET 事件补充（x2 rung；默认 false = 旧行为）。 */
  events: boolean
  /** 相邻两次决策的最小间隔（tick）；仅事件模式生效（均匀 k ≥ minGap 时等价恒真）。 */
  minGap: number
}

export interface DecisionGateState {
  /** 上一 tick 是否在威胁带上（onset = `!prevThreat && inThreatLane(world)`）。 */
  prevThreat: boolean
  /** 上一次决策的 tick；-1 = 决策流尚未开始（首个候选恒放行）。 */
  lastDecisionTick: number
  /** 事件抑制窗（state-init 交棒首段，R2.4 方案 i）：`t < 本值` 的 threat 事件一律不发。 */
  suppressEventsUntilTick: number
  /** 决策总数（读数）。 */
  decisions: number
  /** 其中由 threat-ONSET 触发的决策数（读数；均匀边界撞上 onset 时记均匀）。 */
  eventDecisions: number
  /** Δt 累计（读数；`decisions-1` 个间隔之和）。 */
  dtSum: number
  /** 实测最小 Δt（-1 = 尚无可比间隔）。 */
  minDt: number
  /** 实测最大 Δt（0 = 无可比间隔）。 */
  maxDt: number
}

/** 新建决策流状态（每局/每次 reset 一份；调用方持有）。 */
export function createDecisionGateState(): DecisionGateState {
  return {
    prevThreat: false,
    lastDecisionTick: -1,
    suppressEventsUntilTick: 0,
    decisions: 0,
    eventDecisions: 0,
    dtSum: 0,
    minDt: -1,
    maxDt: 0,
  }
}

/**
 * 组装门配置（唯一的入口：调用方别手搓字面量，防 K/minGap 走散）。
 * 事件模式下 K < minGap 是配置错误（会让均匀边界被永久跳过）⇒ 响亮抛错。
 */
export function createDecisionGateConfig(
  events: boolean,
  k = DEFAULT_DECISION_K,
  minGap = DEFAULT_DECISION_MIN_GAP,
): DecisionGateConfig {
  if (!Number.isInteger(k) || k < 1) throw new Error(`decision-gate: k 非法（${k}）`)
  if (events && k < minGap) {
    throw new Error(`decision-gate: 事件模式下 k(${k}) < minGap(${minGap})，均匀边界会被永久跳过`)
  }
  return { k, events, minGap }
}

/** threat-ONSET 沿：入带翻沿。**电平禁止入集**（hy P0-3）。 */
export function threatOnsetEdge(prevThreat: boolean, world: World): boolean {
  return !prevThreat && inThreatLane(world)
}

/**
 * 本 tick 是否到期决策；到期时**就地更新状态**（`lastDecisionTick` + 读数），
 * 故调用方必须在**确实要决策**的那一行调用它（loop: `if (decisionDue(...)) { forward… }`）。
 *
 * 逐 tick 调用是契约的一部分：`prevThreat` 的翻沿检测依赖每一 tick 的 `inThreatLane`。
 * 返回 true 后同 tick 再调一次会拿到 false（状态已推进）——这正是「一次决策一步」的语义。
 */
export function decisionDue(
  t: number,
  world: World,
  state: DecisionGateState,
  cfg: DecisionGateConfig,
): boolean {
  const uniform = t % cfg.k === 0
  // 均匀模式（默认，x1 全链）：精确等于旧 `t % k === 0`——不读 inThreatLane、不算最小间隔闸，
  // 热路径（rollout 跑百万局）零额外开销。
  if (!cfg.events) {
    if (!uniform) return false
    markDecision(state, t)
    return true
  }

  const inThreat = inThreatLane(world)
  const onset = t >= state.suppressEventsUntilTick && !state.prevThreat && inThreat
  state.prevThreat = inThreat
  if (!uniform && !onset) return false
  // 最小间隔闸（R2.3）：两次决策（含均匀）Δt ≥ minGap。事件恰好落在 K 边界前 1–2 tick 时
  // 会跳过一个均匀边界（罕见；无事件的局永不受影响）。
  if (state.lastDecisionTick >= 0 && t - state.lastDecisionTick < cfg.minGap) return false

  markDecision(state, t)
  if (onset && !uniform) state.eventDecisions++
  return true
}

/**
 * 记账一次决策（Δt/读数）。`decisionDue` 内部已调用；此导出只为**绕开 cadence 的
 * 决策瞬时**（`NNInput.reset()` 的 tick 0 决策）能进同一条账，免得读数与门分叉。
 */
export function markDecision(state: DecisionGateState, t: number): void {
  if (state.lastDecisionTick >= 0) {
    const dt = t - state.lastDecisionTick
    state.dtSum += dt
    if (state.minDt < 0 || dt < state.minDt) state.minDt = dt
    if (dt > state.maxDt) state.maxDt = dt
  }
  state.lastDecisionTick = t
  state.decisions++
}

/** 落盘读数（R2.3：rollout telemetry + eval 汇总的唯一算法）。 */
export interface DecisionReadout {
  /** 决策总步数。 */
  n: number
  /** 其中 threat-ONSET 触发的步数。 */
  events: number
  /** 平均 Δt（tick/决策；<2 步 = 0）。 */
  avgDt: number
  /** 实测最小 Δt（<2 步 = 0）。 */
  minDt: number
  /** 实测最大 Δt（<2 步 = 0）。 */
  maxDt: number
}

export function decisionReadout(state: DecisionGateState): DecisionReadout {
  const gaps = state.decisions - 1
  return {
    n: state.decisions,
    events: state.eventDecisions,
    avgDt: gaps > 0 ? +(state.dtSum / gaps).toFixed(3) : 0,
    minDt: state.minDt < 0 ? 0 : state.minDt,
    maxDt: state.maxDt,
  }
}

/**
 * 汇总多局读数（rollout 批次 / eval 套件）：n/events 求和，avgDt 按 gap 数加权，
 * min/max 取极值。单局读数已四舍五入到 3 位，故加权和是读数级精度（非训练量纲）。
 */
export function poolDecisionReadouts(list: readonly DecisionReadout[]): DecisionReadout {
  let n = 0
  let events = 0
  let gaps = 0
  let dtSum = 0
  let minDt = -1
  let maxDt = 0
  for (const r of list) {
    n += r.n
    events += r.events
    const g = r.n - 1
    if (g > 0) {
      gaps += g
      dtSum += r.avgDt * g
      if (minDt < 0 || r.minDt < minDt) minDt = r.minDt
      if (r.maxDt > maxDt) maxDt = r.maxDt
    }
  }
  return {
    n,
    events,
    avgDt: gaps > 0 ? +(dtSum / gaps).toFixed(3) : 0,
    minDt: minDt < 0 ? 0 : minDt,
    maxDt,
  }
}
