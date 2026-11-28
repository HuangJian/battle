/**
 * intent-option-probe.ts — P′-3 E2′ 质量门探针（oracle 意图 → nn-option 委托）。
 *
 * 与 `intent-oracle-probe.ts` 同构：oracle 全链提供「该状态下的 God-AI 意图」，
 * 执行侧 = E2′ `nn-option` candidate（`_candidateOverride={'nnOption'}` +
 * `_optionCmd`），option 子链由 `candidateOverrideForIntent` 唯一展开。
 *
 * 用途：P′-3 第二门（700 局 ≥−3pp）与 forced coverage —— 与 intent-oracle
 * 逐局对账时，本探针应与现役 E1 子链**逐字节等价**（byte-identical 门的
 * 整局版本）：同 seed 的 outcome/ticks/world 必须相同。
 *
 * 确定性：双 God 各带独立 RNG（§47）；纯 World 读；零 world.rng 消费。
 */
import { type Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import { RNG } from '../utils/RNG'
import {
  GodAIInput,
  DEFAULT_GOD_AI_PARAMS,
  type GodAIParams,
  type GodAIDecisionState,
} from '../ai/GodAIInput'
import { currentIntent } from '../ai/intent/tagger'
import { INTENT_IDS, type IntentId } from '../ai/intent/vocab'
import { INTENT_REPLAN_TICKS } from '../ai/intent/tagger'
import type { NNOptionCommand } from '../ai/god/NNOption'

/**
 * P′-2b1：probe 的 oracle + executor 决策状态快照（分支 rollout 用）。
 * 与 `cloneWorld/restoreWorld` 同一 tick 取；恢复顺序 `restoreWorld → restoreDecisionState`。
 */
export interface IntentOptionProbeState {
  version: 1
  oracle: GodAIDecisionState
  exec: GodAIDecisionState
}

export interface IntentOptionProbeOptions {
  godParams?: GodAIParams
  /** 基础 seed（§47 派生两个独立 RNG：oracle/exec 互不污染）。 */
  seed?: number
  replanEvery?: number
}

export class IntentOptionProbe implements InputLike {
  private world: World
  private oracle: GodAIInput
  private exec: GodAIInput
  private replanEvery: number

  private thought = false
  private moveDir: Direction | null = null
  private firing = false

  /** telemetry：每 replan 记录的 oracle 意图 id。 */
  readonly intentTrace: number[] = []

  constructor(world: World, opts: IntentOptionProbeOptions) {
    this.world = world
    const params = opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }
    const base = opts.seed ?? (Date.now() & 0x7fffffff) >>> 0
    this.oracle = new GodAIInput(world, { ...params }, new RNG(base))
    this.exec = new GodAIInput(world, { ...params }, new RNG((base ^ 0x9e3779b9) >>> 0))
    this.replanEvery = opts.replanEvery ?? INTENT_REPLAN_TICKS
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
    this.oracle.endFrame()
    this.exec.endFrame()
  }

  reset(): void {
    this.thought = false
    this.intentTrace.length = 0
    this.oracle.reset()
    this.exec.reset()
    this.exec._candidateOverride = null
    this.exec._optionCmd = null
  }

  /** 每 tick：oracle 全链（意图源）+ E2′ 执行器（nn-option 委托）；replan 窗注入。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame

    this.oracle.getMoveDirection()
    this.oracle.isFiring()
    this.exec.getMoveDirection()
    this.exec.isFiring()

    if (f % this.replanEvery === 0) {
      const intent = currentIntent(this.oracle)
      if (intent) {
        this.intentTrace.push(INTENT_IDS.indexOf(intent))
        this.applyOption(intent)
      }
    }

    this.moveDir = this.exec._moveDir
    this.firing = this.exec._fire
  }

  /** option → E2′ 注入（targetKind='none' / pathMode='direct' = 纯委托）。 */
  private applyOption(intent: IntentId): void {
    const cmd: NNOptionCommand = {
      option: intent,
      targetKind: 'none',
      target: null,
      pathMode: 'direct',
      param: 0,
    }
    this.exec._optionCmd = cmd
    this.exec._candidateOverride = new Set(['nnOption'])
  }

  // ================================================================
  // P′-2b1: decision-state capture / restore (executor branch rollout)
  // ================================================================

  /** 捕获 oracle + executor 的跨 tick 决策状态（深拷贝；与 cloneWorld 同一 tick 取）。 */
  captureDecisionState(): IntentOptionProbeState {
    return {
      version: 1,
      oracle: this.oracle.captureDecisionState(),
      exec: this.exec.captureDecisionState(),
    }
  }

  /** 恢复决策状态（restoreWorld 之后调用；内部深拷贝，同一份 state 可复用于多个分支）。 */
  restoreDecisionState(state: IntentOptionProbeState): void {
    if (!state || state.version !== 1) {
      throw new Error(
        `IntentOptionProbeState: unsupported version ${(state as { version?: unknown })?.version}`,
      )
    }
    this.oracle.restoreDecisionState(state.oracle)
    this.exec.restoreDecisionState(state.exec)
  }

  /** 当前注入的 option 指令（只读；P′-2b1 option 分支的基线）。 */
  readOptionCommand(): NNOptionCommand | null {
    return this.exec._optionCmd
  }
}
