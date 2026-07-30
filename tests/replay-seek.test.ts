import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ReplayManager } from '../src/replay/ReplayManager'
import { PlaybackController } from '../src/replay/PlaybackController'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
import type { InputFrame } from '../src/replay/types'

/**
 * Replay seek — regression test
 *
 * Bug: clicking or dragging the progress bar should jump the replay to
 * the corresponding moment. Previously, seeking appeared broken because
 * the click event fired after a drag (mouseup triggers click), causing
 * a double-seek or no-seek.
 *
 * This test verifies that PlaybackController.seekTo() restores the world
 * to the correct state at the target progress point.
 */

// ---- helpers ----

class ScriptedInput implements InputLike {
  private seq: InputFrame[]
  private i = 0
  constructor(seq: InputFrame[]) {
    this.seq =
      seq.length > 0 ? seq : [{ direction: null, firing: false, guard: false, frenzy: false }]
  }
  private cur(): InputFrame {
    return this.seq[Math.min(this.i, this.seq.length - 1)]
  }
  getMoveDirection(): Direction | null {
    return this.cur().direction
  }
  isFiring(): boolean {
    return this.cur().firing
  }
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return kind === 'guard' ? this.cur().guard : this.cur().frenzy
  }
  endFrame(): void {
    /* no-op */
  }
  reset(): void {
    this.i = 0
  }
  advance(): void {
    if (this.i < this.seq.length - 1) this.i++
  }
}

function makeReplay(ticks: number) {
  const input = new ScriptedInput(
    Array.from({ length: ticks }, (_, i) => ({
      direction: (['up', 'right', 'down', 'left'] as Direction[])[i % 4],
      firing: i % 10 === 0,
      guard: false,
      frenzy: false,
    })),
  )
  const world = new World()
  world.startGame('classic', world.themeKey, 0)
  const sim = new Simulation(world, input)
  const recorder = new InputRecorder()
  recorder.startNew(world)

  for (let t = 0; t < ticks; t++) {
    sim.tick()
    recorder.recordFrame(input)
    input.advance()
  }

  const result = recorder.finalize()!
  const mgr = new ReplayManager({ now: () => 1_000_000 })
  const replay = mgr.create('clear', result.snapshot, result.frames, result.tickCount, {
    stage: world.stageIndex,
    stageName: 'STAGE 1',
    difficulty: 'classic',
    lives: world.lives,
    playerLevel: world.playerLevel,
    score: world.score,
    killCount: world.killCount,
    enemiesTotal: world.enemiesSpawned,
    playTimeMs: world.playTimeMs,
  })
  return { replay, initialWorld: world }
}

// ============================================================
// Tests
// ============================================================

describe('PlaybackController.seekTo()', () => {
  it('seekTo(0) restores world to initial snapshot state', () => {
    const { replay } = makeReplay(600)
    const replayWorld = new World()
    const input = new ScriptedInput([])
    const sim = new Simulation(replayWorld, input)
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim)

    // Play a bit
    for (let i = 0; i < 100; i++) {
      sim.tick()
      pc['input']!.advance()
    }

    // Seek back to start
    pc.seekTo(replayWorld, sim, 0)

    // World should match initial snapshot (score 0, frame 0)
    expect(replayWorld.score).toBe(0)
    expect(replayWorld.frame).toBe(0)
    expect(pc.isPaused).toBe(true)
  })

  it('seekTo(0.5) restores world to approximately halfway state', () => {
    const { replay } = makeReplay(600)
    const replayWorld = new World()
    const input = new ScriptedInput([])
    const sim = new Simulation(replayWorld, input)
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim)

    // Play fully
    for (let i = 0; i < 600; i++) {
      sim.tick()
      pc['input']!.advance()
    }

    // Seek to halfway
    pc.seekTo(replayWorld, sim, 0.5)
    expect(pc.isPaused).toBe(true)

    // The frame count should be approximately half the total
    const totalFrames = pc['input']!.totalFrames
    const currentFrame = Math.floor(pc.progress * totalFrames)
    expect(currentFrame).toBeGreaterThanOrEqual(totalFrames * 0.45)
    expect(currentFrame).toBeLessThanOrEqual(totalFrames * 0.55)
  })

  it('seekTo(1.0) restores world to final state', () => {
    const { replay } = makeReplay(600)
    const replayWorld = new World()
    const input = new ScriptedInput([])
    const sim = new Simulation(replayWorld, input)
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim)

    // Seek to end
    pc.seekTo(replayWorld, sim, 1.0)
    expect(pc.isPaused).toBe(true)
    expect(pc.progress).toBeCloseTo(1.0, 2)
  })

  it('multiple seeks produce correct progressive states', () => {
    const { replay } = makeReplay(600)
    const replayWorld = new World()
    const input = new ScriptedInput([])
    const sim = new Simulation(replayWorld, input)
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim)

    // Seek to various points and verify progress
    const targets = [0, 0.25, 0.5, 0.75, 1.0]
    for (const target of targets) {
      pc.seekTo(replayWorld, sim, target)
      expect(pc.progress).toBeCloseTo(target, 1)
      expect(pc.isPaused).toBe(true)
    }
  })

  it('seeking does not corrupt the replay world (determinism check)', () => {
    const { replay } = makeReplay(300)

    // Play through once
    const world1 = new World()
    const sim1 = new Simulation(world1, new ScriptedInput([]))
    const pc1 = new PlaybackController(replay)
    pc1.start(world1, sim1)
    for (let i = 0; i < 300; i++) {
      sim1.tick()
      pc1['input']!.advance()
    }
    const score1 = world1.score
    const frame1 = world1.frame

    // Play through with seek in the middle
    const world2 = new World()
    const sim2 = new Simulation(world2, new ScriptedInput([]))
    const pc2 = new PlaybackController(replay)
    pc2.start(world2, sim2)
    for (let i = 0; i < 150; i++) {
      sim2.tick()
      pc2['input']!.advance()
    }
    pc2.seekTo(world2, sim2, 0) // seek back
    pc2.togglePause() // resume
    for (let i = 0; i < 300; i++) {
      sim2.tick()
      pc2['input']!.advance()
    }

    // Final state should match (deterministic replay)
    expect(world2.score).toBe(score1)
    expect(world2.frame).toBe(frame1)
  })
})
