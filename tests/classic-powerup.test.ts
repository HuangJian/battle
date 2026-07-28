import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL, TANK, GRID } from '../src/constants'
import { SUPER_POWERUP_TYPES } from '../src/config/powerups'

/**
 * Classic drop schedule — power-up carrier enemies (plan/classic-faithful-feel.md
 * Phase 4). FC-faithful: exactly 3 carrier enemies per stage (the 4th / 11th /
 * 18th SPAWNED, marked with the red bonus box) drop when destroyed. The drop is
 * keyed on the carrier flag (`tank.bonus`), NOT a kill counter, so a red-box
 * enemy always drops when killed regardless of order. No elite drops, no
 * every-10-kills cadence, no score milestone, no super power-ups
 * (superDropChance 0), no boat.
 */

function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

function findClearTile(world: World): { x: number; y: number } {
  const span = GRID * CELL
  for (let y = 0; y < span; y += CELL) {
    for (let x = 0; x < span; x += CELL) {
      if (!world.rectHitsTerrain(x, y, TANK, TANK)) return { x, y }
    }
  }
  throw new Error('no clear tile found')
}

/** Plant a rookie enemy and kill it with a point-blank player bullet. */
function killOne(
  world: World,
  sim: Simulation,
  opts: { elite?: boolean; bonus?: boolean } = {},
): void {
  const e = world.createTank('basic', 0, 0, 'down')
  e.spawnTimer = 0
  e.bonus = opts.bonus ?? false
  if (e.aiState) {
    e.aiState.level = opts.elite ? 'commander' : 'rookie'
    e.aiState.isCommander = opts.elite === true
  }
  const at = findClearTile(world)
  e.x = at.x
  e.y = at.y
  world.tanks.push(e)
  world.addBullet({
    id: genId(),
    ownerId: world.player!.id,
    ownerKind: 'player',
    isPlayer: true,
    allegiance: 'player',
    x: e.x + 8,
    y: e.y + 8,
    w: 4,
    h: 4,
    dir: 'up',
    speed: 0,
    power: 1,
    damage: 999,
    alive: true,
  })
  sim.tick()
}

describe('classic drop schedule — fixed indices 4/11/18 (plan Phase 4)', () => {
  it('exactly 3 drops in 20 kills, landing on kills #4, #11, #18', () => {
    const { world, sim } = buildSeededWorld(0xc1a55)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // never stage-clear

    const dropIndices: number[] = []
    let last = world.powerUps.length
    for (let i = 1; i <= 20; i++) {
      // Carriers are the 4th / 11th / 18th enemy (1-based) — the red-box ones.
      killOne(world, sim, { bonus: i === 4 || i === 11 || i === 18 })
      expect(world.killCount).toBe(i)
      if (world.powerUps.length > last) {
        dropIndices.push(i)
        last = world.powerUps.length
      }
    }
    expect(dropIndices).toEqual([4, 11, 18])
    expect(world.powerUps.length).toBe(3)

    // Faithful: a carrier killed OUT of kill-order still drops (drop is keyed
    // on the carrier flag, not `killCount`). Re-run killing #4 first.
    const w2 = buildSeededWorld(0xc1a55 + 1)
    w2.world.tanks.length = 0
    w2.world.spawnQueue.length = 0
    w2.world.enemiesRemaining = 1000
    killOne(w2.world, w2.sim, { bonus: true }) // carrier killed as the 1st kill
    expect(w2.world.killCount).toBe(1)
    expect(w2.world.powerUps.length).toBe(1) // still drops — red box = drop
  })

  it('elite kills and score milestones do NOT drop in classic', () => {
    const { world, sim } = buildSeededWorld(0xe11e)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000
    world.score = 4999 // next kill crosses the 5000 milestone (modern rule)

    killOne(world, sim, { elite: true }) // kill #1: elite + milestone crossed
    expect(world.killCount).toBe(1)
    // Neither the elite rule nor the milestone rule exists under 'fixed'.
    expect(world.powerUps.length).toBe(0)
    expect(world.pendingDrops.length).toBe(0)
  })

  it('classic never rolls super power-ups or the boat', () => {
    const { world, sim } = buildSeededWorld(0x5eed)
    expect(world.rules.superDropChance).toBe(0)
    expect(world.rules.allowedPowerups).not.toContain('boat')

    // Force water so the boat WOULD be reachable if the pool allowed it.
    world.tileMap.grid[5][5] = 'water'
    world.tileMap.rebuildBaseCache()
    expect(world.tileMap.hasWater()).toBe(true)

    const roll = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType
    for (let i = 0; i < 1000; i++) {
      const t = roll.call(sim)
      expect(SUPER_POWERUP_TYPES as readonly string[]).not.toContain(t)
      expect(t).not.toBe('boat')
    }
  })

  it('all classic drops come from the FC pool (star/bomb/shield/freeze/tank/fence)', () => {
    const { world, sim } = buildSeededWorld(7)
    const allowed = world.rules.allowedPowerups
    const roll = (sim as unknown as { rollPowerUpType: () => string }).rollPowerUpType
    for (let i = 0; i < 500; i++) {
      expect(allowed as readonly string[]).toContain(roll.call(sim))
    }
  })
})

describe('classic bonus carriers are config-driven (no hardcoded `i % 4`)', () => {
  it('classic spawn-queue marks the fixedDropKillIndices enemies as bonus carriers', () => {
    const { world } = buildSeededWorld(0x1234)
    const carriers = world.spawnQueue.map((e, i) => (e.bonus ? i + 1 : -1)).filter((n) => n > 0)
    expect(carriers).toEqual([4, 11, 18])
  })

  it("modern 'hard' marks every 4th spawned enemy as a bonus carrier", () => {
    const world = new World()
    world.rng = new RNG(0x1234)
    new Simulation(world, new Input())
    world.startGame('hard', 'modern', 0)
    const carriers = world.spawnQueue.map((e, i) => (e.bonus ? i + 1 : -1)).filter((n) => n > 0)
    // every 4th of 20 → 4, 8, 12, 16, 20
    expect(carriers).toEqual([4, 8, 12, 16, 20])
  })
})
