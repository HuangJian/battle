import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RULES } from '../src/config/rules'
import { CELL, TANK, GRID } from '../src/constants'
import type { TankKind } from '../src/types'

/**
 * Classic end-to-end integration (plan/classic-faithful-feel.md §7 DoD):
 * one seeded classic game where TTK, per-kind scoring, jitter-free speed,
 * and the 1.8s spawn cadence are all faithful AT THE SAME TIME — through
 * the real Simulation tick, not unit shims.
 */

function buildClassicWorld(seed: number): { world: World; sim: Simulation } {
  const world = seedWorld(seed)
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

/** Plant a spawned (vulnerable) rookie enemy of the given kind on a clear tile. */
function plant(world: World, kind: TankKind) {
  const e = world.createTank(kind, 0, 0, 'down')
  e.spawnTimer = 0
  e.bonus = false
  if (e.aiState) {
    e.aiState.level = 'rookie'
    e.aiState.isCommander = false
  }
  const at = findClearTile(world)
  e.x = at.x
  e.y = at.y
  world.tanks.push(e)
  return e
}

/** Fire one point-blank player-damage shell at the enemy and tick once. */
function shell(world: World, sim: Simulation, e: { x: number; y: number }): void {
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
    damage: world.player!.damage, // the REAL classic player shell (100)
    alive: true,
  })
  sim.tick()
}

describe('classic stage — end-to-end faithful feel (plan §7)', () => {
  it('spawn cadence uses the classic 1.8s interval through the real tick', () => {
    const { world, sim } = buildClassicWorld(2026)
    expect(world.rules).toBe(RULES.classic)
    expect(world.rules.spawnIntervalMs).toBe(1800)

    // Tick until the spawn system releases the first queued enemy.
    const before = world.tanks.length
    let guard = 0
    while (world.tanks.length === before && guard < 300) {
      sim.tick()
      guard++
    }
    expect(world.tanks.length).toBeGreaterThan(before)
    // Timer was re-armed from rules (1800), not the modern 1500.
    expect(world.spawnTimer).toBeGreaterThan(1500)
  })

  it('TTK + per-kind scoring: basic/fast/power die in 1 shell, armor in 4; scores 100/200/300/400', () => {
    const { world, sim } = buildClassicWorld(99)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // never stage-clear mid-test
    expect(world.player!.damage).toBe(100)

    // One-shot kinds — including FAST (issue #2: flat referenceDamage).
    for (const [kind, pts] of [
      ['basic', 100],
      ['fast', 200],
      ['power', 300],
    ] as Array<[TankKind, number]>) {
      const e = plant(world, kind)
      expect(e.maxHp).toBe(100) // hitsToKill 1 × referenceDamage 100
      const s0 = world.score
      shell(world, sim, e)
      expect(e.alive).toBe(false) // exactly one hit
      expect(world.score - s0).toBe(pts)
    }

    // Armor: survives 3 shells, dies on the 4th, scores 400.
    const a = plant(world, 'armor')
    expect(a.maxHp).toBe(400)
    const s0 = world.score
    shell(world, sim, a)
    shell(world, sim, a)
    shell(world, sim, a)
    expect(a.alive).toBe(true)
    expect(a.hp).toBe(100)
    shell(world, sim, a)
    expect(a.alive).toBe(false)
    expect(world.score - s0).toBe(400)
  })

  it('speed jitter is OFF: same-kind tanks share the exact config speed', () => {
    const { world } = buildClassicWorld(5)
    const t1 = world.createTank('basic', 0, 0, 'down')
    const t2 = world.createTank('basic', 64, 0, 'down')
    const t3 = world.createTank('basic', 128, 0, 'down')
    expect(t1.speed).toBe(t2.speed)
    expect(t2.speed).toBe(t3.speed)
  })

  it('modern regression: hard keeps pool TTK (basic ≠ one-shot) and flat scoring', () => {
    const world = seedWorld(99)
    const sim = new Simulation(world, new Input())
    world.startGame('hard', 'modern', 0)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000

    const e = plant(world, 'basic')
    expect(e.maxHp).toBeGreaterThan(world.player!.damage) // pool model: no one-shot
    shell(world, sim, e)
    expect(e.alive).toBe(true) // survives the first player shell
  })
})
