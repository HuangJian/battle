import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findFreezePickupTargetImpl } from '../src/ai/god/StrategyPlanner'
import { CELL } from '../src/constants'
import type { PowerUp } from '../src/types'
import { clearArena, placeEnemy, makePowerUp as makePowerUpShared, seedWorld } from './helpers'

/**
 * §156: freeze-window power-up pickup (unlimited range) — unit tests.
 *
 * Root cause (hard S12 Lattice, 0:18~0:28): during freeze, PICKUP_HIGH
 * (weight 800) is gated by `!self.aggressive` and skipped. AGGRO (700) then
 * prioritizes stop-and-aim at any aligned frozen enemy, never checking for
 * nearby power-ups. A power-up 2 cells away was ignored for the entire
 * freeze window while the player camped shooting a frozen enemy.
 *
 * Fix: AGGRO checks for reachable power-ups BEFORE stop-and-aim. Enemies
 * are frozen — they cannot move or fire. DODGE (1000) already handled any
 * in-flight bullet threat.
 *
 * §156-v2 (user request 2026-08-06): range changed from 2 to 999
 * (effectively unlimited). During freeze the player should traverse the
 * map to grab any reachable power-up.
 *
 * These tests lock:
 *   1. findFreezePickupTargetImpl finds a power-up within range.
 *   2. freezePickupRange=0 returns null (byte-identical).
 *   3. A power-up beyond a small explicit range returns null.
 *   4. Default (999) picks up a FAR power-up (unlimited range).
 *   5. The AGGRO branch picks up a power-up during freeze (not stop-and-aim).
 *   6. The AGGRO branch does NOT pick up via freeze path during shield.
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
  input.reset()
  return { world, input }
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
const makePowerUp = (world: World, col: number, row: number, type: PowerUp['type']): PowerUp => {
  const pu = makePowerUpShared(col, row, type)
  world.powerUps.push(pu)
  return pu
}

describe('§156: findFreezePickupTargetImpl (function-level)', () => {
  it('finds a power-up within range', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'freeze') // 2 cells right
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    const target = findFreezePickupTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(12)
    expect(target!.row).toBe(10)
  })

  it('returns null when freezePickupRange=0 (byte-identical)', () => {
    const { world, input } = setupWorld({ freezePickupRange: 0 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'freeze')
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findFreezePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })

  it('returns null when power-up is beyond range', () => {
    const { world, input } = setupWorld({ freezePickupRange: 2 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 15, 10, 'freeze') // 5 cells right
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findFreezePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })

  it('returns the nearest power-up when multiple are in range', () => {
    const { world, input } = setupWorld({ freezePickupRange: 2 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 11, 10, 'bomb') // 1 cell right — nearest
    makePowerUp(world, 10, 12, 'star') // 2 cells down — farther
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    const target = findFreezePickupTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(11)
    expect(target!.row).toBe(10)
  })

  it('finds a far power-up with default range (999 = unlimited)', () => {
    const { world, input } = setupWorld() // default freezePickupRange=999
    positionPlayer(world, 2, 2)
    makePowerUp(world, 20, 20, 'freeze') // 36 cells away — within 999
    const pcx = 2 * CELL + CELL
    const pcy = 2 * CELL + CELL
    const target = findFreezePickupTargetImpl(input, pcx, pcy)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(20)
    expect(target!.row).toBe(20)
  })

  it('returns null when no power-ups exist', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    const pcx = 10 * CELL + CELL
    const pcy = 10 * CELL + CELL
    expect(findFreezePickupTargetImpl(input, pcx, pcy)).toBeNull()
  })
})

describe('§156: AGGRO branch freeze pickup (end-to-end)', () => {
  it('picks up a close power-up during freeze before stop-and-aim', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    // Enemy aligned with the player (same column, would trigger stop-and-aim)
    placeEnemy(world, 10, 6)
    // Power-up 2 cells away (within default freezePickupRange=999)
    makePowerUp(world, 12, 10, 'freeze')

    // Activate freeze
    world.freezeTimer = 600 // 10 seconds at 60fps
    input._thought = false
    input.getMoveDirection()

    // The player should be navigating toward the power-up, NOT camping
    expect(input._lastBranch).toBe('powerup')
    expect(input._moveDir).not.toBeNull()
  })

  it('freezePickupRange=0 is byte-identical (no freeze pickup, stop-and-aim fires)', () => {
    const { world, input } = setupWorld({ freezePickupRange: 0 })
    positionPlayer(world, 10, 10)
    placeEnemy(world, 10, 6)
    makePowerUp(world, 12, 10, 'freeze')

    world.freezeTimer = 600
    input._thought = false
    input.getMoveDirection()

    // With freezePickupRange=0, the aggressive stop-and-aim fires instead
    expect(input._lastBranch).toBe('aggressive')
  })

  it('does NOT pick up a power-up beyond freezePickupRange during freeze', () => {
    const { world, input } = setupWorld({ freezePickupRange: 2 })
    positionPlayer(world, 10, 10)
    placeEnemy(world, 10, 6)
    // Power-up 5 cells away (beyond range 2)
    makePowerUp(world, 15, 10, 'freeze')

    world.freezeTimer = 600
    input._thought = false
    input.getMoveDirection()

    // Too far — the aggressive stop-and-aim fires instead
    expect(input._lastBranch).toBe('aggressive')
  })

  it('freeze pickup does not fire when freezeTimer=0 (shield-only scenario)', () => {
    // During shield, self.aggressive = false (aggressive = freeze, not shield).
    // The AGGRO branch (where freeze pickup lives) doesn't run at all.
    // Verify findFreezePickupTargetImpl still works (it doesn't check
    // freezeTimer — the caller does), but the AGGRO branch won't call it.
    const { world, input } = setupWorld({ freezePickupRange: 2 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 12, 10, 'freeze')

    // Shield active but NOT freeze
    world.freezeTimer = 0
    world.player!.shieldTimer = 600
    input._thought = false
    input.getMoveDirection()

    // During shield, self.aggressive = false, so AGGRO's freeze pickup
    // code path is never entered. The power-up may be picked up via
    // other candidates (PICKUP_HIGH/LOW), but NOT via the freeze path.
    // The key invariant: the AGGRO branch did not commit.
    // If _lastBranch is 'powerup', it came from PICKUP_HIGH or PICKUP_LOW,
    // not from the freeze pickup code.
    // We verify the invariant indirectly: with no enemies and no base threat,
    // PICKUP_LOW should handle the pickup (not AGGRO).
    expect(input._lastBranch).not.toBe('aggressive')
  })
})

describe('§184: freeze pickup falls through when stuck', () => {
  it('freeze pickup falls through to aggressive after 90 ticks of immobility', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    // Enemy in line of fire (same column, above)
    placeEnemy(world, 10, 5)
    // Power-up within range
    makePowerUp(world, 12, 10, 'shield')

    // Activate freeze
    world.freezeTimer = 600

    // First tick — freeze pickup should commit (not stuck yet)
    input._thought = false
    input.getMoveDirection()
    expect(input._lastBranch).toBe('powerup')

    // Simulate 90+ ticks of immobility by setting _digBlockTicks
    // (normally incremented by endFrame when player doesn't move)
    input._digBlockTicks = 91
    input._thought = false
    input.getMoveDirection()

    // After 90 ticks stuck, freeze pickup should fall through to aggressive
    // (stop-and-aim at the enemy), NOT stay in powerup
    expect(input._lastBranch).not.toBe('powerup')
  })

  it('freeze pickup commits normally when not stuck (byte-identical)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    placeEnemy(world, 10, 5)
    makePowerUp(world, 12, 10, 'shield')

    world.freezeTimer = 600
    // _digBlockTicks = 0 (not stuck)
    input._thought = false
    input.getMoveDirection()

    // Should commit freeze pickup normally
    expect(input._lastBranch).toBe('powerup')
    expect(input._moveDir).not.toBeNull()
  })
})
