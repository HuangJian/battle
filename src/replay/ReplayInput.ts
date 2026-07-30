import type { Direction } from '../constants'
import type { InputLike } from '../game/Input'
import type { InputFrame } from './types'
import { unpackFrames } from './pack'

// ================================================================
// ReplayInput — feeds recorded inputs during playback
// (plan/replay.md §3, §10)
//
// v1 replays: single stream (p1 only, p2 = idle).
// v2 replays: dual interleaved streams.
// ================================================================

const IDLE_FRAME: InputFrame = {
  direction: null,
  firing: false,
  guard: false,
  frenzy: false,
}

/**
 * InputLike wrapper over packed replay frames.
 * Cursor advances via advance() (once per sim tick), not per render frame.
 */
export class ReplayInput implements InputLike {
  private frames1: InputFrame[]
  private frames2: InputFrame[] | null
  private cursor = 0
  /** Current tick index into the interleaved stream (v2) or simple cursor (v1). */
  private tick = 0
  private _totalTicks: number

  /**
   * @param data  Packed frame bytes (Uint8Array), prefixed with schema version.
   */
  constructor(data: Uint8Array) {
    const result = unpackFrames(data)
    if (result) {
      this.frames1 = result.p1
      this.frames2 = result.p2
    } else {
      this.frames1 = []
      this.frames2 = null
    }
    this._totalTicks = this.frames1.length
  }

  /** Second player input (God AI). Null for v1 replays. */
  get input2(): InputLike | null {
    if (!this.frames2) return null
    return new ReplayInputSlice(this.frames2, () => this.tick)
  }

  // ---- InputLike implementation (player1) ----

  getMoveDirection(): Direction | null {
    const frame = this.frames1[this.cursor]
    return frame?.direction ?? IDLE_FRAME.direction
  }

  isFiring(): boolean {
    const frame = this.frames1[this.cursor]
    return frame?.firing ?? IDLE_FRAME.firing
  }

  wasItemPressed(kind: 'guard' | 'frenzy'): boolean {
    const frame = this.frames1[this.cursor]
    if (!frame) return false
    return kind === 'guard' ? frame.guard : frame.frenzy
  }

  endFrame(): void {
    // Intentional no-op — cursor advances via advance(), not per render frame.
  }

  // ---- Playback control ----

  /** Advance the cursor by one tick. Called by PlaybackController after each sim tick. */
  advance(): void {
    this.cursor++
    this.tick++
  }

  reset(): void {
    this.cursor = 0
    this.tick = 0
  }

  seekTo(frameIndex: number): void {
    this.cursor = Math.max(0, Math.min(frameIndex, this.frames1.length))
    this.tick = this.cursor
  }

  get totalFrames(): number {
    return this.frames1.length
  }

  get totalTicks(): number {
    return this._totalTicks
  }

  get progress(): number {
    return this._totalTicks > 0 ? this.cursor / this._totalTicks : 0
  }

  get isFinished(): boolean {
    return this.cursor >= this.frames1.length
  }
}

/**
 * Thin InputLike slice for the second player stream in v2 replays.
 * Shares the tick counter with the parent ReplayInput.
 */
class ReplayInputSlice implements InputLike {
  private frames: InputFrame[]
  private getTick: () => number

  constructor(frames: InputFrame[], getTick: () => number) {
    this.frames = frames
    this.getTick = getTick
  }

  getMoveDirection(): Direction | null {
    const frame = this.frames[this.getTick()]
    return frame?.direction ?? IDLE_FRAME.direction
  }

  isFiring(): boolean {
    const frame = this.frames[this.getTick()]
    return frame?.firing ?? IDLE_FRAME.firing
  }

  wasItemPressed(kind: 'guard' | 'frenzy'): boolean {
    const frame = this.frames[this.getTick()]
    if (!frame) return false
    return kind === 'guard' ? frame.guard : frame.frenzy
  }

  endFrame(): void {}
  reset(): void {}
}
