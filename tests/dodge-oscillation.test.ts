import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput } from '../src/ai/GodAIInput'
import { findMostDangerousBulletImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, TANK } from '../src/constants'
import type { Bullet } from '../src/types'
import { clearArena, makeBullet as makeBulletShared } from './helpers'

/**
 * §86 threat-alignment baseline regression guard.
 *
 * `findMostDangerousBulletImpl` detects an incoming bullet as a dodge threat
 * only when it is aligned with the player within the standard `< TANK`
 * threshold. This file pins that baseline AFTER the M0.5 retirement of the
 * §86 hysteresis/persistence params (dodgeHysteresis, dodgeDirPersistence —
 * A/B -1.1pp / -1.7pp, never shipped, archived in experimental.ts).
 *
 * Historical context (DECISIONS §86): a global `<= TANK` widening was
 * rejected because it caused -37pp on S33 Diamond — bullets in adjacent steel
 * corridors sit at exactly 32px, so widening the threshold made the player
 * dodge bullets that could never reach it. These tests guard that exact
 * boundary: < TANK detected, == TANK not detected.
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  clearArena(world)
  return { world, input, sim }
}

// Local positional flavor → shared field-complete fixture (遗留 #5;
// 口径差异表 in tests/helpers.ts).
const makeBullet = (x: number, y: number, dir: Bullet['dir'], speed = 4): Bullet =>
  makeBulletShared({ x, y, dir, speed })

function positionPlayer(world: World, x: number, y: number): void {
  const p = world.player!
  p.x = x
  p.y = y
}

// Player center at (col 8, row 10) = (136, 168)
const PCX = 8 * CELL + CELL / 2 // 136
const PCY = 10 * CELL + CELL / 2 // 168

describe('dodge threat alignment threshold (baseline §86 regression guard)', () => {
  it('detects a threat within standard TANK alignment (< TANK)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet center 31px from player center (TANK - 1 = within standard threshold).
    const bulletY = PCY - (TANK - 1) - BULLET / 2
    const bullet = makeBullet(15 * CELL, bulletY, 'left')
    world.bullets.push(bullet)

    const threat = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat).not.toBeNull()
  })

  it('does NOT detect a threat at exactly TANK distance (boundary pin)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet center EXACTLY TANK (32px) from player center. The standard
    // `< TANK` threshold must NOT detect it — 32 is not < 32. This pins the
    // S33 Diamond lesson: a global `<= TANK` widening caused -37pp by
    // detecting bullets in adjacent steel corridors at exactly 32px.
    const bulletY = PCY - TANK - BULLET / 2
    const bullet = makeBullet(15 * CELL, bulletY, 'left')
    world.bullets.push(bullet)

    const threat = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat).toBeNull()
  })
})
