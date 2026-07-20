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

  private onKeyDown = (e: KeyboardEvent): void => {
    // Prevent page scroll for game keys
    if (this.isGameKey(e.code)) {
      e.preventDefault()
    }
    if (!this.pressed.has(e.code)) {
      this.justPressed.add(e.code)
    }
    this.pressed.add(e.code)
  }

  private onKeyUp = (e: KeyboardEvent): void => {
    this.pressed.delete(e.code)
    this.justReleased.add(e.code)
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

  /** Get player movement direction, or null if no movement key pressed */
  getMoveDirection(): Direction | null {
    const k = this.keys
    // Priority: last pressed wins — but for simplicity, check in order
    if (this.isDown(k.up)) return 'up'
    if (this.isDown(k.down)) return 'down'
    if (this.isDown(k.left)) return 'left'
    if (this.isDown(k.right)) return 'right'
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
}
