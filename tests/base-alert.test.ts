import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { PowerUp } from '../src/types'
import { clearArena, placeEnemy, makePowerUp as makePowerUpShared, seedWorld } from './helpers'

/**
 * §225 "too late" defense structure (toolate-audit: 40 base_destroyed runs —
 * window median 271 ticks, sentry engaged in only 37.5% of windows with a
 * median 2 ticks; 62.5% of windows have ZERO sentry ticks while the player
 * navigates blindly / picks up items). Two candidate fixes:
 *
 *  A `baseLaneSentryInBandNav` — ring breached + in-band enemy (row ≥ 23) +
 *    player off-lane → step sideways onto the enemy's column (colGap ≤ 3) to
 *    plug the lane. The station nav only serves out-of-band enemies (rows
 *    20-22), leaving an in-band blind spot.
 *  B `baseAlertPickupSuppress` — ring breached → MID-tier (star/tank/shield)
 *    pickups yield to defense; HIGH tier (bomb/freeze/fence) is exempt (they
 *    are dire-state tools, §180: fence = structural base defense).
 *
 * Both default 0 = OFF (byte-identical).
 */

function setupWorld(): { world: World; input: GodAIInput } {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  clearArena(world)
  void sim
  return { world, input }
}

// Local flavor → shared factory (遗留 #5): explicit ids were never asserted.
const makePowerUp = (type: PowerUp['type'], col: number, row: number): PowerUp =>
  makePowerUpShared(col, row, type)

function placePlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * 16 - 8 // center col = floor((x+16)/16) = col
  p.y = row * 16 - 8 // center row = floor((y+16)/16) = row
  p.spawnTimer = 0
  p.shieldTimer = 0
}

describe('§225-B baseAlertPickupSuppress (MID-tier yield, HIGH exempt)', () => {
  it('defaults to OFF (0) — byte-identical baseline', () => {
    expect(DEFAULT_GOD_AI_PARAMS.baseAlertPickupSuppress).toBe(0)
  })

  it('OFF: ring breached + star nearby still diverts to the power-up branch', () => {
    const { world, input } = setupWorld()
    placePlayer(world, 1, 24) // center col 1, row 24
    world.addPowerUp(makePowerUp( 'star', 4, 24)) // dist 4 → MID tier
    input.getMoveDirection()
    expect(input.branchCounts.powerup).toBe(1)
  })

  it('ON: ring breached + star nearby yields to defense (no power-up branch)', () => {
    const { world, input } = setupWorld()
    input.params.baseAlertPickupSuppress = 1
    placePlayer(world, 1, 24)
    world.addPowerUp(makePowerUp( 'star', 4, 24))
    input.getMoveDirection()
    expect(input.branchCounts.powerup).toBe(0)
  })

  it('ON: ring breached + bomb nearby still diverts (HIGH tier exempt)', () => {
    const { world, input } = setupWorld()
    input.params.baseAlertPickupSuppress = 1
    placePlayer(world, 1, 24)
    world.addPowerUp(makePowerUp( 'bomb', 4, 24)) // dist 4 → HIGH tier
    input.getMoveDirection()
    expect(input.branchCounts.powerup).toBe(1)
  })
})

describe('§225-A baseLaneSentryInBandNav (in-band lane plug)', () => {
  it('defaults to OFF (0) — byte-identical baseline', () => {
    expect(DEFAULT_GOD_AI_PARAMS.baseLaneSentryInBandNav).toBe(0)
  })

  it('OFF: in-band enemy + off-lane player does NOT enter the sentry branch', () => {
    const { world, input } = setupWorld()
    placePlayer(world, 10, 21) // 3 cols left of the enemy
    placeEnemy(world, 12, 23) // in-band, aligned with base cols 12-13
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('baseLaneSentry')
  })

  it('ON: in-band enemy + player 3 cols off-lane → step onto the enemy column', () => {
    const { world, input } = setupWorld()
    input.params.baseLaneSentryInBandNav = 1
    placePlayer(world, 10, 21)
    placeEnemy(world, 12, 23)
    input.getMoveDirection()
    expect(input._lastBranch).toBe('baseLaneSentry')
    expect(input._moveDir).toBe('right')
  })

  it('ON: colGap 1 does not hijack (already near the lane)', () => {
    const { world, input } = setupWorld()
    input.params.baseLaneSentryInBandNav = 1
    placePlayer(world, 11, 21)
    placeEnemy(world, 12, 23)
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('baseLaneSentry')
  })

  it('ON: colGap 4 exceeds the distance limit — falls through', () => {
    const { world, input } = setupWorld()
    input.params.baseLaneSentryInBandNav = 1
    placePlayer(world, 8, 21)
    placeEnemy(world, 12, 23)
    input.getMoveDirection()
    expect(input._lastBranch).not.toBe('baseLaneSentry')
  })
})
