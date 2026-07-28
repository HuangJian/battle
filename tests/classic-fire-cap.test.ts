import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import type { Tank } from '../src/types'

/**
 * Classic fire model — on-screen bullet cap (plan/classic-faithful-feel.md
 * Phase 2). Under `fireModel: 'bulletCap'` a tank may only have
 * `maxBullets[kind]` of its OWN bullets alive at once; the player gains +1
 * at/above `playerDoubleShotLevel` (2★ double-shot, FC-style). Modern
 * difficulties keep the pure time-cooldown model with NO cap.
 */

function buildSeededWorld(seed: number, difficulty: string): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame(difficulty, 'modern', 0)
  return { world, sim }
}

/** Count a tank's own live bullets. */
function liveBullets(world: World, ownerId: number): number {
  let n = 0
  for (const b of world.bullets) {
    if (b.alive && b.ownerId === ownerId) n++
  }
  return n
}

/** Clear the time-cooldown gate so ONLY the bullet cap can block the shot. */
function readyToFire(t: Tank): void {
  t.lastFire = -1e9
}

type FireSim = { tryFire: (t: Tank) => void }
const fire = (sim: Simulation, t: Tank) => (sim as unknown as FireSim).tryFire(t)

describe('classic fire cap — player (plan Phase 2)', () => {
  it('0★ player: max 1 live bullet; slot frees when the bullet dies', () => {
    const { world, sim } = buildSeededWorld(41, 'classic')
    const p = world.player!
    expect(world.rules.fireModel).toBe('bulletCap')
    expect(p.level ?? 0).toBeLessThan(world.rules.playerDoubleShotLevel)

    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1)

    // Cooldown cleared — the ONLY thing blocking now is the cap.
    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1) // capped

    // Bullet resolves → slot freed → next shot goes out.
    for (const b of world.bullets) if (b.ownerId === p.id) b.alive = false
    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1)
    expect(world.bullets.filter((b) => b.ownerId === p.id).length).toBe(2)
  })

  it('2★ player: double-shot — 2 live bullets allowed, 3rd blocked', () => {
    const { world, sim } = buildSeededWorld(42, 'classic')
    const p = world.player!
    p.level = world.rules.playerDoubleShotLevel // 2★

    readyToFire(p)
    fire(sim, p)
    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(2)

    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(2) // capped at 2
  })
})

describe('classic fire cap — enemies', () => {
  it('enemy tank: max 1 live bullet regardless of cooldown', () => {
    const { world, sim } = buildSeededWorld(43, 'classic')
    const e = world.createTank('basic', 128, 128, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)

    readyToFire(e)
    fire(sim, e)
    expect(liveBullets(world, e.id)).toBe(1)

    readyToFire(e)
    fire(sim, e)
    expect(liveBullets(world, e.id)).toBe(1) // capped
  })
})

describe('classic fire cap — no time cooldown after bullet resolves', () => {
  it('player refires instantly once the shell hits a wall (no ~1.2s wait)', () => {
    const { world, sim } = buildSeededWorld(45, 'classic')
    const p = world.player!
    expect(world.rules.fireModel).toBe('bulletCap')

    // Fire the first shell — lastFire is set to NOW. Do NOT clear the cooldown.
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1)

    // The shell strikes a wall this frame → it dies. The time gate must NOT
    // block the next shot (faithful FC: fire again as soon as it resolves).
    for (const b of world.bullets) if (b.ownerId === p.id) b.alive = false

    fire(sim, p) // no readyToFire() — proves there is no residual time gate
    expect(liveBullets(world, p.id)).toBe(1) // exactly one live bullet again
    expect(world.bullets.filter((b) => b.ownerId === p.id).length).toBe(2) // old + new
  })
})

describe('modern fire model — no cap (regression)', () => {
  it("0★ player on 'hard' can have 2+ live bullets (cooldown-only gate)", () => {
    const { world, sim } = buildSeededWorld(44, 'hard')
    const p = world.player!
    expect(world.rules.fireModel).toBe('cooldown')

    readyToFire(p)
    fire(sim, p)
    readyToFire(p)
    fire(sim, p)
    readyToFire(p)
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(3) // no bullet cap in modern modes
  })
})
