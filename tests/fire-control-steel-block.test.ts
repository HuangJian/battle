import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { scanAheadImpl, shouldFireInDirImpl } from '../src/ai/god/FireControl'
import { CELL, GRID } from '../src/constants'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import type { Tank } from '../src/types'

/**
 * FireControl steel-blocking unit tests.
 *
 * Bug: God AI fires at an enemy even when steel blocks the bullet path.
 * Root cause: scanAheadImpl uses two independent offset scan lines. If
 * offset 0 hits steel but offset 1 finds an enemy, BOTH result.steel=true
 * AND result.enemy=true are set. shouldFireInDirImpl checked enemy BEFORE
 * steel, so it fired when result.enemy was true, ignoring the steel.
 *
 * Fix: moved steel/baseWall check BEFORE enemy check in shouldFireInDirImpl.
 * Also added scanAhead check in think()'s aggressive mode.
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)

  // Clear all terrain and place base cells.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }

  return { world, input, sim }
}

/** Create an enemy tank and place it at the given grid position. */
function placeEnemy(world: World, col: number, row: number): Tank {
  const e = world.createTank('basic', col * CELL, row * CELL, 'down')
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

describe('scanAheadImpl — steel and enemy on dual offset lines', () => {
  it('detects enemy when no obstacle is in the way', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    expect(result.enemy).toBe(true)
    expect(result.steel).toBe(false)
  })

  it('steel on BOTH offset lines blocks enemy detection', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel at BOTH cols 8 and 9 — blocks both offset scan lines.
    world.tileMap.grid[6][8] = 'steel'
    world.tileMap.grid[6][9] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    expect(result.steel).toBe(true)
    expect(result.enemy).toBe(false)
  })

  it('dual-offset: steel on one line + enemy on the other sets BOTH flags', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2 // 136
    const pcy = 10 * CELL + CELL / 2 // 168

    // Enemy at col 8, row 3 — spans x=128-160, so it overlaps BOTH
    // offset scan lines (sx=128 and sx=144).
    placeEnemy(world, 8, 3)
    // Steel at col 8, row 6 — only hits offset 0 (sx=128, col=8).
    // Offset 1 (sx=144, col=9) has no steel and finds the enemy.
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    // Offset 0 hits steel → steel=true. Offset 1 finds enemy → enemy=true.
    // This is the root cause of the bug: both flags are true.
    expect(result.steel).toBe(true)
    expect(result.enemy).toBe(true)
  })
})

describe('shouldFireInDirImpl — steel must block fire even when enemy is visible', () => {
  it('fires when enemy is visible and no steel blocks', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(true)
  })

  it('does NOT fire when steel blocks both offset lines (level < 3)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel on BOTH offset lines — enemy not detected.
    world.tileMap.grid[6][8] = 'steel'
    world.tileMap.grid[6][9] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(false)
  })

  it('does NOT fire through steel on dual-offset line (level < 3)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel on offset 0 only — enemy visible on offset 1.
    // Before fix: fired because result.enemy was checked first.
    // After fix: does NOT fire because result.steel blocks it.
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(false)
  })

  it('fires through steel when player level >= 3 (can pierce)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    if (world.player) world.player.level = 3

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(true)
  })
})
