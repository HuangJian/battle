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
 * A1 — the base mechanic: **a shot always leaves along the tank's committed
 * facing** (plan/nn-capability-upgrade.plan.md §3-A1).
 *
 * This is the *legacy* path (human `Input` / God-AI / enemies): those inputs do
 * NOT declare `usesHoldFire3`, so `SimulationPlayer` sets `dir` and then fires
 * without adjudicating the pending turn first. The engine must therefore
 * adjudicate the turn BEFORE deriving the bullet direction inside `tryFire`.
 *
 * The bug (pre-fix): requesting a new direction inside the turn cooldown +
 * firing the same tick left the bullet flying along the *requested* direction
 * while the tank's committed facing (and its drawn cannon) stayed on the old
 * one — the "side-shot".
 */
class LegacyInput extends Input {
  private move: Direction | null = null
  private firing = false

  set(move: Direction | null, firing = false): void {
    this.move = move
    this.firing = firing
  }
  override getMoveDirection(): Direction | null {
    return this.move
  }
  override isFiring(): boolean {
    return this.firing
  }
}

function setup(turnCooldownMs = 200): { world: World; sim: Simulation; input: LegacyInput } {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'], turnCooldownMs }
  const input = new LegacyInput()
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

describe('A1 base mechanic: shot direction == committed facing (legacy path)', () => {
  it('turn rejected by cooldown ⇒ the shot flies along the OLD (committed) facing', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0 // cooldown active

    input.set('right', true)
    sim.tick()

    // The turn was requested but must be rejected → facing stays 'up'.
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(false)

    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot).toBeDefined()
    // The invariant: the bullet leaves along the committed facing, NOT 'right'.
    expect(shot!.dir as string).toBe('up')
    expect(shot!.dir as string).toBe(p.dir as string)
  })

  it('turn accepted ⇒ the shot flies along the NEW facing', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999 // cooldown expired

    input.set('right', true)
    sim.tick()

    expect(p.dir as string).toBe('right')
    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot!.dir as string).toBe('right')
    expect(shot!.dir as string).toBe(p.dir as string)
  })
})
