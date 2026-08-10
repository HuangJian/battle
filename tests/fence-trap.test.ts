import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL, TANK, BASE_POS } from '../src/constants'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'

/**
 * §188: Fence power-up must not trap tanks inside steel.
 *
 * Root cause (S9@seed119, 532.7s stuck): when the fence power-up converts
 * base-ring cells to steel, it does not check whether a tank overlaps the
 * target cell. During gameplay, base-ring bricks can be destroyed (by bullets),
 * creating empty cells. A player tank can then move onto those cells. When the
 * fence power-up later converts those empty cells to steel, the tank is
 * permanently trapped — rectHitsTerrain detects the steel overlap on every
 * subsequent move attempt. The nav-stuck escape fires every 240 ticks but
 * cannot help (the tank is physically walled in at the pixel level). The game
 * times out (532s of 600s max).
 *
 * Fix: applyFencePowerUp skips any ring cell that overlaps a tank body.
 */
describe('§188 fence power-up must not trap tanks', () => {
  function setup(): { world: World; sim: Simulation } {
    const world = new World()
    world.rng = new RNG(42)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard']
    world.playerLevel = 1
    world.lives = 3
    world.spectate = true
    // Load stage 9 (Twin Towers) — has base ring bricks at col 11/14.
    world.loadStageData(STAGES[8], 8)
    return { world, sim }
  }

  it('does not convert a ring cell to steel when a tank overlaps it', () => {
    const { world, sim } = setup()
    const p = world.player!
    // Simulate bricks at col 14 being destroyed during gameplay.
    world.tileMap.set(14, 24, 'empty')
    world.tileMap.set(14, 25, 'empty')

    // Place player at the right edge of the base ring (col 14, row 24).
    // Tank top-left at (14*16, 24*16) = (224, 384). Body spans cols 14-15,
    // rows 24-25 — overlaps ring cell (14, 24) and (14, 25).
    p.x = BASE_POS.col * CELL + 2 * CELL // 14 * 16 = 224
    p.y = BASE_POS.row * CELL // 24 * 16 = 384
    p.dir = 'right'

    // Verify the ring cells are empty (destroyed earlier).
    expect(world.tileMap.get(14, 24)).toBe('empty')
    expect(world.tileMap.get(14, 25)).toBe('empty')

    // Apply fence power-up.
    ;(sim as unknown as { applyFencePowerUp: () => void }).applyFencePowerUp()

    // The ring cells overlapping the tank should NOT be converted to steel.
    expect(world.tileMap.get(14, 24)).not.toBe('steel')
    expect(world.tileMap.get(14, 25)).not.toBe('steel')

    // Other ring cells (not overlapping the tank) SHOULD be steel.
    expect(world.tileMap.get(11, 23)).toBe('steel') // top-left, no overlap
    expect(world.tileMap.get(12, 23)).toBe('steel') // top-mid, no overlap
  })

  it('player can still move after fence when adjacent to base ring', () => {
    const { world, sim } = setup()
    const p = world.player!
    // Destroy bricks at col 14 (simulating battle damage).
    world.tileMap.set(14, 24, 'empty')
    world.tileMap.set(14, 25, 'empty')

    // Place player overlapping right ring cells (14, 24) and (14, 25).
    p.x = BASE_POS.col * CELL + 2 * CELL // 224
    p.y = BASE_POS.row * CELL // 384
    p.dir = 'right'

    // Apply fence.
    ;(sim as unknown as { applyFencePowerUp: () => void }).applyFencePowerUp()

    // Player should be able to move right (into col 15-16, which is empty).
    // rectHitsTerrain at the new position should not detect steel overlap.
    const newX = p.x + 1 // try to move 1px right
    const canMove = !world.rectHitsTerrain(newX, p.y, TANK, TANK, false)
    expect(canMove).toBe(true)
  })

  it('player is not trapped: can move away from base ring after fence', () => {
    const { world, sim } = setup()
    const p = world.player!
    // Destroy bricks at col 14 (simulating battle damage).
    world.tileMap.set(14, 24, 'empty')
    world.tileMap.set(14, 25, 'empty')

    // Place player at right edge of ring.
    p.x = BASE_POS.col * CELL + 2 * CELL // 224
    p.y = BASE_POS.row * CELL // 384
    p.dir = 'right'

    // Apply fence.
    ;(sim as unknown as { applyFencePowerUp: () => void }).applyFencePowerUp()

    // Simulate movement: move player right by ~17px (1 cell at 0.7px/tick × 24).
    // If the fence had trapped the player, this would be blocked by steel.
    for (let i = 0; i < 24; i++) {
      const newX = p.x + 0.7
      if (!world.rectHitsTerrain(newX, p.y, TANK, TANK, false)) {
        p.x = newX
      } else {
        // If blocked, the test fails — player is trapped.
        expect(false).toBe(true)
      }
    }
    // Player should have moved significantly to the right.
    expect(p.x).toBeGreaterThan(BASE_POS.col * CELL + 2 * CELL)
  })
})
