import type { InputLike } from './Input'
import type { Direction } from '../constants'

/**
 * Lie-Back-Win-Mode §3.4: Auto-fire decorator for the human player.
 *
 * Wraps a real `Input` and makes `isFiring()` return `true` automatically
 * until the player presses the actual fire key — at which point control
 * is fully handed back to the human. Re-armed on each `reset()` (called
 * by Game at stage start).
 *
 * Movement is always transparent (pass-through).
 *
 * Key design: the decorated (auto-fired) input is what the replay records,
 * so playback reproduces the exact same auto-fire frames. This is intentional —
 * the human can always press fire to take over, and the auto-fire is just
 * a convenience, not a separate input source.
 */
export class AutoFireInput implements InputLike {
  private inner: InputLike
  /** Whether auto-fire is armed. Disarmed permanently on first real fire press. */
  private armed = true

  constructor(inner: InputLike) {
    this.inner = inner
  }

  getMoveDirection(): Direction | null {
    return this.inner.getMoveDirection()
  }

  isFiring(): boolean {
    if (this.armed) {
      // If the real input is firing, disarm auto-fire permanently this stage.
      if (this.inner.isFiring()) {
        this.armed = false
        return true // still fire this frame
      }
      return true // auto-fire
    }
    return this.inner.isFiring()
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    // Auto-fire only fires; super-items are always human-controlled.
    return this.inner.wasItemPressed(kind)
  }

  endFrame(): void {
    this.inner.endFrame()
  }

  /** Re-arm auto-fire (called by Game at each stage start). */
  reset(): void {
    this.armed = true
    this.inner.reset()
  }
}
