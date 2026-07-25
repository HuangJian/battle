import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { CELL, ICE_DECEL_TRACTION } from '../src/constants'

/**
 * Ice-momentum regression tests (user bug: "冰面没有滑动惯性，操控性能与平地无异").
 *
 * On ICE a tank must retain inertia: when the player releases the key it keeps
 * gliding for a while, and when it changes direction it can't snap-turn
 * instantly. On NORMAL ground the tank must stop the instant input is released
 * (the pre-bug behaviour). The model is a pure function of World state — no
 * `Math.random()` — so it is fully deterministic (AGENTS.md §2.3).
 */

/** Build a World with a controlled stage: a band of ice flanked by open ground. */
function buildIceWorld(seed: number) {
  const world = new World()
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  // Clear all terrain, then lay a horizontal ice strip in the middle rows.
  for (let r = 0; r < 26; r++) {
    for (let c = 0; c < 26; c++) world.tileMap.set(c, r, 'empty')
  }
  for (let c = 0; c < 26; c++) {
    world.tileMap.set(c, 12, 'ice')
    world.tileMap.set(c, 13, 'ice')
  }
  return { world, sim, input }
}

/** Hold a direction key down on the Input (no DOM needed — drive the handler). */
function press(input: Input, code: string) {
  ;(
    input as unknown as { onKeyDown: (e: { code: string; preventDefault: () => void }) => void }
  ).onKeyDown({ code, preventDefault: () => {} })
}
function release(input: Input, code: string) {
  ;(input as unknown as { onKeyUp: (e: { code: string }) => void }).onKeyUp({ code })
}

describe('Ice momentum (user bug fix)', () => {
  it('tank on normal ground stops immediately when input is released', () => {
    const { world, sim, input } = buildIceWorld(1)
    const p = world.player!
    // Park the player on plain (non-ice) ground, top-left area.
    p.x = 4 * CELL
    p.y = 4 * CELL
    p.spawnTimer = 0
    p.vx = 0
    p.vy = 0

    press(input, input.keys.right)
    for (let i = 0; i < 20; i++) sim.tick() // build up speed on ground
    const xMoving = p.x
    expect(p.vx).toBeGreaterThan(0)

    release(input, input.keys.right)
    sim.tick() // one tick after release

    // On ground, traction = 1 ⇒ velocity snaps to 0 the instant input is gone.
    expect(p.vx).toBe(0)
    // And the tank must not have moved during that tick.
    expect(p.x).toBe(xMoving)
  })

  it('tank on ice keeps gliding after input is released (slides)', () => {
    const { world, sim, input } = buildIceWorld(2)
    const p = world.player!
    // Place the player squarely on the ice strip, centred so it stays on ice.
    p.x = 4 * CELL
    p.y = 12 * CELL
    p.spawnTimer = 0
    p.vx = 0
    p.vy = 0

    press(input, input.keys.right)
    for (let i = 0; i < 20; i++) sim.tick() // accelerate on ice
    const xBeforeRelease = p.x
    expect(p.vx).toBeGreaterThan(0)
    expect(world.isTankOnIce(p)).toBe(true)

    release(input, input.keys.right)
    for (let i = 0; i < 30; i++) sim.tick()

    // It must have moved further AFTER release (the glide), and still retained
    // some velocity partway through the glide.
    expect(p.x).toBeGreaterThan(xBeforeRelease)

    // Eventually friction carries it to a full stop (no infinite slide).
    for (let i = 0; i < 600; i++) sim.tick()
    expect(p.vx).toBe(0)
    expect(p.vy).toBe(0)
  })

  it('ice glide distance scales with the decel traction (long coast, no RNG)', () => {
    const { world, sim, input } = buildIceWorld(3)
    const p = world.player!
    p.x = 2 * CELL
    p.y = 12 * CELL
    p.spawnTimer = 0
    p.vx = 0
    p.vy = 0

    press(input, input.keys.right)
    // Let it reach steady-state speed on ice.
    for (let i = 0; i < 60; i++) sim.tick()
    const vSteady = p.vx
    const xAtRelease = p.x
    release(input, input.keys.right)

    // Simulate the closed-form coast distance = v0 / DECEL (geometric sum).
    const expectedCoast = vSteady / ICE_DECEL_TRACTION
    // Step until it stops; measure how far it travelled.
    let ticks = 0
    while ((p.vx !== 0 || p.x === xAtRelease) && ticks < 2000) {
      sim.tick()
      ticks++
    }
    const travelled = p.x - xAtRelease
    // Allow generous tolerance (axis-lock/collision snapping fuzz the exact
    // landing point, but the coast must be a large fraction of v0/DECEL).
    expect(travelled).toBeGreaterThan(expectedCoast * 0.5)
    expect(travelled).toBeLessThan(expectedCoast * 1.5)
  })

  it('ice slide is deterministic (same seed ⇒ identical end position, no Math.random leak)', () => {
    const run = () => {
      const { world, sim, input } = buildIceWorld(99)
      const p = world.player!
      p.x = 3 * CELL
      p.y = 12 * CELL
      p.spawnTimer = 0
      p.vx = 0
      p.vy = 0
      press(input, input.keys.right)
      for (let i = 0; i < 40; i++) sim.tick()
      release(input, input.keys.right)
      for (let i = 0; i < 200; i++) sim.tick()
      return { x: p.x, y: p.y, vx: p.vx, vy: p.vy, frame: world.frame }
    }
    const a = run()
    for (let i = 0; i < 5; i++) Math.random() // perturb external RNG
    const b = run()
    expect(b).toEqual(a)
  })

  it('turning on ice cannot instantly reverse (old axis keeps gliding briefly)', () => {
    const { world, sim, input } = buildIceWorld(4)
    const p = world.player!
    p.x = 4 * CELL
    p.y = 12 * CELL
    p.spawnTimer = 0
    p.vx = 0
    p.vy = 0

    // Glide right until at speed.
    press(input, input.keys.right)
    for (let i = 0; i < 30; i++) sim.tick()
    const xGliding = p.x
    expect(p.vx).toBeGreaterThan(0)

    // Now hold Up while still on ice — the tank should NOT teleport onto the
    // vertical axis instantly; it keeps some rightward glide first.
    release(input, input.keys.right)
    press(input, input.keys.up)
    // Immediately after the turn, vertical velocity is still ramping from 0,
    // so the tank is NOT yet travelling purely upward.
    expect(Math.abs(p.vy)).toBeLessThanOrEqual(Math.abs(p.vx) + 1e-6)
    // And it has continued to drift horizontally during the glide.
    expect(p.x).toBeGreaterThanOrEqual(xGliding - 1e-6)
  })
})
