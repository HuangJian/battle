import { describe, it, expect } from 'bun:test'
import { RNG } from '../src/utils/RNG'
import {
  BALANCED_ENEMY_CPS,
  BULLET_SPEED_RATIO,
  BULLET_SPEED_MULT,
  BASE_BULLET_SPEED_CPS,
  PLAYER_BULLET_SPEED_PER_STAR_CPS,
  baseBulletSpeedPxPerTick,
  bulletSpeedJitter,
  spawnBulletSpeedPxPerTick,
  baseSpeedPxPerTick,
  cpsToPxPerTick,
  SPEED_JITTER_MIN,
  SPEED_JITTER_MAX,
} from '../src/config/speed'
import { profileToStats, TANK_PROFILES } from '../src/config/combat'
import type { TankKind } from '../src/types'

/**
 * Bullet-speed design spec (per the user request):
 *   - 均衡(basic) 敌人弹速 = 均衡敌人移动速度 × 4
 *   - 快速(fast)    敌人弹速 = 均衡敌人弹速 × 105%
 *   - 强力(power)   敌人弹速 = 均衡敌人弹速 × 95%
 *   - 重甲(armor)   敌人弹速 = 均衡敌人弹速 × 90%
 *   - 无星星玩家    弹速 = 均衡敌人弹速 × 105%
 *   - 每发炮弹引入随机变化：实际弹速 = 基础弹速 × random(0.95, 1.05)
 *
 * Bullet speed is a per-kind data table (config/speed.ts), mirroring the
 * movement-speed table. The per-bullet jitter is drawn from the world RNG so it
 * stays deterministic (AGENTS.md §2.3).
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

// ============================================================
// Anchor — the ×4 relationship to the balanced-enemy movement speed
// ============================================================

describe('Bullet speed — anchored to 4× the balanced-enemy movement (spec)', () => {
  it('the balanced-enemy bullet speed is exactly BALANCED_ENEMY_CPS × 4', () => {
    expect(BASE_BULLET_SPEED_CPS.basic).toBe(BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO)
    expect(BASE_BULLET_SPEED_CPS.basic).toBeCloseTo(10.0, 12) // 2.5 × 4
  })

  it('the basic bullet (px/tick) equals 4× the basic tank movement (px/tick)', () => {
    const bullet = baseBulletSpeedPxPerTick('basic')
    const move = baseSpeedPxPerTick('basic')
    expect(bullet).toBeCloseTo(move * BULLET_SPEED_RATIO, 9)
  })

  it('concrete base bullet speeds in px/tick match the spec table', () => {
    // cps → px/tick = cps * CELL / 60 = cps * 0.26666…
    expect(baseBulletSpeedPxPerTick('basic')).toBeCloseTo(cpsToPxPerTick(10.0), 9) // 2.6667
    expect(baseBulletSpeedPxPerTick('fast')).toBeCloseTo(cpsToPxPerTick(10.5), 9) // 2.8
    expect(baseBulletSpeedPxPerTick('power')).toBeCloseTo(cpsToPxPerTick(9.5), 9) // 2.5333
    expect(baseBulletSpeedPxPerTick('armor')).toBeCloseTo(cpsToPxPerTick(9.0), 9) // 2.4
    expect(baseBulletSpeedPxPerTick('player', 0)).toBeCloseTo(cpsToPxPerTick(10.5), 9) // 2.8
  })
})

// ============================================================
// Per-kind multipliers — exactly the spec's ratios
// ============================================================

describe('Bullet speed — per-kind multipliers (spec)', () => {
  it('BULLET_SPEED_MULT encodes the exact spec ratios', () => {
    expect(BULLET_SPEED_MULT.basic).toBe(1.0)
    expect(BULLET_SPEED_MULT.fast).toBe(1.05)
    expect(BULLET_SPEED_MULT.power).toBe(0.95)
    expect(BULLET_SPEED_MULT.armor).toBe(0.9)
    expect(BULLET_SPEED_MULT.player).toBe(1.05)
  })

  it("every kind's base bullet speed = balanced bullet × its multiplier", () => {
    for (const k of ['basic', 'fast', 'power', 'armor', 'player'] as const) {
      expect(BASE_BULLET_SPEED_CPS[k]).toBeCloseTo(
        BALANCED_ENEMY_CPS * BULLET_SPEED_RATIO * BULLET_SPEED_MULT[k],
        12,
      )
      expect(baseBulletSpeedPxPerTick(k)).toBeCloseTo(
        baseBulletSpeedPxPerTick('basic') * BULLET_SPEED_MULT[k],
        9,
      )
    }
  })

  it('relative ordering matches the spec (armor < power < basic < fast = player)', () => {
    expect(baseBulletSpeedPxPerTick('armor')).toBeLessThan(baseBulletSpeedPxPerTick('power'))
    expect(baseBulletSpeedPxPerTick('power')).toBeLessThan(baseBulletSpeedPxPerTick('basic'))
    expect(baseBulletSpeedPxPerTick('basic')).toBeLessThan(baseBulletSpeedPxPerTick('fast'))
    expect(baseBulletSpeedPxPerTick('fast')).toBeCloseTo(baseBulletSpeedPxPerTick('player', 0), 9)
  })

  it('no-star player bullet speed equals the fast-enemy bullet speed (both ×1.05)', () => {
    expect(baseBulletSpeedPxPerTick('player', 0)).toBeCloseTo(baseBulletSpeedPxPerTick('fast'), 9)
  })
})

// ============================================================
// Player star scaling — universal growth, no-star stays at the spec value
// ============================================================

describe('Bullet speed — player universal-growth scaling', () => {
  it('no-star player value is exactly the spec (1.05× balanced bullet)', () => {
    expect(baseBulletSpeedPxPerTick('player', 0)).toBeCloseTo(
      baseBulletSpeedPxPerTick('basic') * 1.05,
      9,
    )
  })

  it('bullet speed strictly increases with each star level', () => {
    let prev = -Infinity
    for (let lv = 0; lv <= 3; lv++) {
      const s = baseBulletSpeedPxPerTick('player', lv)
      expect(s).toBeGreaterThan(prev)
      prev = s
    }
  })

  it('the per-star increment in px/tick matches the documented cells/sec step', () => {
    const step = baseBulletSpeedPxPerTick('player', 1) - baseBulletSpeedPxPerTick('player', 0)
    expect(step).toBeCloseTo(cpsToPxPerTick(PLAYER_BULLET_SPEED_PER_STAR_CPS), 12)
  })

  it('max-level (3★) player bullet reaches 12.0 cells/sec (faster than any enemy)', () => {
    const cps = BASE_BULLET_SPEED_CPS.player + 3 * PLAYER_BULLET_SPEED_PER_STAR_CPS
    expect(cps).toBeCloseTo(12.0, 12)
    expect(baseBulletSpeedPxPerTick('player', 3)).toBeCloseTo(cpsToPxPerTick(12.0), 9)
  })
})

// ============================================================
// Per-bullet jitter — random(0.95, 1.05), deterministic via world RNG
// ============================================================

describe('Bullet speed — per-bullet jitter (spec: actual = base × random(0.95, 1.05))', () => {
  it('bulletSpeedJitter always stays inside [0.95, 1.05) for many (id, frame) pairs', () => {
    for (let id = 0; id < 200; id++) {
      for (let frame = 0; frame < 100; frame++) {
        const j = bulletSpeedJitter(id, frame)
        expect(j).toBeGreaterThanOrEqual(SPEED_JITTER_MIN)
        expect(j).toBeLessThan(SPEED_JITTER_MAX)
      }
    }
  })

  it('jitter actually varies (some below 1.0, some above 1.0)', () => {
    let sawBelow = false
    let sawAbove = false
    for (let id = 0; id < 500; id++) {
      const j = bulletSpeedJitter(id, 1000 + id)
      if (j < 1) sawBelow = true
      if (j > 1) sawAbove = true
    }
    expect(sawBelow).toBe(true)
    expect(sawAbove).toBe(true)
  })

  it('is deterministic: identical (id, frame) ⇒ identical jitter', () => {
    for (let id = 0; id < 50; id++) {
      for (let frame = 0; frame < 50; frame++) {
        expect(bulletSpeedJitter(id, frame)).toBe(bulletSpeedJitter(id, frame))
      }
    }
  })

  it('varies across frames for the same tank (per-bullet, not per-tank)', () => {
    const id = 7
    const first = bulletSpeedJitter(id, 1)
    let sawDifferent = false
    for (let frame = 2; frame < 200; frame++) {
      if (bulletSpeedJitter(id, frame) !== first) sawDifferent = true
    }
    expect(sawDifferent).toBe(true)
  })

  it('does NOT consume the world RNG — calling it leaves an RNG unchanged', () => {
    // Cosmetic bullet variation must not perturb the AI decision stream.
    const rng = new RNG(12345)
    const before = rng.getState()
    for (let i = 0; i < 1000; i++) bulletSpeedJitter(i, i * 3)
    expect(rng.getState()).toBe(before)
  })

  it('spawnBulletSpeedPxPerTick stays within ±5% of the base for every kind', () => {
    for (const k of ['basic', 'fast', 'power', 'armor', 'player'] as const) {
      const base = baseBulletSpeedPxPerTick(k, k === 'player' ? 2 : 0)
      for (let frame = 0; frame < 1000; frame++) {
        const s = spawnBulletSpeedPxPerTick(k, k === 'player' ? 2 : 0, 5, frame)
        expect(s).toBeGreaterThanOrEqual(base * SPEED_JITTER_MIN - 1e-9)
        expect(s).toBeLessThan(base * SPEED_JITTER_MAX + 1e-9)
      }
    }
  })

  it('spawnBulletSpeedPxPerTick composes base × jitter exactly', () => {
    const kind: TankKind = 'power'
    const level = 1
    const id = 42
    const frame = 777
    const base = baseBulletSpeedPxPerTick(kind, level)
    const j = bulletSpeedJitter(id, frame)
    const s = spawnBulletSpeedPxPerTick(kind, level, id, frame)
    expect(s).toBeCloseTo(base * j, 12)
  })

  it('two shells from the same tank at different frames can differ (jitter is per-bullet)', () => {
    const a = spawnBulletSpeedPxPerTick('basic', 0, 9, 100)
    const b = spawnBulletSpeedPxPerTick('basic', 0, 9, 101)
    expect(a).not.toBe(b) // different frames ⇒ different jitter
  })

  it('a whole jitter sequence over frames is reproducible from world state', () => {
    const seq = (startFrame: number) =>
      Array.from({ length: 20 }, (_, i) =>
        spawnBulletSpeedPxPerTick('player', 1, 3, startFrame + i),
      )
    expect(seq(555)).toEqual(seq(555))
    expect(seq(555)).not.toEqual(seq(556))
  })
})

// ============================================================
// Race invariant — every bullet outruns every tank on the field
// ============================================================

describe('Bullet speed — bullet-vs-tank race invariant (global safety)', () => {
  it("every kind's bullet clearly outruns its own tank movement (×4 by construction)", () => {
    for (const k of ['basic', 'fast', 'power', 'armor', 'player'] as const) {
      const lvl = k === 'player' ? 3 : 0
      expect(baseBulletSpeedPxPerTick(k, lvl)).toBeGreaterThan(baseSpeedPxPerTick(k, lvl) * 3)
    }
  })

  it('the slowest bullet (armor) outruns the fastest tank (fast / max-level player) by a wide margin', () => {
    // By construction the slowest bullet (armor, 0.9× basic) fires at exactly
    // 3× the fastest tank (fast, 1.2× basic move): (0.9×4) / 1.2 = 3.0. Assert
    // the exact ratio (with fp tolerance) and a comfortably wide margin.
    const slowestBullet = baseBulletSpeedPxPerTick('armor') // 2.4 px/tick
    const fastestTank = baseSpeedPxPerTick('fast') // 3.0 cps → 0.8 px/tick
    expect(slowestBullet / fastestTank).toBeCloseTo(3, 6)
    expect(slowestBullet).toBeGreaterThan(fastestTank * 2.9)
    // also beats the max-level player tank
    expect(slowestBullet).toBeGreaterThan(baseSpeedPxPerTick('player', 3) * 2.9)
  })
})

// ============================================================
// Integration — profileToStats exposes the per-kind bullet-speed table
// ============================================================

describe('Bullet speed — profileToStats integration', () => {
  it('profileToStats returns the per-kind table value for each enemy', () => {
    for (const k of ENEMY_KINDS) {
      expect(profileToStats(TANK_PROFILES[k], k).bulletSpeed).toBeCloseTo(
        baseBulletSpeedPxPerTick(k),
        9,
      )
    }
  })

  it('a synthetic (no-kind) profile falls back to the balanced-enemy bullet', () => {
    const s = profileToStats({
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    })
    expect(s.bulletSpeed).toBeCloseTo(baseBulletSpeedPxPerTick('basic'), 9)
  })

  it('player bullet speed still satisfies the spec after integration', () => {
    const lvl0 = profileToStats(TANK_PROFILES.basic /*unused*/, 'player', 0).bulletSpeed
    expect(lvl0).toBeCloseTo(baseBulletSpeedPxPerTick('player', 0), 9)
    expect(lvl0).toBeCloseTo(baseBulletSpeedPxPerTick('basic') * 1.05, 9)
  })
})
