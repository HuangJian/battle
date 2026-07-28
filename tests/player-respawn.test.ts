import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { resolveProfile, profileToStats } from '../src/config/combat'
import { SPEED_JITTER_MIN, SPEED_JITTER_MAX } from '../src/config/speed'

/**
 * Regression tests for two player-bug reports (2026-07-23):
 *   #1  Star buff must NOT persist across respawns — death reverts the player
 *       to the difficulty's starting level (all earned stars lost).
 *   #2  The key used to confirm "start game" (Space) must not bleed into the
 *       gameplay fire input and make the player auto-fire on the first frame.
 */

// Minimal fake window so we can drive Input handlers without a real DOM.
class FakeWindow {
  listeners: Record<string, ((e: { code: string; preventDefault(): void }) => void)[]> = {}
  addEventListener(type: string, fn: (e: { code: string; preventDefault(): void }) => void): void {
    ;(this.listeners[type] ??= []).push(fn)
  }
  removeEventListener(): void {}
  dispatch(type: string, code: string): void {
    const ev = { code, preventDefault() {} }
    for (const fn of this.listeners[type] ?? []) fn(ev)
  }
}

describe('Bug #1 — star buff is lost on respawn', () => {
  it('classic death resets earned stars to 0 (not a one-level decrement)', () => {
    const world = new World()
    world.startGame('classic', 'modern', 0) // playerStartLevel = 0
    world.playerLevel = 2
    world.player!.level = 2
    world.player!.alive = false
    world.player!.hp = 0

    const sim = new Simulation(world, new Input())
    sim.tick()

    expect(world.playerLevel).toBe(0) // reset to baseline, NOT 1
    expect(world.player!.level).toBe(0)
    // Derived stats reflect the baseline, not the pre-death upgraded tank.
    const base = profileToStats(resolveProfile('player', 0), 'player', 0, world.rules)
    // Speed carries a ±5% per-instance jitter (drawn from world.rng), so we
    // assert it lands inside the jitter band rather than an exact value.
    expect(world.player!.speed).toBeGreaterThanOrEqual(base.speed * SPEED_JITTER_MIN - 1e-9)
    expect(world.player!.speed).toBeLessThanOrEqual(base.speed * SPEED_JITTER_MAX + 1e-9)
    expect(world.player!.bulletPower).toBe(base.bulletPower)
  })

  it('relax keeps its starting star on death but loses earned ones', () => {
    const world = new World()
    world.startGame('relax', 'modern', 0) // playerStartLevel = 1
    world.playerLevel = 3
    world.player!.level = 3
    world.player!.alive = false
    world.player!.hp = 0

    const sim = new Simulation(world, new Input())
    sim.tick()

    expect(world.playerLevel).toBe(1) // back to baseline, not 2 (decrement) nor 0
    expect(world.player!.level).toBe(1)
  })
})

describe('Bug #2 — start key must not auto-fire the player', () => {
  it('Input.reset() drops a held fire key so isFiring() goes false', () => {
    const fw = new FakeWindow()
    const input = new Input()
    input.attach(fw as unknown as Window)

    fw.dispatch('keydown', 'Space')
    expect(input.isFiring()).toBe(true) // key is held

    input.reset()
    expect(input.isFiring()).toBe(false) // cleared — no auto-fire
  })

  it('a held Space from the start press auto-fires after spawn (the bug)', () => {
    const fw = new FakeWindow()
    const input = new Input()
    input.attach(fw as unknown as Window)
    const world = new World()
    world.startGame('classic', 'modern', 0)
    const sim = new Simulation(world, input)
    fw.dispatch('keydown', 'Space') // held from pressing "start"
    // Run past the spawn animation (1s) + the player's per-shot fire interval
    // (~1.24s at level 0). With the key still held, the player opens fire on
    // its own — the reported auto-fire.
    for (let i = 0; i < 100; i++) sim.tick()
    expect(world.bullets.some((b) => b.isPlayer)).toBe(true) // bug: auto-fired
  })

  it('clearing the input before play prevents the auto-fire (the fix)', () => {
    const fw = new FakeWindow()
    const input = new Input()
    input.attach(fw as unknown as Window)
    const world = new World()
    world.startGame('classic', 'modern', 0)
    const sim = new Simulation(world, input)
    fw.dispatch('keydown', 'Space') // held from pressing "start"
    input.reset() // Game does this right after startGame
    for (let i = 0; i < 70; i++) sim.tick()
    expect(world.bullets.some((b) => b.isPlayer)).toBe(false) // no auto-fire
  })
})
