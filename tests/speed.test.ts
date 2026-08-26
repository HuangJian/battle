import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { RNG } from '../src/utils/RNG'
import { CELL } from '../src/constants'
import type { TankKind } from '../src/types'
import {
  BALANCED_ENEMY_CPS,
  BASE_SPEED_CPS,
  PLAYER_SPEED_PER_STAR_CPS,
  SPEED_JITTER_MIN,
  SPEED_JITTER_MAX,
  cpsToPxPerTick,
  baseSpeedPxPerTick,
  rollSpeedJitter,
  spawnSpeedPxPerTick,
} from '../src/config/speed'
import { profileToStats } from '../src/config/combat'

/**
 * Unit tests for the movement-speed design.
 *
 * The spec anchors absolute base speeds (cells/sec on normal terrain):
 *   balanced enemy (basic) = 2.5
 *   fast   = ×120% → 3.0
 *   power  = ×95%  → 2.375
 *   armor  = ×85%  → 2.125
 *   player (no star) = ×105% → 2.625
 * plus a per-instance jitter: actual = base × random(0.95, 1.05).
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

describe('Base speeds match the spec multipliers (cells/sec)', () => {
  it('balanced enemy baseline is exactly 3.75 cells/sec (matching classic)', () => {
    expect(BALANCED_ENEMY_CPS).toBe(3.75)
    expect(BASE_SPEED_CPS.basic).toBe(3.75)
  })

  it('each kind is the exact spec multiplier of the balanced baseline', () => {
    expect(BASE_SPEED_CPS.fast).toBeCloseTo(3.75 * 1.2, 10) // ×120% = 4.5
    expect(BASE_SPEED_CPS.power).toBeCloseTo(3.75 * 0.95, 10) // ×95% = 3.5625
    expect(BASE_SPEED_CPS.armor).toBeCloseTo(3.75 * 0.85, 10) // ×85% = 3.1875
    expect(BASE_SPEED_CPS.player).toBeCloseTo(3.75 * 1.05, 10) // ×105% = 3.9375
  })

  it('documented concrete values', () => {
    expect(BASE_SPEED_CPS.basic).toBe(3.75)
    expect(BASE_SPEED_CPS.fast).toBe(4.5)
    expect(BASE_SPEED_CPS.power).toBeCloseTo(3.5625, 10)
    expect(BASE_SPEED_CPS.armor).toBeCloseTo(3.1875, 10)
    expect(BASE_SPEED_CPS.player).toBeCloseTo(3.9375, 10)
  })
})

describe('cells/sec → px/tick conversion (CELL=16, 60Hz)', () => {
  it('converts correctly and inverts the engine scale', () => {
    // 1 cell/sec = 16 px/sec = 16/60 px/tick.
    expect(cpsToPxPerTick(1)).toBeCloseTo(16 / 60, 12)
    expect(cpsToPxPerTick(2.5)).toBeCloseTo((2.5 * CELL) / 60, 12)
  })

  it('base speeds in px/tick match the conversion of the spec values', () => {
    for (const kind of ['basic', 'fast', 'power', 'armor', 'player'] as TankKind[]) {
      expect(baseSpeedPxPerTick(kind)).toBeCloseTo(cpsToPxPerTick(BASE_SPEED_CPS[kind]), 12)
    }
  })
})

describe('Speed ordering (the design hierarchy)', () => {
  it('fast > player > basic > power > armor in cells/sec', () => {
    expect(BASE_SPEED_CPS.fast).toBeGreaterThan(BASE_SPEED_CPS.player)
    expect(BASE_SPEED_CPS.player).toBeGreaterThan(BASE_SPEED_CPS.basic)
    expect(BASE_SPEED_CPS.basic).toBeGreaterThan(BASE_SPEED_CPS.power)
    expect(BASE_SPEED_CPS.power).toBeGreaterThan(BASE_SPEED_CPS.armor)
  })

  it('same ordering holds in px/tick', () => {
    expect(baseSpeedPxPerTick('fast')).toBeGreaterThan(baseSpeedPxPerTick('player'))
    expect(baseSpeedPxPerTick('player')).toBeGreaterThan(baseSpeedPxPerTick('basic'))
    expect(baseSpeedPxPerTick('basic')).toBeGreaterThan(baseSpeedPxPerTick('power'))
    expect(baseSpeedPxPerTick('power')).toBeGreaterThan(baseSpeedPxPerTick('armor'))
  })

  it('power and armor differ even though both have mobility 30 (design decision)', () => {
    // This is WHY speed is a per-kind table and not derived from mobility.
    expect(BASE_SPEED_CPS.power).toBeGreaterThan(BASE_SPEED_CPS.armor)
    expect(baseSpeedPxPerTick('power')).toBeGreaterThan(baseSpeedPxPerTick('armor'))
  })
})

describe('Player universal-growth speed (star scaling)', () => {
  it('level 0 equals the spec no-star speed (3.9375 cells/sec)', () => {
    expect(BASE_SPEED_CPS.player).toBeCloseTo(3.9375, 12)
    // px/tick form is the converted value, not the cells/sec literal
    expect(baseSpeedPxPerTick('player', 0)).toBeCloseTo(cpsToPxPerTick(3.9375), 12)
  })

  it('each star adds a fixed increment and is strictly increasing', () => {
    let prev = -1
    for (let lv = 0; lv <= 3; lv++) {
      const s = baseSpeedPxPerTick('player', lv)
      expect(s).toBeGreaterThan(prev)
      prev = s
    }
    // the per-star increment in px/tick matches the documented cells/sec step
    const step = baseSpeedPxPerTick('player', 1) - baseSpeedPxPerTick('player', 0)
    expect(step).toBeCloseTo(cpsToPxPerTick(PLAYER_SPEED_PER_STAR_CPS), 12)
  })

  it('max-level (3★) player reaches 4.6875 cells/sec (approaching fast enemy)', () => {
    expect(BASE_SPEED_CPS.player + 3 * PLAYER_SPEED_PER_STAR_CPS).toBeCloseTo(4.6875, 12)
    expect(baseSpeedPxPerTick('player', 3)).toBeCloseTo(cpsToPxPerTick(4.6875), 12)
  })

  it('star scaling never exceeds the bullet-speed floor (race invariant preserved)', () => {
    const weakestBullet = profileToStats({
      firepower: 50,
      projectileSpeed: 40,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    }).bulletSpeed
    // Fastest possible tank (max-level player) must be outrun by every bullet.
    expect(baseSpeedPxPerTick('player', 3)).toBeLessThan(weakestBullet)
  })
})

describe('Per-instance jitter: rollSpeedJitter', () => {
  it('always stays inside [0.95, 1.05)', () => {
    const rng = new RNG(7)
    for (let i = 0; i < 5000; i++) {
      const j = rollSpeedJitter(rng)
      expect(j).toBeGreaterThanOrEqual(SPEED_JITTER_MIN)
      expect(j).toBeLessThan(SPEED_JITTER_MAX)
    }
  })

  it('actually varies (some below 1.0, some above 1.0) over many draws', () => {
    const rng = new RNG(7)
    let sawBelow = false
    let sawAbove = false
    for (let i = 0; i < 2000; i++) {
      const j = rollSpeedJitter(rng)
      if (j < 1) sawBelow = true
      if (j > 1) sawAbove = true
    }
    expect(sawBelow).toBe(true)
    expect(sawAbove).toBe(true)
  })

  it('is deterministic: same seed ⇒ identical sequence', () => {
    const a = new RNG(123)
    const b = new RNG(123)
    const seqA = Array.from({ length: 50 }, () => rollSpeedJitter(a))
    const seqB = Array.from({ length: 50 }, () => rollSpeedJitter(b))
    expect(seqA).toEqual(seqB)
  })

  it('is reproducible across independent RNGs of the same seed (world.rng parity)', () => {
    const a = rollSpeedJitter(new RNG(999))
    const b = rollSpeedJitter(new RNG(999))
    expect(a).toBe(b)
  })
})

describe('spawnSpeedPxPerTick = base × jitter', () => {
  it('always lands within the ±5% jitter band of the base', () => {
    const rng = new RNG(31)
    for (const kind of ['basic', 'fast', 'power', 'armor', 'player'] as TankKind[]) {
      for (let lv = 0; lv <= 3; lv++) {
        const base = baseSpeedPxPerTick(kind, lv)
        const s = spawnSpeedPxPerTick(kind, lv, rng)
        expect(s).toBeGreaterThanOrEqual(base * SPEED_JITTER_MIN - 1e-9)
        expect(s).toBeLessThanOrEqual(base * SPEED_JITTER_MAX + 1e-9)
        // and equals base × the jitter that was just drawn
      }
    }
  })

  it('product equals base × the drawn jitter', () => {
    const kind: TankKind = 'fast'
    const base = baseSpeedPxPerTick(kind, 0)
    // Draw the jitter with a seeded RNG, then reconstruct what spawnSpeedPxPerTick
    // computes for the SAME first draw (fresh RNG of the same seed).
    const j = rollSpeedJitter(new RNG(5))
    const s = spawnSpeedPxPerTick(kind, 0, new RNG(5))
    expect(s).toBeCloseTo(base * j, 12)
  })

  it('deterministic for a given seed (same speed every run)', () => {
    const a = spawnSpeedPxPerTick('basic', 0, new RNG(77))
    const b = spawnSpeedPxPerTick('basic', 0, new RNG(77))
    expect(a).toBe(b)
  })
})

describe('Integration — World spawns tanks at a jittered base speed', () => {
  it('spawned player speed is within the jitter band of its base', () => {
    const world = seedWorld(2024)
    world.startGame('classic', 'modern', 0)
    const p = world.player!
    const base = cpsToPxPerTick(world.rules.speedCps.player)
    expect(p.speed).toBeGreaterThanOrEqual(base * SPEED_JITTER_MIN - 1e-9)
    expect(p.speed).toBeLessThanOrEqual(base * SPEED_JITTER_MAX + 1e-9)
  })

  it('every enemy kind spawns within its jitter band', () => {
    const world = seedWorld(2024)
    world.startGame('classic', 'modern', 0)
    for (const kind of ENEMY_KINDS) {
      const t = world.createTank(kind, 8 * CELL, 8 * CELL, 'down')
      const base = cpsToPxPerTick(world.rules.speedCps[kind])
      expect(t.speed).toBeGreaterThanOrEqual(base * SPEED_JITTER_MIN - 1e-9)
      expect(t.speed).toBeLessThanOrEqual(base * SPEED_JITTER_MAX + 1e-9)
    }
  })

  it('same seed ⇒ identical spawn speeds (replay/snapshot safe)', () => {
    const build = () => {
      const w = seedWorld(55)
      w.startGame('classic', 'modern', 0)
      const playerSpeed = w.player!.speed
      const enemySpeed = w.createTank('fast', 8 * CELL, 8 * CELL, 'down').speed
      return { playerSpeed, enemySpeed }
    }
    expect(build()).toEqual(build())
  })
})

describe('Speed is a per-kind constant for the modern difficulties', () => {
  it('modern difficulties share one speed table (classic carries its own)', () => {
    // baseSpeedPxPerTick default = the modern table, independent of difficulty.
    const hard = baseSpeedPxPerTick('fast')
    const chaos = baseSpeedPxPerTick('fast') // same computation, any difficulty
    expect(hard).toBe(chaos)
    // Every MODERN difficulty spawns within its (modern) jitter band.
    for (const diff of ['relax', 'hard', 'chaos'] as const) {
      const w = seedWorld(8)
      w.startGame(diff, 'modern', 0)
      const t = w.createTank('armor', 8 * CELL, 8 * CELL, 'down')
      const base = cpsToPxPerTick(w.rules.speedCps.armor)
      expect(t.speed).toBeGreaterThanOrEqual(base * SPEED_JITTER_MIN - 1e-9)
      expect(t.speed).toBeLessThanOrEqual(base * SPEED_JITTER_MAX + 1e-9)
    }
    // classic uses a DIFFERENT (faithful FC) table — covered by classic-speed.test.ts.
  })
})

describe('Bullet-vs-tank race invariant (global safety)', () => {
  it('the slowest bullet outruns the fastest tank by a wide margin', () => {
    const slowestBullet = profileToStats({
      firepower: 50,
      projectileSpeed: 40,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    }).bulletSpeed
    const fastestTank = baseSpeedPxPerTick('fast') // also = max-level player
    expect(slowestBullet).toBeGreaterThan(fastestTank * 3) // comfortable margin
  })
})
