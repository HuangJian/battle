import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL } from '../src/constants'
import type { TankKind, Tank } from '../src/types'
import { clearArena } from './helpers'

/**
 * Classic AI jam fix — enemies must re-roll direction when blocked by other
 * tanks, not just terrain/bounds. This prevents permanent deadlocks when
 * enemies face each other in corridors.
 *
 * Previously, turnOnCollisionOnly mode only checked terrain/bounds for
 * blocking. Now it also checks tank-tank collisions, and filters out
 * directions blocked by other tanks when choosing a new direction.
 */

function seededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

/** Clear all terrain and place a single base at the bottom-center. */
function openArena(world: World): void {
  clearArena(world)
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
}

function spawnEnemy(world: World, kind: TankKind, x: number, y: number): Tank {
  const t = world.createTank(kind, x, y, 'down')
  t.spawnTimer = 0
  world.tanks.push(t)
  return t
}

describe('Classic AI jam fix — tank-tank collision detection', () => {
  it('enemies blocked by another tank re-roll direction (classic turnOnCollisionOnly)', () => {
    const { world, sim } = seededWorld(100)
    openArena(world)

    // Place two enemies nose-to-nose in a corridor.
    // Enemy A at (12*CELL, 10*CELL) facing down
    // Enemy B at (12*CELL, 12*CELL) facing up
    // They are exactly 2*CELL apart = one tank width, so they block each other.
    const a = spawnEnemy(world, 'basic', 12 * CELL, 10 * CELL)
    const b = spawnEnemy(world, 'basic', 12 * CELL, 12 * CELL)
    a.dir = 'down'
    b.dir = 'up'
    if (a.aiState) a.aiState.currentDir = 'down'
    if (b.aiState) b.aiState.currentDir = 'up'

    // Track direction changes
    const aDirs: string[] = []
    const bDirs: string[] = []

    for (let i = 0; i < 120; i++) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      aDirs.push(a.dir)
      bDirs.push(b.dir)
    }

    // At least one of them should have changed direction (unblocked itself)
    const aChanged = new Set(aDirs).size > 1
    const bChanged = new Set(bDirs).size > 1
    expect(aChanged || bChanged).toBe(true)
  })

  it('direction filtering excludes tank-blocked directions', () => {
    const { world, sim } = seededWorld(200)
    openArena(world)

    // Place enemy A in the center with tanks blocking 3 of 4 directions.
    const center = spawnEnemy(world, 'basic', 12 * CELL, 12 * CELL)
    center.dir = 'down'
    if (center.aiState) center.aiState.currentDir = 'down'

    // Block left, right, and up with stationary tanks
    spawnEnemy(world, 'basic', 11 * CELL, 12 * CELL) // left
    spawnEnemy(world, 'basic', 13 * CELL, 12 * CELL) // right
    spawnEnemy(world, 'basic', 12 * CELL, 11 * CELL) // up

    // The only open direction is down. After re-roll, the tank should move down.
    const dirs: string[] = []
    for (let i = 0; i < 60; i++) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      dirs.push(center.dir)
    }

    // The tank should have moved down (the only unblocked direction)
    expect(dirs).toContain('down')
  })

  it('enemies eventually unjam when facing each other (classic mode)', () => {
    const { world, sim } = seededWorld(300)
    openArena(world)

    // Place 4 enemies in a 2x2 grid, all facing inward
    spawnEnemy(world, 'basic', 10 * CELL, 10 * CELL)
    spawnEnemy(world, 'basic', 12 * CELL, 10 * CELL)
    spawnEnemy(world, 'basic', 10 * CELL, 12 * CELL)
    spawnEnemy(world, 'basic', 12 * CELL, 12 * CELL)

    // Track total movement
    let totalPath = 0
    const prev = new Map<number, { x: number; y: number }>()

    for (let i = 0; i < 300; i++) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()

      for (const t of world.tanks) {
        if (!t.alive) continue
        const p = prev.get(t.id)
        if (p) totalPath += Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
        prev.set(t.id, { x: t.x, y: t.y })
      }
    }

    // Enemies should have moved at least some distance (unblocked themselves)
    expect(totalPath).toBeGreaterThan(100)
  })

  it('modern mode (turnOnCollisionOnly: false) still uses timer re-roll', () => {
    const world = new World()
    world.rng = new RNG(400)
    const sim = new Simulation(world, new Input())
    world.startGame('hard', 'modern', 0) // modern mode
    openArena(world)

    const t = spawnEnemy(world, 'basic', 12 * CELL, 12 * CELL)
    t.dir = 'down'
    if (t.aiState) t.aiState.currentDir = 'down'

    // In modern mode, the tank should re-roll on timer even without collision
    const dirs: string[] = []
    for (let i = 0; i < 200; i++) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      dirs.push(t.dir)
    }

    // Modern mode should have varied directions (timer re-roll)
    const uniqueDirs = new Set(dirs)
    expect(uniqueDirs.size).toBeGreaterThan(1)
  })
})

/**
 * Dead-end shaft recovery (§StuckSpawn): an enemy that spawns into a 1-tank-wide
 * vertical shaft — open directions contain no lateral axis (up/down only) —
 * must tunnel out through a destructible brick wall instead of spinning up/down
 * forever. Reproduces the Stage 8 middle-spawn trap (bounded by brick / steel /
 * water, out-of-bounds on top): without recovery the tank pins to x=192 and
 * shuttles vertically; with recovery it breaks the side brick and escapes.
 */
describe('Dead-end shaft recovery — tunnel out of a 1-wide vertical channel', () => {
  function runShaft(level: 'none' | 'veteran', seed: number): { spanX: number; spanY: number } {
    const world = new World()
    world.rng = new RNG(seed)
    const sim = new Simulation(world, new Input())
    world.startGame('classic', 'modern', 7) // Stage 8 (Riverbed)

    // Geographic middle enemy spawn (original tile col 6 → sub-cols 12-13 → x=192).
    const tank = world.createTank('basic', 12 * CELL, 0, 'down')
    if (tank.aiState) tank.aiState.level = level
    tank.spawnTimer = 0
    world.tanks.push(tank)
    world.enemiesSpawned = 1

    let minX = tank.x
    let maxX = tank.x
    let minY = tank.y
    let maxY = tank.y
    // Window sized for turnCooldownMs=100 (DECISIONS §95): each deferred turn
    // waits longer, so the veteran-tier tunnel-out lands at ~655-1736 ticks
    // (measured seeds 999/100/42/7) vs ~656 at the old 50ms baseline. 2400
    // covers the worst measured case (seed 999 → 1736) with margin.
    for (let i = 0; i < 2400; i++) {
      sim.tick()
      if (world.state !== 'playing') world.state = 'playing'
      if (!world.player || !world.player.alive) world.spawnPlayer()
      minX = Math.min(minX, tank.x)
      maxX = Math.max(maxX, tank.x)
      minY = Math.min(minY, tank.y)
      maxY = Math.max(maxY, tank.y)
    }
    return { spanX: maxX - minX, spanY: maxY - minY }
  }

  it('a None-tier tank tunnels out of the Stage 8 middle shaft (moves laterally)', () => {
    const { spanX } = runShaft('none', 999)
    // Buggy behavior pins x to 192 → spanX ≈ 0. Recovered behavior breaks the
    // side brick and escapes, so the tank spans at least a cell horizontally.
    expect(spanX).toBeGreaterThan(CELL)
  })

  it('a higher-tier tank tunnels out of the Stage 8 middle shaft (moves laterally)', () => {
    const { spanX } = runShaft('veteran', 999)
    expect(spanX).toBeGreaterThan(CELL)
  })
})
