import type { InputLike } from '../game/Input'
import type { Simulation } from '../game/Simulation'
import type { World } from '../game/World'
import { TICK_MS } from '../constants'
import { restoreWorld } from '../snapshot/WorldSerializer'
import type { Replay } from './types'
import { ReplayInput } from './ReplayInput'

// ================================================================
// PlaybackController — replay playback lifecycle
// (plan/replay.md §6.2)
//
// Operates on the EXISTING Game.world and Game.simulation — it does
// NOT create a second World or Simulation. It mirrors RecoveryController's
// pattern: restore the world from a snapshot, then drive the simulation
// by swapping the active input.
// ================================================================

/** Speed multiplier options. */
export type PlaybackSpeed = 1 | 1.5 | 2 | 4

/**
 * Max sim ticks per render frame. Must cover the fastest speed at 60 FPS
 * (×4 → 4 ticks/frame) with headroom for frame-rate dips; anything beyond
 * is dropped by the accumulator clamp below instead of spiraling.
 */
const MAX_STEPS_PER_FRAME = 8

/** Capture a keyframe every N simulation ticks. */
const KEYFRAME_INTERVAL = 60 // 1 second at 60fps

export type PlaybackPhase = 'playing' | 'paused' | 'ended'

export class PlaybackController {
  private _replay: Replay
  private simulation: Simulation | null = null
  private input: ReplayInput | null = null
  private speed: PlaybackSpeed = 1
  private accumulator: number = 0
  private phase: PlaybackPhase = 'playing'

  /** Pre-computed thumbnail keyframes: frame → ImageData. */
  private _keyframes: Map<number, ImageData> = new Map()
  private _keyframeInterval = KEYFRAME_INTERVAL

  constructor(replay: Replay) {
    this._replay = replay
  }

  /**
   * Start playback. Operates on Game's own world and simulation.
   *
   * 1. Restore world from replay.initialSnapshot (via restoreWorld)
   * 2. Create ReplayInput from replay.frames
   * 3. Swap simulation.input to replayInput
   * 4. world.state = 'playing' (NOT a new state — the sim gates on this)
   */
  start(world: World, simulation: Simulation): void {
    this.simulation = simulation
    restoreWorld(world, this._replay.initialSnapshot)
    this.input = new ReplayInput(this._replay.frames)
    simulation.input = this.input // swap input source
    // Lie-Back-Win-Mode: wire replay input2 for coop replays.
    simulation.input2 = this.input.input2 ?? null
    world.state = 'playing'
    this.phase = 'playing'
    this.accumulator = 0
  }

  /**
   * Pre-compute thumbnail keyframes for instant hover preview.
   * Replays the entire simulation once, capturing the canvas at regular
   * intervals. Should be called after start() and before any hover.
   *
   * @param world   The game world (will be temporarily modified and restored)
   * @param simulation The simulation engine
   * @param renderFn   Renders the world to the main canvas (for capture)
   * @param captureFn  Captures a 160×160 ImageData from the canvas
   * @param onProgress Called with (current, total) during build for progress UI
   */
  buildKeyframes(
    world: World,
    simulation: Simulation,
    renderFn: (w: World) => void,
    captureFn: () => ImageData,
    onProgress?: (current: number, total: number) => void,
  ): void {
    this._keyframes.clear()
    if (!this.input) return

    const totalFrames = this.input.totalFrames
    // Adaptive interval: for very long replays, space keyframes further apart
    // to keep memory bounded (~300 keyframes max).
    this._keyframeInterval = Math.max(KEYFRAME_INTERVAL, Math.floor(totalFrames / 300))

    // Save current playback state
    const savedPhase = this.phase
    const savedAccum = this.accumulator
    const currentFrame = Math.floor(this.input.progress * totalFrames)

    // Restore world from initial snapshot
    restoreWorld(world, this._replay.initialSnapshot)
    const buildInput = new ReplayInput(this._replay.frames)
    simulation.input = buildInput
    // Lie-Back-Win-Mode: wire replay input2 for coop replays.
    simulation.input2 = buildInput.input2 ?? null

    // Replay the entire simulation and capture keyframes
    let lastCapturedFrame = -this._keyframeInterval // force frame 0 capture
    for (let frame = 0; frame < totalFrames; frame++) {
      simulation.tick()
      buildInput.advance()
      // #78: silent catch-up — drain events so the keyframe pass never leaves
      // an audio/presentation backlog for the render loop to fire at once.
      world.consumeEvents()

      if (frame - lastCapturedFrame >= this._keyframeInterval || frame === totalFrames - 1) {
        // Render and capture
        renderFn(world)
        this._keyframes.set(frame, captureFn())
        lastCapturedFrame = frame
        onProgress?.(frame, totalFrames)
      }
    }

    // Restore to current playback position
    restoreWorld(world, this._replay.initialSnapshot)
    const restoredInput = new ReplayInput(this._replay.frames)
    this.input = restoredInput
    simulation.input = restoredInput
    // Lie-Back-Win-Mode: restore input2 for coop replays.
    simulation.input2 = restoredInput.input2 ?? null
    // Replay frames 0..currentFrame-1 (advance each tick) so the world matches
    // the cursor — same contract as seekTo()/update().
    for (let i = 0; i < currentFrame; i++) {
      simulation.tick()
      restoredInput.advance()
      // #78: drain so the restored position has no pending audio backlog.
      world.consumeEvents()
    }
    this.phase = savedPhase
    this.accumulator = savedAccum
  }

  /**
   * Get a pre-computed thumbnail for the given progress (0..1).
   * Returns the nearest keyframe's ImageData, or null if no keyframes exist.
   */
  getThumbnailAt(progress: number): ImageData | null {
    if (this._keyframes.size === 0 || !this.input) return null
    const targetFrame = Math.floor(progress * this.input.totalFrames)

    // Find the nearest keyframe ≤ targetFrame
    let bestFrame = 0
    for (const frame of this._keyframes.keys()) {
      if (frame <= targetFrame) {
        bestFrame = frame
      } else {
        break // keys are inserted in order
      }
    }
    return this._keyframes.get(bestFrame) ?? null
  }

  /** Number of pre-computed keyframes. */
  get keyframeCount(): number {
    return this._keyframes.size
  }

  /** Clear keyframes (e.g. on exit). */
  clearKeyframes(): void {
    this._keyframes.clear()
  }

  /**
   * Per-frame update: runs multiple sim ticks based on speed.
   * Game.loop() calls this INSTEAD of its own while-loop when
   * this.playback is set.
   */
  update(dt: number): void {
    if (this.phase === 'paused' || this.phase === 'ended') return
    if (!this.simulation || !this.input) return

    this.accumulator += dt * this.speed
    let steps = 0
    while (this.accumulator >= TICK_MS && !this.input.isFinished && steps < MAX_STEPS_PER_FRAME) {
      this.simulation.tick()
      this.input.advance() // one frame per tick
      this.accumulator -= TICK_MS
      steps++
    }
    // Anti-spiral clamp: dt is capped at 100 ms upstream, but ×4 speed can
    // deposit up to 400 ms per frame while the step cap only drains
    // MAX_STEPS_PER_FRAME × TICK_MS. Without the clamp the accumulator grows
    // without bound after a hitch and playback fast-forwards forever.
    if (this.accumulator > TICK_MS) this.accumulator = TICK_MS
    if (this.input.isFinished) {
      this.phase = 'ended'
    }
  }

  togglePause(): void {
    if (this.phase === 'ended') return
    this.phase = this.phase === 'paused' ? 'playing' : 'paused'
  }

  setSpeed(speed: PlaybackSpeed): void {
    this.speed = speed
  }

  /**
   * Seek to a specific progress (0..1). Restores the world from the initial
   * snapshot, then fast-forwards to the target frame. Pauses after seek.
   */
  seekTo(world: World, simulation: Simulation, progress: number): void {
    if (!this.input) return
    const targetFrame = Math.floor(progress * this.input.totalFrames)
    // Restore world from initial snapshot
    restoreWorld(world, this._replay.initialSnapshot)
    // Re-create ReplayInput from frames and replay up to the target frame.
    this.input = new ReplayInput(this._replay.frames)
    simulation.input = this.input
    // Lie-Back-Win-Mode: wire replay input2 for coop replays.
    simulation.input2 = this.input.input2 ?? null
    // Fast-forward: replay frames 0..targetFrame-1 so the world lands exactly
    // at the target frame, then resume. advance() MUST be called once per tick
    // (mirroring update()); without it every tick re-consumes the same frame and
    // the timeline desyncs — the "drag the seek bar → replay breaks" bug.
    for (let i = 0; i < targetFrame; i++) {
      simulation.tick()
      this.input.advance()
      // DECISIONS #78: the catch-up loop MUST drain (and discard) world events
      // every tick. Otherwise the sound effects generated across all
      // `targetFrame` ticks pile up in world.events and detonate at once when
      // the next render frame runs world.consumeEvents() -> a harsh burst of the
      // whole stage's audio on "drag the seek bar". (The render loop normally
      // drains one frame's worth per frame; during fast-forward nobody does, so
      // we drain here instead and stay silent.)
      world.consumeEvents()
    }
    // Pause after seek
    this.phase = 'paused'
    this.accumulator = 0
  }

  /**
   * Exit playback. Restores the live inputs and cleans up.
   * Game calls this to stop playback.
   *
   * The caller passes BOTH live inputs because only it knows the current
   * wiring: in Lie-Back-Win-Mode the human input is decorated by AutoFireInput
   * and player 2 is driven by GodAIInput. Defaulting `realInput2` to null and
   * telling the caller to "re-wire if coop is active" was a trap — no caller
   * ever did, so exiting a replay mid-coop silently dropped both the auto-fire
   * decoration and the God AI (DECISIONS #76).
   *
   * @param realInput  The live player-1 input, already decorated if applicable.
   * @param realInput2 The live player-2 input, or null when coop is off.
   */
  exit(simulation: Simulation, realInput: InputLike, realInput2: InputLike | null = null): void {
    simulation.input = realInput
    simulation.input2 = realInput2
    this.phase = 'ended'
  }

  get isActive(): boolean {
    return this.phase === 'playing' || this.phase === 'paused'
  }

  get isPaused(): boolean {
    return this.phase === 'paused'
  }

  get isEnded(): boolean {
    return this.phase === 'ended'
  }

  get currentSpeed(): PlaybackSpeed {
    return this.speed
  }

  /** Access the underlying replay data. */
  get replay(): Replay {
    return this._replay
  }

  /** Progress through the replay (0..1). */
  get progress(): number {
    return this.input?.progress ?? 1
  }

  /** Whether all frames have been consumed. */
  get isFinished(): boolean {
    return this.input?.isFinished ?? true
  }
}
