import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { BULLET, TANK } from '../src/constants'
import type { Direction } from '../src/constants'
import type { Bullet, Tank } from '../src/types'

/**
 * 天降神兵 (DECISIONS.md §31 Phase 2) — third-faction ally coverage:
 *   1. activateGuard summons one purple ally + "balance" enemies outside the
 *      per-stage 20-cap (1 when no guards active, 2 per new summon at 1+).
 *   2. 3-way friendly fire: player+ally share a team; enemy bullets strike
 *      allies; ally bullets strike enemies but never the player or other allies.
 *   3. Accompanying (isExtra) enemies are excluded from enemyCount / stage-clear.
 *   4. A guard auto-expires at its 2-minute (120*60 frame) lifespan.
 *   5. 同归于尽 AoE ignores allied guards.
 */

const GUARD_LIFESPAN = 120 * 60 // frames (mirrors Simulation.GUARD_LIFESPAN_FRAMES)

/** Fresh, seeded World on stage 0 in 'playing' state. */
function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = seedWorld(seed)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

/** Plant a tank with explicit allegiance + spawn ready (not invulnerable). */
function plantTank(world: World, kind: 'player' | 'basic', x: number, y: number): Tank {
  const t = world.createTank(kind, x, y, 'down')
  t.spawnTimer = 0
  if (t.aiState) {
    t.aiState.level = 'rookie'
    t.aiState.isCommander = false
  }
  return t
}

/** Summon a guard and assert its spawn position is free of terrain and tanks. */
function activateAndCheckSpawn(world: World, sim: Simulation, p: Tank): void {
  const before = world.allies.length
  sim.systems.enemies.activateGuard(p)
  expect(world.allies.length).toBe(before + 1)
  const g = world.allies[world.allies.length - 1]
  expect(world.rectHitsTerrain(g.x, g.y, TANK, TANK)).toBe(false)
  for (const t of world.allTanks) {
    if (t === g || !t.alive) continue
    expect(g.x < t.x + t.w && g.x + g.w > t.x && g.y < t.y + t.h && g.y + g.h > t.y).toBe(false)
  }
}

// KEPT LOCAL (遗留 #5 audit): the 9000+ id scheme, speed 6 and 'basic' hull
// are load-bearing for this file's ally-fire assertions — see the 口径差异表
// in tests/helpers.ts before touching.
function makeBullet(over: Partial<Bullet>): Bullet {
  return {
    id: 9000 + Math.floor(over.id ?? 0),
    x: 0,
    y: 0,
    w: BULLET,
    h: BULLET,
    dir: 'up' as Direction,
    alive: true,
    ownerId: -1,
    ownerKind: 'basic',
    isPlayer: false,
    speed: 6,
    power: 1,
    damage: 1,
    allegiance: 'enemy',
    ...over,
  } as Bullet
}

describe('天降神兵 — activateGuard summon (DECISIONS.md §31 Phase 2)', () => {
  it('summons exactly one ally + one balance enemy on the first summon', () => {
    const { world, sim } = buildSeededWorld(21)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.guardStock = 1

    const alliesBefore = world.allies.length
    const tanksBefore = world.tanks.length

    const activate = sim.systems.enemies.activateGuard.bind(sim.systems.enemies)
    activate(p)

    // One ally appears.
    expect(world.allies.length).toBe(alliesBefore + 1)
    const g = world.allies[world.allies.length - 1]
    expect(g.allegiance).toBe('ally')
    expect(g.isPlayer).toBe(false)
    expect(g.spawnTimer).toBe(1000) // still spawning (invulnerable) on arrival
    expect(g.guardExpireFrame).toBe(world.frame + GUARD_LIFESPAN)
    // Consumed the stock.
    expect(world.guardStock).toBe(0)
    // Exactly one accompanying "balance" enemy (outside the cap).
    expect(world.tanks.length).toBe(tanksBefore + 1)
    expect(world.tanks.some((t) => t.isExtra === true)).toBe(true)
  })

  it('adds TWO balance enemies on a second summon when a guard is already active', () => {
    const { world, sim } = buildSeededWorld(22)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.guardStock = 2

    const activate = sim.systems.enemies.activateGuard.bind(sim.systems.enemies)

    // First summon.
    activate(p)
    // Make the first guard "active" (spawned, alive) so the rule sees 1+ guards.
    world.allies.forEach((a) => (a.spawnTimer = 0))
    const tanksAfterFirst = world.tanks.length

    // Second summon → 2 accompanying enemies.
    world.guardStock = 1
    activate(p)
    expect(world.tanks.length).toBe(tanksAfterFirst + 2)
    // We now have 2 allied guards.
    expect(world.allies.length).toBe(2)
  })

  it('never spawns the guard stuck inside blocking terrain when the base sides are walled', () => {
    const { world, sim } = buildSeededWorld(24)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.guardStock = 1

    // Wall off both candidate side columns (base is 2×2 at cols 12-13, rows
    // 24-25; spawn candidates live at cols 11 and 14, rows 22-25) so the old
    // "fallback onto the wall" path used to fire.
    const tm = world.tileMap
    for (const col of [11, 14]) {
      for (let r = 20; r <= 25; r++) tm.set(col, r, 'brick')
    }

    activateAndCheckSpawn(world, sim, p)
  })

  it('never spawns the guard overlapping terrain on a normal stage either', () => {
    const { world, sim } = buildSeededWorld(25)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.guardStock = 1
    activateAndCheckSpawn(world, sim, p)
  })

  it('is a no-op when guardStock is empty', () => {
    const { world, sim } = buildSeededWorld(23)
    const p = world.player!
    p.spawnTimer = 0
    world.guardStock = 0
    const alliesBefore = world.allies.length
    const tanksBefore = world.tanks.length

    const activate = sim.systems.enemies.activateGuard.bind(sim.systems.enemies)
    activate(p)

    expect(world.allies.length).toBe(alliesBefore)
    expect(world.tanks.length).toBe(tanksBefore)
  })
})

describe('天降神兵 — 3-way friendly fire (DECISIONS.md §31 Phase 2)', () => {
  /** Place `target` in the world (allies[] or tanks[]) and fire `bullet` at it. */
  function fireAt(world: World, sim: Simulation, bullet: Bullet, target: Tank): void {
    // Overlap the bullet with the target so the AABB test passes.
    bullet.x = target.x + target.w / 2 - BULLET / 2
    bullet.y = target.y + target.h / 2 - BULLET / 2
    if (target.allegiance === 'ally') world.allies.push(target)
    else if (target.allegiance === 'enemy') world.tanks.push(target)
    else world.player = target
    // bulletHitsTank takes the allTanks buffer as a parameter (perf: avoids
    // N getter calls per tick). Must pass a fresh snapshot that includes the
    // just-pushed target.
    sim.systems.combat.bulletHitsTank.bind(sim.systems.combat)(bullet, world.allTanks)
  }

  it('ally bullet strikes an enemy (opposing sides)', () => {
    const { world, sim } = buildSeededWorld(31)
    const p = world.player!
    p.shieldTimer = 0
    p.spawnTimer = 0
    const enemy = plantTank(world, 'basic', 100, 100)
    enemy.hp = 1 // pin to 1 so a single damage-1 bullet is lethal (isolates faction rule)
    const bullet = makeBullet({ allegiance: 'ally', ownerId: 7777, ownerKind: 'basic' })
    const hp0 = enemy.hp
    fireAt(world, sim, bullet, enemy)
    expect(enemy.alive).toBe(false) // basic enemy hp=1 → destroyed
    expect(hp0).toBeGreaterThan(0)
  })

  it('ally bullet does NOT strike the player (same team)', () => {
    const { world, sim } = buildSeededWorld(32)
    const p = world.player!
    p.shieldTimer = 0
    p.spawnTimer = 0
    const hp0 = p.hp
    const bullet = makeBullet({ allegiance: 'ally', ownerId: 7777, ownerKind: 'basic' })
    fireAt(world, sim, bullet, p)
    expect(p.alive).toBe(true)
    expect(p.hp).toBe(hp0) // untouched — no friendly fire within the team
  })

  it('enemy bullet strikes an allied guard', () => {
    const { world, sim } = buildSeededWorld(33)
    const p = world.player!
    p.shieldTimer = 0
    p.spawnTimer = 0
    const ally = plantTank(world, 'basic', 150, 150)
    ally.allegiance = 'ally'
    ally.isPlayer = false
    ally.hp = 1 // pin to 1 so a single damage-1 bullet is lethal (isolates faction rule)
    const bullet = makeBullet({ allegiance: 'enemy', ownerId: 8888, ownerKind: 'basic' })
    fireAt(world, sim, bullet, ally)
    expect(ally.alive).toBe(false) // enemy fire kills the ally
  })

  it('player bullet does NOT strike an allied guard (same team)', () => {
    const { world, sim } = buildSeededWorld(34)
    const p = world.player!
    p.shieldTimer = 0
    p.spawnTimer = 0
    const ally = plantTank(world, 'basic', 150, 150)
    ally.allegiance = 'ally'
    ally.isPlayer = false
    const hp0 = ally.hp
    const bullet = makeBullet({ allegiance: 'player', ownerId: p.id, ownerKind: 'player' })
    fireAt(world, sim, bullet, ally)
    expect(ally.alive).toBe(true)
    expect(ally.hp).toBe(hp0) // friendly fire off between player + ally
  })
})

describe('天降神兵 — balance enemies outside the cap (DECISIONS.md §31 Phase 2)', () => {
  it('enemyCount excludes isExtra tanks', () => {
    const { world } = buildSeededWorld(41)
    const before = world.enemyCount
    const extra = plantTank(world, 'basic', 0, 0)
    extra.isExtra = true
    world.tanks.push(extra)
    const normal = plantTank(world, 'basic', TANK, 0)
    world.tanks.push(normal)
    // Only the normal enemy counts; the isExtra one is ignored.
    expect(world.enemyCount).toBe(before + 1)
  })

  it('allTanks includes allied guards', () => {
    const { world } = buildSeededWorld(42)
    const ally = plantTank(world, 'basic', 64, 64)
    ally.allegiance = 'ally'
    ally.isPlayer = false
    world.allies.push(ally)
    expect(world.allTanks.some((t) => t.allegiance === 'ally')).toBe(true)
  })
})

describe('天降神兵 — guard lifespan expiry (DECISIONS.md §31 Phase 2)', () => {
  it('a guard retires (alive=false) once world.frame reaches guardExpireFrame', () => {
    const { world, sim } = buildSeededWorld(51)
    const p = world.player!
    p.spawnTimer = 0
    world.tanks = [] // no target → no fire side-effects
    const g = plantTank(world, 'basic', 100, 100)
    g.allegiance = 'ally'
    g.isPlayer = false
    g.spawnTimer = 0
    g.alive = true
    g.guardExpireFrame = world.frame // already due
    world.allies.push(g)
    const explosionsBefore = world.explosions.length

    const updateGuards = sim.systems.enemies.updateGuards.bind(sim.systems.enemies)
    updateGuards()

    expect(g.alive).toBe(false) // expired
    expect(world.explosions.length).toBeGreaterThan(explosionsBefore) // retire explosion
  })

  it('a guard with a future expiry survives a tick', () => {
    const { world, sim } = buildSeededWorld(52)
    const p = world.player!
    p.spawnTimer = 0
    world.tanks = []
    const g = plantTank(world, 'basic', 100, 100)
    g.allegiance = 'ally'
    g.isPlayer = false
    g.spawnTimer = 0
    g.alive = true
    g.guardExpireFrame = world.frame + 600 // far future
    world.allies.push(g)

    const updateGuards = sim.systems.enemies.updateGuards.bind(sim.systems.enemies)
    updateGuards()

    expect(g.alive).toBe(true) // not yet expired
  })
})

describe('天降神兵 — 同归于尽 ignores allied guards (DECISIONS.md §31 Phase 2)', () => {
  it('sacrifice AoE leaves an adjacent ally alive', () => {
    const { world, sim } = buildSeededWorld(61)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    const ally = plantTank(world, 'basic', p.x, p.y)
    ally.allegiance = 'ally'
    ally.isPlayer = false
    ally.spawnTimer = 0
    ally.alive = true
    world.allies.push(ally)

    world.sacrificeStock = 1
    const trigger = sim.systems.enemies.triggerSacrificeAoE.bind(sim.systems.enemies)
    trigger(p)

    expect(ally.alive).toBe(true) // allies are never harmed by the player's AoE
    expect(world.sacrificeStock).toBe(0) // stock still consumed
  })
})

describe('freeze powerup must NOT freeze allied guards (§184)', () => {
  it('a guard (ally) keeps moving when freezeTimer > 0', () => {
    const { world, sim } = buildSeededWorld(77)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.tanks = []

    // Plant a guard with a God AI brain — it wants to move.
    const g = plantTank(world, 'basic', 100, 100)
    g.allegiance = 'ally'
    g.isPlayer = false
    g.spawnTimer = 0
    g.alive = true
    g.guardExpireFrame = world.frame + 600
    g.moving = true
    g.dir = 'up'
    world.allies.push(g)

    // Activate freeze (should freeze ENEMIES, not allies)
    world.freezeTimer = 5000

    // Record guard position before tick
    const yBefore = g.y

    // Run updateGuards + updateMovement (the movement pipeline)
    const updateGuards = sim.systems.enemies.updateGuards.bind(sim.systems.enemies)
    updateGuards()
    sim.tick() // advances movement

    // The guard should have moved (not frozen by freezeTimer)
    expect(g.alive).toBe(true)
    // Guard was moving up — y should have decreased (or at least the velocity
    // was not zeroed). If frozen, y would be unchanged.
    // Note: the guard might not move if blocked by terrain, but the key is
    // that vx/vy were NOT zeroed by the freeze check.
    // We verify by checking the guard's velocity was not zeroed.
    // After sim.tick(), if freeze affected the guard, g.y === yBefore.
    // If freeze did NOT affect the guard, the guard's brain set g.moving=true
    // and the movement system applied velocity.
    // In this empty-field setup, the guard should have moved up.
    expect(g.y).toBeLessThan(yBefore)
  })

  it('an enemy tank stops moving when freezeTimer > 0', () => {
    // Verify the freeze still works on enemies (regression guard)
    const { world, sim } = buildSeededWorld(78)
    const p = world.player!
    p.x = 200
    p.y = 200
    p.spawnTimer = 0
    world.tanks = []

    const enemy = plantTank(world, 'basic', 100, 100)
    enemy.allegiance = 'enemy'
    enemy.isPlayer = false
    enemy.spawnTimer = 0
    enemy.alive = true
    enemy.moving = true
    enemy.dir = 'up'
    world.tanks.push(enemy)

    world.freezeTimer = 5000
    const yBefore = enemy.y

    sim.tick()

    // Enemy should be frozen — y unchanged
    expect(enemy.y).toBe(yBefore)
  })
})
