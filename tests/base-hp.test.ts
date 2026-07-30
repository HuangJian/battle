import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { genId } from '../src/game/World'
import { CELL, BULLET, GRID } from '../src/constants'
import type { TankKind } from '../src/types'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { BASE_MAX_HP, CLASSIC_BASE_MAX_HP } from '../src/config/base'
import { resolveProfile } from '../src/config/combat'

/**
 * Base (eagle) HP tests — 2026-07-27 (revised: one fixed pool, damage =
 * firePower, NO runtime formula / NO per-kind code).
 *
 * The base has ONE fixed HP on every non-classic difficulty. Each bullet chips
 * exactly `firePower` off it (player and enemy share the path; the player's
 * star level scales its firepower). Hit counts fall out of
 * ceil(BASE_MAX_HP / firePower):
 *   fast  (36) → 4 hits
 *   basic (50) → 3 hits
 *   power (64) → 2 hits
 *   armor (43) → 3 hits   (consequence of dmg=firePower; flagged)
 * A new enemy kind needs NO change here — its firepower lands on this pool.
 *
 * All entropy flows through world.rng, so tests are deterministic.
 */

/** Resolve a kind/level's firepower the same way the call site does. */
function fp(kind: TankKind, level = 0): number {
  return resolveProfile(kind, level).firepower
}

function seededWorld(seed: number, difficulty = 'relax'): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

/** Clear all terrain and place a single base at the bottom-center (open arena). */
function openArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  world.tileMap.rebuildBaseCache()
  // Stop stray spawns from intercepting the test bullet.
  world.spawnQueue = []
  world.tanks = []
  world.bullets = []
  world.state = 'playing'
}

/** Fire one bullet straight down into the base from just above it. */
function shootOnce(world: World, sim: Simulation, kind: TankKind): void {
  world.bullets = []
  world.bullets.push({
    id: genId(),
    x: 13 * CELL - BULLET / 2,
    y: 24 * CELL - 2,
    w: BULLET,
    h: BULLET,
    dir: 'down',
    alive: true,
    ownerId: -1,
    ownerKind: kind,
    isPlayer: kind === 'player',
    allegiance: kind === 'player' ? 'player' : 'enemy',
    speed: 6,
    power: 1,
    damage: 1,
  })
  for (let i = 0; i < 8; i++) {
    sim.tick()
    if (world.tileMap.isBaseDestroyed()) break
  }
}

describe('Base HP — fixed values', () => {
  it('non-classic difficulties share BASE_MAX_HP; classic is the 1-shot', () => {
    // Pre-interpolated from the firepower config (fast 36 / basic 50 / power 64)
    // to honor fast 4, basic 3, power 2 hits under damage=firePower; see config/base.ts.
    expect(BASE_MAX_HP).toBe(120)
    for (const d of ['relax', 'hard', 'chaos']) {
      const { world } = seededWorld(1, d)
      expect(world.baseMaxHp).toBe(BASE_MAX_HP)
    }
    const { world: cw } = seededWorld(1, 'classic')
    expect(cw.baseMaxHp).toBe(CLASSIC_BASE_MAX_HP)
  })
})

describe('Base HP — damage equals firePower (one fixed pool)', () => {
  it('one bullet removes exactly `firepower` HP', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    const before = world.baseHp
    shootOnce(world, sim, 'basic')
    expect(world.baseHp).toBe(before - fp('basic', 0)) // 120 - 50 = 70
  })

  it('fast (36) → exactly 4 hits', () => {
    const { world, sim } = seededWorld(11, 'relax')
    openArena(world)
    for (let i = 0; i < 3; i++) {
      shootOnce(world, sim, 'fast')
      expect(world.tileMap.isBaseDestroyed()).toBe(false)
    }
    expect(world.baseHp).toBe(120 - 3 * fp('fast', 0)) // 12 (>0)
    shootOnce(world, sim, 'fast')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })

  it('basic (50) → exactly 3 hits', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    shootOnce(world, sim, 'basic')
    expect(world.baseHp).toBe(120 - fp('basic', 0)) // 70
    shootOnce(world, sim, 'basic')
    expect(world.baseHp).toBe(120 - 2 * fp('basic', 0)) // 20
    shootOnce(world, sim, 'basic')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })

  it('power (64) → exactly 2 hits', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    shootOnce(world, sim, 'power')
    expect(world.baseHp).toBe(120 - fp('power', 0)) // 56
    shootOnce(world, sim, 'power')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })

  it('armor (43) → 3 hits (CONSEQUENCE of damage=firePower; a single pool cannot also pin it to 4 while power=2)', () => {
    const { world, sim } = seededWorld(13, 'relax')
    openArena(world)
    shootOnce(world, sim, 'armor')
    expect(world.baseHp).toBe(120 - fp('armor', 0)) // 77
    shootOnce(world, sim, 'armor')
    expect(world.baseHp).toBe(120 - 2 * fp('armor', 0)) // 34
    shootOnce(world, sim, 'armor')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })
})

describe('Base HP — player bullets use the same firePower path', () => {
  it('no-star player (firepower 50) → 3 hits', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    world.playerLevel = 0
    shootOnce(world, sim, 'player')
    expect(world.baseHp).toBe(120 - fp('player', 0))
    shootOnce(world, sim, 'player')
    expect(world.baseHp).toBe(120 - 2 * fp('player', 0))
    shootOnce(world, sim, 'player')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })

  it('player with 1 star (firepower 60) → 2 hits', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    world.playerLevel = 1
    shootOnce(world, sim, 'player')
    expect(world.baseHp).toBe(120 - fp('player', 1)) // 60
    shootOnce(world, sim, 'player')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
  })
})

describe('Base HP — classic is one-shot', () => {
  it('ANY single hit destroys the base', () => {
    const { world, sim } = seededWorld(7, 'classic')
    openArena(world)
    expect(world.baseMaxHp).toBe(1)
    // Even the weakest gun (firePower 36) exceeds HP 1 on the first hit.
    shootOnce(world, sim, 'basic')
    expect(world.tileMap.isBaseDestroyed()).toBe(true)
    expect(world.events.some((e) => e.type === 'base_destroyed')).toBe(true)
  })

  it('a protection brick blocks a bullet before it can hit the base in the same tick', () => {
    const { world, sim } = seededWorld(7, 'classic')
    openArena(world)
    world.tileMap.grid[24][11] = 'brick'

    world.bullets.push({
      id: genId(),
      x: 11 * CELL + CELL - BULLET / 2,
      y: 24 * CELL + 4,
      w: BULLET,
      h: BULLET,
      dir: 'right',
      alive: true,
      ownerId: -1,
      ownerKind: 'fast',
      isPlayer: false,
      allegiance: 'enemy',
      speed: 0,
      power: 1,
      damage: 1,
    })

    sim.tick()

    expect(world.tileMap.get(11, 24)).toBe('empty')
    expect(world.tileMap.isBaseDestroyed()).toBe(false)
    expect(world.baseHp).toBe(CLASSIC_BASE_MAX_HP)
    expect(world.events.some((e) => e.type === 'base_destroyed')).toBe(false)
  })

  it('base_destroyed records the owner kind of the bullet that hit it', () => {
    const { world, sim } = seededWorld(7, 'classic')
    openArena(world)

    world.bullets.push({
      id: genId(),
      x: 13 * CELL - BULLET / 2,
      y: 24 * CELL - 2,
      w: BULLET,
      h: BULLET,
      dir: 'down',
      alive: true,
      ownerId: -1,
      ownerKind: 'fast',
      isPlayer: false,
      allegiance: 'enemy',
      speed: 0,
      power: 1,
      damage: 1,
    })

    sim.tick()

    const event = world.events.find((e) => e.type === 'base_destroyed')
    expect(event).toEqual({ type: 'base_destroyed', by: 'fast' })
  })

  it('a player bullet cannot self-destroy the base through a protection brick', () => {
    const { world, sim } = seededWorld(7, 'classic')
    openArena(world)
    // Place a protection brick at the top-left of the base ring (col 11, row 23).
    world.tileMap.grid[23][11] = 'brick'

    world.bullets.push({
      id: genId(),
      x: 11 * CELL + CELL - BULLET / 2,
      y: 23 * CELL - 2,
      w: BULLET,
      h: BULLET,
      dir: 'down',
      alive: true,
      ownerId: 0,
      ownerKind: 'player',
      isPlayer: true,
      allegiance: 'player',
      speed: 0,
      power: 1,
      damage: 1,
    })

    sim.tick()

    // The protection brick is destroyed, but the base survives.
    expect(world.tileMap.get(11, 23)).toBe('empty')
    expect(world.tileMap.isBaseDestroyed()).toBe(false)
    expect(world.baseHp).toBe(CLASSIC_BASE_MAX_HP)
  })

  it('a steel protection cell blocks a normal-power bullet without being destroyed', () => {
    const { world, sim } = seededWorld(7, 'classic')
    openArena(world)
    // Place steel at the top-left of the base ring (col 11, row 23).
    world.tileMap.grid[23][11] = 'steel'

    world.bullets.push({
      id: genId(),
      x: 11 * CELL + CELL / 2 - BULLET / 2,
      y: 22 * CELL + 4,
      w: BULLET,
      h: BULLET,
      dir: 'down',
      alive: true,
      ownerId: -1,
      ownerKind: 'fast',
      isPlayer: false,
      allegiance: 'enemy',
      speed: 0,
      power: 1,
      damage: 1,
    })

    sim.tick()

    // Steel survives (power 1 < 2), base survives, bullet is stopped.
    expect(world.tileMap.get(11, 23)).toBe('steel')
    expect(world.tileMap.isBaseDestroyed()).toBe(false)
    expect(world.baseHp).toBe(CLASSIC_BASE_MAX_HP)
  })
})

describe('Base HP — snapshot round-trips baseHp / baseMaxHp', () => {
  it('preserves damaged base state across clone + restore', () => {
    const { world, sim } = seededWorld(7, 'relax')
    openArena(world)
    shootOnce(world, sim, 'basic') // 120 -> 70
    const snap = cloneWorld(world)
    const restored = new World()
    restoreWorld(restored, snap)
    expect(restored.baseMaxHp).toBe(120)
    expect(restored.baseHp).toBe(70)
    expect(restored.tileMap.isBaseDestroyed()).toBe(false)
  })
})
