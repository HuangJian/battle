/**
 * intent-executor.ts — M6 脚本执行器：意图网络选分支 + God-AI 候选子链共享委托
 * (plan/Intent-Policy-NN-Plan.md §2/§3.2/§7-M6)。
 *
 * 三层契约（P0-5）：
 *   window  — 意图主行为：God-AI 候选子链（WHITELISTS[intent] 的 window 层，经
 *             thinkImpl 的 _candidateOverride 共享委托执行——复用 evaluate 闭包，
 *             禁第二份实现）
 *   overlay — 跨意图辅助（PICKUP 顺路拾取：仅无直接威胁时经 God-AI 全链 overlay
 *             候选自然生效——见 §3.2 仲裁）
 *   reflex  — 硬代码 dodge/survive 逐 tick 生效（不在子链内，保底闪避）
 *
 * 承诺期：网络每 replan 周期选一次意图，窗口内保持该意图的子链（不动 _candidate-
 * Override）；replan 时若新意图 ≠ 旧且 argmax 边际 ≥ threshold 才切换（预注册 #1
 * 初值：replan=30、边际=0.15——最终由 M7① 扫描定）。
 *
 * 仲裁规则（写死）：reflex 覆盖移动默认成立；PICKUP overlay 只在无直接威胁时生效；
 * suicideReturn 压制 dodge 仅随 RETURN_DEFENSE 白名单启用（§116/§117 默认 OFF）。
 *
 * 确定性：网络纯前向 + God-AI 子链纯 World 读 → 同 seed 逐字节一致（单测断言）；
 * 零 world.rng 消费（决策 RNG 走 God-AI 实例内部，与全链同源）。
 */
import { type Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { RNG } from '../utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../ai/GodAIInput'
import { ObsEncoder } from './obs-encoder'
import { buildIntentModelFromText, type IntentModelLike } from './infer'
import { INTENT_IDS, WHITELISTS, LABEL_TO_CANDIDATE, type IntentId } from '../ai/intent/vocab'
import { INTENT_REPLAN_TICKS } from '../ai/intent/tagger'

const MASKED_INTENTS: ReadonlySet<string> = new Set(['ESCAPE']) // reflex-only
const SWITCH_MARGIN = 0.15 // 预注册 #1 初值

function argmaxWithMargin(logits: Float32Array, masked: ReadonlySet<string>): number {
  let best = -1
  let bestV = -Infinity
  let secondV = -Infinity
  for (let i = 0; i < INTENT_IDS.length; i++) {
    if (masked.has(INTENT_IDS[i] as string)) continue
    const v = logits[i]
    if (v > bestV) {
      secondV = bestV
      bestV = v
      best = i
    } else if (v > secondV) {
      secondV = v
    }
  }
  // 返回 (bestV - secondV) 边际由调用方算；这里返回 index。
  void secondV
  return best
}

export interface IntentExecutorOptions {
  weightsText?: string
  godParams?: GodAIParams
  /** 内部 God-AI 决策 RNG（§47：与 world.rng 解耦，重放保真）。缺省 = world.rng（不推荐）。 */
  rng?: RNG
  replanEvery?: number
  switchMargin?: number
  /** M7① risk-gated（Q7）：危险窗口（enemyCanShootBase/近环突破，isBaseUnderThreat 纯函数）
   * 内 cadence 临时压缩至 dangerCadence，窗口外维持 baseCadence。默认关闭。 */
  riskGated?: boolean
  baseCadence?: number
  dangerCadence?: number
  /** M7② 探针（Q：rollout 意图分布熵 / HUNT 占比）：每 replan 记录原始 argmax 意图
   *  + 相对次大的边际。只读、零 RNG、确定性；默认关 = 不产数组分配（AGENTS §14）。 */
  recordReplanTrace?: boolean
  /**
   * M8 RL：外部采样器（rollout collector）。在 replan 帧调用，返回要锁定的意图
   * idx（替代网络 argmax）；返回 -1 = 保持当前承诺不变（不提交新意图）。采样器负责
   * 网络前向 + 采样 + logp/value 记录（collector 侧持有模型）。设此回调时：
   *  - 网络 argmax/switch-margin 路径被跳过（RL 的承诺由策略输出本身决定，margin 门控
   *    是 RL 前的启发式，RL 用切换成本奖励替代——plan §6 I13 semi-MDP）；
   *  - 注入态（prev/duration）仍由 executor 单源维护并推进（自馈语义同 tagger）；
   *  - weightsText 可省略（executor 不再构建模型）。
   */
  rlPick?: (obs: Uint8Array, scalars: Float32Array, inject: Float32Array, tick: number) => number
}

/**
 * IntentExecutor — 意图网络 + God-AI 子链执行器（InputLike）。
 * 与 GodAIInput 同生命周期；reset() 清空执行器态（网络注入 prev/duration + 承诺）。
 */
export class IntentExecutor implements InputLike {
  private world: World
  private model: IntentModelLike | null
  private encoder = new ObsEncoder()
  private god: GodAIInput
  private replanEvery: number
  private switchMargin: number
  private riskGated: boolean
  private baseCadence: number
  private dangerCadence: number
  /** M8 RL 外部采样器（rlPick 选项；undefined = 网络 argmax 路径）。 */
  private rlPick: IntentExecutorOptions['rlPick']
  /** risk-gated 动态 replan 调度：下一次 replan 的帧号。 */
  private nextReplanTick = -1

  // 网络注入态
  private prevIntentId = -1
  private duration = 0

  // 当前承诺意图（-1 = 未承诺/全链）
  private currentIntentId = -1

  private thought = false
  private moveDir: Direction | null = null
  private firing = false

  /** telemetry：每 replan 记录所选意图 id。 */
  readonly intentTrace: number[] = []

  /** 探针 telemetry（仅 recordReplanTrace 时填充）：每 replan 的原始 argmax 意图
   *  + 相对次大的边际。只读、零 RNG、确定性。 */
  readonly replanTrace: Array<{ tick: number; intent: number; margin: number }> = []
  private recordReplan = false

  constructor(world: World, opts: IntentExecutorOptions) {
    this.world = world
    // rlPick 模式下 executor 不构建模型（collector 持有并前向），避免双份。
    this.model = opts.rlPick ? null : buildIntentModelFromText(opts.weightsText ?? '')
    this.rlPick = opts.rlPick
    this.god = new GodAIInput(world, opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }, opts.rng)
    this.replanEvery = opts.replanEvery ?? INTENT_REPLAN_TICKS
    this.switchMargin = opts.switchMargin ?? SWITCH_MARGIN
    this.riskGated = opts.riskGated ?? false
    this.baseCadence = opts.baseCadence ?? this.replanEvery
    this.dangerCadence = opts.dangerCadence ?? 8
    this.recordReplan = opts.recordReplanTrace === true
  }

  getMoveDirection(): Direction | null {
    if (!this.thought) this.decide()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.thought) this.decide()
    return this.firing
  }

  wasItemPressed(): false {
    return false // AI 不使用主动道具
  }

  endFrame(): void {
    this.thought = false
    this.god.endFrame()
  }

  reset(): void {
    this.thought = false
    this.prevIntentId = -1
    this.duration = 0
    this.currentIntentId = -1
    this.nextReplanTick = -1
    this.intentTrace.length = 0
    this.replanTrace.length = 0
    this.god.reset()
  }

  /** 是否到达 replan 帧（risk-gated 用动态调度，否则固定 cadence）。 */
  private isReplanFrame(f: number): boolean {
    if (this.riskGated) {
      if (this.nextReplanTick < 0) this.nextReplanTick = f
      return f >= this.nextReplanTick
    }
    return f % this.replanEvery === 0
  }

  /** 更新下一次 replan 帧（risk-gated：危险窗口压缩 cadence，纯函数 isBaseUnderThreat）。 */
  private advanceReplan(f: number): void {
    if (!this.riskGated) return
    const danger = this.god.isBaseUnderThreat()
    this.nextReplanTick = f + (danger ? this.dangerCadence : this.baseCadence)
  }

  /** 每 replan 窗口：网络选意图 → 更新 God-AI 子链 override。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame

    // reflex 保底：God-AI 每 tick 全链先跑（dodge/survive 等硬代码），供 off-tick
    // 输出；replan 帧我们再覆盖为意图子链。
    this.god.getMoveDirection()
    this.god.isFiring()

    if (!this.isReplanFrame(f)) {
      // 承诺期 off-tick：直接采用 God-AI 已跑结果（全链输出作为窗口内默认）。
      this.moveDir = this.god._moveDir
      this.firing = this.god._fire
      return
    }

    if (this.rlPick) {
      // M8 RL 模式：外部采样器负责网络前向+采样+记录（collector 持有模型）。executor
      // 只做注入态推进 + 子链锁定——采样到的意图即为本窗口动作（无 margin 门控，RL
      // 的承诺由策略输出本身决定，切换成本奖励治理摇摆——plan §6 I13 semi-MDP）。
      this.encoder.encode(this.world)
      const inject = new Float32Array(9)
      if (this.prevIntentId >= 0) inject[this.prevIntentId] = 1
      inject[8] = Math.min(this.duration, 300) / 300
      const intentIdx = this.rlPick(this.encoder.obs, this.encoder.scalars, inject, f)
      if (intentIdx >= 0) {
        if (this.prevIntentId === intentIdx) this.duration++
        else {
          this.prevIntentId = intentIdx
          this.duration = 1
        }
        // 承诺切换：RL 策略输出即承诺（与 argmax 路径不同，不做 margin 门控）。
        if (intentIdx !== this.currentIntentId) {
          this.currentIntentId = intentIdx
          this.applyIntent(intentIdx)
          this.intentTrace.push(intentIdx)
        }
      }
      this.advanceReplan(f)
      this.moveDir = this.god._moveDir
      this.firing = this.god._fire
      return
    }

    // replan 帧：网络选意图。
    this.encoder.encode(this.world)
    const inject = new Float32Array(9)
    if (this.prevIntentId >= 0) inject[this.prevIntentId] = 1
    inject[8] = Math.min(this.duration, 300) / 300
    this.model!.intentForward(this.encoder.obs, this.encoder.scalars, inject)

    const intentIdx = argmaxWithMargin(this.model!.intentLogits, MASKED_INTENTS)
    if (this.prevIntentId === intentIdx) this.duration++
    else {
      this.prevIntentId = intentIdx
      this.duration = 1
    }
    // 探针 telemetry：每 replan 记录原始 argmax 意图（含未承诺切换的）——这就是自馈注入
    // 序列所推进的意图流，是反映网络自身意图偏好的最诚实口径（只读、零 RNG）。默认关。
    if (this.recordReplan)
      this.replanTrace.push({ tick: f, intent: intentIdx, margin: this.argmaxMargin() })

    // 承诺切换：仅当新意图 ≠ 当前承诺 且 argmax 边际 ≥ 阈值。
    if (intentIdx >= 0 && intentIdx !== this.currentIntentId) {
      const margin = this.argmaxMargin()
      if (margin >= this.switchMargin || this.currentIntentId < 0) {
        this.currentIntentId = intentIdx
        this.applyIntent(intentIdx)
        this.intentTrace.push(intentIdx)
      }
    }
    this.advanceReplan(f)

    // 本帧输出：意图子链已提交到 god._moveDir/_fire。
    this.moveDir = this.god._moveDir
    this.firing = this.god._fire
  }

  /** 计算当前 argmax 相对次大的边际。 */
  private argmaxMargin(): number {
    const l = this.model!.intentLogits
    let best = -Infinity
    let second = -Infinity
    for (let i = 0; i < INTENT_IDS.length; i++) {
      if (MASKED_INTENTS.has(INTENT_IDS[i] as string)) continue
      const v = l[i]
      if (v > best) {
        second = best
        best = v
      } else if (v > second) {
        second = v
      }
    }
    return best - second
  }

  /** 意图 → God-AI 候选白名单（三层契约 P0-5）→ 子链 override。
   *
   * 仲裁规则（写死）：reflex 候选（dodge/survive）**默认保留**在 override 内——reflex
   * 覆盖移动默认成立（dodge 在决策链顶层、先跑即赢）；**唯一例外** = 白名单内某 window
   * 候选显式标注 `suppressDodge`（当前仅 RETURN_DEFENSE 的 suicideReturn）→ 剔除 dodge。
   * overlay 候选（powerup 顺路拾取）随白名单保留（自带威胁门控，PICKUP overlay 只在
   * 无直接威胁时经候选自身 gate 生效）。
   *
   * M7① 修复（2026-08-27）：WHITELISTS 引用细分支标签（t8/t2a/navigate...），override
   * 过滤用候选 ActionId——必须经 LABEL_TO_CANDIDATE 翻译，否则 46% 候选被静默丢弃。 */
  private applyIntent(intentIdx: number): void {
    const intent = INTENT_IDS[intentIdx] as IntentId
    const rows = WHITELISTS[intent]
    const ids = new Set<string>()
    let suppressDodge = false
    for (const r of rows) {
      if (r.layer === 'window' && r.suppressDodge === true) suppressDodge = true
      const mapped = LABEL_TO_CANDIDATE[r.branch]
      if (mapped) for (const c of mapped) ids.add(c)
    }
    if (suppressDodge) ids.delete('dodge')
    // 子链 override：window + overlay + reflex（减被压制项）；reflex 层由 God-AI 全链
    // 逐 tick 保底。空集→thinkImpl fallback 全链（安全兜底）。
    this.god._candidateOverride = ids.size > 0 ? ids : null
  }
}
