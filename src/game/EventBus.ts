import type { GameEvent } from '../types'

/**
 * Double-buffered game-event bus (plan/refactor.agy.md §1.3 Phase E),
 * composed into World. Simulation systems push events during a tick
 * (`world.pushEvent`); the presentation/audio/stats layers consume them once
 * per frame via `consumeEvents()`.
 *
 * The double buffer means consuming never allocates: the just-returned array
 * becomes the next accumulation buffer after being cleared.
 */
export class EventBus {
  private active: GameEvent[] = []
  private spare: GameEvent[] = []

  push(event: GameEvent): void {
    this.active.push(event)
  }

  /** Swap buffers and return the events accumulated since the last consume. */
  consume(): GameEvent[] {
    const out = this.active
    this.active = this.spare
    this.spare = out
    this.active.length = 0
    return out
  }

  /** Drop all pending events without delivering them (preview / reset paths). */
  clear(): void {
    this.active.length = 0
  }

  /**
   * Read-only view of the events accumulated since the last consume — for
   * tests and diagnostics that inspect pending events without draining them.
   */
  get items(): readonly GameEvent[] {
    return this.active
  }
}
