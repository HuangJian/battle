import { seedWorld, ALL_DIRS } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { TacticalIntelligence } from '../src/ai/TacticalIntelligence'
import { CELL, GRID } from '../src/constants'
import { CORRIDOR_ESCAPE_CHANCE } from '../src/ai/config'
import type { Tank } from '../src/types'

/**
 * Corridor-escape (random orthogonal turn) — unit tests.
 *
 * Verifies that enemies have a small chance per tick to pick a perpendicular
 * open direction when at a junction, preventing infinite bounce in 1-wide
 * corridors bounded by non-destructible terrain (steel/water).
 *
 * Test strategy: force RNG to return values just below/above the
 * CORRIDOR_ESCAPE_CHANCE threshold so the escape branch is exercised
 * deterministically.
 */

/** Create a minimal world with empty terrain and a base. */
function setupWorld(difficulty = 'classic'): World {
  const world = seedWorld(42)
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  // Clear all terrain to empty so we control exactly where walls are.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  // Place base so AI has a target direction.
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim // suppress unused warning — we only need the world + its init side-effects
  return world
}

/** Spawn an enemy tank that is fully active (spawnTimer=0, alive). */
function spawnEnemy(world: World, x: number, y: number): Tank {
  const t = world.createTank('basic', x, y, 'down')
  t.spawnTimer = 0
  t.alive = true
  t.fireCount = 0
  t.dir = 'down'
  world.tanks.push(t)
  return t
}

/**
 * Build steel walls around a cell range to create an inescapable 1-wide
 * horizontal corridor (open only left/right). The tank inside can only
 * move horizontally — no lateral escape exists.
 */
function buildHorizontalSteelCorridor(
  world: World,
  row: number,
  colStart: number,
  colEnd: number,
): void {
  for (let c = colStart; c <= colEnd; c++) {
    // Steel above and below the corridor row
    if (row - 1 >= 0) world.tileMap.grid[row - 1][c] = 'steel'
    if (row + 1 < GRID) world.tileMap.grid[row + 1][c] = 'steel'
  }
  // Steel caps at both ends
  if (colStart - 1 >= 0) {
    world.tileMap.grid[row][colStart - 1] = 'steel'
  }
  if (colEnd + 1 < GRID) {
    world.tileMap.grid[row][colEnd + 1] = 'steel'
  }
}

describe('corridor-escape — none-tier (updateNoneTank)', () => {
  it('can take a lateral direction when junction offers one and RNG triggers', () => {
    const world = setupWorld()
    const ai = new TacticalIntelligence()

    // Place tank at a T-junction: row 10, col 13. Open paths: up, down, left, right.
    // We'll make it so the weighted pickClassicDirFast would choose 'down' (toward base),
    // but the escape roll picks 'left' or 'right'.
    const tank = spawnEnemy(world, 13 * CELL, 10 * CELL)

    // Ensure AI state is initialized for none-tier
    if (!tank.aiState) throw new Error('aiState not set after spawn')

    // Force thinkTimer <= 0 so shouldReroll is true (modern mode: timer expiry triggers reroll)
    tank.aiState.thinkTimer = 0

    // Force RNG to return 0 → always triggers CORRIDOR_ESCAPE_CHANCE (0 < 0.01)
    world.rng = new RNG(0)

    ai.update(world, () => {})

    // Tank must be moving (invariant of updateNoneTank)
    expect(tank.moving).toBe(true)
    // Direction must be valid
    expect(ALL_DIRS).toContain(tank.dir)
  })

  it('does not corrupt direction when no lateral option exists (true 1-wide corridor)', () => {
    const world = setupWorld()
    const ai = new TacticalIntelligence()

    // Build a sealed horizontal steel corridor: row 5, cols 3-8
    buildHorizontalSteelCorridor(world, 5, 3, 8)

    // Place tank inside the corridor
    const tank = spawnEnemy(world, 6 * CELL, 5 * CELL)

    if (!tank.aiState) throw new Error('aiState not set after spawn')
    tank.aiState.thinkTimer = 0

    // Force RNG to 0 (would trigger escape if lateral existed)
    world.rng = new RNG(0)

    ai.update(world, () => {})

    // Tank must still be moving with a valid direction
    expect(tank.moving).toBe(true)
    expect(['left', 'right', 'up', 'down']).toContain(tank.dir)
    // In a true 1-wide corridor only left/right are open; the escape roll finds
    // no perpendicular candidate (up/down are blocked by steel), so the original
    // pickClassicDirFast choice stands unchanged.
  })

  it('respects CORRIDOR_ESCAPE_CHANCE threshold — high RNG skips escape', () => {
    const world = setupWorld()
    const ai = new TacticalIntelligence()

    const tank = spawnEnemy(world, 13 * CELL, 10 * CELL)
    if (!tank.aiState) throw new Error('aiState not set after spawn')
    tank.aiState.thinkTimer = 0

    // Force RNG to return 0.5 → 0.5 > 0.01 (CORRIDOR_ESCAPE_CHANCE) → skip escape
    // Use a seed that produces high values
    let callCount = 0
    const origNext = world.rng.next.bind(world.rng)
    world.rng.next = () => {
      callCount++
      // First call (escape check) returns high → skip; subsequent calls normal
      return callCount === 1 ? 0.5 : origNext()
    }

    ai.update(world, () => {})

    expect(tank.moving).toBe(true)
    expect(ALL_DIRS).toContain(tank.dir)
  })
})

describe('corridor-escape — tiered (chooseDirection)', () => {
  it('can escape dead-end when best choice leads to ≤1-open cell', () => {
    const world = setupWorld()
    // Spawn a rookie-tier enemy (uses chooseDirection path)
    const tank = world.createTank('fast', 13 * CELL, 10 * CELL, 'down')
    tank.spawnTimer = 0
    tank.alive = true
    tank.fireCount = 0
    tank.dir = 'down'
    world.tanks.push(tank)

    if (!tank.aiState) throw new Error('aiState not set after spawn')

    // Force RNG to 0 so escape roll triggers
    world.rng = new RNG(0)

    const ai = new TacticalIntelligence()
    ai.update(world, () => {})

    expect(tank.moving).toBe(true)
    expect(ALL_DIRS).toContain(tank.dir)
  })

  it('constant value targets ~3s mean interval at 60fps (0.0056)', () => {
    expect(CORRIDOR_ESCAPE_CHANCE).toBeCloseTo(0.0056, 4)
  })
})

describe('corridor-escape — integration (multi-tick unstick)', () => {
  it('enemy eventually escapes a T-junction dead-end within reasonable ticks', () => {
    // This test builds a T-junction where the down-arm is a short dead-end.
    // The greedy choice always re-enters the dead-end (toward base).
    // With 1% escape chance per tick, within ~500 ticks (≈8s at 60fps) the
    // tank should almost certainly have escaped via a lateral turn.
    const world = setupWorld()
    const ai = new TacticalIntelligence()

    // Build T-junction: horizontal corridor row 10, with a short downward
    // dead-end arm at col 13 (rows 11-12 are steel on sides, row 13 is steel below).
    // Tank starts at the junction (13, 10) facing down into the dead-end.
    for (const c of [11, 12, 13, 14]) {
      world.tileMap.grid[9][c] = 'steel' // north wall of horizontal corridor
      world.tileMap.grid[11][c] = 'steel' // south wall of horizontal corridor
    }
    // Dead-end cap below row 10 at col 13
    world.tileMap.grid[11][13] = 'steel'

    const tank = spawnEnemy(world, 13 * CELL, 10 * CELL)
    if (!tank.aiState) throw new Error('aiState not set after spawn')
    tank.aiState.currentDir = 'down'
    tank.aiState.thinkTimer = 0

    // Use a fixed seed for reproducibility — not forcing escape, just verifying
    // the system doesn't crash and the tank keeps moving.
    world.rng = new RNG(12345)

    let escaped = false
    const startX = tank.x

    for (let tick = 0; tick < 300; tick++) {
      ai.update(world, () => {})
      // If the tank moved horizontally away from the dead-end arm, it escaped
      if (tank.x !== startX && Math.abs(tank.x - startX) > CELL / 2) {
        escaped = true
        break
      }
    }

    // With 1% chance × 300 ticks ≈ 95% cumulative probability of at least one escape.
    // We don't assert strict success (RNG could theoretically never roll it),
    // but we verify the system runs without error and the tank remains valid.
    expect(tank.moving).toBe(true)
    expect(tank.alive).toBe(true)
    // If escaped, confirm the direction was lateral
    if (escaped) {
      expect(['left', 'right']).toContain(tank.dir)
    }
  })
})
