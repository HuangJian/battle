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
 * §188→§189: Fence power-up pushes tanks outside the ring before placing steel.
 *
 * Root cause (S9@seed119, 532.7s stuck): when the fence power-up converts
 * base-ring cells to steel, it must not trap tanks inside.
 * Pre-fix (§188): skip ring cells that overlap a tank.
 * §189 fix: force-move the tank to the nearest clear position outside
 * the ring, then place steel. This ensures a complete steel ring
 * while avoiding the trap.
 */
describe('§189 fence power-up pushes tanks outside the ring', () => {
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

  it('pushes a tank outside the ring and places steel on the ring cell', () => {
    const { world, sim } = setup()
    const p = world.player!
    // Simulate bricks at col 14 being destroyed during gameplay.
    world.tileMap.set(14, 24, 'empty')
    world.tileMap.set(14, 25, 'empty')

    // Place player at the right edge of the base ring (col 14, row 24).
    // Tank top-left at (14*16, 24*16) = (224, 384). Body spans cols 14-15,
    // rows 24-25 — overlaps ring cell (14, 24) and (14, 25).
    const originalX = BASE_POS.col * CELL + 2 * CELL // 224
    const originalY = BASE_POS.row * CELL // 384
    p.x = originalX
    p.y = originalY
    p.dir = 'right'

    // Verify the ring cells are empty (destroyed earlier).
    expect(world.tileMap.get(14, 24)).toBe('empty')
    expect(world.tileMap.get(14, 25)).toBe('empty')

    // Apply fence power-up.
    ;(sim as unknown as { applyFencePowerUp: () => void }).applyFencePowerUp()

    // The tank should have been pushed outside the ring (position changed).
    const moved = p.x !== originalX || p.y !== originalY
    expect(moved).toBe(true)

    // The ring cells should now be steel (tank was pushed out).
    expect(world.tileMap.get(14, 24)).toBe('steel')
    expect(world.tileMap.get(14, 25)).toBe('steel')

    // Other ring cells (not overlapping the tank) SHOULD also be steel.
    expect(world.tileMap.get(11, 23)).toBe('steel') // top-left, no overlap
    expect(world.tileMap.get(12, 23)).toBe('steel') // top-mid, no overlap
  })

  it('player can still move after being pushed outside the ring', () => {
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

    // Player should be able to move from the pushed position.
    // Try multiple directions to ensure the tank is not boxed in.
    const canMoveRight = !world.rectHitsTerrain(p.x + 1, p.y, TANK, TANK, false)
    const canMoveUp = !world.rectHitsTerrain(p.x, p.y - 1, TANK, TANK, false)
    const canMoveLeft = !world.rectHitsTerrain(p.x - 1, p.y, TANK, TANK, false)
    const canMoveDown = !world.rectHitsTerrain(p.x, p.y + 1, TANK, TANK, false)
    // At least one direction should be passable.
    expect(canMoveRight || canMoveUp || canMoveLeft || canMoveDown).toBe(true)
  })

  it('player is not trapped: can move freely after fence push', () => {
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

    // Simulate movement in each direction — the tank should be able to move
    // at least one direction for 24 ticks (0.7px/tick × 24 ≈ 17px).
    let moved = false
    const dirs: Array<{ dx: number; dy: number }> = [
      { dx: 1, dy: 0 },
      { dx: 0, dy: -1 },
      { dx: -1, dy: 0 },
      { dx: 0, dy: 1 },
    ]
    for (const dir of dirs) {
      const startX = p.x
      const startY = p.y
      for (let i = 0; i < 24; i++) {
        const newX = p.x + dir.dx * 0.7
        const newY = p.y + dir.dy * 0.7
        if (!world.rectHitsTerrain(newX, newY, TANK, TANK, false)) {
          p.x = newX
          p.y = newY
        }
      }
      if (Math.abs(p.x - startX) > 1 || Math.abs(p.y - startY) > 1) {
        moved = true
        break
      }
      // Reset and try next direction.
      p.x = startX
      p.y = startY
    }
    expect(moved).toBe(true)
  })
})
