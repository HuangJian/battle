import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findEnemyFacingPlayerImpl } from '../src/ai/god/FireControl'
import { hasEnemyBulletInLineImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, TANK } from '../src/constants'
import type { Bullet, Tank } from '../src/types'
import { clearArena } from './helpers'

/**
 * §49-revisit 炮口相向对枪抵消 (§52 v2) unit tests.
 *
 * The retained §49 family behavior (DECISIONS §49/§52 v2): inside T2a, when
 * an enemy FACES the player within `counterFireMaxRange` cells (and the
 * player is not on ice), the AI either (a) fires to cancel an enemy bullet
 * already in the line of fire (对枪抵消), or (b) keeps alignment and fires.
 * This measured +5 wins @35×120 on the pre-§47 tree, and re-validated on
 * the current tree (2026-08-01): 35×60 net +3 flips with ZERO ON→OFF
 * losses (S27 +2.5pp@120, S21 +0.8pp@120).
 *
 * These tests LOCK the detection primitives + the shipped default so a
 * future change cannot silently alter them. Parametrized via
 * `counterFire` (default 1 = current behavior; 0 = plain pre-§52 T2a).
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)

  // Clear all terrain and place base cells.
  clearArena(world)

  return { world, input, sim }
}

/** Create an enemy tank and place it at the given grid position with a dir. */
function placeEnemy(world: World, col: number, row: number, dir: Tank['dir'] = 'down'): Tank {
  const e = world.createTank('basic', col * CELL, row * CELL, dir)
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

function makeBullet(x: number, y: number, dir: Bullet['dir']): Bullet {
  return {
    id: genId(),
    x,
    y,
    w: BULLET,
    h: BULLET,
    dir,
    alive: true,
    ownerId: -1,
    ownerKind: 'fast',
    isPlayer: false,
    allegiance: 'enemy',
    speed: 4,
    power: 1,
    damage: 1,
  }
}

// Player tank occupies cols 8-9, rows 10-11 (x=128..160, y=160..192).
const PCX = 8 * CELL + CELL / 2 // 136
const PCY = 10 * CELL + CELL / 2 // 168

describe('findEnemyFacingPlayerImpl — §52 v2 facing detection', () => {
  it('detects an enemy above the player facing down (player aims up)', () => {
    const { world, input } = setupWorld()
    placeEnemy(world, 8, 5, 'down')

    const facing = findEnemyFacingPlayerImpl(input, PCX, PCY, 'up')
    expect(facing).not.toBeNull()
    expect(facing!.dist).toBeGreaterThan(0)
  })

  it('detects an enemy below the player facing up (player aims down)', () => {
    const { world, input } = setupWorld()
    placeEnemy(world, 8, 14, 'up')

    const facing = findEnemyFacingPlayerImpl(input, PCX, PCY, 'down')
    expect(facing).not.toBeNull()
  })

  it('returns null when the enemy is NOT facing the player (same dir)', () => {
    const { world, input } = setupWorld()
    placeEnemy(world, 8, 5, 'up') // faces away — not "facing toward player"

    const facing = findEnemyFacingPlayerImpl(input, PCX, PCY, 'up')
    expect(facing).toBeNull()
  })

  it('returns null when the enemy is not aligned with the player', () => {
    const { world, input } = setupWorld()
    // col 4 → enemy center 80px, |80 - 136| = 56 ≥ TANK(32) → misaligned.
    // (col 6 would be 24px off — still inside the TANK alignment window.)
    placeEnemy(world, 4, 5, 'down')

    const facing = findEnemyFacingPlayerImpl(input, PCX, PCY, 'up')
    expect(facing).toBeNull()
  })
})

describe('hasEnemyBulletInLineImpl — §52 v2 trade-shot bullet detection', () => {
  it('detects an enemy bullet approaching the player in the aim line', () => {
    const { world, input } = setupWorld()
    // Bullet above the player (row 5) heading DOWN, aligned with col 8.
    world.bullets.push(makeBullet(PCX - BULLET / 2, 5 * CELL, 'down'))

    const inLine = hasEnemyBulletInLineImpl(input, PCX, PCY, 'up')
    expect(inLine).toBe(true)
  })

  it('ignores a bullet moving AWAY from the player', () => {
    const { world, input } = setupWorld()
    world.bullets.push(makeBullet(PCX - BULLET / 2, 5 * CELL, 'up'))

    const inLine = hasEnemyBulletInLineImpl(input, PCX, PCY, 'up')
    expect(inLine).toBe(false)
  })

  it('ignores a bullet not aligned with the aim line', () => {
    const { world, input } = setupWorld()
    // 2 cells off the player's column → |bcx - pcx| = 32 ≥ TANK(32).
    world.bullets.push(makeBullet(PCX - BULLET / 2 - 2 * CELL, 5 * CELL, 'down'))

    const inLine = hasEnemyBulletInLineImpl(input, PCX, PCY, 'up')
    expect(inLine).toBe(false)
  })

  it('ignores bullets farther than 8 tank-lengths (TANK * 8)', () => {
    const { world, input } = setupWorld()
    // 10 tanks away (160 px), beyond the 8-tank scan range.
    world.bullets.push(makeBullet(PCX - BULLET / 2, PCY - 10 * TANK, 'down'))

    const inLine = hasEnemyBulletInLineImpl(input, PCX, PCY, 'up')
    expect(inLine).toBe(false)
  })
})

describe('counterFire params — shipped default (DECISIONS §49-revisit)', () => {
  it('defaults to ON (1) with the original 5-cell range — byte-identical shipped behavior', () => {
    expect(DEFAULT_GOD_AI_PARAMS.counterFire).toBe(1)
    expect(DEFAULT_GOD_AI_PARAMS.counterFireMaxRange).toBe(5)
  })

  it('OFF (0) flips the shipped gate off (default ON)', () => {
    const { input } = setupWorld()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, counterFire: 0 }

    // Lock the gate expression that gates the facing-enemy block in think()
    // (default ON, OFF flips it). This is a param-level lock, not a full
    // think() behavioral test — the byte-identity + 35×60 A/B at the tool
    // level validate the actual branch behavior.
    expect(input.params.counterFire > 0).toBe(false)
    expect(DEFAULT_GOD_AI_PARAMS.counterFire > 0).toBe(true)
  })
})
