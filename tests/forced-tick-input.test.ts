/**
 * P′-0：ForcedTickInput 的 forceTick 契约。
 * 强制窗口内返回 forced 动作且不查询 inner；endFrame 每拍都委托；窗口结束后
 * inner 从原状态继续（语义 = 只替换指定拍数）。
 */
import { describe, it, expect } from 'bun:test'
import { ForcedTickInput } from '../tools/sim/forced-input'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'

class FakeInput implements InputLike {
  calls = { move: 0, fire: 0, end: 0, reset: 0 }
  constructor(private readonly dir: Direction | null = 'up') {}
  getMoveDirection(): Direction | null {
    this.calls.move++
    return this.dir
  }
  isFiring(): boolean {
    this.calls.fire++
    return true
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {
    this.calls.end++
  }
  reset(): void {
    this.calls.reset++
  }
}

describe('ForcedTickInput', () => {
  it('强制窗口内不查询 inner；endFrame 后回到 inner', () => {
    const inner = new FakeInput('up')
    const input = new ForcedTickInput(inner, { moveDir: 'left', fire: false }, 1)
    expect(input.active).toBe(true)
    expect(input.getMoveDirection()).toBe('left')
    expect(input.isFiring()).toBe(false)
    expect(inner.calls.move).toBe(0)
    expect(inner.calls.fire).toBe(0)

    input.endFrame()
    expect(inner.calls.end).toBe(1)
    expect(input.active).toBe(false)
    expect(input.getMoveDirection()).toBe('up')
    expect(input.isFiring()).toBe(true)
    expect(inner.calls.move).toBe(1)
  })

  it('多拍强制 + reset 重置窗口', () => {
    const inner = new FakeInput('right')
    const input = new ForcedTickInput(inner, { moveDir: 'down', fire: true }, 2)
    expect(input.getMoveDirection()).toBe('down')
    input.endFrame()
    expect(input.getMoveDirection()).toBe('down')
    input.endFrame()
    expect(input.getMoveDirection()).toBe('right')

    input.reset()
    expect(inner.calls.reset).toBe(1)
    expect(input.getMoveDirection()).toBe('down')
  })

  it('v3 动作位：窗口内用 forced，窗口外 delegate（缺省 false）', () => {
    const inner = new FakeInput()
    const input = new ForcedTickInput(inner, {
      moveDir: 'up',
      fire: false,
      holding: true,
      fireBeforeTurn: true,
    })
    expect(input.isHolding()).toBe(true)
    expect(input.isFireBeforeTurn()).toBe(true)
    expect(input.usesHoldFire3()).toBe(false)
    input.endFrame()
    expect(input.isHolding()).toBe(false)
    expect(input.isFireBeforeTurn()).toBe(false)
  })

  it('非法拍数 fail-closed', () => {
    expect(() => new ForcedTickInput(new FakeInput(), { moveDir: null, fire: false }, 0)).toThrow(
      /positive integer/,
    )
  })
})
