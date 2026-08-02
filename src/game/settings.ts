import { DEFAULT_KEYS, isModifierCode, parseBinding } from './Input'
import { DEFAULT_THEME } from '../config/theme'
import type { GameSettings, KeyBindings } from '../types'

export const SETTINGS_KEY = 'bc_settings'

/**
 * Load persisted settings, merging over defaults and repairing any corrupt
 * key bindings (a binding whose primary key is a pure modifier can never
 * fire — fall back to its default).
 */
export function loadSettings(): GameSettings {
  const defaults: GameSettings = {
    volume: 0.3,
    difficulty: 'classic',
    theme: DEFAULT_THEME,
    screenScale: 1,
    performanceMode: false,
    keys: { ...DEFAULT_KEYS },
  }

  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      const saved = JSON.parse(raw)
      const merged = { ...defaults, ...saved, keys: { ...defaults.keys, ...saved.keys } }
      // Repair any previously-saved binding whose primary key is a pure
      // modifier (e.g. the old "Alt+AltLeft" capture bug). Such a binding can
      // never fire, so we fall back to its default.
      merged.keys = sanitizeKeys(merged.keys)
      return merged
    }
  } catch {
    /* ignore */
  }
  return defaults
}

/**
 * Reset any binding whose primary key is a pure modifier (Alt/Shift/Ctrl/
 * Meta themselves) — these are un-fireable — back to its default. Guards
 * against the historical rebind bug and any corrupt saved value.
 */
export function sanitizeKeys(keys: KeyBindings): KeyBindings {
  const out: KeyBindings = { ...keys }
  for (const action of Object.keys(DEFAULT_KEYS) as (keyof KeyBindings)[]) {
    const binding = out[action]
    if (!binding || isModifierCode(parseBinding(binding).code)) {
      out[action] = DEFAULT_KEYS[action]
    }
  }
  return out
}

/** Persist the settings object to localStorage (silently ignoring failures). */
export function persistSettings(settings: GameSettings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    /* ignore */
  }
}
