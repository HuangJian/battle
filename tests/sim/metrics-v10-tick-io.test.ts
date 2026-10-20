import { describe, expect, it } from 'bun:test'
import type { Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'
import { isFireTick, isStopTick } from '../../tools/sim/export-eval-game'

/**
 * metrics v10 两列的**口径契约**（`stopTicks` 列 54 / `fireHeldTicks` 列 55）。
 *
 * 为什么单独成家：这两列的语义**随动作空间切换**（plan/nn-capability-upgrade §4-B1 S2-3）——
 * v6 的 move 头**没有 null 类**，"不动"只能由 hold 位表达。口径若被改回「一律
 * `getMoveDirection() === null`」，v6 通电后 `stopTicks` 会**静默归零**，
 * 依赖它的 wBlock 项从 −0.5 分暴涨到 −16.3 分（idle 148.5/千 − stop 143.7/千 ⇒ 5/千；
 * 归零则 −110 × 0.1485）。这种失败**不崩、不红、只是奖励函数悄悄变了**，
 * 所以必须在口径层钉死，而不是靠端到端 eval 撞见。
 *
 * 口径产出点：`tools/sim/export-eval-game.ts` 主循环（第 825/829 行原先内联，
 * 现抽成本文件测的两个纯函数；行为零变化）。
 */
class FakeInput implements InputLike {
  constructor(
    private moveDir: Direction | null,
    private firing: boolean,
    private hold?: boolean,
  ) {}
  getMoveDirection(): Direction | null {
    return this.moveDir
  }
  isFiring(): boolean {
    return this.firing
  }
  isHolding(): boolean {
    return this.hold === true
  }
  usesHoldFire3(): boolean {
    return this.hold !== undefined
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

/** legacy 输入源（人类 / God-AI）：根本没有 `isHolding` 这个方法。 */
class LegacyInput implements InputLike {
  constructor(
    private moveDir: Direction | null,
    private firing: boolean,
  ) {}
  getMoveDirection(): Direction | null {
    return this.moveDir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

describe('metrics v10 tick 口径：stopTicks / fireHeldTicks', () => {
  it('v6（hold 头）：hold=1 即有方向也算「没在前进」', () => {
    // ★ 这条是 v6 通电后会走到的路径：v6 的 move 头没有 null 类，
    //   只有 hold 位能表达「转向但不前进」。
    expect(isStopTick(new FakeInput('up', false, true))).toBe(true)
    expect(isStopTick(new FakeInput('up', true, true))).toBe(true)
  })

  it('v6（hold 头）：hold=0 且有方向 ⇒ 在前进', () => {
    expect(isStopTick(new FakeInput('up', false, false))).toBe(false)
    expect(isStopTick(new FakeInput('down', true, false))).toBe(false)
  })

  it('legacy（无 isHolding）：只看 move 是否为 null', () => {
    expect(isStopTick(new LegacyInput(null, false))).toBe(true)
    expect(isStopTick(new LegacyInput('up', false))).toBe(false)
    // 反向保护：别把「有方向」也判成 stop
    expect(isStopTick(new LegacyInput('left', true))).toBe(false)
  })

  it('fireHeldTicks = 开火输出，不是实弹（不受冷却/弹量门控影响）', () => {
    expect(isFireTick(new FakeInput('up', true, false))).toBe(true)
    expect(isFireTick(new FakeInput('up', false, false))).toBe(false)
    expect(isFireTick(new LegacyInput(null, true))).toBe(true)
    expect(isFireTick(new LegacyInput(null, false))).toBe(false)
  })

  it('★ 回归锚：hold 位失效会让 stopTicks 全盘归零（wBlock −0.5 → −16.3）', () => {
    // 一个「有方向 + hold=1」的输入，在两条口径下结论相反 —— 这就是事故的形状。
    const held = new FakeInput('up', false, true)
    expect(held.getMoveDirection()).not.toBeNull() // 有方向
    expect(isStopTick(held)).toBe(true) // 但按 v6 口径 = 没在前进
    // 若口径退化成只看 move，下面这条就会是 false ⇒ 本用例红
    const wouldBreak = held.getMoveDirection() === null
    expect(wouldBreak).toBe(false)
    expect(isStopTick(held)).toBe(!wouldBreak || held.isHolding())
  })
})
