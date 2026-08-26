/**
 * intent-oracle-probe.ts — M7① 天花板探针：oracle 意图选择器（plan/Intent-Policy-NN-Plan.md §7-M7①）。
 *
 * 语义：规则选择器 ≠ 最优选择器，是天花板的下界测量仪器。本探针测量"若意图选择完美
 * （= God-AI 全链自己的意图，tagger 口径），词表×执行器能复现多少 God-AI 胜率"——
 * 前置标定（I12）：须复现 God-AI WIN ≥74%（噪声带），不过则先修探针再解释低分。
 *
 * 机制（双 God 实例，避免 override↔branch 自锁回环）：
 *   - oracleGod：全链（无 override），每 tick 跑，经 tagger currentIntent() 提供
 *     "该状态下 God-AI 本会做的意图"——纯观测源，不驱动世界。
 *   - executorGod：受限链（_candidateOverride = 当前承诺意图的白名单），驱动世界。
 *   - 每 replan 窗口：读 oracle 意图 → 提交 executorGod 白名单 override。
 *   - 承诺期：窗口内保持该意图白名单（与 M6 执行器相同语义）。
 *
 * 确定性：双 God 各带独立 RNG（§47，与 world.rng 解耦）；纯 World 读 + 网络无关
 * （无 NN）→ 同 seed 逐字节一致。零 world.rng 消费。
 */
import { type Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import { RNG } from '../utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../ai/GodAIInput'
import { currentIntent } from '../ai/intent/tagger'
import { INTENT_IDS, WHITELISTS, LABEL_TO_CANDIDATE, type IntentId } from '../ai/intent/vocab'
import { INTENT_REPLAN_TICKS } from '../ai/intent/tagger'

export interface IntentOracleProbeOptions {
  godParams?: GodAIParams
  /** 基础 seed（§47 派生两个独立 RNG：oracle/exec 互不污染）。缺省用当前帧时间派生。 */
  seed?: number
  replanEvery?: number
}

/**
 * IntentOracleProbe — InputLike。意图选择 = oracle（God-AI 全链 tagger 意图），
 * 执行 = God-AI 候选白名单子链（共享委托）。纯探针，不落产品线。
 */
export class IntentOracleProbe implements InputLike {
  private world: World
  private oracle: GodAIInput
  private exec: GodAIInput
  private replanEvery: number

  private thought = false
  private moveDir: Direction | null = null
  private firing = false
  private currentIntentId = -1

  /** telemetry：每 replan 记录的 oracle 意图 id。 */
  readonly intentTrace: number[] = []

  constructor(world: World, opts: IntentOracleProbeOptions) {
    this.world = world
    const params = opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }
    const base = opts.seed ?? (Date.now() & 0x7fffffff) >>> 0
    // §47：两实例各带独立 RNG（决策 RNG 与 world.rng 解耦，且 oracle/exec 互不污染）。
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
    this.currentIntentId = -1
    this.intentTrace.length = 0
    this.oracle.reset()
    this.exec.reset()
    this.exec._candidateOverride = null
  }

  /** 每 tick：oracle 全链（提供意图源）+ executor 受限链（驱动世界）；replan 窗口提交白名单。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame

    // oracle 全链先跑（提供该状态下的 God-AI 意图；不驱动世界）。
    this.oracle.getMoveDirection()
    this.oracle.isFiring()

    // executor 受限链跑（当前 override 白名单；默认 = 上一窗口提交）。
    this.exec.getMoveDirection()
    this.exec.isFiring()

    if (f % this.replanEvery === 0) {
      const intent = currentIntent(this.oracle)
      if (intent) {
        this.currentIntentId = INTENT_IDS.indexOf(intent)
        this.applyIntent(intent)
        this.intentTrace.push(this.currentIntentId)
      }
    }

    this.moveDir = this.exec._moveDir
    this.firing = this.exec._fire
  }

  /** 意图 → God-AI 候选白名单（三层契约，与 M6 执行器同仲裁）→ override。
   *  白名单细分支标签经 LABEL_TO_CANDIDATE 翻译为候选 ActionId（M7① 修复）。 */
  private applyIntent(intent: IntentId): void {
    const rows = WHITELISTS[intent]
    const ids = new Set<string>()
    let suppressDodge = false
    for (const r of rows) {
      if (r.layer === 'window' && r.suppressDodge === true) suppressDodge = true
      const mapped = LABEL_TO_CANDIDATE[r.branch]
      if (mapped) for (const c of mapped) ids.add(c)
    }
    if (suppressDodge) ids.delete('dodge')
    this.exec._candidateOverride = ids.size > 0 ? ids : null
  }
}
