import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { MAX_ENEMIES_ALIVE, COOP_MAX_ENEMIES_ALIVE } from '../src/constants'

/**
 * Co-op (躺赢 / 督战x2) concurrent-enemy floor (plan/tasks.chat.md §27):
 * the field must keep at least 5 regular enemies alive unless the per-stage
 * queue is exhausted. The spawner refills to its cap every interval, so the
 * floor is enforced by raising the co-op concurrent cap to COOP_MAX_ENEMIES_ALIVE.
 *
 * With no player input, enemies never die, so the count ramps to the cap and
 * holds — this pinpoints the cap exactly (single-player = 4, co-op = 5).
 */
function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

function runNoInput(sim: Simulation, ticks: number): number[] {
  const counts: number[] = []
  for (let t = 0; t < ticks; t++) {
    sim.tick()
    counts.push(sim.world.enemyCount)
  }
  return counts
}

describe('co-op minimum concurrent enemies (tasks.chat.md §27)', () => {
  it('single-player caps at MAX_ENEMIES_ALIVE (4) and never 5', () => {
    const { world, sim } = buildSeededWorld(7)
    expect(world.coop).toBe(false)
    expect(world.spectateDual).toBe(false)
    const counts = runNoInput(sim, 1500)
    expect(Math.max(...counts)).toBe(MAX_ENEMIES_ALIVE)
    expect(Math.max(...counts)).not.toBe(COOP_MAX_ENEMIES_ALIVE)
  })

  it('co-op (躺赢) maintains the 5-enemy floor and never exceeds it', () => {
    const { world, sim } = buildSeededWorld(7)
    world.coop = true
    world.spawnPlayer2()
    const counts = runNoInput(sim, 1500)
    const max = Math.max(...counts)
    expect(max).toBe(COOP_MAX_ENEMIES_ALIVE)
    // Steady state: once the field has filled, it holds at exactly the floor
    // (no input → enemies never die → no dips below the cap once reached).
    const tail = counts.slice(-200)
    expect(tail.every((c) => c === COOP_MAX_ENEMIES_ALIVE)).toBe(true)
    expect(max).not.toBe(MAX_ENEMIES_ALIVE)
  })

  it('co-op (督战x2 / spectateDual) also maintains the 5-enemy floor', () => {
    const { world, sim } = buildSeededWorld(11)
    world.spectateDual = true
    world.spawnPlayer2()
    const counts = runNoInput(sim, 1500)
    expect(Math.max(...counts)).toBe(COOP_MAX_ENEMIES_ALIVE)
  })

  it('a 天降神兵 guard summon stacks isExtra enemies ON TOP of the 5 floor', () => {
    const { world, sim } = buildSeededWorld(7)
    world.coop = true
    world.spawnPlayer2()
    // Warm up to the co-op floor.
    for (let t = 0; t < 1500; t++) sim.tick()
    expect(world.enemyCount).toBe(COOP_MAX_ENEMIES_ALIVE)
    // Force a guard summon (consumes stock → spawns 1 ally guard + 1 isExtra enemy).
    world.guardStock = 1
    if (world.player) world.player.spawnTimer = 0
    const activate = (sim as unknown as { activateGuard: (p: import('../src/types').Tank) => void }).activateGuard
    if (world.player) activate.call(sim, world.player)
    // isExtra excluded from enemyCount → the floor is untouched at 5.
    expect(world.enemyCount).toBe(COOP_MAX_ENEMIES_ALIVE)
    // The summon spawned an isExtra balance enemy on top of the floor (it carries
    // a 1s spawnTimer, so it's alive but not yet counted in enemyCount).
    expect(world.tanks.some((t) => t.isExtra)).toBe(true)
    const totalLiveTanks = world.tanks.filter((t) => t.alive).length
    expect(totalLiveTanks).toBeGreaterThan(COOP_MAX_ENEMIES_ALIVE)
  })
})
