import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput } from '../src/ai/GodAIInput'
import { dodgeDirectionImpl, findMostDangerousBulletImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, TANK, GRID } from '../src/constants'
import type { Bullet } from '../src/types'
import type { Direction } from '../src/constants'

/**
 * §86: Dodge direction oscillation + threat detection boundary flicker.
 *
 * Two bugs found in classic-s12-died-l0-t43-seed1322088985.replay:
 *
 * Bug 1 (0:21): The player IS in the dodge branch and IS detecting the
 * threat, but `dodgeDirectionImpl` returns different directions on consecutive
 * ticks (up↔down) as the player's sub-cell position shifts by 1px. This makes
 * the player oscillate between two positions (e.g., y=55↔56) — effectively
 * stationary while the bullet approaches and hits.
 *
 * Bug 2 (0:42): The player oscillates at the alignment boundary (|bcy - pcy|
 * = 31 or 32). The old `< TANK` check detected the threat at 31 but not at 32,
 * causing it to flicker between the dodge and navigate branches every tick.
 * A fast bullet (8.3 px/tick) then hit the stationary player.
 *
 * Fixes (DECISIONS §86):
 * 1. Dodge direction persistence: `dodgeDirectionImpl` returns the last
 *    direction if the same threat is still active and the direction is still
 *    safe.
 * 2. Threat hysteresis: `findMostDangerousBulletImpl` uses a wider threshold
 *    (TANK + 2) for the recently-dodged threat bullet, keeping it detected
 *    through 1px boundary oscillation. New threats still use the standard
 *    TANK threshold. A global `<= TANK` was rejected (S32 Diamond -37pp).
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  return { world, input, sim }
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
}

// Player center at (col 8, row 10) = (136, 168)
const PCX = 8 * CELL + CELL / 2 // 136
const PCY = 10 * CELL + CELL / 2 // 168

describe('§86 Bug 1: dodge direction persistence prevents oscillation', () => {
  it('returns the SAME dodge direction on consecutive calls (no flip)', () => {
    const { world, input } = setupWorld()
    input.params.dodgeDirPersistence = 1
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet approaching from the right (dir=left), aligned with player.
    const bullet = makeBullet(15 * CELL, PCY - BULLET / 2, 'left')
    world.bullets.push(bullet)

    // First call — picks a direction (perpendicular to bullet = up or down).
    const dodge1 = dodgeDirectionImpl(input, bullet, PCX, PCY)
    expect(dodge1).not.toBeNull()

    // Simulate think() storing the dodge direction for persistence.
    input._lastDodgeDir = dodge1
    input._lastDodgeThreatId = bullet.id

    // Second call with same bullet — must return the SAME direction
    // (persistence). Without the fix, a sub-cell position change could flip
    // the direction, causing 1px oscillation.
    const dodge2 = dodgeDirectionImpl(input, bullet, PCX, PCY)
    expect(dodge2).toBe(dodge1)
  })

  it('does NOT persist when the threat bullet changes', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    const bullet1 = makeBullet(15 * CELL, PCY - BULLET / 2, 'left')
    world.bullets.push(bullet1)

    const dodge1 = dodgeDirectionImpl(input, bullet1, PCX, PCY)
    input._lastDodgeDir = dodge1
    input._lastDodgeThreatId = bullet1.id

    // New bullet from above (different direction).
    const bullet2 = makeBullet(PCX - BULLET / 2, 3 * CELL, 'down')
    bullet2.id = genId()
    world.bullets.push(bullet2)

    // Must NOT return the persisted direction — different threat.
    const dodge2 = dodgeDirectionImpl(input, bullet2, PCX, PCY)
    expect(dodge2).not.toBe(dodge1)
  })

  it('falls through to recompute when the persisted direction is no longer passable', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet from the right, aligned with player.
    const bullet = makeBullet(15 * CELL, PCY - BULLET / 2, 'left')
    world.bullets.push(bullet)

    // First call — should pick 'up' or 'down' (perpendicular).
    const dodge1 = dodgeDirectionImpl(input, bullet, PCX, PCY)
    expect(dodge1).not.toBeNull()

    // Persist the direction.
    input._lastDodgeDir = dodge1
    input._lastDodgeThreatId = bullet.id

    // Block the persisted direction with a wall.
    const blockedDir = dodge1 as Direction
    const v =
      blockedDir === 'up' || blockedDir === 'down'
        ? { dx: 0, dy: blockedDir === 'up' ? -1 : 1 }
        : { dx: blockedDir === 'left' ? -1 : 1, dy: 0 }
    // Block the cells the player would move into.
    const nx = 8 * CELL + v.dx * CELL
    const ny = 10 * CELL + v.dy * CELL
    const c0 = Math.floor(nx / CELL)
    const r0 = Math.floor(ny / CELL)
    for (let r = r0; r <= r0 + 1; r++) {
      for (let c = c0; c <= c0 + 1; c++) {
        if (r >= 0 && r < GRID && c >= 0 && c < GRID) {
          world.tileMap.grid[r][c] = 'brick'
        }
      }
    }

    // Clear the per-tick canMoveDir cache (simulates endFrame between ticks).
    // Without this, the cached "passable" result from the first call would
    // mask the new wall.
    input._canMoveComputed = 0

    // Must NOT return the blocked direction — should recompute.
    const dodge2 = dodgeDirectionImpl(input, bullet, PCX, PCY)
    expect(dodge2).not.toBe(blockedDir)
  })
})

describe('§86 Bug 2: threat hysteresis prevents boundary flickering', () => {
  it('detects a NEW threat within standard TANK alignment (< TANK)', () => {
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

  it('does NOT detect a NEW threat at exactly TANK distance (standard threshold)', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet center EXACTLY TANK (32px) from player center. This is a NEW
    // threat (not recently dodged) — the standard `< TANK` threshold applies.
    // 32 is NOT < 32, so it's not detected. This prevents false positives on
    // steel-maze stages where bullets in adjacent corridors are at exactly
    // 32px (the global `<= TANK` change caused -37pp on S32 Diamond).
    const bulletY = PCY - TANK - BULLET / 2
    const bullet = makeBullet(15 * CELL, bulletY, 'left')
    world.bullets.push(bullet)

    const threat = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat).toBeNull()
  })

  it('KEEPS detecting a recently-dodged threat at exactly TANK distance (hysteresis)', () => {
    const { world, input } = setupWorld()
    input.params.dodgeHysteresis = 1
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Place a bullet within standard alignment (31px) so it's detected.
    const bulletY = PCY - (TANK - 1) - BULLET / 2
    const bullet = makeBullet(15 * CELL, bulletY, 'left')
    world.bullets.push(bullet)

    // First call: threat detected (31px < 32).
    const threat1 = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat1).not.toBeNull()

    // Simulate think() storing the threat for persistence.
    input._lastDodgeThreatId = threat1!.id

    // Now the player moves 1px, making the alignment exactly TANK (32px).
    // Without hysteresis, the threat would be LOST (32 is NOT < 32).
    // With hysteresis, the recently-dodged threat uses TANK + 2 = 34 threshold,
    // so 32 < 34 → still detected.
    positionPlayer(world, 8 * CELL, 10 * CELL + 1) // pcy shifts by 1
    const pcy2 = 10 * CELL + 1 + CELL / 2 // 169
    // Bullet center Y = 137. |137 - 169| = 32 = TANK. Standard < TANK fails,
    // but hysteresis (TANK+2=34) keeps it detected.
    const threat2 = findMostDangerousBulletImpl(input, PCX, pcy2)
    expect(threat2).not.toBeNull() // hysteresis keeps it detected
  })

  it('simulates the 0:42 scenario: threat does NOT flicker at the boundary', () => {
    const { world, input } = setupWorld()
    input.params.dodgeHysteresis = 1
    input.hasBase = world.tileMap.hasBase()

    // Simulate the exact 0:42 geometry: a fast bullet approaching from the
    // right, with the player oscillating between two Y positions at the
    // alignment boundary.
    //
    // Player center Y oscillates between 111 and 112 (y=95 and y=96).
    // Bullet center Y = 80. |80-111|=31 (< 32, detected).
    // |80-112|=32 (NOT < 32, but < 34 with hysteresis for recent threat).
    //
    // With the fix, the threat should be detected at BOTH positions.
    const bulletCenterY = 80
    const bullet = makeBullet(15 * CELL, bulletCenterY - BULLET / 2, 'left', 8.3)
    world.bullets.push(bullet)
    const pcx = 8 * CELL + CELL / 2

    // Position 1: pcy = 111 (|80-111| = 31) — detected with standard threshold.
    positionPlayer(world, 8 * CELL, 95)
    const pcy1 = 95 + CELL / 2 // 111
    const threat1 = findMostDangerousBulletImpl(input, pcx, pcy1)
    expect(threat1).not.toBeNull()

    // Store as recent threat (simulates think() persistence).
    input._lastDodgeThreatId = threat1!.id

    // Position 2: pcy = 112 (|80-112| = 32) — would be NOT detected with
    // standard `< TANK` (32 is NOT < 32), but IS detected with hysteresis
    // (32 < 34 for the recently-dodged threat).
    positionPlayer(world, 8 * CELL, 96)
    const pcy2 = 96 + CELL / 2 // 112
    const threat2 = findMostDangerousBulletImpl(input, pcx, pcy2)
    // This is the KEY assertion: with the old `< TANK` and no hysteresis,
    // this would be null, causing the threat to flicker. With the hysteresis,
    // the recently-dodged threat stays detected.
    expect(threat2).not.toBeNull()
  })

  it('does NOT detect a bullet beyond TANK+2 even with hysteresis', () => {
    const { world, input } = setupWorld()
    input.params.dodgeHysteresis = 1
    positionPlayer(world, 8 * CELL, 10 * CELL)
    input.hasBase = world.tileMap.hasBase()

    // Bullet center 31px from player center — detected.
    const bulletY = PCY - (TANK - 1) - BULLET / 2
    const bullet = makeBullet(15 * CELL, bulletY, 'left')
    world.bullets.push(bullet)

    const threat1 = findMostDangerousBulletImpl(input, PCX, PCY)
    expect(threat1).not.toBeNull()
    input._lastDodgeThreatId = threat1!.id

    // Move player so the distance is TANK + 3 = 35 — beyond even the
    // hysteresis threshold (34). Should NOT be detected.
    positionPlayer(world, 8 * CELL, 10 * CELL + 3)
    const pcy2 = 10 * CELL + 3 + CELL / 2
    const threat2 = findMostDangerousBulletImpl(input, PCX, pcy2)
    expect(threat2).toBeNull()
  })
})
