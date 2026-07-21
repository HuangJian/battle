import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'

/**
 * Determinism tests for the Simulation layer.
 *
 * AGENTS.md §2.3 promises: "Same inputs + same RNG state + same World ⇒
 * identical replay, always." Any entropy that affects gameplay must flow
 * through `world.rng` so that snapshots/replays reproduce. These tests
 * guard that invariant by running the same seeded simulation twice and
 * asserting identical World state, while perturbing `Math.random()`
 * between runs to prove it no longer leaks into gameplay.
 */

/** Build a fresh World seeded for determinism, on stage 0, in 'playing' state. */
function buildSeededWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  // Override the constructor's Date.now()-seeded RNG with a fixed seed.
  // loadStage() does not reseed, so this seed survives startGame().
  world.rng = new RNG(seed)
  const input = new Input()
  const sim = new Simulation(world, input)
  world.startGame('classic', 'modern', 0)
  return { world, sim }
}

/**
 * Capture the gameplay-relevant state driven by RNG. Entity `id` fields are
 * deliberately excluded: `genId()` advances a module-level counter that
 * persists across World instances, so IDs differ between two runs even when
 * the underlying simulation is identical. IDs don't affect gameplay.
 */
function snapshot(world: World): string {
  const enemies = world.tanks.map((t) => ({
    kind: t.kind,
    x: t.x,
    y: t.y,
    dir: t.dir,
    moving: t.moving,
    hp: t.hp,
    alive: t.alive,
    spawnTimer: t.spawnTimer,
    thinkTimer: t.aiState?.thinkTimer,
    fireTimer: t.aiState?.fireTimer,
    currentDir: t.aiState?.currentDir,
  }))
  const bullets = world.bullets.map((b) => ({
    x: b.x,
    y: b.y,
    dir: b.dir,
    isPlayer: b.isPlayer,
    alive: b.alive,
  }))
  const powerUps = world.powerUps.map((p) => ({ type: p.type, x: p.x, y: p.y, alive: p.alive }))
  const explosions = world.explosions.map((e) => ({ x: e.x, y: e.y, kind: e.kind, timer: e.timer }))
  return JSON.stringify({
    frame: world.frame,
    state: world.state,
    score: world.score,
    lives: world.lives,
    enemiesSpawned: world.enemiesSpawned,
    enemiesRemaining: world.enemiesRemaining,
    spawnTimer: world.spawnTimer,
    freezeTimer: world.freezeTimer,
    player: world.player
      ? {
          x: world.player.x,
          y: world.player.y,
          dir: world.player.dir,
          alive: world.player.alive,
          hp: world.player.hp,
          shieldTimer: world.player.shieldTimer,
          spawnTimer: world.player.spawnTimer,
        }
      : null,
    enemies,
    bullets,
    powerUps,
    explosions,
    rngState: world.rng.getState(),
  })
}

describe('Simulation determinism (AGENTS.md §2.3)', () => {
  it('produces identical World state across two runs with the same seed, even when Math.random() is perturbed between runs', () => {
    const SEED = 0xc0ffee
    const TICKS = 300 // 5 seconds — long enough for enemy AI to think many times

    const runA = buildSeededWorld(SEED)
    for (let i = 0; i < TICKS; i++) runA.sim.tick()
    const snapA = snapshot(runA.world)

    // Perturb Math.random()'s internal state to simulate external use
    // (UI jitter, particles, anything outside the Simulation). If the
    // Simulation honours §2.3, this perturbation must NOT affect the
    // next run. Before the fix, updateEnemyAI/spawnPowerUp read
    // Math.random() directly, so this leak would diverge the two runs.
    for (let i = 0; i < 7; i++) Math.random()

    const runB = buildSeededWorld(SEED)
    for (let i = 0; i < TICKS; i++) runB.sim.tick()
    const snapB = snapshot(runB.world)

    expect(snapB).toEqual(snapA)
  })

  it('spawnPowerUp uses world.rng (deterministic across runs)', () => {
    const SEED = 424242

    const run = (seed: number) => {
      const { world, sim } = buildSeededWorld(seed)
      // spawnPowerUp is private — invoke it directly to isolate the RNG
      // behaviour from the bonus-enemy-kill precondition. Two calls give
      // us two power-ups to compare across runs.
      ;(sim as unknown as { spawnPowerUp: () => void }).spawnPowerUp()
      ;(sim as unknown as { spawnPowerUp: () => void }).spawnPowerUp()
      return world.powerUps.map((p) => `${p.type}@${p.x},${p.y}`)
    }

    const a = run(SEED)
    // Perturb Math.random between runs.
    for (let i = 0; i < 5; i++) Math.random()
    const b = run(SEED)

    expect(b).toEqual(a)
  })

  it('different seeds produce different enemy AI state (sanity check)', () => {
    const TICKS = 120

    const runA = buildSeededWorld(1)
    for (let i = 0; i < TICKS; i++) runA.sim.tick()
    const snapA = snapshot(runA.world)

    const runB = buildSeededWorld(2)
    for (let i = 0; i < TICKS; i++) runB.sim.tick()
    const snapB = snapshot(runB.world)

    // Different seeds must yield different simulations — otherwise the
    // determinism test above would be vacuous.
    expect(snapB).not.toEqual(snapA)
  })
})
