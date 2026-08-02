import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { RNG } from '../src/utils/RNG'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ReplayInput } from '../src/replay/ReplayInput'
import { serializeReplayFile, parseReplayFile } from '../src/replay/file'
import { restoreWorld } from '../src/snapshot/WorldSerializer'
import type { InputLike } from '../src/game/Input'

// ============================================================
// Helpers
// ============================================================

function runWithRecording(stageIdx: number, seed: number, maxTicks: number) {
  const stage = STAGES[stageIdx]
  const difficulty = 'classic'
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  const godAIParams = DEFAULT_GOD_AI_PARAMS
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, godAIParams, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(stage, 0)
  input.reset()

  const recorder = new InputRecorder()
  recorder.startNew(world)

  let tick = 0
  while (tick < maxTicks) {
    sim.tick()
    recorder.recordFrame(input)
    input.endFrame()
    tick++
    world.consumeEvents()
    if (world.state === 'stageclear' || world.state === 'gameover') break
    if (world.state === 'victory') break
  }

  const result = recorder.finalize()!
  return {
    result,
    finalScore: world.score,
    finalLives: world.lives,
    finalKillCount: world.killCount,
    finalState: world.state,
    finalTick: tick,
    stageIndex: stageIdx,
    stageName: stage.name,
    difficulty,
    seed,
  }
}

function replayFromSerialized(original: any, maxTicks: number) {
  const { result, finalScore, finalLives, finalKillCount, finalState, finalTick } = original

  // Serialize then parse (full file pipeline)
  const text = serializeReplayFile({
    source: 'sim',
    seed: original.seed,
    sim: {
      seed: original.seed,
      difficulty: original.difficulty,
      stageIndex: original.stageIndex,
      stageName: original.stageName,
      outcome: 'stage_clear',
      status: 'clear',
      maxTicks: 36000,
    },
    initialSnapshot: result.snapshot,
    frames: result.frames,
    totalTicks: result.tickCount,
    metadata: {
      stage: original.stageIndex,
      stageName: original.stageName,
      difficulty: original.difficulty,
      lives: 3,
      playerLevel: 0,
      score: 0,
      killCount: 0,
      enemiesTotal: 20,
      playTimeMs: 0,
    },
  })

  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)

  // Replay the parsed data
  const replayWorld = new World()
  replayWorld.rng.reseed(original.seed)
  replayWorld.difficultyKey = original.difficulty
  replayWorld.difficulty = DIFFICULTIES[original.difficulty] ?? DIFFICULTIES['classic']
  replayWorld.rules = RULES[original.difficulty] ?? DEFAULT_RULES
  const stage = STAGES[original.stageIndex]
  replayWorld.loadStageData(stage, 0)

  restoreWorld(replayWorld, parsed.replay.initialSnapshot)
  const replayInput = new ReplayInput(parsed.replay.frames)
  const sim = new Simulation(replayWorld, replayInput)

  let replayTick = 0
  while (!replayInput.isFinished && replayTick < maxTicks) {
    sim.tick()
    replayInput.advance()
    replayTick++
    if (replayWorld.state === 'stageclear' || replayWorld.state === 'gameover') break
    if (replayWorld.state === 'victory') break
  }

  return {
    replayScore: replayWorld.score,
    replayLives: replayWorld.lives,
    replayKillCount: replayWorld.killCount,
    replayState: replayWorld.state,
    replayTick,
    originalScore: finalScore,
    originalLives: finalLives,
    originalKillCount: finalKillCount,
    originalState: finalState,
    originalTick: finalTick,
  }
}

// ============================================================
// Tests
// ============================================================

describe('Replay round-trip determinism', () => {
  it('classic-s01 short run produces identical world state', () => {
    const original = runWithRecording(0, 42, 6000)
    const comparison = replayFromSerialized(original, 6000)

    expect(comparison.replayScore).toBe(comparison.originalScore)
    expect(comparison.replayLives).toBe(comparison.originalLives)
    expect(comparison.replayKillCount).toBe(comparison.originalKillCount)
    expect(comparison.replayState).toBe(comparison.originalState)
    expect(comparison.replayTick).toBe(comparison.originalTick)
  })

  it('classic-s05 with different seed', () => {
    const original = runWithRecording(4, 999, 6000)
    const comparison = replayFromSerialized(original, 6000)

    expect(comparison.replayScore).toBe(comparison.originalScore)
    expect(comparison.replayLives).toBe(comparison.originalLives)
    expect(comparison.replayKillCount).toBe(comparison.originalKillCount)
    expect(comparison.replayState).toBe(comparison.originalState)
  })

  it('same seed produces identical results (determinism)', () => {
    const a = runWithRecording(0, 12345, 3000)
    const b = runWithRecording(0, 12345, 3000)

    expect(a.finalScore).toBe(b.finalScore)
    expect(a.finalLives).toBe(b.finalLives)
    expect(a.finalKillCount).toBe(b.finalKillCount)
    expect(a.finalState).toBe(b.finalState)
    expect(a.result.tickCount).toBe(b.result.tickCount)
  })

  it('different seeds produce different results', () => {
    const a = runWithRecording(0, 11111, 6000)
    const b = runWithRecording(0, 22222, 6000)

    // With different seeds, at least one field should differ
    const same =
      a.finalScore === b.finalScore &&
      a.finalLives === b.finalLives &&
      a.finalKillCount === b.finalKillCount &&
      a.finalState === b.finalState
    expect(same).toBe(false)
  })
})

// ============================================================
// Mid-bonus-time recordings (DECISIONS §86 → §87)
//
// The replay format carries world state ONLY in the initialSnapshot (a
// WorldSnapshot from cloneWorld); the packed frame stream is pure input
// (direction/fire/guard/frenzy). So the pickup window travels with the
// snapshot — once §86 serialized it, replay inherited the fix with zero
// changes to file.ts/pack.ts. This test locks that guarantee in end-to-end:
// a recording that STARTS with the window already open must restore the
// window on playback and let it tick down identically, not reset it.
// ============================================================

describe('Replay — recording that starts mid-bonus-time replays the window faithfully', () => {
  it('initialSnapshot captures the window; playback restores and evolves it identically', () => {
    const stage = STAGES[0]
    const difficulty = 'classic'
    const seed = 4242
    const TICKS = 120 // ~2 s

    // --- Record: a session whose first tick already sits mid-window ---
    const world = new World()
    world.rng.reseed(seed)
    world.difficultyKey = difficulty
    world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
    world.rules = RULES[difficulty] ?? DEFAULT_RULES
    world.loadStageData(stage, 0)
    // Force mid-window state: entered, 3.5 s of the 10 s window remaining.
    world.pickupWindowEntered = true
    world.pickupWindowTimer = 3500

    const idle: InputLike = {
      getMoveDirection: () => null,
      isFiring: () => false,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim = new Simulation(world, idle)
    const recorder = new InputRecorder()
    recorder.startNew(world)
    for (let i = 0; i < TICKS; i++) {
      sim.tick()
      recorder.recordFrame(idle)
      world.consumeEvents()
    }
    const result = recorder.finalize()!
    // DECISIONS §86: the snapshot must carry the window state at record time.
    expect(result.snapshot.pickupWindowEntered).toBe(true)
    expect(result.snapshot.pickupWindowTimer).toBe(3500)

    // --- Replay: full file pipeline, same tick count ---
    const text = serializeReplayFile({
      source: 'sim',
      seed,
      sim: {
        seed,
        difficulty,
        stageIndex: 0,
        stageName: stage.name,
        outcome: 'stage_clear',
        status: 'clear',
        maxTicks: 36000,
      },
      initialSnapshot: result.snapshot,
      frames: result.frames,
      totalTicks: result.tickCount,
      metadata: {
        stage: 0,
        stageName: stage.name,
        difficulty,
        lives: 3,
        playerLevel: 0,
        score: 0,
        killCount: 0,
        enemiesTotal: 20,
        playTimeMs: 0,
      },
    })
    const parsed = parseReplayFile(text)
    if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)

    const replayWorld = new World()
    replayWorld.rng.reseed(seed)
    replayWorld.difficultyKey = difficulty
    replayWorld.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
    replayWorld.rules = RULES[difficulty] ?? DEFAULT_RULES
    replayWorld.loadStageData(stage, 0)
    restoreWorld(replayWorld, parsed.replay.initialSnapshot)
    const replayInput = new ReplayInput(parsed.replay.frames)
    const rsim = new Simulation(replayWorld, replayInput)
    for (let i = 0; i < TICKS; i++) {
      rsim.tick()
      replayInput.advance()
      replayWorld.consumeEvents()
    }

    // Window restored and ticked down identically in both worlds — the replay
    // world must NOT have reset the window (entered=false → re-open at 10 s).
    expect(replayWorld.pickupWindowEntered).toBe(true)
    expect(replayWorld.pickupWindowTimer).toBe(world.pickupWindowTimer)
    expect(replayWorld.pickupWindowTimer).toBeGreaterThan(0)
    expect(replayWorld.pickupWindowTimer).toBeLessThan(3500)
  })
})
