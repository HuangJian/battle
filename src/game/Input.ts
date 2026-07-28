import type { Direction } from '../constants'
import type { KeyBindings } from '../types'

/**
 * Minimal input contract the Simulation consumes.
 *
 * Extracted from the concrete `Input` class so headless tools (GodAIInput,
 * level-sim, etc.) can inject a programmatic input source without a DOM.
 * Simulation only ever calls `getMoveDirection()` and `isFiring()`; the
 * remaining methods exist so Game.ts can call `endFrame()` / `reset()` on
 * the same reference it hands to Simulation (AGENTS §2.1 — only Simulation
 * mutates the World, but input state cleanup is Input's own concern).
 */
export interface InputLike {
  getMoveDirection(): Direction | null
  isFiring(): boolean
  /** Whether a super-item release key (guard/frenzy/rewind) was pressed this frame. */
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean
  endFrame(): void
  reset(): void
}

/**
 * Default key bindings.
 *
 * Non-combat shortcuts (reset / theme / snapshot) use a *modifier* combo so
 * they can't be hit by accident mid-play, and — crucially — they avoid
 * browser-reserved combos: Ctrl+R (reload) and Ctrl+T (new tab) are off-limits,
 * so we use Alt+R / Alt+T / Alt+S, which no browser claims.
 *
 * A binding string is `Modifier+...Modifier+Code` (modifiers optional, any
 * order, case-insensitive): e.g. 'Alt+KeyR', 'Control+KeyT', 'ArrowUp'.
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
  reset: 'Alt+KeyR',
  snapshot: 'Alt+KeyS',
  theme: 'Alt+KeyT',
  guard: 'F5',
  frenzy: 'F6',
  fullscreen: 'Alt+KeyF',
}

/**
 * Ensure every action has a binding, filling missing fields in place on the
 * passed object and returning the SAME reference. Used by the Input constructor
 * so the live key-bindings object stays shared with settings + the Controls UI.
 */
function ensureKeys(keys: KeyBindings): KeyBindings {
  for (const action of Object.keys(DEFAULT_KEYS) as (keyof KeyBindings)[]) {
    if (keys[action] === undefined) keys[action] = DEFAULT_KEYS[action]
  }
  return keys
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
 * Physical key codes that are *pure modifiers* — they have no "primary" key of
 * their own. A binding must end in a non-modifier code, so these are rejected
 * as the final segment. This is what stops the rebind UI from capturing
 * "Alt+S" as the Alt key's own keydown ("Alt+AltLeft") instead of waiting for
 * the real key. (See UIManager.onControlsKeyDown.)
 */
export const MODIFIER_CODES = new Set<string>([
  'AltLeft',
  'AltRight',
  'ShiftLeft',
  'ShiftRight',
  'ControlLeft',
  'ControlRight',
  'MetaLeft',
  'MetaRight',
])

/** True if `code` is a pure modifier key (Alt/Shift/Ctrl/Meta), not a primary key. */
export function isModifierCode(code: string): boolean {
  return MODIFIER_CODES.has(code)
}

/**
 * Canonical id for a binding spec, e.g. 'S:KeyR' (shift) or ':ArrowUp' (none).
 * Two ids are equal iff they describe the exact same physical key + modifier
 * state, which is what lets Alt+R and R coexist as distinct keys.
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
 * Alt+KeyR). Exported so the controls-rebind UI can capture modifier combos
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
export class Input implements InputLike {
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
    // Hold the SAME key-bindings object the rest of the app shares (the
    // settings object + the Controls panel's `controlsBindings`). That way a
    // live rebind in the Controls UI mutates this exact object and gameplay
    // sees it immediately — without it, a clone here would go stale the moment
    // the user remaps a key (e.g. movement → EDSF would update the UI label
    // but never reach `getMoveDirection()`). We only fill in any missing field
    // in place (older saves that predate an added action).
    this.keys = keys ? ensureKeys(keys) : { ...DEFAULT_KEYS }
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
    // so the browser keeps its reload; Alt+R is ours → we claim it.
    if (this.isGameKey(e)) {
      e.preventDefault()
    }
    // Suppress the browser's Alt menu / access-key focus-steal. On Windows a
    // bare Alt press moves keyboard focus out of the page (to the browser
    // chrome), so a followed Alt+S/R/T keydown never reaches `window` and the
    // shortcuts silently die until the player clicks back into the canvas.
    // Claiming the Alt keydown prevents that. Alt+Tab is OS-level and
    // unaffected by preventDefault.
    if (e.altKey && (e.code === 'AltLeft' || e.code === 'AltRight')) {
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
      id === keyIdFromBinding(k.theme) ||
      id === keyIdFromBinding(k.guard) ||
      id === keyIdFromBinding(k.frenzy) ||
      id === keyIdFromBinding(k.fullscreen!)
    ) {
      return true
    }
    // Rewind (F7) is always owned (not rebindable)
    if (e.code === 'F7') return true
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

  /** Theme-cycle shortcut (configurable, default Alt+T). */
  isThemePressed(): boolean {
    return this.wasPressed(this.keys.theme)
  }

  /** Super-item release key (guard/frenzy/rewind) pressed this frame. */
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    // 'rewind' is not in keys — it uses F7 which is hardcoded
    if (kind === 'rewind') return this.wasPressed('F7')
    return this.wasPressed(this.keys[kind])
  }

  /** Manual snapshot shortcut (configurable, default Alt+S). */
  isSnapshotPressed(): boolean {
    return this.wasPressed(this.keys.snapshot)
  }

  /** Fullscreen toggle shortcut (configurable, default Alt+F). */
  isFullscreenPressed(): boolean {
    return this.wasPressed(this.keys.fullscreen)
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
