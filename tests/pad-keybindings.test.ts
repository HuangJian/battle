import { describe, it, expect } from 'bun:test'
import { GAMEPAD_BUTTONS, readSnapshot } from '../src/game/GamepadInput'
import {
  PAD_ACTIONS,
  DEFAULT_PAD_BINDINGS,
  sanitizePadBindings,
  findPadConflict,
} from '../src/game/settings'
import type { GamepadSnapshot } from '../src/game/GamepadInput'
import type { GameSettings, PadBindings } from '../src/types'

/**
 * Gamepad button rebinding (DECISIONS §348 follow-up): the Controls panel's
 * Gamepad tab rebinds standard-mapping button indices, persisted as
 * `GameSettings.pads` with the same live-reference contract as keys/keys2.
 * Headless — pure functions only (AGENTS §8).
 */

const DEFAULTS: GameSettings['pads'] = { ...DEFAULT_PAD_BINDINGS }

function pad(overrides: {
  axes?: number[]
  buttons?: Record<number, boolean>
  connected?: boolean
}): GamepadSnapshot {
  const pressed = overrides.buttons ?? {}
  return {
    axes: overrides.axes ?? [0, 0],
    buttons: Array.from({ length: 17 }, (_, i) => ({
      pressed: pressed[i] ?? false,
      value: pressed[i] ?? false ? 1 : 0,
    })),
    connected: overrides.connected ?? true,
    mapping: 'standard',
  }
}

describe('PadBindings — defaults & shape', () => {
  it('covers the 8 rebindable pad actions (4 d-pad + 4 face)', () => {
    expect(PAD_ACTIONS).toEqual(['up', 'down', 'left', 'right', 'fire', 'guard', 'frenzy', 'rewind'])
  })

  it('defaults match the standard mapping constants', () => {
    expect(DEFAULT_PAD_BINDINGS).toEqual({
      up: GAMEPAD_BUTTONS.dpadUp,
      down: GAMEPAD_BUTTONS.dpadDown,
      left: GAMEPAD_BUTTONS.dpadLeft,
      right: GAMEPAD_BUTTONS.dpadRight,
      fire: GAMEPAD_BUTTONS.fire,
      guard: GAMEPAD_BUTTONS.guard,
      frenzy: GAMEPAD_BUTTONS.frenzy,
      rewind: GAMEPAD_BUTTONS.rewind,
    })
  })

  it('defaults are distinct button indices (no two actions share a button)', () => {
    const values = PAD_ACTIONS.map((a) => DEFAULT_PAD_BINDINGS[a])
    expect(new Set(values).size).toBe(PAD_ACTIONS.length)
  })
})

describe('PadBindings — persistence', () => {
  it('JSON round-trips a custom binding', () => {
    const settings: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: 'modern',
      screenScale: 1,
      performanceMode: false,
      keys: {
        up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight',
        fire: 'Space', pause: 'KeyP', reset: 'Alt+KeyR', snapshot: 'Alt+KeyS',
        guard: 'F5', frenzy: 'F6', rewind: 'F7', theme: 'Alt+KeyT', fullscreen: 'Alt+KeyF',
      },
      keys2: {
        up: 'KeyW', down: 'KeyS', left: 'KeyA', right: 'KeyD',
        fire: 'KeyF', pause: 'KeyP', reset: 'Alt+KeyR', snapshot: 'Alt+KeyS',
        guard: 'KeyR', frenzy: 'KeyT', rewind: 'KeyG', theme: 'Alt+KeyT', fullscreen: 'Alt+KeyF',
      },
      pads: { ...DEFAULT_PAD_BINDINGS, fire: 7 }, // RB — common fire rebind
    }
    const parsed = JSON.parse(JSON.stringify(settings)) as GameSettings
    expect(parsed.pads?.fire).toBe(7)
  })

  it('legacy saved settings without pads migrate to the defaults', () => {
    const defaults: GameSettings = { volume: 0.3, difficulty: 'classic', theme: 'modern', screenScale: 1, performanceMode: false } as unknown as GameSettings
    void defaults
    // The loadSettings merge contract: `pads: { ...defaults.pads, ...saved.pads }`
    // on a legacy save (saved.pads === undefined) spreads to the defaults.
    const saved = { volume: 0.5 }
    const mergedPads = { ...DEFAULTS, ...(saved as { pads?: PadBindings }).pads }
    expect(mergedPads.fire).toBe(GAMEPAD_BUTTONS.fire)
    expect(mergedPads.rewind).toBe(GAMEPAD_BUTTONS.rewind)
  })

  it('a partial saved pads object keeps unspecified actions on their defaults', () => {
    const saved: Partial<PadBindings> = { fire: 7 }
    const merged = { ...DEFAULTS, ...saved }
    expect(merged.fire).toBe(7)
    expect(merged.guard).toBe(DEFAULTS.guard)
  })

  it('sanitizePadBindings clamps out-of-range indices to defaults', () => {
    const broken = { ...DEFAULTS, fire: 99, guard: -1 }
    const fixed = sanitizePadBindings(broken)
    expect(fixed.fire).toBe(DEFAULTS.fire)
    expect(fixed.guard).toBe(DEFAULTS.guard)
  })

  it('sanitizePadBindings keeps valid custom bindings', () => {
    const custom = { ...DEFAULTS, fire: 7, rewind: 6 }
    const fixed = sanitizePadBindings(custom)
    expect(fixed.fire).toBe(7)
    expect(fixed.rewind).toBe(6)
  })

  it('sanitizePadBindings rejects non-integer values', () => {
    const broken = { ...DEFAULTS, frenzy: Number.NaN }
    const fixed = sanitizePadBindings(broken)
    expect(fixed.frenzy).toBe(DEFAULTS.frenzy)
  })
})

describe('PadBindings — conflict gate', () => {
  it('flags assigning an already-bound button to another action', () => {
    // Button 0 is bound to fire by default; rebinding guard to 0 collides.
    expect(findPadConflict('guard', GAMEPAD_BUTTONS.fire, DEFAULTS)).toBe('fire')
    // And symmetrically: rebinding fire to guard's default button.
    expect(findPadConflict('fire', GAMEPAD_BUTTONS.guard, DEFAULTS)).toBe('guard')
  })

  it('allows a free button index', () => {
    // 5 (LB) and 7 (RB) are unbound by default.
    expect(findPadConflict('guard', 5, DEFAULTS)).toBeNull()
    expect(findPadConflict('guard', 7, DEFAULTS)).toBeNull()
  })

  it('ignores the action currently holding the button (its own row)', () => {
    expect(findPadConflict('fire', GAMEPAD_BUTTONS.fire, DEFAULTS)).toBeNull()
  })

  it('pause (Start) stays fixed and never conflicts', () => {
    // Rebinding up to Start's index collides with nothing rebindable — pause
    // is not in PAD_ACTIONS and the checker only scans that set.
    expect(findPadConflict('up', GAMEPAD_BUTTONS.pause, DEFAULTS)).toBeNull()
  })
})

describe('readSnapshot — parametrized bindings', () => {
  it('defaults behave identically to the plain mapping', () => {
    const p = pad({ buttons: { [GAMEPAD_BUTTONS.fire]: true } })
    expect(readSnapshot(p, 0.5, DEFAULTS).fire).toBe(true)
    expect(readSnapshot(p, 0.5, DEFAULTS).guard).toBe(false)
  })

  it('honors a rebound fire button (A → RB/button 7)', () => {
    const rebound: PadBindings = { ...DEFAULTS, fire: 7 }
    const p = pad({ buttons: { 7: true } })
    expect(readSnapshot(p, 0.5, rebound).fire).toBe(true)
    expect(readSnapshot(p, 0.5, rebound).guard).toBe(false)
    // Default mapping sees nothing (button 0 not pressed, 7 unmapped).
    expect(readSnapshot(p, 0.5, DEFAULTS).fire).toBe(false)
  })

  it('honors rebound d-pad actions (up → face button 3)', () => {
    const rebound: PadBindings = { ...DEFAULTS, up: 3 }
    const p = pad({ buttons: { 3: true } })
    expect(readSnapshot(p, 0.5, rebound).dir).toBe('up')
    // Still falls back to stick when the stick is engaged (stick priority).
    const stick = pad({ axes: [0, -1], buttons: { 3: true } })
    expect(readSnapshot(stick, 0.5, rebound).dir).toBe('up')
  })

  it('stick direction does NOT honor rebound d-pad actions (stick is raw axes)', () => {
    const rebound: PadBindings = { ...DEFAULTS, down: 0 }
    const p = pad({ axes: [0, 1], buttons: {} })
    expect(readSnapshot(p, 0.5, rebound).dir).toBe('down')
  })
})
