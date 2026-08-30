import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/sim/simulation-runner'
import { evaluate, DEFAULT_BASELINE } from '../tools/eval/evaluator'
import { STAGES } from '../src/config/stages'
import type { StageData } from '../src/types'
import { GRID } from '../src/constants'

/** A simple test stage with minimal terrain. */
function testStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
  // Base
  const setChar = (c: number, r: number, ch: string) => {
    tiles[r] = tiles[r].slice(0, c) + ch + tiles[r].slice(c + 1)
  }
  setChar(12, 24, 'E')
  setChar(13, 24, 'E')
  setChar(12, 25, 'E')
  setChar(13, 25, 'E')
  // Some brick cover
  setChar(10, 20, 'b')
  setChar(11, 20, 'b')
  setChar(14, 20, 'b')
  setChar(15, 20, 'b')
  return {
    id: 999,
    name: 'Test Stage',
    tiles,
    enemies: ['basic', 'fast', 'power', 'armor'],
  }
}

describe('SimulationRunner', () => {
  it('runs a simulation and produces a valid result', () => {
    const result = runSimulation({
      seed: 42,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600, // 10 seconds
      sampleInterval: 6,
    })

    expect(result.outcome).toBeOneOf(['stage_clear', 'gameover', 'max_ticks'])
    expect(result.ticks).toBeGreaterThan(0)
    expect(result.ticks).toBeLessThanOrEqual(600)
    expect(result.events.length).toBeGreaterThan(0)
    expect(result.metrics.length).toBeGreaterThan(0)
    expect(result.seed).toBe(42)
    expect(result.difficulty).toBe('classic')
  })

  it('is deterministic: same seed = same result', () => {
    const opts = {
      seed: 123,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600,
      sampleInterval: 6,
    }
    const r1 = runSimulation(opts)
    const r2 = runSimulation(opts)

    expect(r1.outcome).toBe(r2.outcome)
    expect(r1.ticks).toBe(r2.ticks)
    expect(r1.finalState.score).toBe(r2.finalState.score)
    expect(r1.finalState.killCount).toBe(r2.finalState.killCount)
    expect(r1.finalState.lives).toBe(r2.finalState.lives)
    expect(r1.events.length).toBe(r2.events.length)
  })

  it('different seeds produce different results', () => {
    const r1 = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600,
      sampleInterval: 6,
    })
    const r2 = runSimulation({
      seed: 2,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600,
      sampleInterval: 6,
    })

    // At least the event counts or tick counts should differ.
    const same = r1.ticks === r2.ticks && r1.events.length === r2.events.length
    expect(same).toBe(false)
  })

  it('respects maxTicks limit', () => {
    const result = runSimulation({
      seed: 1,
      stage: testStage(),
      difficulty: 'classic',
      maxTicks: 100,
      sampleInterval: 10,
    })
    expect(result.ticks).toBeLessThanOrEqual(100)
  })

  it('works with custom StageData', () => {
    const stage = testStage()
    const result = runSimulation({
      seed: 7,
      stage,
      difficulty: 'hard',
      maxTicks: 300,
      sampleInterval: 6,
    })
    expect(result.outcome).toBeOneOf(['stage_clear', 'gameover', 'max_ticks'])
    expect(result.ticks).toBeGreaterThan(0)
  })

  it('collects events (bullet_fired, explosions, etc.)', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600,
      sampleInterval: 6,
    })
    const types = new Set(result.events.map((e) => e.type))
    expect(types.has('bullet_fired')).toBe(true)
  })

  it('metrics contain per-frame samples', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 120,
      sampleInterval: 6,
    })
    expect(result.metrics.length).toBeGreaterThan(0)
    const m = result.metrics[0]
    expect(m).toHaveProperty('tick')
    expect(m).toHaveProperty('bullets')
    expect(m).toHaveProperty('enemyCount')
    expect(m).toHaveProperty('playerX')
    expect(m).toHaveProperty('playerY')
    expect(m).toHaveProperty('enemyPositions')
  })
})

describe('Evaluator', () => {
  it('produces a valid evaluation report', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 600,
      sampleInterval: 6,
    })
    const report = evaluate(result, STAGES[0], DEFAULT_BASELINE)

    expect(report).toHaveProperty('hardPass')
    expect(report).toHaveProperty('softScore')
    expect(report).toHaveProperty('totalScore')
    expect(report).toHaveProperty('pass')
    expect(report).toHaveProperty('details')
    expect(report).toHaveProperty('hardMetrics')
    expect(report).toHaveProperty('outcome')
    expect(typeof report.softScore).toBe('number')
    expect(typeof report.totalScore).toBe('number')
    expect(typeof report.pass).toBe('boolean')
  })

  it('hardPass is false when stage is not cleared', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 60, // very short — can't clear
      sampleInterval: 6,
    })
    const report = evaluate(result, STAGES[0], DEFAULT_BASELINE)
    // With only 60 ticks, the stage can't be cleared.
    if (result.outcome !== 'stage_clear') {
      expect(report.hardPass).toBe(false)
      expect(report.totalScore).toBe(0)
      expect(report.pass).toBe(false)
    }
  })

  it('totalScore = softScore when hardPass', () => {
    // Create a fake "perfect" result.
    const fakeResult = {
      outcome: 'stage_clear' as const,
      ticks: 10000,
      wallMs: 100,
      finalState: {
        score: 5000,
        lives: 3,
        killCount: 20,
        playTimeMs: 180000, // 3 minutes
        stageIndex: 0,
        baseAlive: true,
        playerAlive: true,
        playerLevel: 2,
      },
      events: [],
      metrics: [],
      seed: 1,
      difficulty: 'hard',
      paramsHash: '',
      cleared: true,
    }
    const report = evaluate(fakeResult, STAGES[0], DEFAULT_BASELINE)
    expect(report.hardPass).toBe(true)
    expect(report.totalScore).toBe(report.softScore)
  })

  it('evaluates terrain utilization correctly', () => {
    // Empty stage (no terrain) → low utilization.
    const emptyStage: StageData = {
      id: 0,
      name: 'empty',
      tiles: Array.from({ length: GRID }, () => '.'.repeat(GRID)),
      enemies: ['basic'],
    }
    // Override base placement
    emptyStage.tiles[24] = emptyStage.tiles[24].slice(0, 12) + 'EE' + emptyStage.tiles[24].slice(14)
    emptyStage.tiles[25] = emptyStage.tiles[25].slice(0, 12) + 'EE' + emptyStage.tiles[25].slice(14)

    const fakeResult = {
      outcome: 'stage_clear' as const,
      ticks: 100,
      wallMs: 1,
      finalState: {
        score: 0,
        lives: 3,
        killCount: 0,
        playTimeMs: 120000,
        stageIndex: 0,
        baseAlive: true,
        playerAlive: true,
        playerLevel: 0,
      },
      events: [],
      metrics: [],
      seed: 1,
      difficulty: 'classic',
      paramsHash: '',
      cleared: true,
    }
    const report = evaluate(fakeResult, emptyStage, DEFAULT_BASELINE)
    // Terrain utilization should be very low (only 4 base cells out of 676).
    expect(report.details.terrainUtil.value).toBeCloseTo(4 / 676, 2)
  })

  it('detects dead zones in a disconnected stage', () => {
    // Create a stage with a sealed-off area.
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) tiles.push('.'.repeat(GRID))
    const setChar = (c: number, r: number, ch: string) => {
      tiles[r] = tiles[r].slice(0, c) + ch + tiles[r].slice(c + 1)
    }
    // Base
    setChar(12, 24, 'E')
    setChar(13, 24, 'E')
    setChar(12, 25, 'E')
    setChar(13, 25, 'E')
    // Steel wall sealing off the top-left corner (cols 0-3, rows 0-3)
    for (let c = 0; c <= 4; c++) {
      setChar(c, 4, 's')
    }
    for (let r = 0; r <= 4; r++) {
      setChar(4, r, 's')
    }

    const stage: StageData = { id: 0, name: 'deadzone', tiles, enemies: ['basic'] }
    const fakeResult = {
      outcome: 'stage_clear' as const,
      ticks: 100,
      wallMs: 1,
      finalState: {
        score: 0,
        lives: 3,
        killCount: 0,
        playTimeMs: 120000,
        stageIndex: 0,
        baseAlive: true,
        playerAlive: true,
        playerLevel: 0,
      },
      events: [],
      metrics: [],
      seed: 1,
      difficulty: 'classic',
      paramsHash: '',
      cleared: true,
    }
    const report = evaluate(fakeResult, stage, DEFAULT_BASELINE)
    // The spawn at (0,0) is now sealed off → dead zone.
    expect(report.details.noDeadZones.value).toBe(0)
  })
})

describe('God AI Functional', () => {
  // These tests verify the God AI can actually play the game: fire bullets,
  // kill enemies, and survive for a reasonable time. This catches regressions
  // where the AI "compiles but can't play" (the original Blocker 1 bug where
  // endFrame() was never called, causing the AI to think only once).

  it('fires bullets during simulation', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 3600, // 60 seconds
      sampleInterval: 60,
    })
    const playerBullets = result.events.filter(
      (e) => e.type === 'bullet_fired' && e.bullet?.isPlayer,
    ).length
    // The AI should fire at least a few bullets in 60 seconds.
    expect(playerBullets).toBeGreaterThan(0)
  })

  it('kills at least one enemy across multiple seeds', () => {
    // Run 5 seeds and check that the AI kills at least 1 enemy in total.
    let totalKills = 0
    for (const seed of [1, 2, 3, 4, 5]) {
      const result = runSimulation({
        seed,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 36000, // full 10 minutes
        sampleInterval: 60,
      })
      totalKills += result.finalState.killCount
    }
    // The AI should kill at least 1 enemy across 5 seeds.
    expect(totalKills).toBeGreaterThan(0)
  })

  it('survives at least 10 seconds on stage 0 classic', () => {
    const result = runSimulation({
      seed: 1,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 36000,
      sampleInterval: 60,
    })
    // The AI should survive at least 600 ticks (10 seconds).
    expect(result.ticks).toBeGreaterThan(600)
  })

  it('is deterministic: same seed produces same killCount and ticks', () => {
    const opts = {
      seed: 42,
      stage: STAGES[0],
      difficulty: 'classic',
      maxTicks: 1800,
      sampleInterval: 60,
    }
    const r1 = runSimulation(opts)
    const r2 = runSimulation(opts)
    expect(r1.finalState.killCount).toBe(r2.finalState.killCount)
    expect(r1.ticks).toBe(r2.ticks)
    expect(r1.finalState.score).toBe(r2.finalState.score)
  })
})
