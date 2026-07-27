import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input, type InputLike } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import type { Direction } from '../src/constants'
import {
  SUPER_POWERUP_TYPES,
  SUPER_POWERUP_DROP_CHANCE,
  FRENZY_SHOTS,
  SACRIFICE_BASE_RADIUS_CELLS,
} from '../src/config/powerups'

/**
 * Super power-up system (强力道具, DECISIONS.md §31) — Phase 1 coverage:
 *   1. 10% super-item roll (uniform across all drop sources).
 *   2. Pickup accumulates into an inventory (not applied instantly).
 *   3. 同归于尽 (sacrifice) AoE on losing a life: radius + consumption + kill
 *      accounting + brick destruction.
 *   4. 狂暴宣泄 (frenzy) barrage: activation, exactly FRENZY_SHOTS shells,
 *      player lock (no move/turn/other items).
 *
 * Phase 2 (天降神兵 summon) is intentionally excluded from the pool here.
 */

const NORMAL_POWERUP_TYPES = ['star', 'bomb', 'shield', 'freeze', 'tank', 'helmet', 'fence', 'boat'] as const

/** Fresh, seeded World on stage 0 in 'playing' state. */
function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

describe('Super power-up — 10% super-item roll (DECISIONS.md §31)', () => {
  it('rollPowerUpType returns a SUPER type when rng < 10% chance', () => {
    const { world, sim } = buildSeededWorld(11)
    // Force the super branch every call.
    world.rng.next = () => 0.01
    const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
    expect(SUPER_POWERUP_TYPES as readonly string[]).toContain(t)
  })

  it('rollPowerUpType returns a NORMAL type when rng >= 10% chance', () => {
    const { world, sim } = buildSeededWorld(11)
    world.rng.next = () => 0.5
    const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
    expect(NORMAL_POWERUP_TYPES as readonly string[]).toContain(t)
  })

  it('super roll probability is ~10% over many deterministic samples', () => {
    const { sim } = buildSeededWorld(0x5eed)
    let superCount = 0
    const N = 4000
    for (let i = 0; i < N; i++) {
      const t = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType()
      if ((SUPER_POWERUP_TYPES as readonly string[]).includes(t)) superCount++
    }
    const frac = superCount / N
    // 10% ± tight tolerance — the decision is purely rng.next() < 0.1.
    expect(frac).toBeGreaterThan(SUPER_POWERUP_DROP_CHANCE - 0.03)
    expect(frac).toBeLessThan(SUPER_POWERUP_DROP_CHANCE + 0.03)
  })
})

describe('Super power-up — pickup accumulates into inventory (DECISIONS.md §31)', () => {
  it('applyPowerUp stores guard/frenzy/sacrifice into stocks, not instant effects', () => {
    const { world, sim } = buildSeededWorld(7)
    const apply = (sim as unknown as { applyPowerUp: (t: string) => void }).applyPowerUp.bind(sim)
    const g0 = world.guardStock
    const f0 = world.frenzyStock
    const s0 = world.sacrificeStock

    apply('guard')
    apply('frenzy')
    apply('sacrifice')

    expect(world.guardStock).toBe(g0 + 1)
    expect(world.frenzyStock).toBe(f0 + 1)
    expect(world.sacrificeStock).toBe(s0 + 1)
    // No gameplay side-effect: player stats / lives untouched by mere pickup.
    expect(world.lives).toBe(3)
    expect(world.player!.hp).toBe(world.player!.maxHp)
  })
})

describe('Super power-up — 同归于尽 (sacrifice) AoE (DECISIONS.md §31)', () => {
  /** Plant an enemy tank at an absolute position, spawned (not invulnerable). */
  function plantEnemy(world: World, x: number, y: number) {
    const e = world.createTank('basic', 0, 0, 'down')
    e.x = x
    e.y = y
    e.spawnTimer = 0
    if (e.aiState) {
      e.aiState.level = 'rookie'
      e.aiState.isCommander = false
    }
    world.tanks.push(e)
    return e
  }

  it('destroys enemies + brick walls within radius, consumes stock, scores kills', () => {
    const { world, sim } = buildSeededWorld(3)
    const p = world.player!
    p.x = 200
    p.y = 200 // center ≈ (216, 216)
    const trigger = (sim as unknown as { triggerSacrificeAoE: (pl: unknown) => void }).triggerSacrificeAoE.bind(sim)

    // Enemy at the blast center → should die.
    const near = plantEnemy(world, p.x, p.y)
    // Brick well within 5-cell (80px) radius → should be destroyed.
    const bc = 10
    const br = 13
    world.tileMap.set(bc, br, 'brick')
    expect(world.tileMap.get(bc, br)).toBe('brick')
    // Brick far away → must survive.
    world.tileMap.set(0, 0, 'brick')
    expect(world.tileMap.get(0, 0)).toBe('brick')

    world.sacrificeStock = 1
    const scoreBefore = world.score
    trigger(p)

    expect(near.alive).toBe(false)
    expect(world.tileMap.get(bc, br)).not.toBe('brick') // destroyed
    expect(world.tileMap.get(0, 0)).toBe('brick') // untouched
    expect(world.sacrificeStock).toBe(0) // consumed
    expect(world.score).toBeGreaterThan(scoreBefore) // normal kill accounting
    expect(world.killCount).toBe(1)
  })

  it('radius scales: 5 cells at stock 1, +1 cell per extra stock', () => {
    const { world, sim } = buildSeededWorld(5)
    const p = world.player!
    p.x = 200
    p.y = 200
    const trigger = (sim as unknown as { triggerSacrificeAoE: (pl: unknown) => void }).triggerSacrificeAoE.bind(sim)

    // Mid enemy at ~88px from center: inside 6 cells (96px) but outside 5 (80px).
    const mid = plantEnemy(world, p.x + 88, p.y)

    // Stock 1 → radius 80px → mid (88px) survives.
    world.sacrificeStock = 1
    trigger(p)
    expect(mid.alive).toBe(true)
    expect(world.sacrificeStock).toBe(0)

    // Reset for stock 2 scenario.
    mid.alive = true
    mid.spawnTimer = 0
    world.sacrificeStock = 2
    trigger(p)
    expect(mid.alive).toBe(false) // now within 96px radius
    expect(world.sacrificeStock).toBe(0)
    // Sanity check the documented base radius constant.
    expect(SACRIFICE_BASE_RADIUS_CELLS).toBe(5)
  })
})

describe('Super power-up — 狂暴宣泄 (frenzy) barrage (DECISIONS.md §31)', () => {
  it('activation consumes stock and fires exactly FRENZY_SHOTS player shells; player locked', () => {
    const { world, sim } = buildSeededWorld(9)
    const p = world.player!
    p.x = 100
    p.y = 100
    p.dir = 'right'
    p.spawnTimer = 0
    world.frenzyStock = 1

    const activate = (sim as unknown as { activateFrenzy: (pl: unknown) => void }).activateFrenzy.bind(sim)
    activate(p)

    expect(world.frenzyStock).toBe(0)
    expect(world.frenzyTimer).toBeGreaterThan(0)
    expect(world.frenzyShotsLeft).toBe(FRENZY_SHOTS)
    expect(world.frenzyDir).toBe('right')

    // Count player bullets added during the barrage (wrapper on addBullet).
    let playerShells = 0
    const orig = world.addBullet.bind(world)
    world.addBullet = ((b: { isPlayer: boolean }) => {
      if (b.isPlayer) playerShells++
      return orig(b as never)
    }) as typeof world.addBullet

    const updatePlayer = (sim as unknown as { updatePlayer: () => void }).updatePlayer.bind(sim)
    let guard = 0
    while (world.frenzyShotsLeft > 0 && guard < 500) {
      world.frame++
      updatePlayer()
      guard++
    }

    expect(playerShells).toBe(FRENZY_SHOTS)
    expect(world.frenzyTimer).toBe(0)
    expect(world.frenzyShotsLeft).toBe(0)
    // Player never moved during the barrage (locked to frenzyDir).
    expect(p.x).toBe(100)
    expect(p.y).toBe(100)
    expect(p.dir).toBe('right')
    expect(p.moving).toBe(false)
  })

  it('frenzy ignores movement input — player cannot turn/move during the barrage', () => {
    const { world } = buildSeededWorld(9)
    const p = world.player!
    p.x = 100
    p.y = 100
    p.dir = 'right'
    p.spawnTimer = 0
    world.frenzyStock = 1

    // Mock input that demands a LEFT move + fire every frame.
    const mockInput: InputLike = {
      getMoveDirection: (): Direction => 'left',
      isFiring: () => true,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim2 = new Simulation(world, mockInput)
    const activate = (sim2 as unknown as { activateFrenzy: (pl: unknown) => void }).activateFrenzy.bind(sim2)
    const updatePlayer = (sim2 as unknown as { updatePlayer: () => void }).updatePlayer.bind(sim2)

    activate(p)
    world.frame++
    updatePlayer()

    // Despite the input demanding LEFT + fire, the barrage forces 'right'
    // (frenzyDir) and suppresses normal firing/movement.
    expect(p.dir).toBe('right')
    expect(p.moving).toBe(false)
  })
})
