import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput } from '../src/ai/GodAIInput'
import { findMostDangerousBulletImpl, isSafeDirImpl } from '../src/ai/god/ThreatAssessor'
import { CELL, BULLET } from '../src/constants'
import type { Bullet } from '../src/types'
import { clearArena } from './helpers'

/**
 * ThreatAssessor unit tests — bullet evasion is DELIBERATELY terrain-blind.
 *
 * DECISIONS.md §48 (negative result): adding a terrain-occlusion check to
 * `findMostDangerousBulletImpl` was measured at S33 −10pp @120 seeds
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
 * S33 @120 seeds + 35×60 A/B first (see DECISIONS.md §48).
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

  describe('findMostDangerousBullet — §48-revisit steel-only occlusion (DECISIONS §71)', () => {
    /** Enable the terrain-gated steel-only occlusion path directly on the input. */
    function enableOcclusion(input: GodAIInput): void {
      input.params.evasionSteelOcclusion = 1
    }

    /** Place the player tank at a known cell so the pinned gate is deterministic. */
    function positionPlayer(world: World, col: number, row: number): void {
      const p = world.player!
      p.x = col * CELL
      p.y = row * CELL
    }

    it('skips a steel-blocked bullet when the player is NOT pinned (wasteful dodge suppressed)', () => {
      const { world, input } = setupWorld()
      enableOcclusion(input)
      positionPlayer(world, 8, 10)
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      // Steel between bullet (row 5) and player (row 10). Steel is permanent
      // for enemy bullets — the dodge could never be needed. Player is in
      // open space (4 open directions), so the pinned gate does NOT fire.
      world.tileMap.grid[7][8] = 'steel'
      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).toBeNull()
    })

    it('STILL detects a brick-blocked bullet even with occlusion ON (brick never occludes)', () => {
      const { world, input } = setupWorld()
      enableOcclusion(input)
      positionPlayer(world, 8, 10)
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      // Brick is temporary — the enemy re-fires through it within ticks, so
      // dodging a brick-blocked bullet is load-bearing anticipatory dodging
      // (DECISIONS §48 / §71). The steel-only scan must NOT skip it.
      world.tileMap.grid[7][8] = 'brick'
      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
    })

    it('KEEPS dodging a steel-blocked bullet when the player IS pinned (dodge is the escape)', () => {
      const { world, input } = setupWorld()
      enableOcclusion(input)
      positionPlayer(world, 8, 10)
      const pcx = 8 * CELL + CELL / 2
      const pcy = 10 * CELL + CELL / 2

      // Pin the player at (8,10): steel walls above (rows 8-9, cols 8-9) and
      // to the left (rows 10-11, cols 6-7) → only right/down remain open
      // (≤ 2 open directions = pinned). The S33 seed-11 lesson: when pinned,
      // the dodge IS the escape — never suppress it, even for a steel-blocked
      // bullet.
      for (let r = 8; r <= 9; r++) {
        for (let c = 8; c <= 9; c++) world.tileMap.grid[r][c] = 'steel'
      }
      for (let r = 10; r <= 11; r++) {
        for (let c = 6; c <= 7; c++) world.tileMap.grid[r][c] = 'steel'
      }

      world.tileMap.grid[7][8] = 'steel' // blocked bullet path
      world.bullets.push(makeBullet(pcx - BULLET / 2, 5 * CELL, 'down'))
      input.hasBase = world.tileMap.hasBase()

      const threat = findMostDangerousBulletImpl(input, pcx, pcy)
      expect(threat).not.toBeNull()
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
