import { type InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { WorldSnapshot } from '../snapshot/types'
import { cloneWorld } from '../snapshot/WorldSerializer'
import { packFrame } from './pack'
import { FRAME_SCHEMA_VERSION, FRAME_SCHEMA_V1 } from './config'

// ================================================================
// InputRecorder — passively captures player input per tick
// (plan/replay.md §3, §10)
//
// One recordFrame() call per simulation tick. The recorder reads
// from the InputLike interface without mutating the World.
// ================================================================

export interface RecorderResult {
  snapshot: WorldSnapshot
  /** Packed input frames (Uint8Array), prefixed with schema version byte. */
  frames: Uint8Array
  /** Lie-Back-Win-Mode: packed God AI frames (v2 only). Null when no coop. */
  frames2: Uint8Array | null
  tickCount: number
}

export class InputRecorder {
  private frames: number[] = []
  private frames2: number[] = []
  private active = false
  private initialSnapshot: WorldSnapshot | null = null
  /** Lie-Back-Win-Mode Q10: captured at recording start, never changes mid-session. */
  private coopAtStart = false
  /** 督战双玩家: captured at recording start for hasP2 determination. */
  private spectateDualAtStart = false

  /** Begin a new recording session. */
  startNew(world: World): void {
    this.frames = []
    this.frames2 = []
    this.active = true
    this.initialSnapshot = cloneWorld(world)
    this.coopAtStart = world.coop
    this.spectateDualAtStart = world.spectateDual
  }

  /**
   * Record one tick of input from both player inputs.
   * Called once per simulation tick (after tick, before endFrame).
   */
  recordFrame(input: InputLike, input2?: InputLike | null): void {
    if (!this.active) return

    // Record player1 input
    this.frames.push(
      packFrame({
        direction: input.getMoveDirection(),
        firing: input.isFiring(),
        guard: input.wasItemPressed('guard'),
        frenzy: input.wasItemPressed('frenzy'),
      }),
    )

    // Record player2 (God AI) input when coop is active
    if (input2) {
      this.frames2.push(
        packFrame({
          direction: input2.getMoveDirection(),
          firing: input2.isFiring(),
          guard: input2.wasItemPressed('guard'),
          frenzy: input2.wasItemPressed('frenzy'),
        }),
      )
    } else {
      // Pad with idle frame to keep streams aligned
      this.frames2.push(packFrame({ direction: null, firing: false, guard: false, frenzy: false }))
    }
  }

  /**
   * Finalize the recording session.
   * Prepends the schema version byte to each stream.
   * v1 compat: if no coop was active (frames2 all idle), downgrade to v1.
   */
  finalize(): RecorderResult | null {
    if (!this.active || this.frames.length === 0) return null

    const snapshot = this.initialSnapshot!
    const tickCount = this.frames.length

    // Lie-Back-Win-Mode Q10: flags fixed at recording start — use coopAtStart,
    // NOT derived from frames2 content. This ensures hasP2 is stable even if
    // God AI never produces non-idle input.
    const hasCoopInput = (this.coopAtStart || this.spectateDualAtStart) && this.frames2.length > 0

    let frames: Uint8Array
    let frames2: Uint8Array | null = null

    if (hasCoopInput) {
      // v2: [version][flags:hasP2][p1_0][p2_0][p1_1][p2_1]...
      const packed = new Uint8Array(2 + tickCount * 2)
      packed[0] = FRAME_SCHEMA_VERSION
      packed[1] = 0x01 // hasP2 flag
      for (let i = 0; i < tickCount; i++) {
        const base = 2 + i * 2
        packed[base] = this.frames[i]
        packed[base + 1] = this.frames2[i]
      }
      frames = packed
      // Also store frames2 separately for the Replay.frames2 field.
      // It is a SINGLE stream, so it must carry the v1 header — stamping it
      // 0x02 declared a dual-stream layout it does not have, and any reader
      // that unpacked it would consume byte 1 as a flags byte.
      frames2 = new Uint8Array(tickCount + 1)
      frames2[0] = FRAME_SCHEMA_V1
      for (let i = 0; i < tickCount; i++) {
        frames2[i + 1] = this.frames2[i]
      }
    } else {
      // v1: [version][frame0][frame1]... — downgrade for backward compat
      frames = new Uint8Array(tickCount + 1)
      frames[0] = FRAME_SCHEMA_V1
      for (let i = 0; i < tickCount; i++) {
        frames[i + 1] = this.frames[i]
      }
    }

    this.active = false
    this.initialSnapshot = null

    return { snapshot, frames, frames2, tickCount }
  }

  reset(): void {
    this.frames = []
    this.frames2 = []
    this.active = false
    this.initialSnapshot = null
  }

  get isActive(): boolean {
    return this.active
  }
}
