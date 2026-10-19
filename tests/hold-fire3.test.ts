import { describe, expect, it } from 'bun:test'
import { Input } from '../src/game/Input'
import { Simulation } from '../src/game/Simulation'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL, GRID, type Direction } from '../src/constants'
import { seedWorld } from './helpers'

/**
 * v3 action contract (plan/nn-capability-upgrade.plan.md B1):
 *   hold=1          -> turn toward `move` but do not advance
 *   fireBeforeTurn  -> fire along the pre-turn facing, then turn
 *   otherwise       -> commit the turn first, then fire along the new facing
 *
 * The contract is opt-in via `usesHoldFire3()`. God-AI/human inputs omit it,
 * so the legacy 5-move/2-fire path stays byte-identical.
 */
class TestInput extends Input {
  private move: Direction | null = null
  private firing = false
  private hold = false
  private fireBeforeTurn = false

  set(move: Direction | null, firing = false, hold = false, fireBeforeTurn = false): void {
    this.move = move
    this.firing = firing
    this.hold = hold
    this.fireBeforeTurn = fireBeforeTurn
  }
  override getMoveDirection(): Direction | null {
    return this.move
  }
  override isFiring(): boolean {
    return this.firing
  }
  isHolding(): boolean {
    return this.hold
  }
  isFireBeforeTurn(): boolean {
    return this.fireBeforeTurn
  }
  usesHoldFire3(): boolean {
    return true
  }
}

function setup(turnCooldownMs = 200): {
  world: World
  sim: Simulation
  input: TestInput
} {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'], turnCooldownMs }
  const input = new TestInput()
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[0], 0)
  world.state = 'playing'

  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty' as never
  }
  world.tanks.length = 0
  world.allies.length = 0
  world.spawnQueue.length = 0
  world.enemiesRemaining = 1

  const p = world.player!
  p.x = 8 * CELL
  p.y = 10 * CELL
  p.moving = false
  p.spawnTimer = 0
  p.alive = true
  p.hp = p.maxHp
  p.nextFireInterval = 1
  p.lastFire = -9999
  return { world, sim, input }
}

describe('v3 action contract: hold / fireBeforeTurn', () => {
  it('hold=1 turns without advancing and still obeys the turn cooldown', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', false, true)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.prevMoveDir as string).toBe('right')
    expect(p.moving).toBe(false)
    expect(p.x).toBe(8 * CELL)
    expect(p.y).toBe(10 * CELL)

    // Second turn inside the 200ms window must be rejected: hold is not free aim.
    input.set('down', false, true)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.moving).toBe(false)
  })

  it('fireBeforeTurn fires along the old facing, then commits the turn', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', true, false, true)
    sim.tick()

    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot?.dir as string).toBe('up')
    expect(p.dir as string).toBe('right')
    expect(p.prevMoveDir as string).toBe('right')
    expect(p.moving).toBe(true)
  })

  it('default order commits the turn before firing (fire=2)', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', true, false, false)
    sim.tick()

    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot?.dir as string).toBe('right')
    expect(p.dir as string).toBe('right')
  })

  it('a rejected turn skips the shot instead of firing sideways (A1 bug)', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    // Requested facing already 'right' but not committed; cooldown blocks it.
    p.dir = 'right'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0

    input.set('right', true, false, false)
    sim.tick()

    expect(world.bullets.some((b) => b.ownerId === p.id)).toBe(false)
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(false)
  })
})
