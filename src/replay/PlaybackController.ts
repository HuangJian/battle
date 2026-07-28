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

export type PlaybackPhase = 'playing' | 'paused' | 'ended'

export class PlaybackController {
  private replay: Replay
  private simulation: Simulation | null = null
  private input: ReplayInput | null = null
  private speed: PlaybackSpeed = 1
  private accumulator: number = 0
  private phase: PlaybackPhase = 'playing'

  constructor(replay: Replay) {
    this.replay = replay
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
    restoreWorld(world, this.replay.initialSnapshot)
    this.input = new ReplayInput(this.replay.frames)
    simulation.input = this.input // swap input source
    world.state = 'playing'
    this.phase = 'playing'
    this.accumulator = 0
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
   * Exit playback. Restores the real Input and cleans up.
   * Game calls this to stop playback.
   */
  exit(simulation: Simulation, realInput: InputLike): void {
    simulation.input = realInput // restore live input
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

  /** Progress through the replay (0..1). */
  get progress(): number {
    return this.input?.progress ?? 1
  }

  /** Whether all frames have been consumed. */
  get isFinished(): boolean {
    return this.input?.isFinished ?? true
  }
}
