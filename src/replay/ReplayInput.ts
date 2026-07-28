import type { Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { InputFrame } from './types'
import { unpackFrame } from './pack'
import { FRAME_SCHEMA_VERSION } from './config'

/** Idle frame returned when cursor is past the end. */
const IDLE_FRAME: InputFrame = {
  direction: null,
  firing: false,
  guard: false,
  frenzy: false,
}

/**
 * ReplayInput — an InputLike implementation that feeds recorded inputs
 * during playback. The cursor advances **only** via advance(), called
 * once per sim tick by PlaybackController.
 *
 * endFrame() is a **no-op** — it exists solely to satisfy the InputLike
 * interface contract. Game.loop() calls it every render frame, so if it
 * advanced the cursor, playback would double-step.
 */
export class ReplayInput implements InputLike {
  private frames: Uint8Array
  private cursor: number = 0

  constructor(frames: Uint8Array) {
    // The first byte is the schema version. Validate it instead of blindly
    // skipping: an unknown layout must never be silently mis-decoded as
    // input (it would "play" garbage). Callers should pre-check via
    // ReplayManager.canPlay(); this guard is the defensive backstop —
    // unknown version ⇒ zero frames ⇒ playback ends immediately.
    if (frames.length > 0 && frames[0] === FRAME_SCHEMA_VERSION) {
      this.frames = frames.subarray(1)
    } else {
      this.frames = new Uint8Array(0)
    }
  }

  private get currentFrame(): InputFrame {
    if (this.cursor >= this.frames.length) return IDLE_FRAME
    return unpackFrame(this.frames[this.cursor])
  }

  getMoveDirection(): Direction | null {
    return this.currentFrame.direction
  }

  isFiring(): boolean {
    return this.currentFrame.firing
  }

  wasItemPressed(kind: 'guard' | 'frenzy'): boolean {
    return kind === 'guard' ? this.currentFrame.guard : this.currentFrame.frenzy
  }

  /**
   * No-op. Exists to satisfy InputLike. Game.loop() calls this every
   * render frame; during playback the cursor is advanced only by
   * advance(), once per sim tick, inside PlaybackController.update().
   */
  endFrame(): void {
    /* intentionally empty */
  }

  reset(): void {
    this.cursor = 0
  }

  /** Advance cursor by one frame. Called once per sim tick. */
  advance(): void {
    this.cursor++
  }

  /** Progress through the replay (0..1). */
  get progress(): number {
    return this.frames.length > 0 ? this.cursor / this.frames.length : 1
  }

  /** Whether all frames have been consumed. */
  get isFinished(): boolean {
    return this.cursor >= this.frames.length
  }
}
