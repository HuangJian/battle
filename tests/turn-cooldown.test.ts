import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL, GRID } from '../src/constants'
import type { Direction } from '../src/constants'

/**
 * §86c: Turn cooldown — minimum turn period (50ms ≈ 3 ticks at 60fps).
 *
 * After a tank turns (dir changes), it must wait 50ms before turning again.
 * This blocks the per-tick direction oscillation that causes the God AI's
 * dodge bug at its source.
 */

class TestInput extends Input {
  private d: Direction | null = null
  setMoveDir(d: Direction | null) {
    this.d = d
  }
  override getMoveDirection(): Direction | null {
    return this.d
  }
  override isFiring(): boolean {
    return false
  }
}

function setupWorld(turnCooldownMs: number = 50): {
  world: World
  sim: Simulation
  input: TestInput
} {
  const world = new World()
  world.rng = new RNG(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'], turnCooldownMs }
  const input = new TestInput()
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[0], 0)
  // Force playing state — startGame would reset rules, so set state directly
  world.state = 'playing'

  // Clear terrain for clean testing
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty' as never
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base' as never
  }

  return { world, sim, input }
}

describe('§86c: Turn cooldown (minimum turn period)', () => {
  it('prevents a direction change within the cooldown window', () => {
    const { world, sim, input } = setupWorld(50)

    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999
    p.spawnTimer = 0
    p.x = 8 * CELL
    p.y = 10 * CELL
    p.moving = true

    // Tick 0: turn from up → right. This should be accepted (cooldown is -9999).
    input.setMoveDir('right')
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.prevMoveDir as string).toBe('right')

    // Tick 1: try to turn from right → down. This should be BLOCKED (only ~16ms since last turn, < 50ms).
    input.setMoveDir('down')
    sim.tick()
    expect(p.dir as string).toBe('right') // still right — cooldown blocked the turn
    expect(p.prevMoveDir as string).toBe('right') // prevMoveDir unchanged

    // Tick 2: try again — still blocked (~33ms < 50ms).
    input.setMoveDir('down')
    sim.tick()
    expect(p.dir as string).toBe('right') // still right

    // Tick 3: try again — now ~50ms has passed, turn should be accepted.
    input.setMoveDir('down')
    sim.tick()
    expect(p.dir as string).toBe('down')
    expect(p.prevMoveDir as string).toBe('down')
  })

  it('allows turning to the SAME direction without cooldown (no actual turn)', () => {
    const { world, sim, input } = setupWorld(50)

    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999
    p.spawnTimer = 0
    p.x = 8 * CELL
    p.y = 10 * CELL
    p.moving = true

    // Turn up → right (accepted, first turn)
    input.setMoveDir('right')
    sim.tick()
    expect(p.dir as string).toBe('right')

    // Keep pressing right — no turn, should keep moving
    input.setMoveDir('right')
    sim.tick()
    expect(p.dir as string).toBe('right')

    // Keep pressing right again
    input.setMoveDir('right')
    sim.tick()
    expect(p.dir as string).toBe('right')
  })

  it('does not block turning when turnCooldownMs = 0 (OFF)', () => {
    const { world, sim, input } = setupWorld(0)

    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999
    p.spawnTimer = 0
    p.x = 8 * CELL
    p.y = 10 * CELL
    p.moving = true

    // Turn up → right (tick 0)
    input.setMoveDir('right')
    sim.tick()
    expect(p.dir as string).toBe('right')

    // Turn right → down (tick 1) — should be ALLOWED because cooldown is 0
    input.setMoveDir('down')
    sim.tick()
    expect(p.dir as string).toBe('down')

    // Turn down → left (tick 2) — should be ALLOWED
    input.setMoveDir('left')
    sim.tick()
    expect(p.dir as string).toBe('left')
  })

  it('prevents per-tick oscillation (up→down→up→down)', () => {
    const { world, sim, input } = setupWorld(50)

    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999
    p.spawnTimer = 0
    p.x = 8 * CELL
    p.y = 10 * CELL
    p.moving = true

    // Tick 0: turn up → down (accepted)
    input.setMoveDir('down')
    sim.tick()
    expect(p.dir as string).toBe('down')

    // Tick 1: try to turn down → up (BLOCKED — oscillation attempt)
    input.setMoveDir('up')
    sim.tick()
    expect(p.dir as string).toBe('down') // blocked — stays down

    // Tick 2: try to turn down → up again (BLOCKED — still in cooldown)
    input.setMoveDir('up')
    sim.tick()
    expect(p.dir as string).toBe('down') // still blocked

    // Tick 3: try again (now ~50ms passed, accepted)
    input.setMoveDir('up')
    sim.tick()
    expect(p.dir as string).toBe('up') // finally accepted
  })
})
