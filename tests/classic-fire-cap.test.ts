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

describe('classic fire cap — minimum cooldown floor', () => {
  it('player cannot refire before bulletCapMinCooldownMs (300ms) after last shot', () => {
    const { world, sim } = buildSeededWorld(45, 'classic')
    const p = world.player!
    expect(world.rules.fireModel).toBe('bulletCap')
    expect(world.rules.bulletCapMinCooldownMs).toBe(300)

    // ReadyToFire clears the initial cooldown so the first shot succeeds.
    readyToFire(p)
    fire(sim, p)
    // Kill the bullet so the cap allows a new one.
    for (const b of world.bullets) if (b.ownerId === p.id) b.alive = false

    // Do NOT clear lastFire — the cooldown floor must block the next shot.
    // 300ms ≈ 18 frames; after just 1 frame the cooldown is not elapsed.
    fire(sim, p)
    // No new bullet should have been created — cooldown blocks it.
    const newBullets = world.bullets.filter((b) => b.ownerId === p.id)
    expect(newBullets.length).toBe(1) // still only the dead one
  })

  it('player can refire after bulletCapMinCooldownMs has elapsed', () => {
    const { world, sim } = buildSeededWorld(45, 'classic')
    const p = world.player!
    expect(world.rules.bulletCapMinCooldownMs).toBe(300)

    // ReadyToFire clears the initial cooldown so the first shot succeeds.
    readyToFire(p)
    fire(sim, p)
    // Kill the bullet.
    for (const b of world.bullets) if (b.ownerId === p.id) b.alive = false

    // Advance world clock by 400ms (> 300ms cooldown).
    const now = world.frame * (1000 / 60)
    p.lastFire = now - 400 // 400ms ago — past the 300ms floor

    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1) // cooldown elapsed, new shot fires
  })

  it('cooldown floor of 0 disables the check (bullet cap only)', () => {
    const { world, sim } = buildSeededWorld(45, 'classic')
    // Override the rule to 0 — no cooldown floor.
    // IMPORTANT: clone the rules object. `world.rules` is a reference to the
    // shared `RULES['classic']` config; mutating it in place leaks the change
    // into every later test (and silently breaks the god-ai-split-parity
    // determinism guard, which reads the same global). See classic-drop-position.test.ts
    // for the same clone pattern.
    world.rules = { ...world.rules, bulletCapMinCooldownMs: 0 }
    const p = world.player!

    // ReadyToFire so the first shot succeeds.
    readyToFire(p)
    fire(sim, p)
    for (const b of world.bullets) if (b.ownerId === p.id) b.alive = false

    // Cooldown floor is 0 so the check is skipped — no readyToFire() needed.
    fire(sim, p)
    expect(liveBullets(world, p.id)).toBe(1) // fires immediately (cap-only)
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
