import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { initEnemyModel, updateEnemyModel, survivalPressure } from '../src/ai/god/EnemyModel'
import { CANDIDATES } from '../src/ai/god/think'
import { orderedCandidates } from '../src/ai/god/DecisionCore'
import { CELL } from '../src/constants'
import type { Tank, TankKind } from '../src/types'
import { clearArena } from './helpers'

/**
 * M3 (plan/God-AI-Redesign-v2 §4.2b): EnemyModel 敌情感知 + survive 候选 +
 * 命数感知 — unit tests.
 *
 * All M3 behavior ships OFF by default (byte-identical gates). These tests
 * drive the model + candidates directly with synthetic World states and
 * assert the mechanics work when the params are explicitly enabled.
 */

function setupWorld(difficulty = 'classic'): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  clearArena(world)
  void sim
  input.reset()
  return { world, input }
}

function spawnEnemy(world: World, kind: TankKind, x: number, y: number): Tank {
  const t = world.createTank(kind, x, y, 'down')
  t.spawnTimer = 0 // fully spawned
  t.alive = true
  t.fireCount = 0
  t.dir = 'down'
  world.tanks.push(t)
  return t
}

function setPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.alive = true
  p.spawnTimer = 0 // fully spawned — thinkImpl would otherwise short-circuit to 'dead'
  p.hp = p.maxHp
}

// Activate the model with a small window for fast tests.
function enableModel(input: GodAIInput, mode = 1, window = 30): void {
  input.params.enemyModelMode = mode
  input.params.enemyModelWindowTicks = window
  input._enemyModel = initEnemyModel(true)
}

describe('M3 EnemyModel — feature observation', () => {
  it('fire accuracy rises when enemies fire at the player and hits land', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    const e = spawnEnemy(world, 'fast', 8 * CELL, 5 * CELL)

    // Drive 12 windows: enemy fires every tick, player takes a hit every 2 ticks.
    for (let w = 0; w < 12; w++) {
      for (let t = 0; t < 10; t++) {
        e.fireCount++
        // Every 2nd tick a hit lands (hp drops).
        if (t % 2 === 0 && world.player) {
          world.player.hp = Math.max(0, world.player.hp - 1)
        }
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.fireAccuracy).toBeGreaterThan(0.3)
  })

  it('attack tendency rises when enemies move toward the base', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    // Enemy starts far above the base (row 2) and moves DOWN each tick
    // (row increases → closer to base at rows 24-25). Bounce back to row 2
    // before leaving the field so every tick is an approach (the feature
    // must measure approach, not the field edge).
    const e = spawnEnemy(world, 'basic', 8 * CELL, 2 * CELL)
    e.dir = 'down'

    for (let w = 0; w < 12; w++) {
      for (let t = 0; t < 10; t++) {
        e.y += 16 // move down one cell per tick (closer to base)
        if (e.y >= 22 * CELL) e.y = 2 * CELL // stay within approach range
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.attackTendency).toBeGreaterThan(0.4)
  })

  it('coordination rises when enemies align with the player', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    // Enemy on the same row as the player.
    spawnEnemy(world, 'basic', 2 * CELL, 10 * CELL)

    for (let w = 0; w < 12; w++) {
      for (let t = 0; t < 10; t++) {
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.coordination).toBeGreaterThan(0.2)
  })

  it('discipline rises when enemies change direction frequently', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    const e = spawnEnemy(world, 'power', 8 * CELL, 2 * CELL)

    const dirs = ['down', 'left', 'down', 'right', 'down', 'left'] as const
    let di = 0
    for (let w = 0; w < 12; w++) {
      for (let t = 0; t < 10; t++) {
        e.dir = dirs[di++ % dirs.length]
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.discipline).toBeGreaterThan(0.3)
  })

  it('estimatedLevel is a blend that rises with all features high', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    const e = spawnEnemy(world, 'fast', 8 * CELL, 5 * CELL)
    const dirs = ['down', 'left', 'down', 'right'] as const
    let di = 0

    for (let w = 0; w < 20; w++) {
      for (let t = 0; t < 10; t++) {
        e.fireCount++
        e.dir = dirs[di++ % dirs.length]
        e.y += 8 // slow approach to base
        if (e.y >= 22 * CELL) e.y = 2 * CELL // stay within approach range
        if (t % 3 === 0 && world.player) {
          world.player.hp = Math.max(0, world.player.hp - 1)
        }
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.estimatedLevel).toBeGreaterThan(0.4)
    expect(input._enemyModel.estimatedLevel).toBeLessThanOrEqual(1)
  })

  it('default params: model stays inactive — estimatedLevel is 0', () => {
    const { world, input } = setupWorld()
    setPlayer(world, 8 * CELL, 10 * CELL)
    spawnEnemy(world, 'fast', 8 * CELL, 5 * CELL)
    // Default params (enemyModelMode=0) — the update hook is never called.
    expect(input._enemyModel.active).toBe(false)
    for (let t = 0; t < 50; t++) {
      updateEnemyModel(input) // no-op: active=false
    }
    expect(input._enemyModel.estimatedLevel).toBe(0)
  })
})

describe('M3 survival pressure', () => {
  it('activates when lives ≤ survivalModeLives', () => {
    const { world, input } = setupWorld('chaos')
    input.params.survivalModeLives = 1
    world.lives = 3
    expect(survivalPressure(input)).toBe(0)
    world.lives = 1
    expect(survivalPressure(input)).toBe(1)
  })

  it('activates early via enemy accuracy threshold', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    const e = spawnEnemy(world, 'fast', 8 * CELL, 5 * CELL)
    input.params.enemyAccuracyRaisesSurvival = 0.3
    input.params.survivalModeLives = 0
    world.lives = 3 // plenty of lives — accuracy alone must activate

    for (let w = 0; w < 20; w++) {
      for (let t = 0; t < 10; t++) {
        e.fireCount++
        if (t % 2 === 0 && world.player) {
          world.player.hp = Math.max(0, world.player.hp - 1)
        }
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.fireAccuracy).toBeGreaterThan(0.3)
    expect(survivalPressure(input)).toBe(1)
  })

  it('activates early via coordination risk weight', () => {
    const { world, input } = setupWorld()
    enableModel(input, 1, 10)
    setPlayer(world, 8 * CELL, 10 * CELL)
    // Two enemies aligned with the player row → coordination ≥ 2/4 = 0.5.
    spawnEnemy(world, 'basic', 2 * CELL, 10 * CELL)
    spawnEnemy(world, 'basic', 4 * CELL, 10 * CELL)
    input.params.coordinationRiskWeight = 3 // ~0.45 * 3 ≥ 1 → activate
    input.params.survivalModeLives = 0
    world.lives = 3

    for (let w = 0; w < 10; w++) {
      for (let t = 0; t < 10; t++) {
        updateEnemyModel(input)
      }
    }
    expect(input._enemyModel.coordination).toBeGreaterThan(0.2)
    expect(survivalPressure(input)).toBe(1)
  })
})

describe('M3 survive candidate', () => {
  it('does NOT commit at default params (weight 0 — parity)', () => {
    const { world, input } = setupWorld()
    setPlayer(world, 8 * CELL, 10 * CELL)
    // Corridor: wall the left/right neighbors (≤2 exits).
    for (const [c, r] of [
      [7, 10],
      [7, 11],
      [10, 10],
      [10, 11],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    input._canMoveComputed = 0
    spawnEnemy(world, 'basic', 8 * CELL, 14 * CELL)
    input.getMoveDirection()
    // Default surviveMinEnemies=0 → the candidate never commits.
    expect(input.branchCounts.survive).toBe(0)
  })

  it('repositions out of a surrounded dead-end when enabled + survival pressure', () => {
    const { world, input } = setupWorld()
    setPlayer(world, 8 * CELL, 10 * CELL)
    // Dead-end: walls on left, right, and up → only down is open.
    for (const [c, r] of [
      [7, 10],
      [7, 11],
      [10, 10],
      [10, 11],
      [8, 9],
      [9, 9],
    ]) {
      world.tileMap.grid[r][c] = 'brick'
    }
    // Open area below is fully empty (more exits).
    input.params.surviveMinEnemies = 1
    input.params.surviveEnemyRadiusCells = 3
    input.params.survivalModeLives = 1
    world.lives = 1
    // Promote survive above hunt (design: M4 tuning surface via actionWeights)
    // and rebuild the ordered chain — otherwise weight 0 sorts below hunt and
    // the candidate is never reached.
    input.params.actionWeights = { survive: 950 }
    input._orderedCandidates = orderedCandidates(CANDIDATES, input.params.actionWeights)
    input._canMoveComputed = 0
    // Enemy on the DIAGONAL (6,12) — within radius 5 of player cell (8,10)
    // (|6-8|+|12-10|=4) but NOT aligned with the player (dx=32, dy=32 — the
    // TANK=32 alignment band), so aimDir is null and survive may commit.
    // Also does not physically block the down exit.
    input.params.surviveEnemyRadiusCells = 5
    spawnEnemy(world, 'basic', 6 * CELL, 12 * CELL)

    const dir = input.getMoveDirection()
    expect(dir).toBe('down')
    expect(input.branchCounts.survive).toBe(1)
  })

  it('does NOT reposition in open ground (exits > 2)', () => {
    const { world, input } = setupWorld()
    setPlayer(world, 8 * CELL, 10 * CELL)
    input.params.surviveMinEnemies = 1
    input.params.surviveEnemyRadiusCells = 3
    input.params.survivalModeLives = 1
    world.lives = 1
    input._canMoveComputed = 0
    spawnEnemy(world, 'basic', 8 * CELL, 12 * CELL)

    input.getMoveDirection()
    // Open field → 4 exits → survive declines; hunt navigates instead.
    expect(input.branchCounts.survive).toBe(0)
  })
})
