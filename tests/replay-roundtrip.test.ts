import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { applyStageOverrides } from '../src/ai/godai-stage-overrides'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { RNG } from '../src/utils/RNG'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ReplayInput } from '../src/replay/ReplayInput'
import { serializeReplayFile, parseReplayFile } from '../src/replay/file'
import { restoreWorld } from '../src/snapshot/WorldSerializer'

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
  const godAIParams = applyStageOverrides(stage.name, DEFAULT_GOD_AI_PARAMS)
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
