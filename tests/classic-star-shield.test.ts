import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { PLAYER_PROGRESSION } from '../src/config/combat'

/**
 * FC "star shield" (plan; DECISIONS §111 2026-08-04 扩展至所有难度):
 *   - A 0★ / 1★ / 2★ player dies on a lethal hit (classic 一击毙命; pool-model
 *     players burn their whole HP buffer) — a life is lost.
 *   - A max-level (3★+) player does NOT die from a would-be-lethal hit: it
 *     spends its top star, drops back to 2★, keeps its life. The shield is
 *     one-time — the next lethal hit on the demoted 2★ tank kills normally.
 *   - Classic (instant model): the 3★ shield was always honoured (§ classic).
 *     Since §111 every difficulty (hard/chaos/relax, pool model) honours it too.
 */
describe('3★ star shield (FC tradition, all difficulties since §111)', () => {
  function setup(difficulty: string, level: number) {
    const world = new World()
    const sim = new Simulation(world, new Input())
    world.startGame(difficulty, 'modern', 0)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // never stage-clear from an empty field
    world.spawnTimer = 1e9 // suppress new enemy spawns during the test

    // Pin to 0★ so the star applies land exactly on `level` (hard/chaos now
    // start at 1★ per §104, and applyPowerUp always increments from current).
    world.playerLevel = 0
    world.player!.level = 0

    const apply = (sim as unknown as { applyPowerUp: (t: 'star') => void }).applyPowerUp.bind(sim)
    for (let i = 0; i < level; i++) apply('star')

    const p = world.player!
    p.spawnTimer = 0 // clear spawn invulnerability so a planted bullet lands
    p.shieldTimer = 0
    return { world, sim }
  }

  function fireEnemyBulletAtPlayer(world: World, damage = 100): void {
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
      damage, // classic referenceDamage=100 is always lethal; pool needs damage ≥ maxHp
      alive: true,
    })
  }

  it('classic: a 3★ player hit drops to 2★ and survives (no life lost)', () => {
    const { world, sim } = setup('classic', PLAYER_PROGRESSION.maximumLevel) // 3★
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

  it('classic: 0★, 1★ and 2★ players die in one hit (faithful 一击毙命)', () => {
    for (const lvl of [0, 1, 2]) {
      const { world, sim } = setup('classic', lvl)
      const livesBefore = world.lives
      expect(world.playerLevel).toBe(lvl)

      fireEnemyBulletAtPlayer(world)
      sim.tick()

      // A dead player is detected in checkConditions → one life is lost.
      expect(world.lives).toBe(livesBefore - 1)
    }
  })

  it('classic: the star shield is one-time — a hit on the demoted 2★ tank kills', () => {
    const { world, sim } = setup('classic', PLAYER_PROGRESSION.maximumLevel) // 3★
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

  it('modern (pool): a LETHAL hit at 3★ also spends the shield (drops to 2★, no life lost)', () => {
    const { world, sim } = setup('hard', 3)
    const livesBefore = world.lives
    expect(world.playerLevel).toBe(3)

    // Pool model: a 3★ player has ~420 HP, so 100-damage bullets chip but don't
    // kill. Force a would-be-lethal hit to prove the §111 shield fires outside
    // classic too.
    fireEnemyBulletAtPlayer(world, 1000)
    sim.tick()

    expect(world.player!.alive).toBe(true) // shield saved the life
    expect(world.playerLevel).toBe(2) // 3★ → 2★
    expect(world.lives).toBe(livesBefore) // no life lost
    expect(world.player!.hp).toBe(world.player!.maxHp) // full HP restored
  })

  it('modern (pool): a 2★ player still dies on a lethal hit', () => {
    const { world, sim } = setup('hard', 2)
    const livesBefore = world.lives

    fireEnemyBulletAtPlayer(world, 1000) // damage ≥ any pool-model 2★ maxHp
    sim.tick()

    expect(world.lives).toBe(livesBefore - 1)
  })

  it('chaos: lethal hit at 3★ also spends the shield (all difficulties, not just classic)', () => {
    const { world, sim } = setup('chaos', 3)
    const livesBefore = world.lives

    fireEnemyBulletAtPlayer(world, 1000)
    sim.tick()

    expect(world.player!.alive).toBe(true)
    expect(world.playerLevel).toBe(2)
    expect(world.lives).toBe(livesBefore)
  })
})
