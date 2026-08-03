import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { PLAYER_PROGRESSION } from '../src/config/combat'

/**
 * Classic FC "star shield" tradition (plan):
 *   - A 0★ / 1★ / 2★ player dies in ONE hit (一击毙命) — a life is lost.
 *   - A 3★ player does NOT die when hit: it spends its top star and drops back
 *     to 2★, keeping its life. The shield is one-time — the next hit on the
 *     demoted 2★ tank kills normally.
 * Only the classic difficulty honours this; modern modes use the pool model.
 */
describe('Classic combat — 3★ star shield (FC tradition)', () => {
  function setup(level: number) {
    const world = new World()
    const sim = new Simulation(world, new Input())
    world.startGame('classic', 'modern', 0)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // never stage-clear from an empty field
    world.spawnTimer = 1e9 // suppress new enemy spawns during the test

    // Promote the player to the requested star level (classic caps at 3★).
    const apply = (sim as unknown as { applyPowerUp: (t: 'star') => void }).applyPowerUp.bind(sim)
    for (let i = 0; i < level; i++) apply('star')

    const p = world.player!
    p.spawnTimer = 0 // clear spawn invulnerability so a planted bullet lands
    p.shieldTimer = 0
    return { world, sim }
  }

  function fireEnemyBulletAtPlayer(world: World): void {
    const p = world.player!
    world.addBullet({
      id: 991001,
      ownerId: -1,
      ownerKind: 'basic',
      isPlayer: false,
      allegiance: 'enemy',
      x: p.x + p.w / 2 - 2,
      y: p.y,
      w: 4,
      h: 4,
      dir: 'down',
      speed: 0,
      power: 1,
      damage: 100, // referenceDamage — always lethal to a classic player
      alive: true,
    })
  }

  it('a 3★ player hit drops to 2★ and survives (no life lost)', () => {
    const { world, sim } = setup(PLAYER_PROGRESSION.maximumLevel) // 3★
    const livesBefore = world.lives
    expect(world.playerLevel).toBe(3)
    expect(world.player!.level).toBe(3)

    fireEnemyBulletAtPlayer(world)
    sim.tick()

    expect(world.player!.alive).toBe(true) // did NOT die
    expect(world.playerLevel).toBe(2) // dropped one star
    expect(world.player!.level).toBe(2)
    expect(world.lives).toBe(livesBefore) // no life lost
    expect(world.player!.hp).toBe(world.player!.maxHp) // shield absorbed the hit
    expect(world.player!.shieldTimer).toBeGreaterThan(0) // brief grace granted
  })

  it('0★, 1★ and 2★ players die in one hit (faithful 一击毙命)', () => {
    for (const lvl of [0, 1, 2]) {
      const { world, sim } = setup(lvl)
      const livesBefore = world.lives
      expect(world.playerLevel).toBe(lvl)

      fireEnemyBulletAtPlayer(world)
      sim.tick()

      // A dead player is detected in checkConditions → one life is lost.
      expect(world.lives).toBe(livesBefore - 1)
    }
  })

  it('the star shield is one-time: a hit on the demoted 2★ tank kills', () => {
    const { world, sim } = setup(PLAYER_PROGRESSION.maximumLevel) // 3★
    const livesBefore = world.lives

    fireEnemyBulletAtPlayer(world)
    sim.tick()
    expect(world.playerLevel).toBe(2) // now 2★, still alive

    // Spend the grace so the next bullet can actually land.
    world.player!.shieldTimer = 0
    fireEnemyBulletAtPlayer(world)
    sim.tick()

    expect(world.lives).toBe(livesBefore - 1) // this hit costs a life
  })

  it('modern (pool) mode does NOT grant the star shield — 3★ keeps its stars', () => {
    const world = new World()
    const sim = new Simulation(world, new Input())
    world.startGame('hard', 'modern', 0) // modern pool model
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000
    world.spawnTimer = 1e9

    // §104 (M6): hard now starts at 1★. Pin to 0 so the 3-star apply lands
    // exactly on 3★ (this test asserts the pool model's no-demotion gate).
    world.playerLevel = 0
    world.player!.level = 0

    const apply = (sim as unknown as { applyPowerUp: (t: 'star') => void }).applyPowerUp.bind(sim)
    for (let i = 0; i < 3; i++) apply('star')
    world.player!.spawnTimer = 0
    world.player!.shieldTimer = 0

    const livesBefore = world.lives
    // In the pool model a 3★ player has ~420 HP, so a 100-damage bullet is
    // non-lethal. The point of this test is the classic-only gate: the star
    // shield must NOT fire in modern, so the player keeps all 3 stars.
    fireEnemyBulletAtPlayer(world)
    sim.tick()

    expect(world.playerLevel).toBe(3) // no demotion outside classic
    expect(world.lives).toBe(livesBefore) // non-lethal, no life lost
  })
})
