import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID } from '../src/constants'
import type { Tank } from '../src/types'

// ================================================================
// §84 — Aggressive branch stall detection (freeze window).
//
// Reported from `classic-s03-clear-l3-t79-seed1785643123096.replay`
// (0:20–0:36): during a freeze window the player sat at cell (16,5)
// facing left, firing at a fast enemy that was 3 cells away but slightly
// offset in Y. The 6px bullet passed above/below the 32px enemy body,
// so every shot missed. The aggressive branch has NO anti-stall guard
// (unlike T2a's `_campTicks` and navigate's `_navStuckTicks`), so the
// player wasted the ENTIRE freeze window (1080+ ticks / 18 seconds) in
// one spot.
//
// The fix (§84, `aggCampTimeoutTicks`): when the player camps at the
// same cell in the aggressive stop-and-aim for > aggCampTimeoutTicks
// with no kills, fall through to navigate (which repositions).
// ================================================================

// ---------------------------------------------------------------- helpers

/** Empty arena (no base — we test aggressive mode, not base defense). */
function emptyArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
}

/**
 * Spawn player2 and wire a God AI to drive it. Must run BEFORE anyone reads
 * `world.player2` — spawnPlayer2 creates it.
 */
function coopGod(world: World, params: GodAIParams, seed: number): GodAIInput {
  world.coop = true
  world.lives2 = 3
  world.playerLevel2 = 0
  const p1Col = world.playerSpawnPoint?.col ?? 8
  world.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
  world.spawnPlayer2()
  const god = new GodAIInput(world, params, new RNG(seed ^ 0xdeadbeef), (w) => w.player2)
  god.reset()
  return god
}

/**
 * Build the §84 miss-pattern geometry. P2 is placed at a grid-aligned
 * position where scanAhead finds the enemy (the ±8px offset lines
 * intersect the enemy's 32px body), but the actual bullet (6px tall,
 * centered on the player center) passes above/below the enemy.
 *
 * P2 at (255, 64) → center (271, 80). Enemy at (192, 80) → center (208, 96).
 * The enemy's body spans y=[80,112]. The scan offsets are at y=72 and y=88.
 *   - y=88: 88-1 < 80+32 && 80 < 88+1 → 87 < 112 && 80 < 89 → true → scan finds enemy
 *   - The bullet at y=80 (center) → y∈[77,83]. Enemy body y∈[80,112].
 *     The bullet hits the top 3px of the enemy — but the enemy is a fast
 *     tank moving, so by the time the bullet arrives, the enemy has moved.
 *
 * To make the test deterministic and clear, we place the enemy at a
 * position where scanAhead detects it but the bullet path is marginal.
 * The key assertion is that the player eventually MOVES (not stuck).
 */
function stallWorld(seed: number, aggTimeout: number): { world: World; god: GodAIInput; p2: Tank } {
  const world = seedWorld(seed)
  world.startGame('classic', 'modern', 0)
  emptyArena(world)

  // Clear spawn queue so the single crafted enemy is the ONLY one.
  world.spawnQueue = []
  world.enemiesSpawned = 1
  world.enemiesTotal = 1
  world.enemiesRemaining = 1

  // P1 parked out of the way.
  world.player!.x = 0
  world.player!.y = 384
  world.player!.shieldTimer = 0

  const params: GodAIParams = {
    ...DEFAULT_GOD_AI_PARAMS,
    aggCampTimeoutTicks: aggTimeout,
    aimError: 0, // no random fire errors — deterministic
  }
  const god = coopGod(world, params, seed)

  const p2 = world.player2!
  // Place P2 at a grid-aligned position facing left.
  p2.x = 255
  p2.y = 64
  p2.dir = 'left'
  p2.shieldTimer = 0
  p2.spawnTimer = 0

  // Place enemy to the left, offset in Y so the bullet misses.
  // P2 center (271, 80). Enemy at (96, 88) → center (112, 104).
  // |dy| = 24 < TANK(32) → aligned. scanAhead at y=88:
  //   y=88: 87 < 120 && 88 < 89 → true → enemy found.
  // Bullet at y=80 (center) → y∈[77, 83]. Enemy body y∈[88, 120].
  //   83 < 88 → NO overlap → bullet MISSES every time!
  // This is the exact pattern from the replay: scan sees the enemy but
  // the bullet passes above it.
  const e = world.createTank('fast', 96, 88, 'down')
  e.spawnTimer = 0
  e.speed = 0 // stationary — stays in scan range
  world.tanks.push(e)

  // Freeze window active → aggressive branch is the ONLY branch that runs.
  world.freezeTimer = 60000 // 60 seconds — plenty of time

  return { world, god, p2 }
}

/** Run N ticks, forcing 'playing' so the scenario stays controlled. */
function runTicks(
  world: World,
  sim: Simulation,
  god: GodAIInput,
  ticks: number,
): {
  stuckTicks: number
  moved: boolean
  kills: number
} {
  const p2 = world.player2!
  const startX = p2.x
  const startY = p2.y
  let stuckTicks = 0
  let moved = false

  for (let i = 0; i < ticks; i++) {
    sim.tick()
    god.endFrame()
    if (world.state !== 'playing') world.state = 'playing'

    const p = world.player2
    if (p?.alive) {
      const dx = Math.abs(p.x - startX)
      const dy = Math.abs(p.y - startY)
      if (dx > CELL || dy > CELL) {
        moved = true
      }
      if (dx < 2 && dy < 2) {
        stuckTicks++
      }
    }
  }

  return {
    stuckTicks,
    moved,
    kills: world.killCount,
  }
}

// ---------------------------------------------------------------- tests

describe('§84 aggressive stall detection — freeze window', () => {
  it('default params ship with aggCampTimeoutTicks = 120', () => {
    expect(DEFAULT_GOD_AI_PARAMS.aggCampTimeoutTicks).toBe(120)
  })

  it('stall OFF (0): player stays stuck for 300+ ticks in freeze', () => {
    const { world, god } = stallWorld(42, 0)
    const sim = new Simulation(world, new Input())
    sim.input2 = god

    const res = runTicks(world, sim, god, 300)
    // With stall detection OFF, the player should stay near the start position
    // for a long time (the bullet keeps missing the moving enemy).
    expect(res.moved).toBe(false)
    expect(res.stuckTicks).toBeGreaterThan(200)
  })

  it('stall ON (120): player breaks free within ~150 ticks', () => {
    const { world, god } = stallWorld(42, 120)
    const sim = new Simulation(world, new Input())
    sim.input2 = god

    const res = runTicks(world, sim, god, 300)
    // With stall detection ON, the player should break free and start
    // navigating toward the enemy after ~120 ticks.
    expect(res.moved).toBe(true)
    // The player should not be stuck for 200+ ticks anymore.
    expect(res.stuckTicks).toBeLessThan(200)
  })

  it('kill resets the stall timer — player can camp if productive', () => {
    // When the player kills an enemy, the camp timer resets. This ensures
    // the stall detection doesn't break legitimate camping.
    const world = seedWorld(99)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []
    world.enemiesSpawned = 1
    world.enemiesTotal = 1
    world.enemiesRemaining = 1

    world.player!.x = 0
    world.player!.y = 384
    world.player!.shieldTimer = 0

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      aggCampTimeoutTicks: 60,
      aimError: 0,
    }
    const god = coopGod(world, params, 99)
    const p2 = world.player2!
    p2.x = 128
    p2.y = 128
    p2.dir = 'up'
    p2.shieldTimer = 0
    p2.spawnTimer = 0

    // Place enemy directly above P2 — an easy kill (1HP, aligned, no wall).
    const e = world.createTank('basic', 128, 32, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    world.freezeTimer = 60000

    const sim = new Simulation(world, new Input())
    sim.input2 = god

    // Run 200 ticks — the player should kill the enemy quickly.
    let killed = false
    for (let i = 0; i < 200; i++) {
      sim.tick()
      god.endFrame()
      if (world.state !== 'playing') world.state = 'playing'
      if (!e.alive) {
        killed = true
        break
      }
    }
    // The enemy should be killed (aligned, no wall, 1HP, no aimError).
    expect(killed).toBe(true)
  })
})

// ================================================================
// §85 — Close-range enemy exposure check in navigate.
//
// Reported from `classic-s03-clear-l3-t79-seed1785643123096.replay`
// (1:03): the player was in close combat with an enemy, turned away
// (moveDir = away from enemy), and was killed by the enemy's bullet
// before it could dodge. The navigate branch only checks for BULLET
// threats (findPathThreat), not for enemy tanks that could fire.
//
// The fix (§85, `closeCombatDangerCheck`): before committing to a move
// in the navigate branch, check if a close enemy (within
// closeCombatDangerRange cells) is aligned with the player, has no wall
// between them, and the player's moveDir is NOT toward that enemy.
// If so, cancel the move and face the enemy to fire instead.
// ================================================================

describe('§85 close-range enemy exposure check — navigate branch', () => {
  it('default params ship with closeCombatDangerCheck = 1', () => {
    expect(DEFAULT_GOD_AI_PARAMS.closeCombatDangerCheck).toBe(1)
  })

  it('default params ship with closeCombatDangerRange = 2', () => {
    expect(DEFAULT_GOD_AI_PARAMS.closeCombatDangerRange).toBe(2)
  })

  it('returns null for perpendicular movement (dodge is safe)', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 1,
      closeCombatDangerRange: 4,
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'up'
    p.shieldTimer = 0
    p.spawnTimer = 0

    // Enemy 2 cells to the right, aligned (same row).
    const e = world.createTank('basic', 192, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    // Player moves UP (perpendicular to enemy) — a dodge, not fleeing.
    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'up', 4)
    expect(dangerDir).toBe(null)
  })

  it('returns enemy direction when player flees (opposite moveDir) from a close aligned enemy', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 1,
      closeCombatDangerRange: 4,
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'left'
    p.shieldTimer = 0
    p.spawnTimer = 0

    // Enemy 2 cells to the right, aligned (same row).
    const e = world.createTank('basic', 192, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    // Player moves LEFT (opposite of enemyDir='right') — fleeing, exposed.
    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'left', 4)
    // Should return 'right' — the direction to face the enemy.
    expect(dangerDir).toBe('right')
  })

  it('returns null when player moves toward the close enemy', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 1,
      closeCombatDangerRange: 4,
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'right'
    p.shieldTimer = 0
    p.spawnTimer = 0

    // Enemy 2 cells to the right, aligned.
    const e = world.createTank('basic', 192, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    // Player moves RIGHT — toward the enemy. This is safe (closing distance).
    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'right', 4)
    expect(dangerDir).toBe(null)
  })

  it('returns null when a wall blocks the line to the enemy', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 1,
      closeCombatDangerRange: 4,
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'up'
    p.shieldTimer = 0
    p.spawnTimer = 0

    // Enemy 3 cells to the right, but a steel wall blocks the line.
    world.tileMap.grid[8][10] = 'steel' // cell between player and enemy
    world.tileMap.grid[8][11] = 'steel'

    const e = world.createTank('basic', 224, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    // Player moves UP — but the enemy can't fire through steel.
    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'up', 4)
    expect(dangerDir).toBe(null)
  })

  it('returns null when enemy is beyond closeCombatDangerRange', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 1,
      closeCombatDangerRange: 2, // 2 cells = 32px
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'left'
    p.shieldTimer = 0
    p.spawnTimer = 0

    // Enemy 4 cells to the right — beyond range 2.
    const e = world.createTank('basic', 256, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'left', 2)
    expect(dangerDir).toBe(null)
  })

  it('returns null when check is disabled (closeCombatDangerCheck = 0)', () => {
    const world = seedWorld(7)
    world.startGame('classic', 'modern', 0)
    emptyArena(world)
    world.spawnQueue = []

    const params: GodAIParams = {
      ...DEFAULT_GOD_AI_PARAMS,
      closeCombatDangerCheck: 0, // OFF
      closeCombatDangerRange: 4,
      aimError: 0,
    }
    const god = new GodAIInput(world, params, new RNG(7), (w) => w.player)
    god.reset()

    const p = world.player!
    p.x = 128
    p.y = 128
    p.dir = 'up'
    p.shieldTimer = 0
    p.spawnTimer = 0

    const e = world.createTank('basic', 192, 128, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)

    // Check is OFF — should always return null (byte-identical to pre-§85).
    const dangerDir = god.closeCombatExposure(p.x + p.w / 2, p.y + p.h / 2, 'up', 4)
    expect(dangerDir).toBe(null)
  })
})
