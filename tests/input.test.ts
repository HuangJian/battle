import { describe, it, expect } from 'bun:test'
import { Input, DEFAULT_KEYS } from '../src/game/Input'
import type { Direction } from '../src/constants'

/**
 * Tests for Input.getMoveDirection() — "last pressed wins" priority.
 *
 * The old implementation used a fixed-order check ("first in a fixed
 * order"), which the code comment itself flagged as a simplification of
 * the intended "last pressed wins" semantics. These tests pin down the
 * last-pressed-wins behaviour. See DECISIONS.md §20 for the rationale.
 *
 * The Input class only reads `e.code` and calls `e.preventDefault()` from
 * its keydown/keyup handlers, so we drive them with a minimal stub event
 * — no DOM required.
 */

interface StubEvent {
  code: string
  preventDefault: () => void
}

function keydown(input: Input, code: string): void {
  const e: StubEvent = { code, preventDefault: () => {} }
  ;(input as unknown as { onKeyDown: (e: StubEvent) => void }).onKeyDown(e)
}

function keyup(input: Input, code: string): void {
  const e: StubEvent = { code, preventDefault: () => {} }
  ;(input as unknown as { onKeyUp: (e: StubEvent) => void }).onKeyUp(e)
}

function dir(input: Input): Direction | null {
  return input.getMoveDirection()
}

describe('Input.getMoveDirection — last pressed wins', () => {
  it('returns null when no movement key is held', () => {
    const input = new Input()
    expect(dir(input)).toBeNull()
  })

  it('returns the direction of a single held movement key', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    expect(dir(input)).toBe('up')
  })

  it('prefers the most-recently-pressed movement key when several are held', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    expect(dir(input)).toBe('up')
    // Press a second movement key while the first is still held.
    keydown(input, DEFAULT_KEYS.right)
    expect(dir(input)).toBe('right') // last pressed wins
  })

  it('falls back to the previously-held movement key when the most recent is released', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    keydown(input, DEFAULT_KEYS.right)
    expect(dir(input)).toBe('right')
    // Release the most-recent — should fall back to 'up'.
    keyup(input, DEFAULT_KEYS.right)
    expect(dir(input)).toBe('up')
  })

  it('returns null once every movement key has been released', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    keydown(input, DEFAULT_KEYS.right)
    keyup(input, DEFAULT_KEYS.right)
    keyup(input, DEFAULT_KEYS.up)
    expect(dir(input)).toBeNull()
  })

  it('tracks press order across a longer sequence (up → down → left → right)', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    expect(dir(input)).toBe('up')
    keydown(input, DEFAULT_KEYS.down)
    expect(dir(input)).toBe('down')
    keydown(input, DEFAULT_KEYS.left)
    expect(dir(input)).toBe('left')
    keydown(input, DEFAULT_KEYS.right)
    expect(dir(input)).toBe('right')
    // Releasing in any order should reveal the next most-recent still-held.
    keyup(input, DEFAULT_KEYS.right)
    expect(dir(input)).toBe('left')
    keyup(input, DEFAULT_KEYS.left)
    expect(dir(input)).toBe('down')
    keyup(input, DEFAULT_KEYS.down)
    expect(dir(input)).toBe('up')
    keyup(input, DEFAULT_KEYS.up)
    expect(dir(input)).toBeNull()
  })

  it('does not push duplicate entries on key auto-repeat', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    keydown(input, DEFAULT_KEYS.up) // browser auto-repeat: still held
    keydown(input, DEFAULT_KEYS.up)
    keydown(input, DEFAULT_KEYS.down)
    // Even after three 'up' keydowns, releasing 'down' must fall back to 'up'.
    keyup(input, DEFAULT_KEYS.down)
    expect(dir(input)).toBe('up')
  })

  it('ignores non-movement keys when computing movement direction', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.fire)
    keydown(input, DEFAULT_KEYS.up)
    keydown(input, DEFAULT_KEYS.pause)
    expect(dir(input)).toBe('up')
    keyup(input, DEFAULT_KEYS.fire)
    expect(dir(input)).toBe('up')
  })

  it('respects custom key bindings', () => {
    const input = new Input({
      ...DEFAULT_KEYS,
      up: 'KeyW',
      down: 'KeyS',
      left: 'KeyA',
      right: 'KeyD',
    })
    keydown(input, 'KeyW')
    expect(dir(input)).toBe('up')
    keydown(input, 'KeyD')
    expect(dir(input)).toBe('right')
    keyup(input, 'KeyD')
    expect(dir(input)).toBe('up')
  })

  it('clears per-frame justPressed/justReleased state on endFrame without dropping held keys', () => {
    const input = new Input()
    keydown(input, DEFAULT_KEYS.up)
    input.endFrame()
    expect(dir(input)).toBe('up') // still held across frames
    keydown(input, DEFAULT_KEYS.right)
    input.endFrame()
    expect(dir(input)).toBe('right') // still last-pressed-wins after a frame boundary
  })
})
