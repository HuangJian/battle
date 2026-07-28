import { describe, it, expect } from 'bun:test'
import {
  BALANCED_BULLET_TRAVEL_TICKS,
  BALANCED_BULLET_TRAVEL_MS,
  BALANCED_FIRE_INTERVAL_MS,
  FIRE_FREQUENCY_MULTIPLIER,
  PLAYER_FIRE_FREQUENCY_PER_STAR,
  FIRE_JITTER_MIN,
  FIRE_JITTER_MAX,
  mapFireJitter,
  fireIntervalJitter,
  fireFrequencyMultiplier,
  baseFireIntervalMs,
  nextFireIntervalMs,
  idealizedBulletsInFlight,
} from '../src/config/fire-rate'
import type { TankKind } from '../src/types'

/** Helper: ms → ticks (60 Hz). */
const msToTicks = (ms: number) => (ms * 60) / 1000

/**
 * Fire-rate standard (user requirement, 2026-07-26).
 *
 * Core constraint: a *balanced* (basic) enemy firing straight down from the
 * top must have AT MOST 3 bullets on the vertical route — the 3rd is fired
 * exactly when the 1st reaches the bottom. That pins the balanced interval to
 * half the bullet's full-field travel time. Every other kind's interval is
 * that baseline divided by its firing-frequency multiplier:
 *   basic 1.00×, fast 1.05×, power 1.10×, armor 0.90×, player 1.05× (no star).
 * Every shot's NEXT interval is the base × random(0.95, 1.05).
 */
describe('Fire-rate standard — derived balanced baseline', () => {
  it('basic bullet crosses the full field in 104 ticks (3.75cps × 4 → 15cps → 104 ticks)', () => {
    expect(BALANCED_BULLET_TRAVEL_TICKS).toBe(104)
    // FIELD(416) / bulletSpeed(240/60) == 104 exactly.
    expect(BALANCED_BULLET_TRAVEL_TICKS).toBeCloseTo(416 / (240 / 60), 9)
  })

  it('full-field travel time is ~1733 ms', () => {
    expect(BALANCED_BULLET_TRAVEL_MS).toBeCloseTo(1733.33, 1)
  })

  it('balanced fire interval is exactly half the travel time (the 3-bullet constraint)', () => {
    expect(BALANCED_FIRE_INTERVAL_MS).toBeCloseTo(866.67, 1)
    expect(BALANCED_FIRE_INTERVAL_MS).toBeCloseTo(BALANCED_BULLET_TRAVEL_MS / 2, 9)
    // Two intervals span exactly the full-field travel: 1st fired → 3rd fired
    // == 1st reaches bottom.
    expect(2 * msToTicks(BALANCED_FIRE_INTERVAL_MS)).toBeCloseTo(BALANCED_BULLET_TRAVEL_TICKS, 9)
  })

  it('idealized bullets-in-flight on the vertical route equals the design target of 3', () => {
    expect(idealizedBulletsInFlight('basic')).toBe(3)
  })
})

describe('Fire-rate standard — per-kind frequency multipliers', () => {
  it('multipliers match the user spec exactly', () => {
    expect(FIRE_FREQUENCY_MULTIPLIER.basic).toBe(1.0)
    expect(FIRE_FREQUENCY_MULTIPLIER.fast).toBe(1.05)
    expect(FIRE_FREQUENCY_MULTIPLIER.power).toBe(1.1)
    expect(FIRE_FREQUENCY_MULTIPLIER.armor).toBe(0.9)
    expect(FIRE_FREQUENCY_MULTIPLIER.player).toBe(1.05)
  })

  it('player gains +0.05 per star on top of the no-star 1.05×', () => {
    expect(fireFrequencyMultiplier('player', 0)).toBeCloseTo(1.05, 9)
    expect(fireFrequencyMultiplier('player', 1)).toBeCloseTo(
      1.05 + PLAYER_FIRE_FREQUENCY_PER_STAR,
      9,
    )
    expect(fireFrequencyMultiplier('player', 3)).toBeCloseTo(
      1.05 + 3 * PLAYER_FIRE_FREQUENCY_PER_STAR,
      9,
    )
    // A max-level player out-rates even the power enemy (the strongest enemy).
    expect(fireFrequencyMultiplier('player', 3)).toBeGreaterThan(fireFrequencyMultiplier('power'))
  })

  it('base intervals are the balanced baseline divided by the multiplier', () => {
    expect(baseFireIntervalMs('basic')).toBeCloseTo(866.67 / 1.0, 0)
    expect(baseFireIntervalMs('fast')).toBeCloseTo(866.67 / 1.05, 0)
    expect(baseFireIntervalMs('power')).toBeCloseTo(866.67 / 1.1, 0)
    expect(baseFireIntervalMs('armor')).toBeCloseTo(866.67 / 0.9, 0)
    expect(baseFireIntervalMs('player', 0)).toBeCloseTo(866.67 / 1.05, 0)
    expect(baseFireIntervalMs('player', 1)).toBeCloseTo(866.67 / 1.1, 0)
    expect(baseFireIntervalMs('player', 3)).toBeCloseTo(866.67 / 1.2, 0)
  })

  it('ordering: armor slowest, then basic, then fast == no-star player, power fastest; max player beats all', () => {
    const armor = baseFireIntervalMs('armor')
    const basic = baseFireIntervalMs('basic')
    const fast = baseFireIntervalMs('fast')
    const player0 = baseFireIntervalMs('player', 0)
    const power = baseFireIntervalMs('power')
    const player3 = baseFireIntervalMs('player', 3)
    expect(armor).toBeGreaterThan(basic)
    expect(basic).toBeGreaterThan(fast)
    // fast and the no-star player share the 1.05× multiplier → identical cadence.
    expect(fast).toBeCloseTo(player0, 9)
    expect(fast).toBeGreaterThan(power)
    // Max-level player out-rates the power enemy.
    expect(player3).toBeLessThan(power)
  })
})

describe('Fire-rate standard — per-fire random jitter (random(0.95, 1.05))', () => {
  it('mapFireJitter maps [0,1) onto [FIRE_JITTER_MIN, FIRE_JITTER_MAX)', () => {
    expect(mapFireJitter(0)).toBeCloseTo(FIRE_JITTER_MIN, 9)
    expect(mapFireJitter(0.5)).toBeCloseTo((FIRE_JITTER_MIN + FIRE_JITTER_MAX) / 2, 9)
    expect(mapFireJitter(0.999)).toBeLessThan(FIRE_JITTER_MAX)
    expect(mapFireJitter(0.999)).toBeGreaterThanOrEqual(FIRE_JITTER_MIN)
    // Input is clamped into [0,1).
    expect(mapFireJitter(-5)).toBeCloseTo(FIRE_JITTER_MIN, 9)
    expect(mapFireJitter(5)).toBeCloseTo(FIRE_JITTER_MAX, 9)
  })

  it('fireIntervalJitter stays inside the band for any (id, frame)', () => {
    for (let id = 0; id < 10; id++) {
      for (let frame = 0; frame < 10; frame++) {
        const j = fireIntervalJitter(id, frame)
        expect(j).toBeGreaterThanOrEqual(FIRE_JITTER_MIN)
        expect(j).toBeLessThan(FIRE_JITTER_MAX)
      }
    }
  })

  it('fireIntervalJitter is deterministic: same (id, frame) ⇒ same jitter', () => {
    expect(fireIntervalJitter(7, 42)).toBe(fireIntervalJitter(7, 42))
    expect(fireIntervalJitter(123, 999)).toBe(fireIntervalJitter(123, 999))
  })

  it('fireIntervalJitter varies across distinct (id, frame) seeds', () => {
    const set = new Set<number>()
    for (let id = 1; id <= 8; id++) {
      for (let frame = 1; frame <= 8; frame++) set.add(fireIntervalJitter(id, frame))
    }
    // Not every seed collapses to one value.
    expect(set.size).toBeGreaterThan(1)
  })

  it('nextFireIntervalMs = base × jitter, and lies within [0.95, 1.05] × base', () => {
    const kind: TankKind = 'power'
    const base = baseFireIntervalMs(kind, 0)
    const next = nextFireIntervalMs(kind, 0, 11, 220)
    expect(next).toBeCloseTo(base * fireIntervalJitter(11, 220), 9)
    expect(next).toBeGreaterThanOrEqual(FIRE_JITTER_MIN * base - 1e-9)
    expect(next).toBeLessThanOrEqual(FIRE_JITTER_MAX * base + 1e-9)
  })

  it('jittered intervals average near the base over many seeds (±5% band is symmetric)', () => {
    const kind: TankKind = 'basic'
    const base = baseFireIntervalMs(kind)
    let sum = 0
    const N = 2000
    for (let i = 0; i < N; i++) {
      // Deterministic pseudo-seeds spanning the space.
      sum += nextFireIntervalMs(kind, 0, i + 1, i * 7 + 3)
    }
    const avg = sum / N
    // Mean jitter ≈ 1.0, so the mean interval ≈ base. Allow generous slack.
    expect(avg).toBeGreaterThan(base * 0.98)
    expect(avg).toBeLessThan(base * 1.02)
    // And every single sample stays inside the ±5% band.
    for (let i = 0; i < N; i++) {
      const v = nextFireIntervalMs(kind, 0, i + 1, i * 7 + 3)
      expect(v).toBeGreaterThanOrEqual(base * FIRE_JITTER_MIN - 1e-6)
      expect(v).toBeLessThanOrEqual(base * FIRE_JITTER_MAX + 1e-6)
    }
  })
})

describe('Fire-rate standard — behavioral cap (the 3-bullet lane)', () => {
  it('a balanced enemy firing straight down never has more than 3 of its bullets in the vertical lane', () => {
    // The constraint is expressed on the route length (full field). Per the
    // user spec the 3rd bullet is fired exactly when the 1st reaches the
    // bottom, i.e. 2 intervals == full-field travel. We assert the standard's
    // own idealized model (which uses the full route) yields exactly 3, and
    // that the multiplier-driven ordering keeps every other kind within the
    // same bound (longer base interval ⇒ fewer bullets).
    expect(idealizedBulletsInFlight('basic')).toBe(3)
    // Faster kinds (shorter interval) still respect the cap because the cap is
    // derived from the SAME travel time; a shorter interval can only reduce the
    // idealized count, never exceed 3.
    for (const kind of ['fast', 'power', 'armor', 'player'] as TankKind[]) {
      expect(idealizedBulletsInFlight(kind, kind === 'player' ? 3 : 0)).toBeLessThanOrEqual(3)
    }
  })
})
