/**
 * forced-input.ts — P′-0 forceTick / endFrame 钩子。
 *
 * 分支 rollout（P′-2b）需要「把某一拍的策略动作替换成指定动作，然后交还原策略」，
 * 且**不得绕过正式 tick 生命周期**（不碰 Simulation、不手改 World）。本包装器把
 * `getMoveDirection/isFiring/...` 在强制窗口内替换为 `ForcedAction`，窗口外的
 * `endFrame/reset` 一律委托给 inner：
 *
 *   · 强制窗口内**不查询** inner 的决策方法 ⇒ inner 的决策状态原地不动；
 *   · `endFrame()` 每拍都调用 inner（tick 生命周期一致）；
 *   · 窗口结束后下一次 `getMoveDirection()` 才回到 inner，它看到的是推进后的
 *     World（frame 已前进），语义 = 「替换 1 拍、其余照旧」。
 *
 * 用法（b0）：
 *   const wrapped = new ForcedTickInput(input, { moveDir: 'left', fire: false }, 1)
 *   sim.input = wrapped
 *   sim.tick(); wrapped.endFrame()   // 这一拍是 forced
 *   sim.tick(); wrapped.endFrame()   // 这一拍恢复 inner
 */
import type { Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'

export interface ForcedAction {
  moveDir: Direction | null
  fire: boolean
  /** v3 action contract：turn-only bit（仅在 inner 使用 hold/fire3 时有意义）。 */
  holding?: boolean
  /** v3 action contract：fire-before-turn bit。 */
  fireBeforeTurn?: boolean
}

export interface ForcedTickOptions {
  /**
   * P′-2b1：强制窗口内**先查询 inner 再覆盖输出**（默认 false = 不查询 inner）。
   *
   * b0 的 NNInput 无跨 tick 决策状态（encoder 每次重算），跳过 inner 无影响；
   * b1 的 executor（GodAIInput）有 tracker / 计数器，跳过一拍 think 会让后续决策
   * 与「只替换动作」的真实语义分叉。开此档后 inner 的决策状态照常推进，只有
   * 对外提供的动作被替换 ⇒ 自检（强制=实际动作 ≡ 不强制）才能逐字段成立。
   */
  decideFirst?: boolean
}

export class ForcedTickInput implements InputLike {
  private remaining: number

  constructor(
    private readonly inner: InputLike,
    private readonly forced: ForcedAction,
    private readonly ticks = 1,
    private readonly opts: ForcedTickOptions = {},
  ) {
    if (!Number.isInteger(ticks) || ticks < 1) {
      throw new Error(`ForcedTickInput: ticks must be a positive integer (got ${ticks})`)
    }
    this.remaining = ticks
  }

  /** True while the forced action is still being served. */
  get active(): boolean {
    return this.remaining > 0
  }

  getMoveDirection(): Direction | null {
    if (this.remaining <= 0) return this.inner.getMoveDirection()
    if (this.opts.decideFirst) this.inner.getMoveDirection()
    return this.forced.moveDir
  }

  isFiring(): boolean {
    if (this.remaining <= 0) return this.inner.isFiring()
    if (this.opts.decideFirst) this.inner.isFiring()
    return this.forced.fire
  }

  isHolding(): boolean {
    if (this.remaining <= 0) return this.inner.isHolding?.() ?? false
    if (this.opts.decideFirst) this.inner.isHolding?.()
    return this.forced.holding === true
  }

  isFireBeforeTurn(): boolean {
    if (this.remaining <= 0) return this.inner.isFireBeforeTurn?.() ?? false
    if (this.opts.decideFirst) this.inner.isFireBeforeTurn?.()
    return this.forced.fireBeforeTurn === true
  }

  usesHoldFire3(): boolean {
    return this.inner.usesHoldFire3?.() ?? false
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return this.inner.wasItemPressed(kind)
  }

  endFrame(): void {
    if (this.remaining > 0) this.remaining--
    this.inner.endFrame()
  }

  reset(): void {
    this.remaining = this.ticks
    this.inner.reset()
  }
}
