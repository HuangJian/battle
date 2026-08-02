import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { AutoFireInput } from '../src/game/AutoFireInput'
import type { InputLike } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { RNG } from '../src/utils/RNG'
import { InputRecorder } from '../src/replay/InputRecorder'
import { PlaybackController } from '../src/replay/PlaybackController'
import type { Replay } from '../src/replay/types'
import type { Direction } from '../src/constants'
import type { GameState } from '../src/types'

const stateOf = (w: World): GameState => w.state

// ---- minimal coop harness (mirrors tests/replay-coop-autofire.test.ts) ----

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

function makeCoopWorld(stageIdx: number, seed: number) {
  const stage = STAGES[stageIdx]
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(stage, stageIdx)
  world.coop = true
  world.lives2 = world.difficulty?.startLives ?? 3
  world.playerLevel2 = world.difficulty?.playerStartLevel ?? 0
  const p1Col = world.playerSpawnPoint?.col ?? 8
  world.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
  world.spawnPlayer2()

  const godParams = DEFAULT_GOD_AI_PARAMS
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const godInput = new GodAIInput(world, godParams, godRng, (w) => w.player2)
  godInput.reset()
  const rawInput = new IdleInput()
  const autoFire = new AutoFireInput(rawInput)
  const sim = new Simulation(world, autoFire)
  sim.input = autoFire
  sim.input2 = godInput
  return { world, sim, autoFire, godInput }
}

interface Outcome {
  state: string
  score: number
  killCount: number
  lives: number
  baseDestroyed: boolean
}

function recordCoop(stageIdx: number, seed: number, maxTicks: number) {
  const { world, sim } = makeCoopWorld(stageIdx, seed)
  const recorder = new InputRecorder()
  recorder.startNew(world)
  let tick = 0
  while (tick < maxTicks) {
    sim.tick()
    recorder.recordFrame(sim.input, sim.input2)
    ;(sim.input as AutoFireInput).endFrame()
    ;(sim.input2 as GodAIInput).endFrame()
    tick++
    world.consumeEvents()
    const st = stateOf(world)
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
  }
  const outcome: Outcome = {
    state: stateOf(world),
    score: world.score,
    killCount: world.killCount,
    lives: world.lives,
    baseDestroyed: world.tileMap.isBaseDestroyed(),
  }
  return { result: recorder.finalize()!, outcome, stageIdx, seed }
}

function recordSingle(stageIdx: number, seed: number, maxTicks: number) {
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
  sim.input = human
  sim.input2 = null
  const recorder = new InputRecorder()
  recorder.startNew(world)
  while (tick < maxTicks) {
    sim.tick()
    recorder.recordFrame(sim.input, sim.input2)
    tick++
    world.consumeEvents()
    const st = stateOf(world)
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
  }
  const outcome: Outcome = {
    state: stateOf(world),
    score: world.score,
    killCount: world.killCount,
    lives: world.lives,
    baseDestroyed: world.tileMap.isBaseDestroyed(),
  }
  return { result: recorder.finalize()!, outcome, stageIdx, seed }
}

/**
 * Drive a replay through the REAL PlaybackController.
 * When `seekFrac` is given, seek to that point then resume — this is the path
 * the progress bar uses and the one that was desyncing.
 */
function runViaController(
  rec: ReturnType<typeof recordCoop> | ReturnType<typeof recordSingle>,
  maxTicks: number,
  seekFrac?: number,
): Outcome {
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
  pb.start(world, new Simulation(world, null as any))
  const sim = (pb as unknown as { simulation: Simulation }).simulation

  if (seekFrac !== undefined) {
    pb.seekTo(world, sim, seekFrac)
    if (pb.isPaused) pb.togglePause()
  }

  let tick = 0
  while (!pb.isFinished && tick < maxTicks) {
    pb.update(1000 / 60)
    tick++
    const st = stateOf(world)
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
  }
  return {
    state: stateOf(world),
    score: world.score,
    killCount: world.killCount,
    lives: world.lives,
    baseDestroyed: world.tileMap.isBaseDestroyed(),
  }
}

// ================================================================
// PlaybackController.seekTo must reproduce the timeline.
//
// Bug: seekTo() fast-forwarded by running N sim ticks but never called
// ReplayInput.advance(), so every one of those ticks re-consumed the SAME
// frame (the one at the seek target). The world therefore diverged from the
// true timeline at the seek point, and the resumed replay desynced — the
// "drag the progress bar → replay breaks" symptom reported on the coop
// (躺赢模式) replay. Fix: advance the input once per tick during the
// fast-forward, exactly like update() does.
// ================================================================

describe('PlaybackController.seekTo reproduces the timeline (no fast-forward desync)', () => {
  const COOP: Array<[number, number]> = [
    [14, 1785585133360], // Citadel — the original bug-report stage
    [0, 12345],
  ]
  const SINGLE: Array<[number, number]> = [
    [2, 777],
    [7, 987654],
  ]
  const FRACS = [0.1, 0.33, 0.5, 0.66, 0.9]

  for (const [stageIdx, seed] of COOP) {
    it(`coop stage ${stageIdx + 1} seed ${seed}: seeking then resuming matches a clean full playback`, () => {
      const rec = recordCoop(stageIdx, seed, 6000)
      const full = runViaController(rec, 20000)
      // Sanity: the full run actually produced a decisive outcome.
      expect(['stageclear', 'gameover', 'victory']).toContain(full.state)

      for (const frac of FRACS) {
        const seek = runViaController(rec, 20000, frac)
        expect(seek.state).toBe(full.state)
        expect(seek.baseDestroyed).toBe(full.baseDestroyed)
        expect(seek.killCount).toBe(full.killCount)
        expect(seek.score).toBe(full.score)
        expect(seek.lives).toBe(full.lives)
      }
    })
  }

  for (const [stageIdx, seed] of SINGLE) {
    it(`single-player stage ${stageIdx + 1} seed ${seed}: seeking is also stable`, () => {
      const rec = recordSingle(stageIdx, seed, 3000)
      const full = runViaController(rec, 20000)
      // The single-player bot may not reach a decisive end within the budget;
      // the invariant under test is that seeking reproduces the SAME state as a
      // clean full playback (the bug made them diverge), not that the run ends.
      for (const frac of FRACS) {
        const seek = runViaController(rec, 20000, frac)
        expect(seek.state).toBe(full.state)
        expect(seek.baseDestroyed).toBe(full.baseDestroyed)
        expect(seek.killCount).toBe(full.killCount)
        expect(seek.score).toBe(full.score)
        expect(seek.lives).toBe(full.lives)
      }
    })
  }

  it('seek target frames must be consumed in order (cursor advances through the fast-forward)', () => {
    // Guards the specific line that was missing: without advance() in the
    // fast-forward loop, every tick replays the seek-target frame.
    const rec = recordCoop(0, 12345, 600)
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
    pb.start(world, new Simulation(world, null as any))
    const sim = (pb as unknown as { simulation: Simulation }).simulation
    pb.seekTo(world, sim, 0.5)
    // After seeking to ~50%, the underlying input cursor should sit at the
    // same offset as the progress implies — not pinned at the target frame for
    // the entire (now paused) fast-forward.
    const ri = (pb as unknown as { input: { cursor: number; totalFrames: number } }).input
    const expectedCursor = Math.floor(0.5 * ri.totalFrames)
    expect(ri.cursor).toBe(expectedCursor)
  })
})

// ================================================================
// PlaybackController seek must NOT queue an audio burst (DECISIONS #78).
//
// Bug: seekTo() (and buildKeyframes) fast-forward N sim ticks; each tick
// pushes sound events into world.events, but nobody drains them during the
// catch-up — the render loop only consumes one frame's worth per rendered
// frame. So the whole stage's sound effects pile up and detonate at once when
// the next frame runs world.consumeEvents() -> the harsh burst on "drag the
// seek bar". Fix: the catch-up loop drains (discards) events every tick.
// ================================================================

describe('PlaybackController seek leaves no audio backlog (DECISIONS #78)', () => {
  function pendingEventsAfterSeek(
    rec: ReturnType<typeof recordCoop> | ReturnType<typeof recordSingle>,
    frac: number,
  ): number {
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
    pb.start(world, new Simulation(world, null as any))
    const sim = (pb as unknown as { simulation: Simulation }).simulation
    pb.seekTo(world, sim, frac)
    return world.events.length
  }

  it('coop seekTo leaves the world event queue empty at every seek point', () => {
    const rec = recordCoop(14, 1785585133360, 6000) // the original bug stage
    for (const frac of [0.1, 0.33, 0.5, 0.66, 0.9]) {
      expect(pendingEventsAfterSeek(rec, frac)).toBe(0)
    }
  })

  it('single-player seekTo also leaves the event queue empty', () => {
    const rec = recordSingle(2, 777, 3000)
    expect(pendingEventsAfterSeek(rec, 0.5)).toBe(0)
  })

  it('buildKeyframes leaves no audio backlog', () => {
    const rec = recordCoop(0, 12345, 600)
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
    pb.start(world, new Simulation(world, null as any))
    const sim = (pb as unknown as { simulation: Simulation }).simulation
    // renderFn / captureFn are no-ops here; only the catch-up audio side matters.
    pb.buildKeyframes(
      world,
      sim,
      () => {},
      () => ({}) as unknown as ImageData,
    )
    expect(world.events.length).toBe(0)
  })
})
