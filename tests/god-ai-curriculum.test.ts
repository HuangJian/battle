import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { CELL, BASE_POS } from '../src/constants'
import type { StageData } from '../src/types'
import {
  makeArena,
  makeMazeStage,
  CURRICULUM_STAGES,
  runCurriculumStage,
} from '../tools/curriculum'
import { runSimulation } from '../tools/simulation-runner'

// ============================================================
// God AI Curriculum Tests (plan/God-AI-Curriculum §4, §5.4)
//
// These tests verify per-subsystem behavior of the God AI using
// isolated mini-stages (toy arenas). Toy stages are NEVER used as
// CMA-ES fitness — only as regression gates (§1).
// ============================================================

// Helper: build a no-base stage with a single enemy in an open arena.
function noBaseArena(enemyCount = 1, size = 12): StageData {
  return makeArena({ size, enemyCount })
}

describe('god-ai-curriculum: hasBase guard (Gap B)', () => {
  it('TileMap.hasBase() returns false for arena without base', () => {
    const stage = noBaseArena(1)
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.tileMap.hasBase()).toBe(false)
  })

  it('TileMap.hasBase() returns true for maze stage with base', () => {
    const stage = makeMazeStage({ base: true })
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.tileMap.hasBase()).toBe(true)
  })

  it('GodAIInput caches hasBase=false on reset for no-base stage', () => {
    const stage = noBaseArena(1)
    const world = new World()
    world.rng.reseed(42)
    world.difficultyKey = 'classic'
    world.difficulty = DIFFICULTIES['classic']
    world.rules = RULES['classic'] ?? DEFAULT_RULES
    const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS)
    world.loadStageData(stage, 0)
    input.reset()
    // The AI should not try to defend a ghost base — verify by running
    // a few ticks and checking the player moves toward the enemy, not
    // toward the default BASE_POS (12, 24).
    const sim = new Simulation(world, input)
    const startPos = { x: world.player!.x, y: world.player!.y }
    for (let i = 0; i < 300; i++) {
      sim.tick()
      input.endFrame()
    }
    const endPos = { x: world.player!.x, y: world.player!.y }
    const baseX = BASE_POS.col * CELL
    const baseY = BASE_POS.row * CELL

    // The player should NOT be sitting at the default base position
    // (12*16=192, 24*16=384). If the hasBase guard is broken, the AI
    // parks the player there defending a ghost base.
    const atGhostBase = Math.abs(endPos.x - baseX) < CELL && Math.abs(endPos.y - baseY) < CELL
    expect(atGhostBase).toBe(false)

    // The player should have moved from its start position (hunting).
    const moved = Math.abs(endPos.x - startPos.x) + Math.abs(endPos.y - startPos.y)
    expect(moved).toBeGreaterThan(0)
  })

  it('enemyCount override controls total enemies (Gap A)', () => {
    const stage = noBaseArena(5, 14)
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.enemiesTotal).toBe(5)
    expect(world.enemiesRemaining).toBe(5)
    expect(world.spawnQueue.length).toBe(5)
  })

  it('playerSpawn override is respected', () => {
    const stage: StageData = {
      ...noBaseArena(1, 12),
      playerSpawn: { col: 10, row: 10 },
    }
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.player).toBeDefined()
    expect(world.player!.x).toBe(10 * CELL)
    expect(world.player!.y).toBe(10 * CELL)
  })

  it('enemySpawns override is respected', () => {
    const customSpawns = [{ col: 5, row: 5 }]
    const stage: StageData = {
      ...noBaseArena(1, 12),
      enemySpawns: customSpawns,
    }
    const world = new World()
    world.loadStageData(stage, 0)
    expect(world.enemySpawnPoints).toEqual([{ x: 5 * CELL, y: 5 * CELL }])
  })
})

describe('god-ai-curriculum: determinism', () => {
  it('same seed + same arena produces identical results', () => {
    const stage = noBaseArena(3, 14)
    const opts = {
      seed: 42,
      stage,
      difficulty: 'classic' as const,
      maxTicks: 600,
      sampleInterval: 30,
    }
    const r1 = runSimulation(opts)
    const r2 = runSimulation(opts)
    expect(r1.outcome).toBe(r2.outcome)
    expect(r1.ticks).toBe(r2.ticks)
    expect(r1.finalState.killCount).toBe(r2.finalState.killCount)
  })
})

describe('god-ai-curriculum: stage ladder', () => {
  // All 5 curriculum stages are hard CI gates. Each stage isolates one
  // subsystem of the God AI and asserts a concrete expected outcome.
  // If a stage fails, the corresponding AI subsystem has regressed.
  //
  // The key navigation improvements that enable all 5 stages to pass:
  // - Distance-adaptive navigation: A* for long-range (maze corridors),
  //   directMove for close-range (tracking moving enemies).
  // - suboptimalPathProb removed from followPath: random perpendicular
  //   directions caused axis-lock snap oscillation, trapping the player.
  // - canHunt without baseUnderThreat gate: the AI hunts freely in the
  //   endgame instead of turtling when enemies approach the base.
  // - Free hunting when base is not under threat: the AI chases the
  //   nearest enemy instead of sitting at the defense position.

  for (const cs of CURRICULUM_STAGES) {
    it(`stage ${cs.id}: ${cs.desc}`, () => {
      const result = runCurriculumStage(cs)
      expect(result.passed).toBe(true)
    }, 30000) // 30s timeout per stage
  }
})
