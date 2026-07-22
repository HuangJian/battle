import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { ENEMY_SPAWNS, CELL, TANK, GRID } from '../src/constants'

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

/**
 * Regression guard for "enemies get stuck / overlap at the spawn point".
 *
 * Two bugs previously produced this:
 *   1. ENEMY_SPAWN_POINTS hardcoded a point at the right edge (x = 24*CELL),
 *      jamming a tank against the wall where it could only move down/left.
 *   2. tankHitsTank() skipped spawning tanks (spawnTimer > 0), so a moving
 *      tank could drive *into* a tank still in its spawn animation. The two
 *      overlapped, and once the spawn timer expired they deadlocked at the
 *      corner/edge with zero free directions — multiple enemies permanently
 *      stuck at the spawn point.
 *
 * Both are prevented by: spawning tanks now block movement, and the spawn
 * area check (which already refused to create a tank on top of any existing
 * tank) is the only way a tank enters a cell. The hard invariant is therefore:
 * no two alive tanks ever occupy the same (x, y) at the same tick.
 */
describe('Enemy spawn does not deadlock or overlap (bug regression)', () => {
  const SPAWN_PTS = ENEMY_SPAWNS.map((s) => ({ x: s.col * CELL, y: s.row * CELL }))
  const atSpawn = (x: number, y: number) =>
    SPAWN_PTS.some((p) => Math.abs(p.x - x) < TANK && Math.abs(p.y - y) < TANK)

  function runStage(stageIndex: number, seed: number, ticks: number) {
    const world = new World()
    world.rng = new RNG(seed)
    const input = new Input()
    const sim = new Simulation(world, input)

    let overlapSeen = false
    let spawnDeadlockTicks = 0
    let maxSpawnDeadlock = 0
    // Track per-tank stuck runs at a spawn point (alive, not spawning, not frozen).
    const stuckAtSpawn = new Map<number, { x: number; y: number; n: number }>()

    for (let t = 0; t < ticks; t++) {
      // Keep the stage alive without a human player so enemies keep spawning.
      if (world.state !== 'playing') world.loadStage(stageIndex)
      if (!world.player || !world.player.alive) world.spawnPlayer()
      sim.tick()

      // Hard invariant: no two alive tanks share a position.
      const alive = world.tanks.filter((tk) => tk.alive)
      for (let i = 0; i < alive.length; i++) {
        for (let j = i + 1; j < alive.length; j++) {
          if (alive[i].x === alive[j].x && alive[i].y === alive[j].y) overlapSeen = true
        }
      }

      // Soft signal: an alive, active enemy parked at a spawn point that cannot
      // move in any of down/left/right (up is always blocked at the top edge).
      const free = (tk: { x: number; y: number }, dx: number, dy: number) => {
        const nx = tk.x + dx
        const ny = tk.y + dy
        if (nx < 0 || ny < 0 || nx + TANK > GRID * CELL || ny + TANK > GRID * CELL) return false
        for (const o of alive) {
          if (o === tk) continue
          if (nx === o.x && ny === o.y) return false
        }
        return true
      }
      for (const tk of alive) {
        if (tk.spawnTimer > 0) {
          stuckAtSpawn.delete(tk.id)
          continue
        }
        const rec = stuckAtSpawn.get(tk.id)
        const trapped = atSpawn(tk.x, tk.y) && !free(tk, 0, tk.speed) && !free(tk, -tk.speed, 0) && !free(tk, tk.speed, 0)
        if (trapped) {
          if (rec && rec.x === tk.x && rec.y === tk.y) rec.n++
          else stuckAtSpawn.set(tk.id, { x: tk.x, y: tk.y, n: 1 })
        } else {
          stuckAtSpawn.delete(tk.id)
        }
      }
      for (const rec of stuckAtSpawn.values()) {
        if (rec.n > maxSpawnDeadlock) maxSpawnDeadlock = rec.n
      }
      void spawnDeadlockTicks
    }
    return { overlapSeen, maxSpawnDeadlock }
  }

  it('no two alive tanks ever overlap during long multi-stage runs', () => {
    let anyOverlap = false
    for (let stage = 0; stage < 35; stage++) {
      for (const seed of [1, 7, 42]) {
        const { overlapSeen } = runStage(stage, seed * 1000 + stage, 800)
        if (overlapSeen) anyOverlap = true
      }
    }
    expect(anyOverlap).toBe(false)
  })

  it('no active enemy is permanently deadlocked at a spawn point', () => {
    let worst = 0
    for (let stage = 0; stage < 35; stage++) {
      for (const seed of [1, 7, 42]) {
        const { maxSpawnDeadlock } = runStage(stage, seed * 1000 + stage, 800)
        if (maxSpawnDeadlock > worst) worst = maxSpawnDeadlock
      }
    }
    // A transient crowd at a spawn point can last a moment, but a true
    // deadlock (all of down/left/right blocked) must never persist. 180 ticks
    // (3 s) is far beyond any legitimate "waiting to move" window.
    expect(worst).toBeLessThan(180)
  })
})
