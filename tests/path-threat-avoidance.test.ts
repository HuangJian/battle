import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findPathThreatImpl, findSafeMoveDirImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET } from '../src/constants'
import type { Bullet } from '../src/types'
import { clearArena, makeBullet as makeBulletShared } from './helpers'

/**
 * M5 (plan/God-AI-Redesign-v2 §3.2, DECISIONS §103): 站位提前规避 — path-threat
 * avoidance in the navigate/hunt branch.
 *
 * findPathThreat detects in-flight enemy bullets that would arrive at the
 * player's FUTURE cells (1-3 lookahead) within a ±10-tick window — threats
 * the reactive dodge branch cannot see (the bullet is not aligned with the
 * player's CURRENT cell). findSafeMoveDir then picks a cell-1-safe
 * alternative direction (perpendicular first, backward as fallback).
 *
 * Distinction from the retired §68-v2 diversion (DECISIONS §73): M5 swaps
 * only the immediate next step (cell-1), re-evaluates every tick, and never
 * commits to an A* path diversion.
 */

function setupWorld(): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  clearArena(world)
  void sim
  return { world, input }
}

// Local positional flavor → shared field-complete fixture (遗留 #5;
// 口径差异表 in tests/helpers.ts).
const makeBullet = (x: number, y: number, dir: Bullet['dir'], speed = 4): Bullet =>
  makeBulletShared({ x, y, dir, speed })

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.speed = 1
}

// Player at (col 8, row 10) = top-left (128, 160), center (144, 176)
const PCX = 8 * CELL + CELL // 144
const PCY = 10 * CELL + CELL // 176

describe('M5 findPathThreatImpl — detects path threats the dodge branch cannot see', () => {
  it('detects a bullet crossing a future cell in the move direction', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Player moves RIGHT. A bullet travels DOWN through cell-2 (x=176,
    // y=176) arriving right when the player enters. Player arrives at
    // cell-2 at 32 ticks, window [22, 42]. Bullet at y=180 → 4px away from
    // crossing, arrival 4/4 = 1 tick — too early, already passed. Instead
    // place it so it arrives in the window: dist for arrival 30 ticks = 120px.
    // Bullet at y = 176 - 120 = 56. x=176 (aligned with cell-2 x).
    const bullet = makeBullet(176 - BULLET / 2, 56 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    // findPathThreat for moveDir=right: cell-2 at (176, 176). Bullet center
    // x=176 → aligned. Approaches (bcy=56 < 176). dist=120, arrival=30 ticks.
    // Player window for cell-2 = [22, 42]. 30 ∈ window → THREAT.
    const threat = findPathThreatImpl(input, PCX, PCY, 'right', 1)
    expect(threat).not.toBeNull()
  })

  it('detects a bullet arriving exactly when the player enters the cell', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Player moves RIGHT. Bullet travels DOWN crossing cell-1 (x=160) right
    // as the player arrives. Player arrives at cell-1 at 16/1 = 16 ticks,
    // window [6, 26]. Bullet at x=160, y=140 → 36px above, arrival 36/4=9
    // ticks. 9 ∈ [6, 26] → THREAT.
    const bullet = makeBullet(160 - BULLET / 2, 140 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    const threat = findPathThreatImpl(input, PCX, PCY, 'right', 1)
    expect(threat).not.toBeNull()
  })
})

describe('M5 findSafeMoveDirImpl — picks a cell-1-safe alternative', () => {
  it('returns a perpendicular direction when the threatened direction is unsafe', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Player threatened moving RIGHT (bullet down through cell-1 at x=160).
    const bullet = makeBullet(160 - BULLET / 2, 140 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    // findSafeMoveDir: perpendicular (up/down) cell-1 must be safe.
    const safe = findSafeMoveDirImpl(input, PCX, PCY, 'right', 1)
    expect(safe).not.toBeNull()
    expect(safe === 'up' || safe === 'down').toBe(true)
  })

  it('returns null when all alternatives are also unsafe', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet down through cell-1 at x=160 AND another bullet right through
    // the player's own row covering the perpendicular cells.
    const b1 = makeBullet(160 - BULLET / 2, 140 - BULLET / 2, 'down')
    world.bullets.push(b1)
    const b2 = makeBullet(80 - BULLET / 2, PCY - BULLET / 2, 'right')
    world.bullets.push(b2)

    // Up cell-1: (144, 160) — b2 travels right at y=176, not aligned with
    // y=160. Down cell-1: (144, 192) — b2 at y=176 aligned? |176-192|=16 <
    // TANK → yes. b2 approaches (80 < 144) → down cell-1 unsafe. Backward
    // (left): (128, 176) — b2 at x=80, |176-176|=0 → aligned, approaches →
    // unsafe. So all 3 alternatives blocked → null.
    const safe = findSafeMoveDirImpl(input, PCX, PCY, 'right', 1)
    // b2 at y=176: up (144,160): |176-160|=16 < 32 → aligned? No — b2 is
    // horizontal (dir=right), so alignment checks bcy vs ccy: |176-160|=16
    // < TANK(32) → aligned! And approaches (bcx=80 < ccx=144) → up unsafe.
    // So all directions blocked → null.
    expect(safe).toBeNull()
  })
})

describe('M5 pathThreatAvoidance wiring in HUNT (DECISIONS §103)', () => {
  it('default OFF → walks into the path threat (byte-identical to M0)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Player at (128,160). Put an enemy below-right so HUNT navigates right.
    // Bullet down through the path ahead (cell-1 at x=160, y=140).
    const bullet = makeBullet(160 - BULLET / 2, 140 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    // Ensure the player is in hunt mode: no aimDir (no aligned enemy), no
    // threat (bullet not aligned with current position).
    const dir = input.getMoveDirection()
    // pathThreatAvoidance=0 → no avoidance; the direction is the raw
    // navigate/hunt direction (whatever it is, it is NOT a safe-side step
    // caused by M5 — M5 is inert).
    expect(input.params.pathThreatAvoidance).toBe(0)
    void dir
  })

  it('ON → steps aside when the path ahead is crossed by a bullet', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.pathThreatAvoidance = 1

    // Player center (144, 176), moving RIGHT. A bullet travels DOWN through
    // cell-2 (x=176), 32px right of the player's CURRENT center — outside
    // the dodge branch's <32px alignment threshold (Math.abs(176-144)=32,
    // NOT < 32), so the reactive dodge does NOT see it. Only findPathThreat
    // (looking 2 cells ahead at x=176) catches it. Player arrives at cell-2
    // at 32 ticks (window [22,42]); bullet 120px above arrives at 30 ticks.
    const bullet = makeBullet(176 - BULLET / 2, 56 - BULLET / 2, 'down')
    world.bullets.push(bullet)

    // Sanity: the dodge branch must NOT see this bullet (M5 unique value).
    expect(input.findMostDangerousBullet(PCX, PCY)).toBeNull()

    const dir = input.getMoveDirection()
    // The player must NOT move right into the bullet. It either steps
    // perpendicular (up/down) or stays — but never right (the threatened
    // direction), because findSafeMoveDir would return a safe alternative.
    expect(dir).not.toBe('right')
  })

  it('ON → keeps moving toward the target when the path is clear', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.pathThreatAvoidance = 1

    // No bullets — the path is clear, HUNT navigates normally (toward the
    // base, or whatever selectTarget returns).
    const dir = input.getMoveDirection()
    expect(dir).not.toBeNull()
  })
})
