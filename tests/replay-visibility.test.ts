import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { PlaybackController } from '../src/replay/PlaybackController'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import type { Replay } from '../src/replay/types'
import type { Direction } from '../src/constants'
import type { InputLike } from '../src/game/Input'

/**
 * Bug: 重放界面，暂停后切换到其它应用，再切换回游戏页面点击「播放」按键，
 * 进度条往前走但画面不动。
 *
 * Root cause: main.ts registers a `visibilitychange` listener that calls
 * `game.simulation.togglePause()` whenever the tab is hidden and
 * `world.state === 'playing'`. During replay playback, `PlaybackController`
 * sets `world.state = 'playing'` (start()) but manages pause independently
 * via its own `phase` field. The visibility handler corrupts `world.state`
 * to `'paused'`, which makes `simulation.tick()` a no-op (it only dispatches
 * on 'playing' / 'stageclear' / 'gameover'). Meanwhile `PlaybackController
 * .update()` still advances the input cursor — so the progress bar moves
 * but the world never updates (画面不动).
 */

// ---- helpers (mirrors tests/replay-seek.test.ts) ----

class IdleInput implements InputLike {
  getMoveDirection(): Direction | null {
    return null
  }
  isFiring(): boolean {
    return false
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

function recordReplay(stageIdx: number, seed: number, maxTicks: number) {
  const stage = STAGES[stageIdx]
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(stage, stageIdx)

  let tick = 0
  const human: InputLike = {
    getMoveDirection: () => (tick % 7 === 0 ? null : 'up'),
    isFiring: () => tick % 5 === 0,
    wasItemPressed: () => false,
    endFrame: () => {},
    reset: () => {},
  }
  const sim = new Simulation(world, human)
  const recorder = new InputRecorder()
  recorder.startNew(world)
  while (tick < maxTicks) {
    sim.tick()
    recorder.recordFrame(sim.input, sim.input2)
    tick++
    world.consumeEvents()
    if (world.state === 'stageclear' || world.state === 'gameover' || world.state === 'victory')
      break
  }
  return { result: recorder.finalize()!, stageIdx, seed }
}

function startPlayback(rec: ReturnType<typeof recordReplay>) {
  const stage = STAGES[rec.stageIdx]
  const world = new World()
  world.rng.reseed(rec.seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(stage, rec.stageIdx)

  const replay = {
    initialSnapshot: rec.result.snapshot,
    frames: rec.result.frames,
  } as unknown as Replay
  const pb = new PlaybackController(replay)
  pb.start(world, new Simulation(world, new IdleInput()))
  const sim = (pb as unknown as { simulation: Simulation }).simulation
  return { world, pb, sim }
}

// ================================================================

describe('PlaybackController survives external world.state corruption (visibilitychange bug)', () => {
  it('world advances even if world.state was corrupted to "paused" by an external handler', () => {
    const rec = recordReplay(0, 42, 600)
    const { world, pb, sim } = startPlayback(rec)

    // Advance one tick to establish a baseline.
    pb.update(1000 / 60)
    const playTimeBefore = world.playTimeMs
    expect(playTimeBefore).toBeGreaterThan(0)

    // Simulate the visibilitychange handler in main.ts: when the tab is
    // hidden it calls simulation.togglePause() if world.state === 'playing'.
    // During replay, world.state IS 'playing' (set by PlaybackController
    // .start()), so this corrupts it to 'paused'.
    expect(world.state).toBe('playing')
    sim.togglePause()
    expect(world.state).toBe('paused')

    // Advance another tick. Without the defensive guard in update(),
    // simulation.tick() is a no-op (it only dispatches on
    // 'playing'/'stageclear'/'gameover'), so the world doesn't update —
    // even though the input cursor advances (progress bar moves).
    pb.update(1000 / 60)

    // playTimeMs is only incremented inside updatePlaying(), which only
    // runs when world.state === 'playing'. If the guard works, the world
    // must have advanced.
    expect(world.playTimeMs).toBeGreaterThan(playTimeBefore)
  })

  it('replay was playing (not user-paused) when tab hidden: world still advances after return', () => {
    // This covers the other manifestation: the replay was actively playing
    // (not user-paused) when the tab was hidden. The visibility handler
    // still corrupts world.state to 'paused'. When the tab comes back,
    // playback.update() must restore it.
    const rec = recordReplay(2, 777, 600)
    const { world, pb, sim } = startPlayback(rec)

    // Replay is playing (phase = 'playing', world.state = 'playing')
    pb.update(1000 / 60)
    const playTimeBefore = world.playTimeMs

    // Tab hidden → visibility handler corrupts world.state
    expect(world.state).toBe('playing')
    sim.togglePause()
    expect(world.state).toBe('paused')

    // Tab returns → rAF restarts → update() is called.
    // playback.phase is still 'playing' (user never paused the replay).
    expect(pb.isPaused).toBe(false)
    pb.update(1000 / 60)

    expect(world.playTimeMs).toBeGreaterThan(playTimeBefore)
  })

  it('user-pauses replay, tab hidden, tab returns, user clicks Play: world advances', () => {
    // The exact user-reported scenario:
    // 1. Pause the replay
    // 2. Switch to another app (tab hidden)
    // 3. Switch back (tab visible)
    // 4. Click Play → progress bar moves but screen frozen (the bug)
    const rec = recordReplay(5, 12345, 600)
    const { world, pb, sim } = startPlayback(rec)

    // 1. User pauses the replay
    pb.togglePause()
    expect(pb.isPaused).toBe(true)

    // 2. Tab hidden → visibility handler corrupts world.state
    sim.togglePause()
    expect(world.state).toBe('paused')

    // 3. Tab returns (rAF restarts)

    // 4. User clicks Play
    pb.togglePause()
    expect(pb.isPaused).toBe(false)

    // The loop calls update() — world must advance.
    pb.update(1000 / 60)
    expect(world.playTimeMs).toBeGreaterThan(0)
  })
})
