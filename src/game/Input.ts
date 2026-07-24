import type { Direction } from '../constants'
import type { KeyBindings } from '../types'

/**
 * Default key bindings.
 *
 * Non-combat shortcuts (reset / theme) use a *modifier* combo so they can't
 * be hit by accident mid-play, and — crucially — they avoid browser-reserved
 * combos: Ctrl+R (reload) and Ctrl+T (new tab) are off-limits, so we use
 * Shift+R / Shift+T, which no browser claims.
 *
 * A binding string is `Modifier+...Modifier+Code` (modifiers optional, any
 * order, case-insensitive): e.g. 'Shift+KeyR', 'Control+KeyT', 'ArrowUp'.
 * The Input layer matches the FULL modifier+code spec, so a bare 'R' no longer
 * triggers reset and Ctrl+R no longer gets swallowed.
 */
export const DEFAULT_KEYS: KeyBindings = {
  up: 'ArrowUp',
  down: 'ArrowDown',
  left: 'ArrowLeft',
  right: 'ArrowRight',
  fire: 'Space',
  pause: 'KeyP',
  reset: 'Shift+KeyR',
  snapshot: 'Shift+KeyS',
  theme: 'Shift+KeyT',
}

/** A parsed binding: the physical `code` plus which modifiers must be held. */
interface BindingSpec {
  code: string
  ctrl: boolean
  shift: boolean
  alt: boolean
  meta: boolean
}

/**
 * Parse a binding string into a spec. Modifiers are the leading '+' segments
 * (case-insensitive: ctrl/control, shift, alt/option, meta/cmd/win/command);
 * the final segment is the `KeyboardEvent.code` (e.g. 'KeyR', 'ArrowUp').
 * Exported so the controls-rebind UI can render modifier combos.
 */
export function parseBinding(s: string): BindingSpec {
  const parts = s.split('+')
  const code = parts[parts.length - 1]
  let ctrl = false
  let shift = false
  let alt = false
  let meta = false
  for (let i = 0; i < parts.length - 1; i++) {
    const m = parts[i].toLowerCase()
    if (m === 'ctrl' || m === 'control') ctrl = true
    else if (m === 'shift') shift = true
    else if (m === 'alt' || m === 'option') alt = true
    else if (m === 'meta' || m === 'cmd' || m === 'command' || m === 'win') meta = true
  }
  return { code, ctrl, shift, alt, meta }
}

/**
 * Canonical id for a binding spec, e.g. 'S:KeyR' (shift) or ':ArrowUp' (none).
 * Two ids are equal iff they describe the exact same physical key + modifier
 * state, which is what lets Shift+R and R coexist as distinct keys.
 */
function specKeyId(spec: BindingSpec): string {
  return `${spec.ctrl ? 'C' : ''}${spec.shift ? 'S' : ''}${spec.alt ? 'A' : ''}${spec.meta ? 'M' : ''}:${spec.code}`
}

/** Canonical id for a live keyboard event. */
function eventKeyId(e: KeyboardEvent): string {
  return `${e.ctrlKey ? 'C' : ''}${e.shiftKey ? 'S' : ''}${e.altKey ? 'A' : ''}${e.metaKey ? 'M' : ''}:${e.code}`
}

/** Canonical id for a binding string (parser entry point used by queries). */
function keyIdFromBinding(s: string): string {
  return specKeyId(parseBinding(s))
}

/**
 * Build a binding string from a live keyboard event, preserving any held
 * modifiers in the same `Modifier+Code` format `parseBinding` expects (e.g.
 * Shift+KeyR). Exported so the controls-rebind UI can capture modifier combos
 * instead of losing them (a bare `e.code` would drop Shift/Ctrl/Alt/Meta).
 */
export function eventToBinding(e: KeyboardEvent): string {
  const mods: string[] = []
  if (e.ctrlKey) mods.push('Control')
  if (e.shiftKey) mods.push('Shift')
  if (e.altKey) mods.push('Alt')
  if (e.metaKey) mods.push('Meta')
  return mods.length ? `${mods.join('+')}+${e.code}` : e.code
}

/**
 * Input — captures keyboard state.
 * The simulation reads from this; Input never modifies the World.
 */
export class Input {
  private pressed = new Set<string>()
  private justPressed = new Set<string>()
  private justReleased = new Set<string>()
  /**
   * Movement keys currently held, in the order they were pressed (most
   * recent last). Drives the "last pressed wins" priority in
   * `getMoveDirection()`. Maintained on keydown/keyup, NOT cleared by
   * `endFrame()` — only by releasing the key.
   */
  private moveStack: string[] = []
  keys: KeyBindings

  // Menu navigation
  menuUp = false
  menuDown = false
  menuConfirm = false
  menuBack = false

  constructor(keys?: KeyBindings) {
    // Merge with defaults so any missing field (e.g. a `theme` saved before
    // it existed) falls back to a valid binding rather than undefined.
    this.keys = { ...DEFAULT_KEYS, ...keys }
  }

  attach(target: Window = window): void {
    target.addEventListener('keydown', this.onKeyDown)
    target.addEventListener('keyup', this.onKeyUp)
  }

  detach(target: Window = window): void {
    target.removeEventListener('keydown', this.onKeyDown)
    target.removeEventListener('keyup', this.onKeyUp)
  }

  /** Map a canonical key id to its movement direction, or null if not a move key. */
  private moveDirFor(id: string): Direction | null {
    const k = this.keys
    if (id === keyIdFromBinding(k.up)) return 'up'
    if (id === keyIdFromBinding(k.down)) return 'down'
    if (id === keyIdFromBinding(k.left)) return 'left'
    if (id === keyIdFromBinding(k.right)) return 'right'
    return null
  }

  private onKeyDown = (e: KeyboardEvent): void => {
    const id = eventKeyId(e)
    // Prevent page scroll / browser default ONLY for keys we actually own
    // (exact modifier+code match). A bare 'R' or Ctrl+R is no longer ours,
    // so the browser keeps its reload; Shift+R is ours → we claim it.
    if (this.isGameKey(e)) {
      e.preventDefault()
    }
    if (!this.pressed.has(id)) {
      this.justPressed.add(id)
      // Track movement keys in press order for "last pressed wins" priority.
      if (this.moveDirFor(id)) {
        this.moveStack.push(id)
      }
    }
    this.pressed.add(id)
  }

  private onKeyUp = (e: KeyboardEvent): void => {
    const id = eventKeyId(e)
    this.pressed.delete(id)
    this.justReleased.add(id)
    if (this.moveDirFor(id)) {
      const i = this.moveStack.indexOf(id)
      if (i >= 0) this.moveStack.splice(i, 1)
    }
  }

  private isGameKey(e: KeyboardEvent): boolean {
    const id = eventKeyId(e)
    const k = this.keys
    // Exact spec match — a bare ArrowUp won't satisfy 'Shift+ArrowUp', etc.
    if (
      id === keyIdFromBinding(k.up) ||
      id === keyIdFromBinding(k.down) ||
      id === keyIdFromBinding(k.left) ||
      id === keyIdFromBinding(k.right) ||
      id === keyIdFromBinding(k.fire) ||
      id === keyIdFromBinding(k.pause) ||
      id === keyIdFromBinding(k.reset) ||
      id === keyIdFromBinding(k.snapshot) ||
      id === keyIdFromBinding(k.theme)
    ) {
      return true
    }
    // Enter / Escape are always owned (menu confirm / pause toggle).
    return e.code === 'Enter' || e.code === 'Escape'
  }

  isDown(binding: string): boolean {
    return this.pressed.has(keyIdFromBinding(binding))
  }

  wasPressed(binding: string): boolean {
    return this.justPressed.has(keyIdFromBinding(binding))
  }

  wasReleased(binding: string): boolean {
    return this.justReleased.has(keyIdFromBinding(binding))
  }

  /**
   * Get player movement direction, or null if no movement key is held.
   * Priority is "last pressed wins": the most-recently-pressed movement
   * key that is still held determines the direction. Releasing it falls
   * back to the next most-recently-pressed still-held movement key.
   *
   * (See DECISIONS.md §20 for the rationale — the old implementation
   * checked keys in a fixed order, which didn't match the documented
   * "last pressed wins" intent.)
   */
  getMoveDirection(): Direction | null {
    // Walk the stack from most-recent to oldest. Any stale entry whose key
    // was released without firing onKeyUp (e.g. window blur) is pruned.
    for (let i = this.moveStack.length - 1; i >= 0; i--) {
      const id = this.moveStack[i]
      if (this.pressed.has(id)) {
        return this.moveDirFor(id)
      }
      this.moveStack.splice(i, 1)
    }
    return null
  }

  isFiring(): boolean {
    return this.isDown(this.keys.fire)
  }

  isPausePressed(): boolean {
    return this.wasPressed(this.keys.pause) || this.wasPressed('Escape')
  }

  isResetPressed(): boolean {
    return this.wasPressed(this.keys.reset)
  }

  /** Theme-cycle shortcut (configurable, default Shift+T). */
  isThemePressed(): boolean {
    return this.wasPressed(this.keys.theme)
  }

  /** Manual snapshot shortcut (configurable, default Shift+S). */
  isSnapshotPressed(): boolean {
    return this.wasPressed(this.keys.snapshot)
  }

  isConfirmPressed(): boolean {
    return this.wasPressed('Enter') || this.wasPressed('Space')
  }

  isUpPressed(): boolean {
    return this.wasPressed(this.keys.up) || this.wasPressed('ArrowUp') || this.wasPressed('KeyW')
  }

  isDownPressed(): boolean {
    return (
      this.wasPressed(this.keys.down) || this.wasPressed('ArrowDown') || this.wasPressed('KeyS')
    )
  }

  /** Clear per-frame state. Call at end of each simulation step. */
  endFrame(): void {
    this.justPressed.clear()
    this.justReleased.clear()
  }

  /**
   * Clear ALL input state (held keys, press order, per-frame edges).
   * Used when leaving a state where a key was used for something other than
   * gameplay — e.g. the Space that confirms "start game" must not carry over
   * and read as a held fire key on the first playing frame (auto-fire bug).
   */
  reset(): void {
    this.pressed.clear()
    this.justPressed.clear()
    this.justReleased.clear()
    this.moveStack.length = 0
  }
}
