import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  steelFireBlockedImpl,
  shouldFireBreakThroughImpl,
  scanAheadImpl,
} from '../src/ai/god/FireControl'
import { CELL, GRID } from '../src/constants'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import type { Tank } from '../src/types'

/**
 * §74: Steel-fire gate unit tests.
 *
 * Bug (user report 2026-08-01): the God AI fired at indestructible steel
 * walls trying to open a path ("射击钢铁障碍物来试图开路"), failed, and then
 * camped in place for the full camp timeout — wasting the bullet cap and
 * drastically reducing combat efficiency.
 *
 * Root cause: T11 steel-blocking lives in shouldFireInDirImpl, but the two
 * break-through fire sites in think() (aggressive navigate + T2b navigate)
 * fire WITHOUT calling shouldFireInDir, so they never applied T11: when
 * navigation chose a move direction blocked by a wall, the AI fired at it
 * unconditionally — including indestructible steel.
 *
 * Fix: `shouldFireBreakThroughImpl` (FireControl.ts) applies the same gate
 * as T11 — `steel && !baseSteel && level < STEEL_PIERCE_PLAYER_LEVEL` — to
 * both break-through sites. Parametrized via `steelFireGate` (default 1 =
 * ON; 0 = OFF = byte-identical pre-§74 behavior, the A/B baseline).
 *
 * SCOPE (per-seed A/B finding, 2026-08-01): the gate is applied ONLY to the
 * break-through sites. It is deliberately NOT applied to the T2a/aggressive
 * stop-and-aim fire (which fires when scan.enemy is true) — the dual-offset
 * case there (steel on one scan line, enemy on the other) means the enemy is
 * genuinely reachable by the center-line bullet, and suppressing that fire
 * costs kills (arena A/B: 20 kills → 7 kills, gameover @1634 vs clear @4592).
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)

  // Clear all terrain and place base cells.
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }

  // Clear the player's spawn timer so think() doesn't early-return
  // (startGame leaves spawnTimer = 1000 — the AI ignores a spawning tank).
  if (world.player) world.player.spawnTimer = 0

  // Mirror the real-game hasBase state (matches fire-control-steel-block
  // test convention) so base-related branches behave like live play.
  input.reset()
  input.hasBase = world.tileMap.hasBase()

  return { world, input, sim }
}

/** Create an enemy tank and place it at the given grid position. */
function placeEnemy(world: World, col: number, row: number): Tank {
  const e = world.createTank('basic', col * CELL, row * CELL, 'down')
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

/** Move the player to a grid cell and face a direction. */
function positionPlayer(world: World, col: number, row: number, dir: Tank['dir']): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.dir = dir
}

describe('steelFireBlockedImpl — §74 gate predicate (mirrors T11)', () => {
  it('blocks steel at level 0 (no pierce)', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, 0, 1)).toBe(true)
  })

  it('blocks steel at level 2 (just below pierce)', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, 2, 1)).toBe(true)
  })

  it('does NOT block steel at level 3 (can pierce)', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, 3, 1)).toBe(false)
  })

  it('does NOT block when no steel is present', () => {
    expect(steelFireBlockedImpl({ steel: false, baseSteel: false }, 0, 1)).toBe(false)
  })

  it('does NOT block base-ring steel (T6 handled at call sites; unbreakable below pierce)', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: true }, 0, 1)).toBe(false)
  })

  it('is inert when the gate is OFF (0) — byte-identical pre-§74 behavior', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, 0, 0)).toBe(false)
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, 3, 0)).toBe(false)
    expect(steelFireBlockedImpl({ steel: false, baseSteel: false }, 0, 0)).toBe(false)
  })

  it('treats undefined level as 0 (safe default)', () => {
    expect(steelFireBlockedImpl({ steel: true, baseSteel: false }, undefined, 1)).toBe(true)
  })
})

describe('shouldFireBreakThroughImpl — §74 break-through fire decision', () => {
  const brick = { enemy: false, baseWall: false, baseSteel: false, steel: false }
  const steel = { enemy: false, baseWall: false, baseSteel: false, steel: true }

  it('fires at a breakable brick wall (gate inert — mobility is load-bearing)', () => {
    expect(shouldFireBreakThroughImpl(brick, 0, 1)).toBe(true)
  })

  it('does NOT fire at steel at level 0 — the reported bug', () => {
    expect(shouldFireBreakThroughImpl(steel, 0, 1)).toBe(false)
  })

  it('does NOT fire at steel at level 2 (just below pierce)', () => {
    expect(shouldFireBreakThroughImpl(steel, 2, 1)).toBe(false)
  })

  it('fires at steel at level 3 (can pierce — break-through is correct)', () => {
    expect(shouldFireBreakThroughImpl(steel, 3, 1)).toBe(true)
  })

  it('gate OFF: fires at steel (byte-identical pre-§74 behavior)', () => {
    expect(shouldFireBreakThroughImpl(steel, 0, 0)).toBe(true)
  })

  it('never fires through the base wall (T6 preserved)', () => {
    const baseWall = { enemy: false, baseWall: true, baseSteel: false, steel: false }
    expect(shouldFireBreakThroughImpl(baseWall, 0, 1)).toBe(false)
  })

  it('never fires through base-ring steel at level >= 3 (§70 preserved)', () => {
    const baseSteel = { enemy: false, baseWall: false, baseSteel: true, steel: true }
    expect(shouldFireBreakThroughImpl(baseSteel, 3, 1)).toBe(false)
  })

  it('fires when an enemy is in the line of fire (enemy wins over wall guards)', () => {
    const enemyAhead = { enemy: true, baseWall: false, baseSteel: false, steel: false }
    expect(shouldFireBreakThroughImpl(enemyAhead, 0, 1)).toBe(true)
  })

  it('matches scanAheadImpl semantics — steel ahead of a brick blocks the fire', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10, 'up')
    input.reset()
    input.hasBase = world.tileMap.hasBase()
    // Steel directly above the player; brick is NOT the first obstacle.
    world.tileMap.grid[9][8] = 'steel'
    world.tileMap.grid[9][9] = 'steel'

    const bs = scanAheadImpl(input, 8 * CELL + CELL / 2, 10 * CELL + CELL / 2, 'up')
    expect(bs.steel).toBe(true)
    expect(shouldFireBreakThroughImpl(bs, 0, 1)).toBe(false)
  })
})

describe('T2a stop-and-aim — deliberately NOT gated (per-seed A/B scope finding)', () => {
  // The dual-offset case (steel on one scan line, enemy on the other) means
  // the enemy is genuinely reachable by the center-line bullet. Suppressing
  // T2a fire in that case costs kills (arena A/B: 20 → 7). These tests lock
  // the scope decision: T2a keeps firing even with steel in the scan result.
  function steelDualOffsetWorld(): { world: World; input: GodAIInput } {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10, 'up')
    placeEnemy(world, 8, 3)
    // Steel on offset-0 line only (col 8); enemy visible on offset-1 (col 9).
    world.tileMap.grid[6][8] = 'steel'
    return { world, input }
  }

  it('gate ON: T2a still fires at a reachable enemy (dual-offset steel+enemy)', () => {
    const { input } = steelDualOffsetWorld()
    input.reset()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, steelFireGate: 1, aimError: 0 }

    input.getMoveDirection()
    expect(input.isFiring()).toBe(true)
  })

  it('gate OFF: identical — T2a fire is load-bearing, not gated', () => {
    const { input } = steelDualOffsetWorld()
    input.reset()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, steelFireGate: 0, aimError: 0 }

    input.getMoveDirection()
    expect(input.isFiring()).toBe(true)
  })

  it('T2a falls through to navigation (no fire) when steel blocks BOTH lines', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10, 'up')
    placeEnemy(world, 8, 3)
    // Steel on BOTH offset lines — the enemy is NOT reachable through steel,
    // so the pre-existing T11 path in scanAhead + shouldFireInDir handles it
    // (enemy stays hidden → T2a falls through; no camp-fire at steel).
    world.tileMap.grid[6][8] = 'steel'
    world.tileMap.grid[6][9] = 'steel'
    input.reset()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, steelFireGate: 1, aimError: 0 }

    const moveDir = input.getMoveDirection()
    // scan.enemy is false (steel blocks both lines) → T2a does NOT camp.
    // The AI navigates instead (real move direction, not a stuck camp).
    expect(moveDir).not.toBeNull()
    expect(input.isFiring()).toBe(false)
  })
})

describe('steelFireGate default — shipped ON (DECISIONS §74)', () => {
  it('defaults to 1 (ON)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.steelFireGate).toBe(1)
  })

  it('OFF (0) flips the gate off', () => {
    const { input } = setupWorld()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, steelFireGate: 0 }
    expect(input.params.steelFireGate > 0).toBe(false)
    expect(DEFAULT_GOD_AI_PARAMS.steelFireGate > 0).toBe(true)
  })
})
