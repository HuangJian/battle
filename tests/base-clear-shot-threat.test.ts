import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID, BASE_POS } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * §157: base clear-shot threat detection — unit tests.
 *
 * Root cause (hard S12 Lattice, 0:38~0:48): an enemy aligned with the base
 * column from far away was actively shooting the base through a cleared lane.
 * isBaseUnderThreat() returned false (row < 18, distance > race range), so
 * selectTarget didn't return the defense position and the player kept
 * hunting at the top of the map while the base was destroyed.
 *
 * Fix: isBaseUnderThreat() also returns true when any alive, spawned enemy
 * can currently shoot the base (enemyCanShootBase — aligned + clear LOS).
 * Gated by baseClearShotThreat (0 = OFF, byte-identical).
 *
 * Tests use chokepointMode=0 to isolate the baseClearShotThreat check from
 * the §88 chokepoint threat-point detection (which also catches aligned
 * enemies that face the base). The §157 check is broader — it fires even
 * when the enemy is NOT facing the base, because the enemy can turn and
 * fire at any moment.
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

function placeEnemy(world: World, col: number, row: number, dir: Tank['dir'] = 'down'): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, dir)
  enemy.alive = true
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
}

function refreshEnemies(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

describe('§157: base clear-shot threat detection', () => {
  it('detects an enemy with a clear shot at the base from far away', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    // Enemy at base column (col 12), row 5 — aligned, clear line to base
    // Distance to base: |12-12| + |5-24| = 19 > baseRaceRangeCells (18)
    placeEnemy(world, 12, 5)
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('baseClearShotThreat=0 is byte-identical (far enemy not detected)', () => {
    const { world, input } = setupWorld({ chokepointMode: 0, baseClearShotThreat: 0 })
    positionPlayer(world, 2, 2)
    placeEnemy(world, 12, 5)
    refreshEnemies(input, world)

    // With baseClearShotThreat=0 AND chokepointMode=0, the far enemy is NOT
    // detected: row 5 < 18 (static box fails), dist 19 > 18 (race fails)
    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('does NOT trigger when a brick wall blocks the line of sight', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    placeEnemy(world, 12, 5)
    // Place a brick wall between the enemy and the base
    world.tileMap.grid[15][12] = 'brick'
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('does NOT trigger when enemy is not aligned with the base', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    // Enemy at col 5, row 10 — not aligned with base (col 12, row 24)
    placeEnemy(world, 5, 10)
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('does NOT trigger for a dead enemy', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    const enemy = placeEnemy(world, 12, 5)
    enemy.alive = false
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('does NOT trigger for a spawning enemy', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    const enemy = placeEnemy(world, 12, 5)
    enemy.spawnTimer = 60
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('triggers for an enemy aligned on the base ROW (horizontal shot)', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    // Enemy at row 24 (base row), col 2 — aligned horizontally
    placeEnemy(world, 2, 24)
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('triggers even when enemy is NOT facing the base (can turn and fire)', () => {
    // This is the key difference from the §88 chokepoint check, which
    // requires the enemy to face the base (facingGate). The §156 check
    // fires regardless of facing — an aligned enemy can turn and fire
    // at any moment.
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    // Enemy at col 12, row 5, facing UP (away from base)
    placeEnemy(world, 12, 5, 'up')
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('causes selectTarget to return the defense position when player is far', () => {
    const { world, input } = setupWorld({ chokepointMode: 0 })
    positionPlayer(world, 2, 2)
    placeEnemy(world, 12, 5)
    refreshEnemies(input, world)

    const playerCell = input.playerCell()
    const target = input.selectTarget(playerCell)

    expect(target).not.toBeNull()
    // The defense position is near the base (col 12, row 23)
    const distToBase = Math.abs(target!.col - BASE_POS.col) + Math.abs(target!.row - BASE_POS.row)
    expect(distToBase).toBeLessThan(10)
  })

  it('works alongside chokepointMode=1 (ORed, never reduces detection)', () => {
    const { world, input } = setupWorld() // default: chokepointMode=1, baseClearShotThreat=1
    positionPlayer(world, 2, 2)
    // Enemy NOT facing the base — chokepoint check (facingGate) would skip it,
    // but baseClearShotThreat catches it.
    placeEnemy(world, 12, 5, 'up')
    refreshEnemies(input, world)

    expect(input.isBaseUnderThreat()).toBe(true)
  })
})
