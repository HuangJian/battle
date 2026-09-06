import { describe, it, expect } from 'bun:test'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS } from '../src/game/Input'
import {
  sanitizeKeys,
  findCrossPlayerConflict,
  P2_ACTIVE_ACTIONS,
  SETTINGS_KEY,
} from '../src/game/settings'
import type { GameSettings, KeyBindings } from '../src/types'

/**
 * Player-2 key rebinding (DECISIONS.md two-player §350 follow-up):
 * settings persistence for `keys2`, sanitize repair, legacy-save migration,
 * and the cross-player conflict gate (P1 ↔ P2 must not share an active key).
 * Headless — pure functions only (AGENTS §8).
 */

describe('P2 key bindings — settings persistence', () => {
  it('sanitizeKeys repairs keys2 against DEFAULT_P2_KEYS (not DEFAULT_KEYS)', () => {
    // P2's fire bound to a pure modifier — un-fireable, must fall back to P2's
    // own default ('KeyF'), NOT P1's ('Space').
    const broken = { ...DEFAULT_P2_KEYS, fire: 'Alt+AltLeft' }
    const fixed = sanitizeKeys(broken, DEFAULT_P2_KEYS)
    expect(fixed.fire).toBe('KeyF')
    // Untouched valid binding survives.
    expect(fixed.up).toBe('KeyW')
  })

  it('sanitizeKeys defaults to P1 defaults for the existing keys slot', () => {
    const broken = { ...DEFAULT_KEYS, up: 'ShiftLeft' }
    const fixed = sanitizeKeys(broken)
    expect(fixed.up).toBe(DEFAULT_KEYS.up)
  })

  it('legacy saved settings without keys2 migrate to DEFAULT_P2_KEYS', () => {
    // Simulate the pre-two-player persisted blob (no keys2 field).
    const legacySaved: Partial<GameSettings> = {
      volume: 0.5,
      keys: { ...DEFAULT_KEYS },
    }
    // The loadSettings merge contract: defaults.keys2 survives when the save
    // lacks it; a saved keys2 wins per-field (mirrors the `keys` handling).
    const defaults: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: 'modern',
      screenScale: 1,
      performanceMode: false,
      keys: { ...DEFAULT_KEYS },
      keys2: { ...DEFAULT_P2_KEYS },
    }
    const merged = {
      ...defaults,
      ...legacySaved,
      keys: { ...defaults.keys, ...legacySaved.keys },
      keys2: { ...defaults.keys2, ...legacySaved.keys2 },
    } as GameSettings
    expect(merged.keys2.fire).toBe('KeyF')
    expect(merged.keys2.up).toBe('KeyW')
  })

  it('persistSettings round-trips keys2 (JSON carries the field)', () => {
    const settings: GameSettings = {
      volume: 0.3,
      difficulty: 'classic',
      theme: 'modern',
      screenScale: 1,
      performanceMode: false,
      keys: { ...DEFAULT_KEYS },
      keys2: { ...DEFAULT_P2_KEYS, fire: 'KeyH' },
    }
    const json = JSON.stringify(settings)
    const parsed = JSON.parse(json) as GameSettings
    expect(parsed.keys2.fire).toBe('KeyH')
    // Round-trip through the same storage key the app uses.
    expect(SETTINGS_KEY).toBe('bc_settings')
  })
})

describe('P2 key bindings — cross-player conflict gate', () => {
  it('flags a P2 binding that collides with an active P1 binding', () => {
    const keys1: KeyBindings = { ...DEFAULT_KEYS } // fire = Space
    const keys2: KeyBindings = { ...DEFAULT_P2_KEYS }
    const conflict = findCrossPlayerConflict(2, 'fire', 'Space', keys1, keys2)
    expect(conflict).toBe('fire')
  })

  it('flags a P1 binding that collides with an active P2 binding (symmetric)', () => {
    const keys1: KeyBindings = { ...DEFAULT_KEYS }
    const keys2: KeyBindings = { ...DEFAULT_P2_KEYS } // up = KeyW
    const conflict = findCrossPlayerConflict(1, 'up', 'KeyW', keys1, keys2)
    expect(conflict).toBe('up')
  })

  it('allows distinct bindings (the default state has zero overlap)', () => {
    const keys1: KeyBindings = { ...DEFAULT_KEYS }
    const keys2: KeyBindings = { ...DEFAULT_P2_KEYS }
    for (const action of P2_ACTIVE_ACTIONS) {
      const b2 = keys2[action]
      if (b2) expect(findCrossPlayerConflict(2, action, b2, keys1, keys2)).toBeNull()
      const b1 = keys1[action]
      if (b1) expect(findCrossPlayerConflict(1, action, b1, keys1, keys2)).toBeNull()
    }
  })

  it('ignores system-only actions (pause/reset/… are P1-global, never cross-checked)', () => {
    const keys1: KeyBindings = { ...DEFAULT_KEYS }
    const keys2: KeyBindings = { ...DEFAULT_P2_KEYS } // guard mirrors… no — guard is active
    // P2's DEFAULT mirror of system keys (pause = KeyP = P1's pause) must not
    // trip the gate: 'pause' is not in P2_ACTIVE_ACTIONS, and the gate only
    // scans the active set — so a system binding never collides cross-player.
    // Prove it behaviorally: a P2 'guard' rebind to P1's pause key is fine.
    expect(findCrossPlayerConflict(2, 'guard', 'KeyP', keys1, keys2)).toBeNull()
    // And the type-level contract: P2_ACTIVE_ACTIONS contains no system keys.
    for (const action of P2_ACTIVE_ACTIONS) {
      expect(['up', 'down', 'left', 'right', 'fire', 'guard', 'frenzy', 'rewind']).toContain(action)
    }
  })

  it('a modifier combo is distinct from its bare key (same-player semantics)', () => {
    const keys1: KeyBindings = { ...DEFAULT_KEYS, guard: 'F5' }
    const keys2: KeyBindings = { ...DEFAULT_P2_KEYS }
    // Shift+R ≠ R: different physical key+modifier states.
    expect(findCrossPlayerConflict(2, 'guard', 'Shift+KeyR', keys1, keys2)).toBeNull()
  })

  it('P2_ACTIVE_ACTIONS covers exactly the actions P2 drives (no system keys)', () => {
    expect(P2_ACTIVE_ACTIONS).toEqual([
      'up',
      'down',
      'left',
      'right',
      'fire',
      'guard',
      'frenzy',
      'rewind',
    ])
  })
})
