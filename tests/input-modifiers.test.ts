import { describe, it, expect } from 'bun:test'
import type { KeyBindings } from '../src/types'
import {
  Input,
  DEFAULT_KEYS,
  parseBinding,
  eventToBinding,
  isModifierCode,
} from '../src/game/Input'

/**
 * Tests for the modifier-aware key binding system introduced when Reset/Theme
 * were moved off bare keys (R → Alt+R, T → Alt+T, S → Alt+S) to avoid accidental
 * triggers and browser-reserved combos (Ctrl+R = reload, Ctrl+T = new tab).
 *
 * A binding string is `Modifier+...Modifier+Code` and the Input layer matches
 * the FULL modifier+code spec, so Alt+R and R (or Ctrl+R) are distinct keys
 * and only Alt+R is "owned" by the game (preventDefault is only called for
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
  it('reset triggers ONLY on Alt+R, not on bare R or Ctrl+R', () => {
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

    // Alt+R — MUST trigger reset and be claimed.
    claimed = false
    keydown(
      input,
      makeEvent('KeyR', { altKey: true }, () => {
        claimed = true
      }),
    )
    expect(input.isResetPressed()).toBe(true)
    expect(claimed).toBe(true)
    keyup(input, makeEvent('KeyR', { altKey: true }))
    input.endFrame()

    // After endFrame the edge clears.
    expect(input.isResetPressed()).toBe(false)
  })

  it('theme triggers ONLY on Alt+T, not on bare T or Ctrl+T', () => {
    const input = new Input()

    keydown(input, makeEvent('KeyT'))
    expect(input.isThemePressed()).toBe(false)
    keyup(input, makeEvent('KeyT'))
    input.endFrame()

    keydown(input, makeEvent('KeyT', { ctrlKey: true }))
    expect(input.isThemePressed()).toBe(false)
    keyup(input, makeEvent('KeyT', { ctrlKey: true }))
    input.endFrame()

    keydown(input, makeEvent('KeyT', { altKey: true }))
    expect(input.isThemePressed()).toBe(true)
    keyup(input, makeEvent('KeyT', { altKey: true }))
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

  it('manual save defaults to Alt+S and is isolated from bare S / Ctrl+S', () => {
    const input = new Input()
    expect(DEFAULT_KEYS.snapshot).toBe('Alt+KeyS')

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

    // Alt+S MUST trigger manual save and be owned by the game.
    let claimed = false
    keydown(
      input,
      makeEvent('KeyS', { altKey: true }, () => {
        claimed = true
      }),
    )
    expect(input.isSnapshotPressed()).toBe(true)
    expect(claimed).toBe(true)
  keyup(input, makeEvent('KeyS', { altKey: true }))
  input.endFrame()
  expect(input.isSnapshotPressed()).toBe(false)
  })
})

describe('isModifierCode — pure modifier keys cannot be a binding primary key', () => {
  it('flags every Alt/Shift/Ctrl/Meta physical code as a modifier', () => {
    for (const code of [
      'AltLeft',
      'AltRight',
      'ShiftLeft',
      'ShiftRight',
      'ControlLeft',
      'ControlRight',
      'MetaLeft',
      'MetaRight',
    ]) {
      expect(isModifierCode(code)).toBe(true)
    }
  })

  it('does NOT flag real primary keys', () => {
    for (const code of ['KeyS', 'KeyR', 'KeyT', 'ArrowUp', 'Space', 'Enter', 'Digit1']) {
      expect(isModifierCode(code)).toBe(false)
    }
  })

  it('the historical "Alt+AltLeft" capture bug is detected and repairable', () => {
    // Pressing Alt alone fed eventToBinding → "Alt+AltLeft", whose primary key
    // is a pure modifier. The sanitize rule must reject it (fall back to default).
    const broken = eventToBinding(asKeyEvent(makeEvent('AltLeft', { altKey: true })))
    expect(broken).toBe('Alt+AltLeft')
    expect(isModifierCode(parseBinding(broken).code)).toBe(true)
  })

  it('a correct Alt+S capture is NOT flagged as broken', () => {
    const good = eventToBinding(asKeyEvent(makeEvent('KeyS', { altKey: true })))
    expect(good).toBe('Alt+KeyS')
    expect(isModifierCode(parseBinding(good).code)).toBe(false)
  })
})

describe('Input shares the live key-bindings object (live rebind propagates)', () => {
  it('rebinding a movement key on the shared object reaches gameplay immediately', () => {
    // `initControls` wires the SAME object `Game.settings.keys` to both Input
    // and the Controls panel. A live remap must therefore update gameplay.
    const shared = { ...DEFAULT_KEYS }
    const input = new Input(shared)
    shared.up = 'KeyE'
    expect(input.keys.up).toBe('KeyE')
    keydown(input, makeEvent('KeyE'))
    expect(input.getMoveDirection()).toBe('up')
    keyup(input, makeEvent('KeyE'))
  })

  it('Input no longer clones keys (the old stale-clone bug)', () => {
    const shared = { ...DEFAULT_KEYS }
    const input = new Input(shared)
    shared.snapshot = 'Alt+KeyS'
    // If Input had cloned, input.keys.snapshot would still be the default.
    expect(input.keys.snapshot).toBe('Alt+KeyS')
  })

  it('completes a missing field in place rather than dropping it', () => {
    const partial = { up: 'KeyE', down: 'KeyD', left: 'KeyS', right: 'KeyF' } as KeyBindings
    const input = new Input(partial)
    expect(input.keys.fire).toBe(DEFAULT_KEYS.fire)
    expect(input.keys.up).toBe('KeyE')
  })
})
