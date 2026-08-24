import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { dodgeDirectionImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, GRID } from '../src/constants'
import type { Bullet } from '../src/types'

/**
 * §223 (dodge idle forensics → candidate): multi-bullet centroid escape —
 * `dodgeCentroidMode`. The counterfactual-dodge hard-away arm survived 75.3%
 * of dodge-death windows vs 0% factual: running away from the CENTROID of
 * the bullet cluster beats dodging the single nearest bullet. These tests
 * lock the function-level behavior: OFF is byte-identical, single-bullet
 * never triggers, multi-bullet picks the furthest-from-centroid safe side,
 * the base gate blocks runaway escapes, and unsafe lanes are never entered.
 */

function setupWorld(): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  return { world, input }
}

function makeBullet(x: number, y: number, dir: Bullet['dir'], speed = 4): Bullet {
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
    speed,
    power: 1,
    damage: 1,
  }
}

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
  p.spawnTimer = 0
  p.shieldTimer = 0
}

// Player center at (col 8, row 10) = (144, 176)
const PCX = 8 * CELL + CELL // 144
const PCY = 10 * CELL + CELL // 176

describe('§223 dodgeCentroidMode (multi-bullet centroid escape)', () => {
  it('defaults to OFF (0) — never active without an explicit A/B override', () => {
    expect(DEFAULT_GOD_AI_PARAMS.dodgeCentroidMode).toBe(0)
  })

  it('single bullet: byte-identical legacy pick (mode ON does not change 1-bullet behavior)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCentroidMode = 1

    // Vertical threat dead-on: legacy base-closer tail picks RIGHT (base at
    // cols 12-13). With only ONE bullet the centroid block must not run.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)

    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')
  })

  it('multi-bullet: picks the direction furthest from the bullet centroid', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCentroidMode = 1

    // Threat 1: vertical bullet coming down dead-on.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)
    // Cluster of 2 bullets ABOVE-LEFT of the player (cols 5-6, row 4) →
    // centroid is up-left → the away pick must be DOWN-RIGHT (passable,
    // safe, and within the base slack).
    world.bullets.push(makeBullet(5 * CELL, 4 * CELL, 'down'))
    world.bullets.push(makeBullet(6 * CELL, 4 * CELL, 'down'))

    const dodge = dodgeDirectionImpl(input, threat, PCX, PCY)
    expect(dodge).toBe('right')
  })

  it('multi-bullet: centroid away beats the legacy base-closer pick', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCentroidMode = 1

    // Legacy pick for a dead-on vertical threat = RIGHT (base-closer). A
    // 2-bullet cluster BELOW-RIGHT of the player (rows 12-13, col 10) puts
    // the centroid below-right → the away pick is UP-LEFT — overturning the
    // base-closer bias. Both lanes safe, both passable.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)
    world.bullets.push(makeBullet(10 * CELL, 12 * CELL, 'up'))
    world.bullets.push(makeBullet(10 * CELL, 13 * CELL, 'up'))

    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('left')
  })

  it('base gate: an away-pick that steps away from the base is rejected', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCentroidMode = 1

    // Cluster BELOW the player (cols 8, rows 14-15): centroid ≈ (131, 232)
    // → the away pick is UP — which is also 16px FURTHER from the base
    // (base at cols 12-13, rows 24-25). The base gate must reject UP.
    // Among the remaining passable directions, RIGHT is the only one that
    // does not increase the base distance (272 ≤ 288) → the centroid block
    // returns RIGHT even though UP is the strongest away direction.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)
    world.bullets.push(makeBullet(8 * CELL, 14 * CELL, 'up'))
    world.bullets.push(makeBullet(8 * CELL, 15 * CELL, 'up'))

    const dodge = dodgeDirectionImpl(input, threat, PCX, PCY)
    expect(dodge).toBe('right')
  })

  it('unsafe lanes are never entered: crossfire lane on the away side is skipped', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()
    input.params.dodgeCentroidMode = 1

    // Cluster below-right (rows 12-13, col 10) → away = LEFT. But a
    // horizontal bullet traveling RIGHT along the player's row at col 4
    // (crossing the LEFT new cell at x=112, within 12px) makes LEFT unsafe
    // → the centroid block must skip LEFT and take RIGHT instead.
    const threat = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    world.bullets.push(threat)
    world.bullets.push(makeBullet(10 * CELL, 12 * CELL, 'up'))
    world.bullets.push(makeBullet(10 * CELL, 13 * CELL, 'up'))
    world.bullets.push(makeBullet(4 * CELL, PCY - BULLET / 2, 'right'))

    expect(dodgeDirectionImpl(input, threat, PCX, PCY)).toBe('right')
  })
})
