import type { Direction } from '../constants'
import type { KeyBindings } from '../types'

/**
 * Default key bindings.
 */
export const DEFAULT_KEYS: KeyBindings = {
  up: 'ArrowUp',
  down: 'ArrowDown',
  left: 'ArrowLeft',
  right: 'ArrowRight',
  fire: 'Space',
  pause: 'KeyP',
  reset: 'KeyR',
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
    this.keys = keys ?? { ...DEFAULT_KEYS }
  }

  attach(target: Window = window): void {
    target.addEventListener('keydown', this.onKeyDown)
    target.addEventListener('keyup', this.onKeyUp)
  }

  detach(target: Window = window): void {
    target.removeEventListener('keydown', this.onKeyDown)
    target.removeEventListener('keyup', this.onKeyUp)
  }

  /** Map a key code to its movement direction, or null if it isn't a movement key. */
  private moveDirFor(code: string): Direction | null {
    const k = this.keys
    if (code === k.up) return 'up'
    if (code === k.down) return 'down'
    if (code === k.left) return 'left'
    if (code === k.right) return 'right'
    return null
  }

  private onKeyDown = (e: KeyboardEvent): void => {
    // Prevent page scroll for game keys
    if (this.isGameKey(e.code)) {
      e.preventDefault()
    }
    if (!this.pressed.has(e.code)) {
      this.justPressed.add(e.code)
      // Track movement keys in press order for "last pressed wins" priority.
      if (this.moveDirFor(e.code)) {
        this.moveStack.push(e.code)
      }
    }
    this.pressed.add(e.code)
  }

  private onKeyUp = (e: KeyboardEvent): void => {
    this.pressed.delete(e.code)
    this.justReleased.add(e.code)
    if (this.moveDirFor(e.code)) {
      const i = this.moveStack.indexOf(e.code)
      if (i >= 0) this.moveStack.splice(i, 1)
    }
  }

  private isGameKey(code: string): boolean {
    const k = this.keys
    return (
      code === k.up ||
      code === k.down ||
      code === k.left ||
      code === k.right ||
      code === k.fire ||
      code === k.pause ||
      code === k.reset ||
      code === 'Enter' ||
      code === 'Escape'
    )
  }

  isDown(code: string): boolean {
    return this.pressed.has(code)
  }

  wasPressed(code: string): boolean {
    return this.justPressed.has(code)
  }

  wasReleased(code: string): boolean {
    return this.justReleased.has(code)
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
      const code = this.moveStack[i]
      if (this.pressed.has(code)) {
        return this.moveDirFor(code)
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
