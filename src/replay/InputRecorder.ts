import { type InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { WorldSnapshot } from '../snapshot/types'
import { cloneWorld } from '../snapshot/WorldSerializer'
import { packFrames } from './pack'
import { REPLAY_HASH_INTERVAL } from './config'
import { worldTickHash } from './tickHash'
import type { InputFrame } from './types'

// ================================================================
// InputRecorder — passively captures player input per tick
// (plan/replay.md §3, §10)
//
// One recordFrame() call per simulation tick. The recorder reads
// from the InputLike interface without mutating the World.
//
// Tick-hash chain (plan/Replay-TickHash-Chain.md §1.2): the recorder
// holds a READ-ONLY reference to the World from startNew(). Inside
// recordFrame() (called right after sim.tick() in GameLoop) it samples
// worldTickHash() whenever the number of recorded frames is a multiple
// of REPLAY_HASH_INTERVAL. Phase expression: frames.length % INTERVAL
// === 0, i.e. after the (frames.length)-th completed sim.tick(). The
// verifier samples at the same tick count — see tickHash.ts header.
// ================================================================

export interface RecorderResult {
  snapshot: WorldSnapshot
  /** Packed input frames (Uint8Array), prefixed with schema version byte. */
  frames: Uint8Array
  /** Lie-Back-Win-Mode: packed God AI frames (v2 only). Null when no coop. */
  frames2: Uint8Array | null
  tickCount: number
  /**
   * World hash at each checkpoint tick (every REPLAY_HASH_INTERVAL frames).
   * Written into the .replay file as the desync-locator chain.
   */
  tickHashes: string[]
}

export class InputRecorder {
  private frames: InputFrame[] = []
  private frames2: InputFrame[] = []
  private tickHashes: string[] = []
  private active = false
  private initialSnapshot: WorldSnapshot | null = null
  /** Read-only world reference for tick-hash sampling (tickHash.ts). */
  private world: World | null = null
  /** Lie-Back-Win-Mode Q10: captured at recording start, never changes mid-session. */
  private coopAtStart = false
  /** 督战双玩家: captured at recording start for hasP2 determination. */
  private spectateDualAtStart = false

  /** Begin a new recording session. */
  startNew(world: World): void {
    this.frames = []
    this.frames2 = []
    this.tickHashes = []
    this.active = true
    this.initialSnapshot = cloneWorld(world)
    this.world = world
    this.coopAtStart = world.coop
    this.spectateDualAtStart = world.spectateDual
  }

  /**
   * Record one tick of input from both player inputs.
   * Called once per simulation tick (after tick, before endFrame).
   */
  recordFrame(input: InputLike, input2?: InputLike | null): void {
    if (!this.active) return

    // Record player1 input. hold/fireBeforeTurn are v3-only and optional:
    // human/God-AI inputs leave them undefined -> false (legacy semantics).
    this.frames.push({
      direction: input.getMoveDirection(),
      firing: input.isFiring(),
      guard: input.wasItemPressed('guard'),
      frenzy: input.wasItemPressed('frenzy'),
      hold: input.isHolding?.() ?? false,
      fireBeforeTurn: input.isFireBeforeTurn?.() ?? false,
    })

    // Record player2 (God AI) input when coop is active
    if (input2) {
      this.frames2.push({
        direction: input2.getMoveDirection(),
        firing: input2.isFiring(),
        guard: input2.wasItemPressed('guard'),
        frenzy: input2.wasItemPressed('frenzy'),
        hold: input2.isHolding?.() ?? false,
        fireBeforeTurn: input2.isFireBeforeTurn?.() ?? false,
      })
    } else {
      // Pad with idle frame to keep streams aligned
      this.frames2.push({ direction: null, firing: false, guard: false, frenzy: false })
    }

    // Tick-hash checkpoint — phase contract in tickHash.ts header: sample the
    // post-tick world state every REPLAY_HASH_INTERVAL frames, using the SAME
    // expression as the verifier (`count % interval === 0`).
    if (this.world && this.frames.length % REPLAY_HASH_INTERVAL === 0) {
      this.tickHashes.push(worldTickHash(this.world))
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

    // Delegate to packFrames so the version choice is in one place: legacy
    // single-stream stays v1, coop or any v3-only bit becomes v3. Writing a
    // v1 header over a frame that carries hold/fireBeforeTurn would silently
    // erase both (review P0-1).
    const frames = packFrames(this.frames, hasCoopInput ? this.frames2 : null)
    // Also store frames2 separately for the Replay.frames2 field. It is a
    // SINGLE stream, so it gets its own header (v1 unless it carries a
    // v3-only bit); it must never claim the interleaved dual layout.
    const frames2 = hasCoopInput ? packFrames(this.frames2) : null

    this.active = false
    this.initialSnapshot = null
    this.world = null

    return { snapshot, frames, frames2, tickCount, tickHashes: this.tickHashes }
  }

  reset(): void {
    this.frames = []
    this.frames2 = []
    this.tickHashes = []
    this.active = false
    this.initialSnapshot = null
    this.world = null
  }

  get isActive(): boolean {
    return this.active
  }
}
