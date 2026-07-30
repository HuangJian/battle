import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput } from '../src/ai/GodAIInput'
import { findMostDangerousBulletImpl, isSafeDirImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET, GRID } from '../src/constants'
import type { Bullet } from '../src/types'

/**
 * ThreatAssessor unit tests — bullet evasion is DELIBERATELY terrain-blind.
 *
 * DECISIONS.md §48 (negative result): adding a terrain-occlusion check to
 * `findMostDangerousBulletImpl` was measured at S32 −10pp @120 seeds
 * (85.0% → 75.0%, lives_exhausted 12 → 22) and 35×60 mean −1.0pp. The
 * "false evasion" (dodging a bullet currently blocked by a brick) turns out
 * to be load-bearing anticipatory dodging: in close combat the blocking
 * brick is usually destroyed by that same bullet stream within a few ticks,
 * so pre-dodging wins. An occlusion check in `isSafeDirImpl` alone measured
 * neutral and was rejected per the "neutral structural change = reject"
 * discipline.
 *
 * These tests LOCK the terrain-blind behavior so a future "fix" of this
 * apparent bug cannot land silently. If you intend to change it, re-run
 * S32 @120 seeds + 35×60 A/B first (see DECISIONS.md §48).
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(42)
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

  return { world, input, sim }
}

function makeBullet(
  x: number,
  y: number,
  dir: Bullet['dir'],
  ownerKind: Bullet['ownerKind'] = 'fast',
): Bullet {
  return {
    id: genId(),
    x,
    y,
    w: BULLET,
    h: BULLET,
    dir,
    alive: true,
    ownerId: -1,
    ownerKind,
    isPlayer: false,
    allegiance: 'enemy',
    speed: 4,
    power: 1,
    damage: 1,
  }
}

describe('ThreatAssessor — deliberate terrain-blind evasion (DECISIONS §48)', () => {
  describe('findMostDangerousBullet', () => {
    it('detects an unobstructed bullet approaching the player', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
      expect(threat!.dir).toBe('down')
    })

    it('STILL detects a bullet behind a brick wall (anticipatory dodge is load-bearing)', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      // Brick at row 7 between bullet (row 5) and player (row 10). The brick
      // will typically be shot through within a few ticks in close combat, so
      // the dodge must trigger anyway. See DECISIONS §48 before "fixing".
      world.tileMap.grid[7][8] = 'brick'

      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
    })

    it('STILL detects a bullet behind a steel wall (terrain-blind by design)', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      world.tileMap.grid[7][8] = 'steel'

      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
    })

    it('picks the closest of two aligned approaching bullets', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      const farBullet = makeBullet(pcx - BULLET / 2, 4 * CELL, 'down')
      farBullet.id = 100
      const nearBullet = makeBullet(pcx - BULLET / 2, 8 * CELL, 'down')
      nearBullet.id = 101
      world.bullets.push(farBullet, nearBullet)
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
      expect(threat!.id).toBe(101)
    })
  })

  describe('isSafeDir', () => {
    it('reports a direction as safe when no bullets threaten it', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      input.hasBase = world.tileMap.hasBase()

      const safe = isSafeDirImpl(input, pcx, pcy, 'left', -1)
      expect(safe).toBe(true)
    })

    it('reports a direction as unsafe when a bullet approaches the candidate cell', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      const candidateX = pcx - CELL
      world.bullets.push(makeBullet(candidateX - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const safe = isSafeDirImpl(input, pcx, pcy, 'left', -1)
      expect(safe).toBe(false)
    })

    it('STILL reports unsafe when the bullet is behind terrain (terrain-blind by design)', () => {
      const { world, input } = setupWorld()
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      // Brick between the bullet and the candidate cell. An occlusion check
      // here measured neutral at 35×60 (87.6% vs 87.7%) — rejected as a
      // neutral structural change (DECISIONS §48).
      world.tileMap.grid[7][7] = 'brick'

      const candidateX = pcx - CELL
      world.bullets.push(makeBullet(candidateX - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const safe = isSafeDirImpl(input, pcx, pcy, 'left', -1)
      expect(safe).toBe(false)
    })
  })
})
