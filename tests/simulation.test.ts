import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
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

/** Find a terrain-clear 32×32 tile so a bullet can reach a planted enemy. */
function findClearTile(world: World): { x: number; y: number } {
  const span = GRID * CELL
  for (let y = 0; y < span; y += CELL) {
    for (let x = 0; x < span; x += CELL) {
      if (!world.rectHitsTerrain(x, y, TANK, TANK)) return { x, y }
    }
  }
  throw new Error('no clear tile found')
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
    // speed is included so per-bullet jitter (seeded off world.bulletSeq, NOT
    // genId) is checked for determinism — a non-deterministic jitter would
    // make bullets travel different distances and diverge the world state.
    speed: b.speed,
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
      sim.systems.powerUps.spawnPowerUp()
      sim.systems.powerUps.spawnPowerUp()
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
    const world = seedWorld(seed)
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
        const trapped =
          atSpawn(tk.x, tk.y) &&
          !free(tk, 0, tk.speed) &&
          !free(tk, -tk.speed, 0) &&
          !free(tk, tk.speed, 0)
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

/**
 * Regression guard for "an enemy spawned on top of an obstacle (steel/brick/
 * wall) gets stuck at the spawn point."
 *
 * Several authentic stages place terrain on a spawn cell — col 6 is steel on
 * stage 2, brick on stages 9/19/21, water on stages 20/26/31, steel on stage
 * 25. The spawn loop only checked for *tank* overlap, so when the rotation
 * landed on a terrain-occupied point it created the tank *inside* that
 * obstacle. Every candidate move still overlapped the cell it stood on, so it
 * could never move off and sat at the spawn point forever.
 *
 * Fix: updateSpawning() now also skips spawn points that overlap blocking
 * terrain (rectHitsTerrain), exactly like it skips points occupied by another
 * tank. The invariant guarded here is the strongest possible: a tank can only
 * ever be embedded in blocking terrain if it was created there (movement
 * collision prevents driving into terrain), so "no alive tank overlaps
 * blocking terrain" proves the spawn bug is gone.
 */
describe('Enemy spawn skips terrain-blocked points (bug regression)', () => {
  function fillSpawnWithSteel(world: World, sbCol: number): void {
    // A tank at this spawn point occupies the 2x2 sub-block area (sbCol..sbCol+1, rows 0..1).
    for (let r = 0; r <= 1; r++) {
      for (let c = sbCol; c <= sbCol + 1; c++) {
        world.tileMap.set(c, r, 'steel')
      }
    }
  }

  it('never creates a tank embedded in blocking terrain when a spawn point is occupied by an obstacle', () => {
    const world = seedWorld(12345)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.startGame('classic', 'modern', 0)

    // Block spawn point col 6 (the cell authentic stages place terrain on),
    // then force the rotation to try it first so the bug path is exercised.
    fillSpawnWithSteel(world, 6)
    ;(sim as unknown as { spawnPointIndex: number }).spawnPointIndex = ENEMY_SPAWNS.findIndex(
      (s) => s.col === 6,
    )

    let embeddedSeen = false
    for (let t = 0; t < 600; t++) {
      sim.tick()
      for (const tk of world.tanks) {
        if (tk.alive && world.rectHitsTerrain(tk.x, tk.y, tk.w, tk.h)) embeddedSeen = true
      }
    }

    // The bug would have embedded a tank in the steel at col 6.
    expect(embeddedSeen).toBe(false)
    // And spawning must NOT silently stall — enemies still appear at the
    // clear points (col 0 / col 12 are always terrain-free).
    expect(world.enemiesSpawned).toBeGreaterThan(0)
  })

  it('still skips safely when ALL three spawn points are terrain-blocked (no crash, resumed when cleared)', () => {
    const world = seedWorld(999)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.startGame('classic', 'modern', 0)

    // Block every spawn point.
    for (const s of ENEMY_SPAWNS) fillSpawnWithSteel(world, s.col)

    // With no clear point, no enemy should spawn but the sim must not crash
    // or throw, and must keep trying each frame.
    for (let t = 0; t < 120; t++) sim.tick()
    expect(world.enemiesSpawned).toBe(0)

    // Now clear the terrain — spawning must resume immediately.
    for (const s of ENEMY_SPAWNS) {
      for (let r = 0; r <= 1; r++) {
        for (let c = s.col; c <= s.col + 1; c++) world.tileMap.set(c, r, 'empty')
      }
    }
    for (let t = 0; t < 120; t++) sim.tick()
    expect(world.enemiesSpawned).toBeGreaterThan(0)
  })
})

/**
 * Regression guard for "fire rate is fixed per combat type and must NOT depend
 * on whether previous shells hit something."
 *
 * The player's fire rate used to be gated by a max-concurrent-bullets count
 * (1 at base level, 2 once promoted). Because a bullet only disappears after
 * it strikes terrain/a tank or leaves the field, that cap coupled the next
 * shot to the *lifetime* of the previous one — so the effective rate depended
 * on whether the last shell hit. The fix removes that cap; fire rate is now
 * governed solely by the tank's frozen per-shot cooldown (`nextFireInterval`,
 * the fire-rate standard's base interval × a deterministic ±5% jitter),
 * measured in time, so it is identical whether the previous bullet is still
 * flying or has just hit a wall.
 *
 * The test fires the player upward in two scenarios and compares the gap
 * between the first two shots:
 *   - OPEN : no terrain in the bullet's path → the first bullet stays alive
 *            far longer than one cooldown.
 *   - WALL : a steel wall jammed against the muzzle → the bullet hits and dies
 *            on the very next tick.
 * With the bug, the OPEN gap ≈ bullet-flight-time (much larger than the
 * cooldown) while WALL's gap ≈ cooldown. With the fix, both gaps equal the
 * cooldown (within the ±5% jitter band), proving the rate is hit-independent
 * and fixed per type.
 */
describe('Fire rate is fixed per type and independent of hit outcomes', () => {
  interface FireRun {
    gaps: number[]
    fireCooldownTicks: number
  }

  function runFireScenario(terrain: 'open' | 'wall'): FireRun {
    const world = seedWorld(7)
    const input = new Input()
    const sim = new Simulation(world, input)
    world.startGame('hard', 'modern', 0)
    // Isolate the fire-rate measurement: no enemies to shoot the player, and
    // skip the spawn-grace delay.
    world.spawnQueue.length = 0
    const player = world.player!
    player.spawnTimer = 0

    if (terrain === 'open') {
      // Clear the player's vertical corridor (cols 8-9) so a shot flies the
      // full field height and stays alive well past one cooldown.
      for (let r = 0; r <= 24; r++) {
        world.tileMap.set(8, r, 'empty')
        world.tileMap.set(9, r, 'empty')
      }
    } else {
      // Steel jammed against the muzzle: every bullet hits and dies at once.
      for (let r = 22; r <= 23; r++) {
        world.tileMap.set(8, r, 'steel')
        world.tileMap.set(9, r, 'steel')
      }
    }

    // Hold fire (drive Input's keydown handler directly — no DOM needed).
    ;(
      input as unknown as { onKeyDown: (e: { code: string; preventDefault: () => void }) => void }
    ).onKeyDown({ code: input.keys.fire, preventDefault: () => {} })

    const fireTicks: number[] = []
    const TOTAL = 4 * 60 // 4 seconds
    for (let t = 1; t <= TOTAL; t++) {
      sim.tick()
      for (const ev of world.consumeEvents()) {
        if (ev.type === 'bullet_fired' && ev.bullet.isPlayer) fireTicks.push(t)
      }
    }

    const gaps: number[] = []
    for (let i = 1; i < fireTicks.length; i++) gaps.push(fireTicks[i] - fireTicks[i - 1])
    return { gaps, fireCooldownTicks: (player.fireCooldown * 60) / 1000 }
  }

  it('player fire cadence equals fireCooldown and is identical whether shots hit or fly free', () => {
    const open = runFireScenario('open')
    const wall = runFireScenario('wall')

    expect(open.gaps.length).toBeGreaterThan(0)
    expect(wall.gaps.length).toBeGreaterThan(0)

    // The first gap in the OPEN scenario is the critical one: the first bullet
    // is still alive (has not hit anything) when the second is fired, so the
    // gap must be ~one cooldown — NOT the bullet-flight time.
    const openFirstGap = open.gaps[0]
    const wallFirstGap = wall.gaps[0]

    // Each gap must lie within the ±5% per-fire jitter band around the type's
    // fixed base cooldown (allow a small slack for integer-tick rounding).
    const lo = 0.94 * open.fireCooldownTicks - 1
    const hi = 1.06 * open.fireCooldownTicks + 1
    expect(openFirstGap).toBeGreaterThanOrEqual(lo)
    expect(openFirstGap).toBeLessThanOrEqual(hi)
    expect(wallFirstGap).toBeGreaterThanOrEqual(lo)
    expect(wallFirstGap).toBeLessThanOrEqual(hi)
    // Every gap in both runs stays inside the band (cadence is stable per type).
    for (const g of open.gaps) {
      expect(g).toBeGreaterThanOrEqual(lo)
      expect(g).toBeLessThanOrEqual(hi)
    }
    for (const g of wall.gaps) {
      expect(g).toBeGreaterThanOrEqual(lo)
      expect(g).toBeLessThanOrEqual(hi)
    }

    // And the two scenarios must agree — the previous bullet's fate must not
    // change the cadence. (Identical first-fire frame ⇒ identical jitter seed ⇒
    // identical first gap; allow 2 ticks for any phase difference.)
    expect(Math.abs(openFirstGap - wallFirstGap)).toBeLessThanOrEqual(2)
  })
})

describe('Star progression — classic cap vs unbounded (spec: 星星增益无限累加)', () => {
  it('classic mode caps the level at maximumLevel (no 4th star)', () => {
    const { world, sim } = buildSeededWorld(12345)
    world.startGame('classic', 'modern', 0)
    world.playerLevel = 3
    world.player!.level = 3
    sim.systems.powerUps.applyPowerUp('star')
    expect(world.playerLevel).toBe(3) // capped, did NOT increment
  })

  it('non-classic modes accumulate the level WITHOUT bound', () => {
    const { world, sim } = buildSeededWorld(12345)
    world.startGame('hard', 'modern', 0)
    world.playerLevel = 3
    world.player!.level = 3
    const apply = (t: 'star') => sim.systems.powerUps.applyPowerUp(t)
    apply('star')
    expect(world.playerLevel).toBe(4) // first unbounded star
    apply('star')
    expect(world.playerLevel).toBe(5) // keeps growing
    // dimension follows the decayed curve (balanced×150% = 75 threshold crossed)
    expect(world.player!.profile!.firepower).toBe(84) // level 5 → 50+30+2·2
  })
})

describe('Item drop rules (DECISIONS.md §30)', () => {
  /**
   * Both rules reuse the single `spawnPowerUp` helper (terrain-safe,
   * world.rng-driven) so they stay deterministic and snapshot-safe.
   */
  it('elite (commander-tier) enemy drops a power-up on death', () => {
    const { world, sim } = buildSeededWorld(123)
    world.startGame('hard', 'modern', 0) // modern drop rules (classic uses fixed schedule)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // avoid a stage-clear transition

    const e = world.createTank('basic', 0, 0, 'down')
    e.spawnTimer = 0 // clear invulnerable spawn state so the bullet lands
    e.bonus = false
    if (e.aiState) {
      e.aiState.level = 'commander'
      e.aiState.isCommander = true
    }
    const at = findClearTile(world)
    e.x = at.x
    e.y = at.y
    world.tanks.push(e)

    const before = world.powerUps.length
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

    expect(world.powerUps.length).toBe(before + 1)
    const pu = world.powerUps[world.powerUps.length - 1]
    expect(pu.alive).toBe(true)
    // Drop position is randomized around the enemy tile (within tier range).
    // Verify it's on-grid and within field bounds.
    expect(pu.x % CELL).toBe(0)
    expect(pu.y % CELL).toBe(0)
    expect(pu.x).toBeGreaterThanOrEqual(0)
    expect(pu.y).toBeGreaterThanOrEqual(0)
    expect(pu.x).toBeLessThan(416) // FIELD
    expect(pu.y).toBeLessThan(416)
  })

  it('every 5th kill drops a power-up; kills 1–4 do not', () => {
    const { world, sim } = buildSeededWorld(7)
    world.startGame('hard', 'modern', 0) // modern drop rules (classic uses fixed schedule)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000

    const killOne = () => {
      const e = world.createTank('basic', 0, 0, 'down')
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

    for (let i = 0; i < 4; i++) killOne()
    expect(world.killCount).toBe(4)
    expect(world.powerUps.length).toBe(0) // no cadence drop before the 5th

    killOne() // 5th kill
    expect(world.killCount).toBe(5)
    expect(world.powerUps.length).toBe(1) // exactly one drop on the 5th
  })

  it('every 5000 points accumulated drops a power-up (score milestone)', () => {
    const { world, sim } = buildSeededWorld(2026)
    world.startGame('hard', 'modern', 0) // modern drop rules (classic uses fixed schedule)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // avoid a stage-clear transition

    const e = world.createTank('basic', 0, 0, 'down')
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

    // Park the score just below a 5000 boundary so this single rookie kill
    // (grants ~105 pts) crosses it exactly once.
    world.score = 4950
    const before = world.powerUps.length
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

    // No elite/bonus/10th-kill rule fires (single rookie kill, killCount 0→1),
    // so the only drop is the 5000-point milestone → exactly one power-up.
    expect(world.powerUps.length).toBe(before + 1)
    const pu = world.powerUps[world.powerUps.length - 1]
    // Drop position is randomized around the enemy tile.
    expect(pu.x % CELL).toBe(0)
    expect(pu.y % CELL).toBe(0)
    expect(pu.x).toBeGreaterThanOrEqual(0)
    expect(pu.y).toBeGreaterThanOrEqual(0)
  })

  it("drop triggered by the FINAL enemy of a stage is deferred, then released on the next stage's first kill", () => {
    const { world, sim } = buildSeededWorld(99)
    world.startGame('hard', 'modern', 0) // modern drop rules (classic uses fixed schedule)
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1 // this kill will be the stage's last enemy

    // A bonus enemy so a drop is triggered.
    const e = world.createTank('basic', 0, 0, 'down')
    e.spawnTimer = 0
    e.bonus = true
    if (e.aiState) {
      e.aiState.level = 'rookie'
      e.aiState.isCommander = false
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

    // The drop is NOT spawned immediately (stage is clearing)...
    expect(world.powerUps.length).toBe(0)
    // ...it is buffered on the World.
    expect(world.pendingDrops.length).toBe(1)
    expect(world.killCount).toBe(1)

    // Stage transition happens (loadStage does NOT wipe the buffer).
    const nextIndex = world.stageIndex + 1
    expect(nextIndex).toBeLessThan(world.totalStages) // has a next stage
    world.loadStage(nextIndex)
    expect(world.pendingDrops.length).toBe(1) // buffer survived the transition

    // First enemy kill of the new stage flushes the deferred drop.
    world.tanks.length = 0
    world.spawnQueue.length = 0
    world.enemiesRemaining = 1000 // not the final enemy of this stage
    const e2 = world.createTank('basic', 0, 0, 'down')
    e2.spawnTimer = 0
    e2.bonus = false
    if (e2.aiState) {
      e2.aiState.level = 'rookie'
      e2.aiState.isCommander = false
    }
    const at2 = findClearTile(world)
    e2.x = at2.x
    e2.y = at2.y
    world.tanks.push(e2)
    world.addBullet({
      id: genId(),
      ownerId: world.player!.id,
      ownerKind: 'player',
      isPlayer: true,
      allegiance: 'player',
      x: e2.x + 8,
      y: e2.y + 8,
      w: 4,
      h: 4,
      dir: 'up',
      speed: 0,
      power: 1,
      damage: 999,
      alive: true,
    })
    sim.tick()

    expect(world.powerUps.length).toBe(1) // the deferred drop is now released
    expect(world.pendingDrops.length).toBe(0) // buffer cleared after flush
  })
})
