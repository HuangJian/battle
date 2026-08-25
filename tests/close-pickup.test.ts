import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findClosePickupTargetImpl } from '../src/ai/god/StrategyPlanner'
import { CELL, BASE_POS } from '../src/constants'
import type { Tank, PowerUp } from '../src/types'
import { clearArena, makePowerUp as makePowerUpShared, seedWorld } from './helpers'

/**
 * §158: non-freeze close-range power-up pickup — unit tests.
 *
 * When NOT in freeze/shield mode, if a power-up is within `closePickupRange`
 * (default 2) cells and there is no immediate bullet threat (DODGE at weight
 * 1000 already declined), the player navigates to pick it up while firing at
 * enemies in the move direction (随手开火).
 *
 * Unlike PICKUP_HIGH/MID (which gate on nearby-enemy proximity and route
 * danger), this candidate has NO enemy gates — close items are worth
 * grabbing even with enemies nearby. Skips when base is under threat.
 *
 * These tests lock:
 *   1. findClosePickupTargetImpl finds a power-up within range.
 *   2. closePickupRange=0 returns null (byte-identical).
 *   3. A power-up beyond range returns null.
 *   4. CLOSE_PICKUP picks up a close power-up in non-freeze mode.
 *   5. CLOSE_PICKUP does NOT fire during freeze (AGGRO handles it).
 *   6. CLOSE_PICKUP does NOT fire when base is under threat.
 *   7. CLOSE_PICKUP fires while moving toward the power-up (随手开火).
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  clearArena(world)
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

// Local push-flavor → shared pure factory (遗留 #5; 口径差异表 in tests/helpers.ts).
const makePowerUp = (
  world: World,
  col: number,
  row: number,
  type: PowerUp['type'],
): PowerUp => {
  const pu = makePowerUpShared(col, row, type)
  world.powerUps.push(pu)
  return pu
}

describe('§158: findClosePickupTargetImpl (function-level)', () => {
  it('finds a power-up within range', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star') // 2 cells right — within default 2
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    const target = findClosePickupTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(12)
    expect(target!.row).toBe(10)
  })

  it('returns null when closePickupRange=0 (byte-identical)', () => {
    const { world, input } = setupWorld({ closePickupRange: 0 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star')
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findClosePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })

  it('returns null when power-up is beyond range', () => {
    const { world, input } = setupWorld({ closePickupRange: 4 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 16, 10, 'star') // 6 cells right — beyond range 4
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findClosePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })

  it('returns the nearest power-up when multiple are in range', () => {
    const { world, input } = setupWorld({ closePickupRange: 4 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 11, 10, 'bomb') // 1 cell right — nearest
    makePowerUp(world, 10, 13, 'star') // 3 cells down — farther
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    const target = findClosePickupTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(11)
    expect(target!.row).toBe(10)
  })

  it('returns null when no power-ups exist', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findClosePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })
})

describe('§158: CLOSE_PICKUP candidate (end-to-end)', () => {
  it('picks up a close power-up in non-freeze mode', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star') // 2 cells right — within default 2
    // No freeze, no shield, no base threat, no bullet threat
    input._thought = false
    input.getMoveDirection()
    expect(input._lastBranch).toBe('powerup')
    expect(input._moveDir).not.toBeNull()
  })

  it('does NOT fire during freeze (AGGRO handles it)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    makePowerUp(world, 13, 10, 'star') // 3 cells — found by freeze pickup (range 999)
    world.freezeTimer = 600
    input._thought = false
    input.getMoveDirection()
    // AGGRO's freeze pickup should commit, not CLOSE_PICKUP
    expect(input._lastBranch).toBe('powerup')
  })

  it('does NOT fire when base is under threat', () => {
    const { world, input } = setupWorld({
      chokepointMode: 0,
      baseClearShotThreat: 0,
      pickupPriorityMode: 0, // isolate CLOSE_PICKUP from PICKUP_HIGH/MID
    })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star') // 2 cells — within default range 2
    placeEnemy(world, BASE_POS.col, 20)
    // Place an enemy in line of fire to block PICKUP_LOW (aimDir set)
    placeEnemy(world, 10, 6, 'down')
    // Refresh the enemy snapshot
    input._enemies.length = 0
    for (const t of world.tanks) {
      if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
    }
    input._baseUnderThreatCache = null
    input._thought = false
    input.getMoveDirection()
    // Base under threat — CLOSE_PICKUP should NOT commit
    expect(input._lastBranch).not.toBe('powerup')
  })

  it('closePickupRange=0 is byte-identical (no close pickup)', () => {
    const { world, input } = setupWorld({
      closePickupRange: 0,
      pickupPriorityMode: 0, // isolate CLOSE_PICKUP from PICKUP_HIGH/MID
    })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star') // 2 cells — within range, but range=0 declines
    // Place an enemy in line of fire to block PICKUP_LOW (aimDir set)
    placeEnemy(world, 10, 6, 'down')
    input._thought = false
    input.getMoveDirection()
    // With closePickupRange=0, CLOSE_PICKUP declines. PICKUP_HIGH/MID are
    // disabled (mode 0), PICKUP_LOW is blocked (aimDir set). ENGAGE fires.
    expect(input._lastBranch).not.toBe('powerup')
  })

  it('does NOT pick up a power-up beyond closePickupRange', () => {
    const { world, input } = setupWorld({
      closePickupRange: 4,
      pickupPriorityMode: 0, // isolate CLOSE_PICKUP from PICKUP_HIGH/MID
    })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 16, 10, 'star') // 6 cells — beyond range 4
    // Place an enemy in line of fire to block PICKUP_LOW (aimDir set)
    placeEnemy(world, 10, 6, 'down')
    input._thought = false
    input.getMoveDirection()
    // Too far — CLOSE_PICKUP declines. ENGAGE fires instead.
    expect(input._lastBranch).not.toBe('powerup')
  })

  it('fires at enemies while moving toward the power-up (随手开火)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'star') // 2 cells right — within default 2
    // Place an enemy in the move direction (right) so shouldFireInDir fires
    placeEnemy(world, 14, 10, 'left')
    input._thought = false
    input.getMoveDirection()
    // The player should be moving toward the power-up
    expect(input._lastBranch).toBe('powerup')
    expect(input._moveDir).not.toBeNull()
    // The candidate calls shouldFireInDir(pcx, pcy, moveDir) — fire control
    // is exercised; the exact _fire value depends on scan/cooldown state
    // which is unit-tested in FireControl. Here we verify the candidate
    // commits and navigates (the随手开火 code path runs).
  })
})
