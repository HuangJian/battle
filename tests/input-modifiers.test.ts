import { describe, it, expect } from 'bun:test'
import { Input, DEFAULT_KEYS, parseBinding, eventToBinding } from '../src/game/Input'

/**
 * Tests for the modifier-aware key binding system introduced when Reset/Theme
 * were moved off bare keys (R → Shift+R, T → Shift+T) to avoid accidental
 * triggers and browser-reserved combos (Ctrl+R = reload, Ctrl+T = new tab).
 *
 * A binding string is `Modifier+...Modifier+Code` and the Input layer matches
 * the FULL modifier+code spec, so Shift+R and R (or Ctrl+R) are distinct keys
 * and only Shift+R is "owned" by the game (preventDefault is only called for
 * exactly-owned combos — bare R / Ctrl+R pass through to the browser).
 */

interface StubEvent {
  code: string
  ctrlKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
  metaKey?: boolean
  preventDefault: () => void
  stopImmediatePropagation?: () => void
}

function makeEvent(
  code: string,
  mods: Partial<{ ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean }> = {},
  preventDefault: () => void = () => {},
): StubEvent {
  return { code, preventDefault, ...mods }
}

/** Cast a stub into the shape eventToBinding reads (code + 4 modifier flags). */
function asKeyEvent(e: StubEvent): KeyboardEvent {
  return e as unknown as KeyboardEvent
}

function keydown(input: Input, e: StubEvent): void {
  ;(input as unknown as { onKeyDown: (e: StubEvent) => void }).onKeyDown(e)
}

function keyup(input: Input, e: StubEvent): void {
  ;(input as unknown as { onKeyUp: (e: StubEvent) => void }).onKeyUp(e)
}

describe('parseBinding — Modifier+Code spec', () => {
  it('parses a single modifier combo (Shift+KeyR)', () => {
    const s = parseBinding('Shift+KeyR')
    expect(s.code).toBe('KeyR')
    expect(s.shift).toBe(true)
    expect(s.ctrl).toBe(false)
    expect(s.alt).toBe(false)
    expect(s.meta).toBe(false)
  })

  it('parses a bare code with no modifiers (ArrowUp)', () => {
    const s = parseBinding('ArrowUp')
    expect(s.code).toBe('ArrowUp')
    expect(s.shift).toBe(false)
    expect(s.ctrl).toBe(false)
  })

  it('parses multiple modifiers case-insensitively (control+alt+KeyT)', () => {
    const s = parseBinding('control+alt+KeyT')
    expect(s.code).toBe('KeyT')
    expect(s.ctrl).toBe(true)
    expect(s.alt).toBe(true)
    expect(s.shift).toBe(false)
  })

  it('accepts meta aliases (cmd / command / win)', () => {
    expect(parseBinding('cmd+KeyM').meta).toBe(true)
    expect(parseBinding('command+KeyM').meta).toBe(true)
    expect(parseBinding('win+KeyM').meta).toBe(true)
  })
})

describe('eventToBinding — capture full combo from a live event', () => {
  it('produces Shift+KeyR for a shift+KeyR event', () => {
    expect(eventToBinding(asKeyEvent(makeEvent('KeyR', { shiftKey: true })))).toBe('Shift+KeyR')
  })

  it('produces a bare code when no modifier is held', () => {
    expect(eventToBinding(asKeyEvent(makeEvent('ArrowUp')))).toBe('ArrowUp')
  })

  it('orders modifiers as Control+Shift+Alt+Meta', () => {
    const e = makeEvent('KeyT', { shiftKey: true, ctrlKey: true })
    expect(eventToBinding(asKeyEvent(e))).toBe('Control+Shift+KeyT')
  })
})

describe('Input — modifier isolation for non-combat shortcuts', () => {
  it('reset triggers ONLY on Shift+R, not on bare R or Ctrl+R', () => {
    const input = new Input()

    // Bare R — must NOT trigger reset, must NOT be owned by the game.
    let claimed = false
    keydown(
      input,
      makeEvent('KeyR', {}, () => {
        claimed = true
      }),
    )
    expect(input.isResetPressed()).toBe(false)
    expect(claimed).toBe(false)
    keyup(input, makeEvent('KeyR'))
    input.endFrame()

    // Ctrl+R (browser reload) — must NOT trigger reset, must NOT be claimed.
    claimed = false
    keydown(
      input,
      makeEvent('KeyR', { ctrlKey: true }, () => {
        claimed = true
      }),
    )
    expect(input.isResetPressed()).toBe(false)
    expect(claimed).toBe(false)
    keyup(input, makeEvent('KeyR', { ctrlKey: true }))
    input.endFrame()

    // Shift+R — MUST trigger reset and be claimed.
    claimed = false
    keydown(
      input,
      makeEvent('KeyR', { shiftKey: true }, () => {
        claimed = true
      }),
    )
    expect(input.isResetPressed()).toBe(true)
    expect(claimed).toBe(true)
    keyup(input, makeEvent('KeyR', { shiftKey: true }))
    input.endFrame()

    // After endFrame the edge clears.
    expect(input.isResetPressed()).toBe(false)
  })

  it('theme triggers ONLY on Shift+T, not on bare T or Ctrl+T', () => {
    const input = new Input()

    keydown(input, makeEvent('KeyT'))
    expect(input.isThemePressed()).toBe(false)
    keyup(input, makeEvent('KeyT'))
    input.endFrame()

    keydown(input, makeEvent('KeyT', { ctrlKey: true }))
    expect(input.isThemePressed()).toBe(false)
    keyup(input, makeEvent('KeyT', { ctrlKey: true }))
    input.endFrame()

    keydown(input, makeEvent('KeyT', { shiftKey: true }))
    expect(input.isThemePressed()).toBe(true)
    keyup(input, makeEvent('KeyT', { shiftKey: true }))
    input.endFrame()

    expect(input.isThemePressed()).toBe(false)
  })

  it('supports custom modifier bindings (e.g. Alt+KeyR for reset)', () => {
    const input = new Input({ ...DEFAULT_KEYS, reset: 'Alt+KeyR' })
    // Bare R is no longer reset.
    keydown(input, makeEvent('KeyR'))
    expect(input.isResetPressed()).toBe(false)
    keyup(input, makeEvent('KeyR'))
    input.endFrame()
    // Alt+KeyR is reset.
    keydown(input, makeEvent('KeyR', { altKey: true }))
    expect(input.isResetPressed()).toBe(true)
    keyup(input, makeEvent('KeyR', { altKey: true }))
    input.endFrame()
  })

  it('movement keys without modifiers are unaffected by the modifier logic', () => {
    const input = new Input()
    keydown(input, makeEvent('ArrowUp'))
    expect(input.getMoveDirection()).toBe('up')
    // Shift+ArrowUp must NOT register as a movement key (distinct from ArrowUp).
    keyup(input, makeEvent('ArrowUp'))
    keydown(input, makeEvent('ArrowUp', { shiftKey: true }))
    // Still only the previous ArrowUp? It was released above, so nothing held.
    expect(input.getMoveDirection()).toBeNull()
    keyup(input, makeEvent('ArrowUp', { shiftKey: true }))
  })

  it('manual save defaults to Shift+S and is isolated from bare S / Ctrl+S', () => {
    const input = new Input()
    expect(DEFAULT_KEYS.snapshot).toBe('Shift+KeyS')

    // Bare S (menu-down navigation) must NOT trigger manual save.
    keydown(input, makeEvent('KeyS'))
    expect(input.isSnapshotPressed()).toBe(false)
    keyup(input, makeEvent('KeyS'))
    input.endFrame()

    // Ctrl+S (browser "save page") must NOT trigger manual save.
    keydown(input, makeEvent('KeyS', { ctrlKey: true }))
    expect(input.isSnapshotPressed()).toBe(false)
    keyup(input, makeEvent('KeyS', { ctrlKey: true }))
    input.endFrame()

    // Shift+S MUST trigger manual save and be owned by the game.
    let claimed = false
    keydown(
      input,
      makeEvent('KeyS', { shiftKey: true }, () => {
        claimed = true
      }),
    )
    expect(input.isSnapshotPressed()).toBe(true)
    expect(claimed).toBe(true)
    keyup(input, makeEvent('KeyS', { shiftKey: true }))
    input.endFrame()
    expect(input.isSnapshotPressed()).toBe(false)
  })
})
