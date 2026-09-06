import { DEFAULT_KEYS, DEFAULT_P2_KEYS, isModifierCode, parseBinding } from './Input'
import { DEFAULT_THEME } from '../config/theme'
import type { GameSettings, KeyBindings } from '../types'

export const SETTINGS_KEY = 'bc_settings'

/**
 * Active gameplay actions each player drives — the only actions relevant to
 * P2 (movement + fire + super items). System keys (pause / reset / snapshot /
 * theme / fullscreen) are P1-global: P2's Input is never polled for them, so
 * its mirrored system bindings are excluded from conflict checks and from
 * the panel's P2 tab. Order is load-bearing for the UI's row layout.
 */
export const P2_ACTIVE_ACTIONS = [
  'up',
  'down',
  'left',
  'right',
  'fire',
  'guard',
  'frenzy',
  'rewind',
] as const

/** Actions shown in the P2 tab — exactly {@link P2_ACTIVE_ACTIONS}. */
export type P2Action = (typeof P2_ACTIVE_ACTIONS)[number]

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
    keys2: { ...DEFAULT_P2_KEYS },
  }

  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      const saved = JSON.parse(raw)
      // Legacy saves predate keys2 (two-player §347): `{ ...saved.keys2 }` on
      // undefined spreads to {} so the per-field merge falls back to P2's
      // defaults — no explicit migration branch needed.
      const merged = {
        ...defaults,
        ...saved,
        keys: { ...defaults.keys, ...saved.keys },
        keys2: { ...defaults.keys2, ...saved.keys2 },
      }
      // Repair any previously-saved binding whose primary key is a pure
      // modifier (e.g. the old "Alt+AltLeft" capture bug). Such a binding can
      // never fire, so we fall back to its default. P2 repairs against its
      // OWN defaults (KeyF, not Space).
      merged.keys = sanitizeKeys(merged.keys)
      merged.keys2 = sanitizeKeys(merged.keys2, DEFAULT_P2_KEYS)
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
export function sanitizeKeys(
  keys: KeyBindings,
  defaults: KeyBindings = DEFAULT_KEYS,
): KeyBindings {
  const out: KeyBindings = { ...keys }
  for (const action of Object.keys(defaults) as (keyof KeyBindings)[]) {
    const binding = out[action]
    if (!binding || isModifierCode(parseBinding(binding).code)) {
      out[action] = defaults[action]
    }
  }
  return out
}

/**
 * Cross-player binding conflict: would assigning `binding` to `action` on
 * `player`'s key set collide with the OTHER player's ACTIVE binding of the
 * same action-class? Only the actions in {@link P2_ACTIVE_ACTIONS} are
 * checked — system keys are P1-global (P2's Input is never polled for them,
 * so its mirrored pause/reset/… defaults can never actually clash), and an
 * exact modifier+code match is required (Shift+R ≠ R, same-player semantics).
 *
 * Pure + headless so the panel gate is regression-tested (AGENTS §8),
 * mirroring the uiFlowGates.ts extraction pattern.
 *
 * @returns the OTHER player's colliding action name, or null when free.
 */
export function findCrossPlayerConflict(
  player: 1 | 2,
  _action: P2Action,
  binding: string,
  keys1: KeyBindings,
  keys2: KeyBindings,
): P2Action | null {
  const otherKeys = player === 1 ? keys2 : keys1
  // NOTE: unlike the same-player dedupe, the SAME action name on the other
  // player IS the meaningful collision (P1.fire = Space, P2 wants fire =
  // Space → conflict), so no `other !== action` guard here.
  for (const other of P2_ACTIVE_ACTIONS) {
    if (otherKeys[other] === binding) return other
  }
  return null
}

/** Persist the settings object to localStorage (silently ignoring failures). */
export function persistSettings(settings: GameSettings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    /* ignore */
  }
}

// ================================================================
// High-score persistence (plan/refactor.agy.md §1.3 Phase C — browser
// I/O does not belong on the World; the `highScore` FIELD stays there
// because it is serialized gameplay state).
// ================================================================

export const HIGH_SCORE_KEY = 'bc_highscore'

/** Read the persisted high score (0 when storage is unavailable/corrupt). */
export function loadHighScore(): number {
  try {
    return parseInt(localStorage.getItem(HIGH_SCORE_KEY) || '0', 10) || 0
  } catch {
    return 0
  }
}

/** Persist a high score to localStorage (silently ignoring failures). */
export function persistHighScore(score: number): void {
  try {
    localStorage.setItem(HIGH_SCORE_KEY, String(score))
  } catch {
    /* ignore */
  }
}
