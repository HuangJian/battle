import { ALL_DIRS } from './helpers'
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
import { ReplayInput } from '../src/replay/ReplayInput'
import { PlaybackController } from '../src/replay/PlaybackController'
import type { Replay } from '../src/replay/types'
import { restoreWorld } from '../src/snapshot/WorldSerializer'
import type { Direction } from '../src/constants'
import type { GameState } from '../src/types'

/** Read world.state without letting TS narrow it to the last assigned literal. */
const stateOf = (w: World): GameState => w.state

// ================================================================
// Lie-Back-Win-Mode (躺赢模式) replay determinism.
//
// Regression guard for the browser recording path in Game.ts: the human
// input is wrapped in AutoFireInput when coop is on, so the Simulation
// fires every tick while the raw keyboard reports "not firing". The
// recorder MUST tap the decorated object the Simulation actually consumed
// (`simulation.input`), never the raw `Input` field — otherwise every
// auto-fired shot is dropped from the stream and playback desyncs from
// tick 0 (observed symptom: the replay drives into and shoots its own base).
// ================================================================

/** Idle keyboard — the literal "躺赢" case: the human touches nothing. */
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

/** A deterministic "human": moves and fires on a fixed cadence. */
class ScriptedInput implements InputLike {
  private t = 0
  private static readonly DIRS: Direction[] = ALL_DIRS
  getMoveDirection(): Direction | null {
    return this.t % 7 === 0 ? null : ScriptedInput.DIRS[Math.floor(this.t / 23) % 4]
  }
  isFiring(): boolean {
    return this.t % 11 === 0
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {
    this.t++
  }
  reset(): void {
    this.t = 0
  }
}

interface Outcome {
  state: string
  score: number
  killCount: number
  lives: number
  baseDestroyed: boolean
  tick: number
}

function makeCoopWorld(stageIdx: number, seed: number) {
  const stage = STAGES[stageIdx]
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(stage, stageIdx)

  // Mirror Game.requestCoopToggle()
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

  return { world, sim, rawInput, autoFire, godInput }
}

/**
 * Record a coop session.
 * @param tapRaw  when true, reproduce the OLD bug (record the undecorated input)
 */
function recordCoop(stageIdx: number, seed: number, maxTicks: number, tapRaw: boolean) {
  const { world, sim, rawInput, autoFire, godInput } = makeCoopWorld(stageIdx, seed)

  const recorder = new InputRecorder()
  recorder.startNew(world)

  let tick = 0
  while (tick < maxTicks) {
    sim.tick()
    if (tapRaw) recorder.recordFrame(rawInput, godInput)
    else recorder.recordFrame(sim.input, sim.input2)
    autoFire.endFrame()
    godInput.endFrame()
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
    tick,
  }
  return { result: recorder.finalize()!, outcome, stageIdx, seed }
}

function playback(rec: ReturnType<typeof recordCoop>, maxTicks: number): Outcome {
  const stage = STAGES[rec.stageIdx]
  const world = new World()
  world.rng.reseed(rec.seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(stage, rec.stageIdx)

  restoreWorld(world, rec.result.snapshot)
  const input = new ReplayInput(rec.result.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null
  world.state = 'playing'

  let tick = 0
  while (!input.isFinished && tick < maxTicks) {
    sim.tick()
    input.advance()
    tick++
    world.consumeEvents()
    const st = stateOf(world)
    if (st === 'stageclear' || st === 'gameover' || st === 'victory') break
  }
  return {
    state: stateOf(world),
    score: world.score,
    killCount: world.killCount,
    lives: world.lives,
    baseDestroyed: world.tileMap.isBaseDestroyed(),
    tick,
  }
}

const MAX = 20000
// A spread of stages so the guard is not tied to one layout.
const CASES: Array<[number, number]> = [
  [14, 1785585133360], // Citadel — the stage from the original bug report
  [0, 12345],
  [7, 987654],
]

describe('Lie-Back-Win-Mode replay determinism (coop + auto-fire)', () => {
  for (const [stageIdx, seed] of CASES) {
    it(`stage ${stageIdx + 1} seed ${seed}: playback reproduces the recorded run`, () => {
      const rec = recordCoop(stageIdx, seed, MAX, false)
      const play = playback(rec, MAX)

      expect(play.state).toBe(rec.outcome.state)
      expect(play.baseDestroyed).toBe(rec.outcome.baseDestroyed)
      expect(play.killCount).toBe(rec.outcome.killCount)
      expect(play.score).toBe(rec.outcome.score)
      expect(play.lives).toBe(rec.outcome.lives)
      expect(play.tick).toBe(rec.outcome.tick)
    })
  }

  it('auto-fire frames are actually captured in the stream (P1 fires from tick 0)', () => {
    const rec = recordCoop(14, 1785585133360, 600, false)
    // frames layout v2: [ver][flags][p1][p2][p1][p2]...  bit4 = fire
    const f = rec.result.frames
    expect(f[0]).toBe(2)
    expect(f[1]).toBe(0x01)
    let p1FireCount = 0
    for (let i = 2; i < f.length; i += 2) if (f[i] & 0x10) p1FireCount++
    const ticks = (f.length - 2) / 2
    // Idle human + armed auto-fire ⇒ P1 requests fire on EVERY tick.
    expect(p1FireCount).toBe(ticks)
    expect(f[2] & 0x10).toBe(0x10) // firing already on tick 0
  })

  it('single-player (non-coop) recording still round-trips exactly', () => {
    // Mirrors Game.ts non-coop wiring: simulation.input IS the raw Input
    // (no AutoFireInput decoration), simulation.input2 is null.
    const seed = 777
    const stageIdx = 2
    const stage = STAGES[stageIdx]

    const build = () => {
      const w = new World()
      w.rng.reseed(seed)
      w.difficultyKey = 'classic'
      w.difficulty = DIFFICULTIES['classic']
      w.rules = RULES['classic'] ?? DEFAULT_RULES
      w.loadStageData(stage, stageIdx)
      return w
    }

    const world = build()
    const human = new ScriptedInput()
    const sim = new Simulation(world, human)
    sim.input = human
    sim.input2 = null

    const recorder = new InputRecorder()
    recorder.startNew(world)
    let tick = 0
    while (tick < 3000) {
      sim.tick()
      recorder.recordFrame(sim.input, sim.input2)
      human.endFrame()
      tick++
      world.consumeEvents()
      if (stateOf(world) !== 'playing') break
    }
    const rec = recorder.finalize()!
    const before = {
      state: stateOf(world),
      score: world.score,
      kills: world.killCount,
      base: world.tileMap.isBaseDestroyed(),
      tick,
    }

    // Non-coop must downgrade to a v1 single-stream replay.
    expect(rec.frames[0]).toBe(1)

    const rw = build()
    restoreWorld(rw, rec.snapshot)
    const rin = new ReplayInput(rec.frames)
    const rsim = new Simulation(rw, rin)
    rsim.input = rin
    rsim.input2 = rin.input2 ?? null
    expect(rsim.input2).toBeNull()
    rw.state = 'playing'
    let rtick = 0
    while (!rin.isFinished && rtick < 3000) {
      rsim.tick()
      rin.advance()
      rtick++
      rw.consumeEvents()
      if (stateOf(rw) !== 'playing') break
    }

    expect(stateOf(rw)).toBe(before.state)
    expect(rw.score).toBe(before.score)
    expect(rw.killCount).toBe(before.kills)
    expect(rw.tileMap.isBaseDestroyed()).toBe(before.base)
    expect(rtick).toBe(before.tick)
  })

  it('regression: tapping the undecorated input desyncs playback', () => {
    const buggy = recordCoop(14, 1785585133360, MAX, true)
    const play = playback(buggy, MAX)
    // The old code dropped every auto-fired shot → the streams diverge.
    const diverged =
      play.state !== buggy.outcome.state ||
      play.killCount !== buggy.outcome.killCount ||
      play.baseDestroyed !== buggy.outcome.baseDestroyed ||
      play.tick !== buggy.outcome.tick
    expect(diverged).toBe(true)
  })
})

// ================================================================
// Leaving playback must hand back the SAME decorated pair.
//
// PlaybackController.exit() used to hard-null input2 and take only the
// raw input, with a comment telling the caller to "re-wire if coop is
// active" — which no caller ever did. Exiting a replay mid-coop then
// silently dropped both the auto-fire decoration and the God AI, so the
// live game resumed with a mute P1 and a frozen P2. (DECISIONS #76)
// ================================================================

describe('Lie-Back-Win-Mode playback exit restores the live inputs', () => {
  function startedPlayback(stageIdx: number, seed: number) {
    const rec = recordCoop(stageIdx, seed, 240, false)
    const live = makeCoopWorld(stageIdx, seed)
    const replay = {
      initialSnapshot: rec.result.snapshot,
      frames: rec.result.frames,
    } as unknown as Replay
    const pb = new PlaybackController(replay)
    pb.start(live.world, live.sim)
    // Playback owns both streams while it runs.
    expect(live.sim.input).not.toBe(live.autoFire)
    expect(live.sim.input2).not.toBe(live.godInput)
    return { pb, ...live }
  }

  it('exit() restores the decorated P1 input AND the God AI', () => {
    const { pb, sim, autoFire, godInput } = startedPlayback(0, 12345)

    pb.exit(sim, autoFire, godInput)

    expect(sim.input).toBe(autoFire)
    expect(sim.input2).toBe(godInput)
  })

  it('the restored P1 input is the auto-firing one, not the bare keyboard', () => {
    const { pb, sim, rawInput, autoFire, godInput } = startedPlayback(0, 12345)

    pb.exit(sim, autoFire, godInput)

    // The human is idle; only the decoration makes the tank shoot. Handing
    // back `rawInput` here is exactly the bug — it would report false.
    expect(rawInput.isFiring()).toBe(false)
    expect(sim.input.isFiring()).toBe(true)
  })

  it('exit() still clears input2 for a non-coop caller (default arg)', () => {
    const { pb, sim, rawInput } = startedPlayback(0, 12345)

    pb.exit(sim, rawInput)

    expect(sim.input).toBe(rawInput)
    expect(sim.input2).toBeNull()
  })
})
