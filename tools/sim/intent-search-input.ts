/**
 * intent-search-input.ts — P′-2a-B oracle-search 意图选择器（零训练测量仪器）。
 *
 * 与 `src/nn/intent-oracle-probe.ts`（E1 oracle：上帝 tagger 选意图）同构，唯一区别是
 * 意图来源：这里用**真模拟分支搜索**，对 8 个 `INTENT_IDS` 各跑 H 拍执行器 rollout，
 * 用 T6 反事实窗口分（`window-score.ts`，与语料导出同一实现）打分，取 argmax。
 *
 * 语义（与 intent-oracle 对齐，保证 B 只测「选择器」）：
 *   · exec = God-AI 候选白名单子链（`_candidateOverride`），驱动主循环；
 *   · 每 `searchEvery` tick 在决策瞬时搜索一次，新 override 从下一拍链生效
 *     （与 IntentOracleProbe 的 commit 时点相同 → 两臂只差选择器）；
 *   · 分支起点 = 同一 tick 的 `cloneWorld` + `captureDecisionState`；每个候选独立
 *     restore（CRN：世界流共享快照，executor 流同种子），分支之间不串味。
 *
 * 事件队列：`restoreWorld` 会 `events.clear()`（WorldSerializer），而搜索发生在
 * `endFrame()` 内、调用方 consume 之前 —— 故搜索前保存 pending events，收尾用
 * `world.pushEvent` 放回，telemetry 不因测量而丢帧。
 *
 * 确定性：无 `Math.random()`；exec RNG 与 IntentOracleProbe 的 exec 同种子
 * （`seed ^ 0x9e3779b9`）⇒ 同 seed 两跑 `intentTrace` 逐项一致。
 */
import { type Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'
import type { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { RNG } from '../../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { INTENT_IDS, candidateOverrideForIntent, type IntentId } from '../../src/ai/intent/vocab'
import { basePressure } from '../../src/nn/intent-rl-reward'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { countBaseWall } from './rl-reward'
import { counterfactualWindowScore, countEnemies } from './window-score'

export interface IntentSearchOptions {
  godParams?: GodAIParams
  /** 基础 seed（派生 exec RNG；与 intent-oracle 的 exec 同源）。 */
  seed?: number
  /** 搜索/承诺周期（tick）。默认 120。 */
  searchEvery?: number
  /** 候选分支 rollout 视界（tick）。默认 = searchEvery。 */
  horizon?: number
}

/** 搜索读数（只读诊断；P′-2a-B 报告用）。 */
export interface IntentSearchStats {
  searches: number
  branchTicks: number
}

/**
 * IntentSearchInput — InputLike。选择 = H 拍搜索 argmax，执行 = God-AI 白名单子链。
 */
export class IntentSearchInput implements InputLike {
  private world: World
  private exec: GodAIInput
  private branchSim: Simulation
  private searchEvery: number
  private horizon: number

  private thought = false
  private moveDir: Direction | null = null
  private firing = false

  /** 每搜索窗选中的意图 id（INTENT_IDS 下标）。 */
  readonly intentTrace: number[] = []
  private searches = 0
  private branchTicks = 0

  constructor(world: World, opts: IntentSearchOptions = {}) {
    this.world = world
    const params = opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }
    const base = opts.seed ?? (Date.now() & 0x7fffffff) >>> 0
    // 与 IntentOracleProbe 的 exec 同种子（§47 独立 RNG + 两臂 CRN）。
    this.exec = new GodAIInput(world, { ...params }, new RNG((base ^ 0x9e3779b9) >>> 0))
    this.branchSim = new Simulation(world, this.exec)
    this.searchEvery = Math.max(1, opts.searchEvery ?? 120)
    this.horizon = Math.max(1, opts.horizon ?? this.searchEvery)
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
    this.exec.endFrame()
  }

  reset(): void {
    this.thought = false
    this.intentTrace.length = 0
    this.searches = 0
    this.branchTicks = 0
    this.exec.reset()
    this.exec._candidateOverride = null
  }

  readSearchStats(): IntentSearchStats {
    return { searches: this.searches, branchTicks: this.branchTicks }
  }

  /** 每 tick：exec 链（当前承诺 override）驱动世界；搜索窗做 H 拍反事实选择。 */
  private decide(): void {
    this.thought = true
    this.exec.getMoveDirection()
    this.exec.isFiring()

    if (this.world.frame % this.searchEvery === 0) this.search()

    this.moveDir = this.exec._moveDir
    this.firing = this.exec._fire
  }

  /**
   * 8 候选各跑 H 拍：同一 World 快照 + 同一 executor 决策状态，只换 override。
   * 分支全部结束后恢复现场（含 pending events），再提交 argmax 的 override。
   */
  private search(): void {
    const w = this.world
    const pending = w.events.items.length > 0 ? [...w.events.items] : null

    let bestIdx = 0
    let bestScore = -Infinity
    for (let i = 0; i < INTENT_IDS.length; i++) {
      const intent = INTENT_IDS[i] as IntentId
      const snap = cloneWorld(w)
      const st = this.exec.captureDecisionState()
      this.exec._candidateOverride = candidateOverrideForIntent(intent)

      const phi0 = basePressure(w)
      const enemies0 = countEnemies(w)
      const kills0 = w.killCount
      const lives0 = w.lives
      let wallNow = countBaseWall(w)
      let wallLoss = 0

      for (let h = 0; h < this.horizon; h++) {
        this.branchSim.tick()
        this.exec.endFrame()
        this.branchTicks++
        const wall = countBaseWall(w)
        if (wall < wallNow) {
          wallLoss += wallNow - wall
          wallNow = wall
        }
        if (w.state !== 'playing') break
      }

      const score = counterfactualWindowScore(
        w.killCount - kills0,
        Math.max(0, lives0 - w.lives),
        wallLoss,
        phi0 - basePressure(w),
        enemies0 - countEnemies(w),
      )

      restoreWorld(w, snap)
      this.exec.restoreDecisionState(st)
      if (score > bestScore) {
        bestScore = score
        bestIdx = i
      }
    }

    this.exec._candidateOverride = candidateOverrideForIntent(INTENT_IDS[bestIdx] as IntentId)
    this.intentTrace.push(bestIdx)
    this.searches++

    // restoreWorld 清了事件队列：把搜索前的 pending events 原样放回（telemetry 不丢）。
    if (pending) for (const e of pending) w.pushEvent(e)
  }
}
