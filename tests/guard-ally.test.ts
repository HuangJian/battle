import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
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
  const world = new World()
  world.rng = new RNG(seed)
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

    const activate = (sim as unknown as { activateGuard: (pl: Tank) => void }).activateGuard.bind(sim)
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

    const activate = (sim as unknown as { activateGuard: (pl: Tank) => void }).activateGuard.bind(sim)

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

  it('is a no-op when guardStock is empty', () => {
    const { world, sim } = buildSeededWorld(23)
    const p = world.player!
    p.spawnTimer = 0
    world.guardStock = 0
    const alliesBefore = world.allies.length
    const tanksBefore = world.tanks.length

    const activate = (sim as unknown as { activateGuard: (pl: Tank) => void }).activateGuard.bind(sim)
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
    ;(sim as unknown as { bulletHitsTank: (b: Bullet) => boolean }).bulletHitsTank.bind(sim)(bullet)
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

    const updateGuards = (sim as unknown as { updateGuards: () => void }).updateGuards.bind(sim)
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

    const updateGuards = (sim as unknown as { updateGuards: () => void }).updateGuards.bind(sim)
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
    const trigger = (sim as unknown as { triggerSacrificeAoE: (pl: Tank) => void }).triggerSacrificeAoE.bind(sim)
    trigger(p)

    expect(ally.alive).toBe(true) // allies are never harmed by the player's AoE
    expect(world.sacrificeStock).toBe(0) // stock still consumed
  })
})
