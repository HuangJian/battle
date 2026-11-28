/**
 * hier-option-executor.ts — P′-4a 选择层执行器：option 联合槽 → E2′ `nn-option`。
 *
 * 与 `IntentExecutor`（8 意图）同生命周期，区别是动作空间 = `src/ai/intent/options.ts`
 * 的 6 个联合槽（3 option × pathMode，HOLD_break 掩码）：
 *   - 每 `replanEvery`（默认 30）拍：obs 编码 → 注入（上一槽 one-hot(6) + 槽龄）→
 *     网络 logits（或外部 `rlPick` 采样）→ argmax/masked 采样 → 生成
 *     `NNOptionCommand`（target 由 options.ts 规则决定，不是学习头）；
 *   - 注入 `_optionCmd` + `_candidateOverride = {'nnOption'}` ⇒ God-AI 的
 *     `thinkImpl → runChain → endFrame` 里由 E2′ candidate 执行（P′-3 已验证与 E1
 *     逐字节等价）；
 *   - 窗口内命令冻结（承诺），切换只在 replan 帧发生——semi-MDP 语义与 intent RL 同源。
 *
 * 确定性：纯前向 + 纯 World 读 + God-AI 独立 RNG（`opts.rng`，与 world.rng 解耦）；
 * 零 `Math.random()`、零 world.rng 消费。同 seed 双跑逐字节一致（单测断言）。
 */
import { type Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { RNG } from '../utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../ai/GodAIInput'
import { INTENT_REPLAN_TICKS } from '../ai/intent/tagger'
import {
  OPTION_DIM,
  OPTION_INJECT_DIM,
  OPTION_SLOT_MASK,
  buildOptionCommand,
  optionSlotId,
} from '../ai/intent/options'
import { DURATION_MAX_TICKS } from '../ai/intent/vocab'
import { ObsEncoder } from './obs-encoder'
import { buildIntentModelFromText, type IntentModelLike } from './infer'

export interface HierOptionExecutorOptions {
  /** option 线权重（`arch.intentDim` 必须 = 6；rlPick 模式下可省略）。 */
  weightsText?: string
  godParams?: GodAIParams
  /** 内部 God-AI 决策 RNG（§47：与 world.rng 解耦，重放保真）。 */
  rng?: RNG
  replanEvery?: number
  /**
   * 训练采样器（rollout collector）：在 replan 帧调用，返回槽 idx（替代 argmax）。
   * sampler 负责网络前向 + 采样 + logp/value 记录（collector 侧持有模型）。
   */
  rlPick?: (obs: Uint8Array, scalars: Float32Array, inject: Float32Array, tick: number) => number
}

const NN_OPTION_SET: ReadonlySet<string> = new Set(['nnOption'])
const DEFAULT_REPLAN = INTENT_REPLAN_TICKS // 30

/** 掩码 argmax（HOLD_break 恒 -inf；越界/全掩码返回 -1）。 */
export function argmaxOptionSlot(logits: Float32Array): number {
  let best = -1
  let bestV = -Infinity
  for (let i = 0; i < OPTION_DIM; i++) {
    if (OPTION_SLOT_MASK[i] !== 1) continue
    if (logits[i] > bestV) {
      bestV = logits[i]
      best = i
    }
  }
  return best
}

/**
 * HierOptionExecutor — InputLike。选择 = option 联合槽，执行 = E2′ `nn-option`。
 */
export class HierOptionExecutor implements InputLike {
  private world: World
  private model: IntentModelLike | null
  private encoder = new ObsEncoder()
  private god: GodAIInput
  private replanEvery: number
  private rlPick: HierOptionExecutorOptions['rlPick']

  private thought = false
  private moveDir: Direction | null = null
  private firing = false

  /** 网络注入态（上一槽 / 槽龄，单源维护）。 */
  private prevSlot = -1
  private duration = 0
  /** 当前承诺槽（-1 = 未承诺）。 */
  private currentSlot = -1

  /** telemetry：每 replan 的槽 (0..5)。 */
  readonly slotTrace: number[] = []

  constructor(world: World, opts: HierOptionExecutorOptions) {
    this.world = world
    this.model = opts.rlPick ? null : buildIntentModelFromText(opts.weightsText ?? '')
    if (this.model && this.model.intentDim !== OPTION_DIM) {
      throw new Error(
        `HierOptionExecutor: 权重的 intentDim=${this.model.intentDim}，` +
          `option 线必须是 ${OPTION_DIM}（用 option_probe --slots 6 --save 产出的 init/RL 权重）`,
      )
    }
    this.rlPick = opts.rlPick
    this.god = new GodAIInput(world, opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }, opts.rng)
    this.replanEvery = Math.max(1, opts.replanEvery ?? DEFAULT_REPLAN)
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
    this.prevSlot = -1
    this.duration = 0
    this.currentSlot = -1
    this.slotTrace.length = 0
    this.god.reset()
    this.god._optionCmd = null
    this.god._candidateOverride = null
  }

  /** 每 replan 窗口：网络/采样器选槽 → 更新 E2′ 注入命令。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame

    // God-AI 每 tick 先跑（reflex 保底 + 上一窗口命令的执行）；replan 帧再改命令。
    this.god.getMoveDirection()
    this.god.isFiring()

    if (f % this.replanEvery !== 0) {
      this.moveDir = this.god._moveDir
      this.firing = this.god._fire
      return
    }

    this.encoder.encode(this.world)
    const inject = new Float32Array(OPTION_INJECT_DIM)
    if (this.prevSlot >= 0) inject[this.prevSlot] = 1
    inject[OPTION_DIM] = Math.min(this.duration, DURATION_MAX_TICKS) / DURATION_MAX_TICKS

    let slot: number
    if (this.rlPick) {
      slot = this.rlPick(this.encoder.obs, this.encoder.scalars, inject, f)
    } else {
      this.model!.intentForward(this.encoder.obs, this.encoder.scalars, inject)
      slot = argmaxOptionSlot(this.model!.intentLogits)
    }

    if (slot >= 0) {
      if (this.prevSlot === slot) this.duration++
      else {
        this.prevSlot = slot
        this.duration = 1
      }
      // RL/argmax 输出即承诺（不做 margin 门控；切换成本由奖励治理）。
      if (slot !== this.currentSlot) {
        this.currentSlot = slot
        this.slotTrace.push(slot)
        this.applySlot(slot)
      }
    }

    this.moveDir = this.god._moveDir
    this.firing = this.god._fire
  }

  /** 槽 → 注入命令 + E2′ 候选 override（命令从下一拍生效，与 E1/E2′ 探针同语义）。 */
  private applySlot(slot: number): void {
    this.god._optionCmd = buildOptionCommand(slot, this.world, this.god.isBaseUnderThreat())
    this.god._candidateOverride = NN_OPTION_SET
  }

  /** 诊断：当前承诺槽 id（未承诺 = '?'）。 */
  currentSlotId(): string {
    return optionSlotId(this.currentSlot)
  }
}
