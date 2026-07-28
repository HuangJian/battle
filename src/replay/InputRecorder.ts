import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { WorldSnapshot } from '../snapshot/types'
import { cloneWorld } from '../snapshot/WorldSerializer'
import { FRAME_SCHEMA_VERSION } from './config'
import { packFrame } from './pack'

// ================================================================
// InputRecorder — captures player input per simulation tick
// (plan/replay.md §13)
//
// Recording is passive: it reads the Input interface and never touches
// the World. Each tick's input is packed to 1 byte and appended to a
// buffer. On finalize, the buffer is converted to a Uint8Array with a
// schema version prefix.
// ================================================================

export interface RecorderResult {
  snapshot: WorldSnapshot
  frames: Uint8Array
  tickCount: number
}

export class InputRecorder {
  private frames: number[] = []
  private active = false
  private initialSnapshot: WorldSnapshot | null = null

  /**
   * Start a new recording session. Captures the current World state
   * as the initial snapshot.
   */
  startNew(world: World): void {
    this.frames = []
    this.active = true
    this.initialSnapshot = cloneWorld(world)
  }

  /**
   * Record one frame of input. Called once per simulation tick,
   * after simulation.tick() and before input.endFrame().
   *
   * The recorded frame must exactly match what the simulation
   * consumed that tick — this is the determinism contract.
   */
  recordFrame(input: InputLike): void {
    if (!this.active) return
    this.frames.push(
      packFrame({
        direction: input.getMoveDirection(),
        firing: input.isFiring(),
        guard: input.wasItemPressed('guard'),
        frenzy: input.wasItemPressed('frenzy'),
      }),
    )
  }

  /**
   * Finalize the recording and return the result.
   * Returns null if no frames were captured (empty recording).
   *
   * After finalize(), the recorder is inactive — recordFrame() becomes
   * a no-op until startNew() is called again.
   */
  finalize(): RecorderResult | null {
    if (!this.active || this.frames.length === 0) {
      this.active = false
      return null
    }
    this.active = false

    // Prepend schema version byte
    const packed = new Uint8Array(this.frames.length + 1)
    packed[0] = FRAME_SCHEMA_VERSION
    for (let i = 0; i < this.frames.length; i++) {
      packed[i + 1] = this.frames[i]
    }

    const snapshot = this.initialSnapshot!
    this.initialSnapshot = null
    return { snapshot, frames: packed, tickCount: this.frames.length }
  }

  /**
   * Discard the current recording without saving.
   * Called on return-to-menu or recovery → restart.
   */
  reset(): void {
    this.frames = []
    this.active = false
    this.initialSnapshot = null
  }

  /** Whether the recorder is currently capturing frames. */
  get isActive(): boolean {
    return this.active
  }
}
