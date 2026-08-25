import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput } from '../src/ai/GodAIInput'
import { dodgeDirectionImpl, findMostDangerousBulletImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET } from '../src/constants'
import type { Bullet } from '../src/types'
import { clearArena, makeBullet as makeBulletShared, seedWorld } from './helpers'

/**
 * §83: dodgeDirection corridor-flee bug �?when the player is pinned in a
 * corridor aligned with an incoming bullet (no perpendicular dodge available),
 * the fallback used to pick "the open direction closest to the base". For a
 * bullet traveling DOWN (toward the base), that is the bullet's OWN travel
 * direction �?the player fled down the corridor in the bullet's wake, but the
 * bullet is faster, so it caught up and killed the player.
 *
 * Reproduced from classic-s02-clear-l1-t62-seed1785636440494.replay @00:27
 * (tick 1641): player at col 4, bullet approaching from above in the same
 * column, walls on both sides �?dodge returned 'down' �?player kept walking
 * down (rec=[down.]) �?bullet overtook and killed.
 *
 * Fix (DECISIONS §83): the fallback must NEVER return the bullet's travel
 * direction (fleeing is futile �?the bullet is faster). It must prefer the
 * direction TOWARD the bullet (opposite of travel) so the player turns to face
 * the incoming bullet and the T5 fire logic cancels it (对枪抵消).
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = seedWorld(42)
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  clearArena(world)
  return { world, input, sim }
}

// Local positional flavor → shared field-complete fixture (遗留 #5;
// 口径差异表 in tests/helpers.ts).
const makeBullet = (x: number, y: number, dir: Bullet['dir']): Bullet =>
  makeBulletShared({ x, y, dir })

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
}

// Player at col 8, row 10 occupies cols 8-9 / rows 10-11 (TANK=32=2 cells).
const PCX = 8 * CELL + CELL / 2 // 136
const PCY = 10 * CELL + CELL / 2 // 168

describe('dodgeDirection �?§83 corridor-flee bug (DECISIONS §83)', () => {
  it('does NOT flee in the bullet travel direction when pinned in a vertical corridor', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10)
    for (const r of [10, 11]) {
      world.tileMap.grid[r][7] = 'brick'
      world.tileMap.grid[r][10] = 'brick'
    }
    input.hasBase = world.tileMap.hasBase()
    const bullet = makeBullet(PCX - BULLET / 2, 5 * CELL, 'down')
    world.bullets.push(bullet)
    const dodge = dodgeDirectionImpl(input, bullet, PCX, PCY)
    // The bug: dodge returned 'down' (the bullet's travel direction = futile
    // flee). It must NOT be the bullet's travel direction.
    expect(dodge).not.toBe('down')
  })

  it('turns TOWARD the bullet (opposite of travel) when pinned, to enable counter-fire', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10)
    for (const r of [10, 11]) {
      world.tileMap.grid[r][7] = 'brick'
      world.tileMap.grid[r][10] = 'brick'
    }
    input.hasBase = world.tileMap.hasBase()
    const bullet = makeBullet(PCX - BULLET / 2, 5 * CELL, 'down')
    world.bullets.push(bullet)
    const dodge = dodgeDirectionImpl(input, bullet, PCX, PCY)
    // Bullet travels DOWN from above �?toward the bullet = UP. The player turns
    // up to face the incoming bullet; shouldFireInDir('up') then fires to cancel
    // it (T5, within TANK*4). This is the 对枪抵消 the user expects.
    expect(dodge).toBe('up')
  })

  it('does NOT flee in the bullet travel direction when pinned in a HORIZONTAL corridor', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10)
    for (const c of [8, 9]) {
      world.tileMap.grid[9][c] = 'brick'
      world.tileMap.grid[12][c] = 'brick'
    }
    input.hasBase = world.tileMap.hasBase()
    const bullet = makeBullet(4 * CELL, PCY - BULLET / 2, 'right')
    world.bullets.push(bullet)
    const dodge = dodgeDirectionImpl(input, bullet, PCX, PCY)
    // Bullet travels RIGHT from the left �?fleeing = 'right' (futile). Toward
    // the bullet = 'left'. Must not flee right.
    expect(dodge).not.toBe('right')
    expect(dodge).toBe('left')
  })

  it('still prefers a perpendicular dodge when one is available (no regression)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10)
    for (const r of [10, 11]) world.tileMap.grid[r][10] = 'brick'
    input.hasBase = world.tileMap.hasBase()
    const bullet = makeBullet(PCX - BULLET / 2, 5 * CELL, 'down')
    world.bullets.push(bullet)
    const dodge = dodgeDirectionImpl(input, bullet, PCX, PCY)
    // Bullet is vertical �?perpendicular candidates are left/right. Right is
    // blocked, left is open �?dodge left. Must NOT be the bullet's direction.
    expect(dodge).toBe('left')
  })

  it('end-to-end: findMostDangerousBullet detects the threat, dodge turns toward it', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8, 10)
    for (const r of [10, 11]) {
      world.tileMap.grid[r][7] = 'brick'
      world.tileMap.grid[r][10] = 'brick'
    }
    input.hasBase = world.tileMap.hasBase()
    const bullet = makeBullet(PCX - BULLET / 2, 5 * CELL, 'down')
    world.bullets.push(bullet)
    const threat = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat).not.toBeNull()
    expect(threat!.dir).toBe('down')
    const dodge = dodgeDirectionImpl(input, threat!, PCX, PCY)
    expect(dodge).toBe('up') // toward the bullet �?counter-fire
  })
})
