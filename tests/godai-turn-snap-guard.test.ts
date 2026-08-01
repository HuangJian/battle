import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../src/ai/GodAIInput'
import { aimSurvivesTurnImpl } from '../src/ai/god/FireControl'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * §80 — the turn-snap aim guard.
 *
 * Reported from `classic-s11-clear-l1-t51-seed1785622102123.replay` (0:31–0:47):
 * during a freeze window player 2 (God AI) stood in one spot firing at nothing.
 *
 * ROOT CAUSE: turning is not free. `Simulation.updateMovement` axis-locks the
 * tank and snaps the PERPENDICULAR coordinate to the grid on every direction
 * change (`axis === 'x' ? tank.y = snap(tank.y, CELL) : tank.x = snap(...)`).
 * A tank parked at a non-grid-aligned sub-cell offset therefore teleports up
 * to CELL/2 px sideways the instant it turns — which can drag the target out
 * of `scanAhead`'s ±CELL/2 offset lines. The `aggressive` (freeze) branch had
 * no anti-stall guard of its own (T2a has `_campTicks`, navigate has
 * `_navStuckTicks`; aggressive has neither), so once the turn-snap broke the
 * firing line it stayed off it, wasting the freeze window.
 *
 * The fix (`aimTurnSnapGuard`, default ON): before committing to a stop-and-aim
 * TURN, re-run the line-of-fire scan from the position the tank would actually
 * occupy AFTER the turn-snap. If the enemy is no longer on that line, the aim
 * is a lie — fall through to navigate (which has real stall detection).
 *
 * These tests pin: (1) the guard's geometry (which aims survive the turn),
 * (2) that the guard is inert for grid-aligned tanks (byte-identical behavior
 * in the overwhelmingly common case), and (3) that the aggressive branch
 * consults the guard so a frozen enemy is actually hunted during the window —
 * while the pre-§80 behavior wastes the entire window pinned next to the enemy
 * (0 kills, sub-cell displacement).
 */

// ---------------------------------------------------------------- helpers

/** Empty arena (no base — the guard's geometry is independent of base logic). */
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
function coopGod(world: World, guard: number, seed: number): GodAIInput {
  world.coop = true
  world.lives2 = 3
  world.playerLevel2 = 0
  const p1Col = world.playerSpawnPoint?.col ?? 8
  world.player2SpawnPoint = { col: 24 - p1Col, row: 24 }
  world.spawnPlayer2()
  const params: GodAIParams = { ...DEFAULT_GOD_AI_PARAMS, aimTurnSnapGuard: guard }
  const god = new GodAIInput(world, params, new RNG(seed ^ 0xdeadbeef), (w) => w.player2)
  god.reset()
  return god
}

/**
 * Build the §80 lie-aim geometry. P2 is placed at (64, 101):
 *
 *   y = 101 is OFF-grid (snap(101, CELL) = 96). The enemy sits LEFT at (32,124)
 *   with body y ∈ [124, 156]:
 *     - PRE-snap  pcy = 117 → scan offsets 109/125; 125 ∈ [124,156] → sees enemy
 *     - POST-snap pcy = 112 → scan offsets 104/120; both < 124 → misses
 *   So `scanAhead` sees the enemy now, but the very act of turning to shoot
 *   slides P2 off the firing line — and the 8px bullet at pcy ≈ 117 (y 113-121)
 *   physically cannot reach a body starting at 124.
 *
 *   Crucially P2 sits at the enemy's column (x = 64 = the enemy's right edge),
 *   so the freeze-window deadlock is geometric: the guard-OFF tank commits to
 *   the turn, snaps onto the grid row (96), fires at an unreachable target,
 *   and never repositions — the whole freeze is wasted. The guard-ON tank
 *   rejects the lie-aim up front and navigates around to the enemy's row,
 *   killing it within ~120 ticks.
 */
function lieAimWorld(seed: number): World {
  const world = new World()
  world.rng = new RNG(seed)
  world.startGame('classic', 'modern', 0)
  emptyArena(world)

  // CRITICAL: startGame() populated the stage-0 spawn queue (20 enemies).
  // Clear it so the single crafted enemy is the ONLY one on the field —
  // otherwise the queue keeps spawning enemies that pollute the scenario.
  world.spawnQueue = []

  // P1 parked out of the way (irrelevant to the P2 scenario).
  world.player!.x = 96
  world.player!.y = 384
  world.player!.shieldTimer = 0

  const e = world.createTank('basic', 32, 124, 'down')
  e.spawnTimer = 0
  world.tanks.push(e)
  world.enemiesSpawned = 1
  world.enemiesTotal = 1
  world.enemiesRemaining = 1

  return world
}

/** Run N ticks, forcing 'playing' so the single enemy stays the scenario. */
function runFreeze(
  world: World,
  sim: Simulation,
  god: GodAIInput,
  ticks: number,
): {
  spanX: number
  spanY: number
  enemyDead: boolean
  kills: number
  /** Ticks with pos(t) == pos(t-2) != pos(t-1) — the §80 period-2 thrash. */
  thrashTicks: number
} {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  let enemyDead = false
  let prev1x = NaN
  let prev1y = NaN
  let prev2x = NaN
  let prev2y = NaN
  let thrash = 0
  for (let i = 0; i < ticks; i++) {
    sim.tick()
    god.endFrame()
    if (world.state !== 'playing') world.state = 'playing'
    const p2 = world.player2
    if (p2?.alive) {
      minX = Math.min(minX, p2.x)
      maxX = Math.max(maxX, p2.x)
      minY = Math.min(minY, p2.y)
      maxY = Math.max(maxY, p2.y)
      if (p2.x === prev2x && p2.y === prev2y && (p2.x !== prev1x || p2.y !== prev1y)) {
        thrash++
      }
    }
    prev2x = prev1x
    prev2y = prev1y
    prev1x = p2?.x ?? NaN
    prev1y = p2?.y ?? NaN
    if (!enemyDead && world.tanks.filter((t) => t.alive && t.spawnTimer <= 0).length === 0) {
      enemyDead = true
    }
  }
  return {
    spanX: maxX - minX,
    spanY: maxY - minY,
    enemyDead,
    kills: world.killCount,
    thrashTicks: thrash,
  }
}

// ---------------------------------------------------------------- guard geometry

describe('§80 aimSurvivesTurnImpl — which aims survive the turn-snap', () => {
  function setup(guard: number): { world: World; god: GodAIInput } {
    const world = lieAimWorld(12345)
    world.player!.x = 100
    world.player!.y = 101
    world.player!.dir = 'up'
    const god = coopGod(world, guard, 12345)
    return { world, god }
  }

  it('gate OFF → always true (byte-identical to pre-§80 behavior)', () => {
    const { world, god } = setup(0)
    const p = world.player!
    // Lie-aim geometry: the aim WILL be broken by the snap, but the gate is
    // off, so the guard must not intervene.
    expect(aimSurvivesTurnImpl(god, p, 'left')).toBe(true)
  })

  it('already facing the aim direction → true (no turn, therefore no snap)', () => {
    const { world, god } = setup(1)
    const p = world.player!
    p.dir = 'left'
    expect(aimSurvivesTurnImpl(god, p, 'left')).toBe(true)
  })

  it('perpendicular axis already grid-aligned → true (snap is a no-op)', () => {
    const { world, god } = setup(1)
    const p = world.player!
    p.y = 96 // snap(96, CELL) === 96 — the turn moves nothing
    p.dir = 'up'
    expect(aimSurvivesTurnImpl(god, p, 'left')).toBe(true)
  })

  it('rejects a lie-aim: enemy on the pre-snap line, lost after the turn-snap', () => {
    const { world, god } = setup(1)
    const p = world.player!
    // p.y = 101 → snap → 96 → pcy drops 117 → 112. Enemy body [124,156] is on
    // the 125 offset line but off both 104/120 post-snap lines.
    expect(aimSurvivesTurnImpl(god, p, 'left')).toBe(false)
  })

  it('accepts a real aim: enemy visible on BOTH pre- and post-snap lines', () => {
    const { world, god } = setup(1)
    const p = world.player!
    // Move the enemy to a row that both scan positions cover.
    const e = world.tanks[0]
    e.y = 112 // body [112,144] — 125 (pre) and 120 (post) both intersect
    expect(aimSurvivesTurnImpl(god, p, 'left')).toBe(true)
  })

  it('default params ship the guard ON', () => {
    expect(DEFAULT_GOD_AI_PARAMS.aimTurnSnapGuard).toBe(1)
  })
})

// ---------------------------------------------------------------- aggressive branch integration

describe('§80 aggressive (freeze) branch hunts through the guard', () => {
  function freezeStandoff(
    seed: number,
    guard: number,
  ): {
    world: World
    sim: Simulation
    god: GodAIInput
    p2: Tank
  } {
    const world = lieAimWorld(seed)
    // Order matters: coopGod() spawns player2 — read it only AFTER.
    const god = coopGod(world, guard, seed)
    const p2 = world.player2!
    p2.x = 64
    p2.y = 101
    p2.dir = 'up'
    p2.shieldTimer = 0
    const sim = new Simulation(world, new Input())
    sim.input2 = god
    // Freeze window active → aggressive branch is the ONLY branch that runs.
    world.freezeTimer = 20000
    return { world, sim, god, p2 }
  }

  it('guard ON: P2 kills the frozen enemy during the freeze window', () => {
    const { world, sim, god, p2 } = freezeStandoff(1785622102123 & 0xffff, 1)

    // Sanity: the lie-aim is real — the enemy is visible NOW but not after the
    // perpendicular snap, and P2 is off-grid on that axis.
    // NOTE: scanAheadImpl writes into the shared `self._scanResult` buffer, so
    // the pre-snap result MUST be captured into a local before the post-snap
    // scan clobbers it (the §80 ordering hazard documented in GodAIInput.ts).
    const pcx = p2.x + p2.w / 2
    const pcy = p2.y + p2.h / 2
    expect(p2.y % CELL).not.toBe(0)
    const pre = god.scanAhead(pcx, pcy, 'left')
    const preEnemy = pre.enemy
    const snapped = Math.round(p2.y / CELL) * CELL
    const post = god.scanAhead(pcx, snapped + p2.h / 2, 'left')
    expect(preEnemy).toBe(true)
    expect(post.enemy).toBe(false)
    expect(aimSurvivesTurnImpl(god, p2, 'left')).toBe(false)

    // 900 ticks = 15s of a 20s freeze window. With the guard ON the tank must
    // NOT burn the window in place — it navigates to a real firing line
    // (the grid-aligned row) and kills the helpless enemy.
    const res = runFreeze(world, sim, god, 900)
    expect(res.enemyDead).toBe(true)
    expect(res.kills).toBeGreaterThan(0)
    expect(res.spanX + res.spanY).toBeGreaterThan(CELL)
    // The guard keeps the tank off the period-2 thrash cycle.
    expect(res.thrashTicks).toBeLessThan(60)
  })

  it('guard OFF: P2 wastes the freeze window pinned next to the enemy', () => {
    const { world, sim, god } = freezeStandoff(424242, 0)

    const res = runFreeze(world, sim, god, 900)
    // Pre-§80: the tank commits to the lie-aim turn, the snap slides it off
    // the firing line, and it never reaches a killing line — the whole freeze
    // window is wasted (verified seed-independent: guard=0 pins with every
    // seed, guard=1 kills with every seed).
    expect(res.kills).toBe(0)
    expect(res.enemyDead).toBe(false)
    // Pinned at the enemy's column: net displacement stays sub-cell.
    expect(res.spanX + res.spanY).toBeLessThan(CELL * 2)
  })
})
