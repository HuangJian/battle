import { seedWorld } from './helpers'
/**
 * Classic bullet speed — faithful 1985 FC Battle City.
 *
 * Before this test, the IN-FLIGHT bullet speed (computed at fire time via
 * `spawnBulletSpeedPxPerTick`) ignored `world.rules` and always used the modern
 * `BASE_BULLET_SPEED_CPS` table — so classic bullets fired at modern speed. Now
 * classic carries the FC bullet table via `rules.bulletSpeedCps`, consumed both
 * in `profileToStats` (the tank stat) AND at fire time (`spawnBulletSpeedPxPerTick`),
 * while modern keeps the differentiated ×4 table — fully config-driven, no
 * modern control logic leaks into classic.
 *
 * FC reference bullet speeds (px/frame @60fps → cells/sec, CELL=16):
 *   Conversion FC px/frame → cells/sec uses a ×7.5 factor (see rules.ts note:
 *   FC px/frame ×60 ÷16 ×2). Per the NES ROM (and a faithful stat table):
 *     basic/fast/armor/player = 2 px/frame → 15 cps (the "slow" projectile)
 *     power                    = 4 px/frame → 30 cps (the "fast" projectile)
 *   The player's base bullet is 2 px/frame; the 1★ star (`fastBullet` perk)
 *   jumps it to 4 px/frame (30 cps) and it stays for every higher star level.
 */

import { describe, it, expect } from 'bun:test'

import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { profileToStats } from '../src/config/combat'
import { cpsToPxPerTick, spawnBulletSpeedPxPerTick } from '../src/config/speed'
import { hasStarPerk } from '../src/config/rules'
import type { TankKind } from '../src/types'

const FC_BULLET_CPS: Record<TankKind, number> = {
  basic: 15,
  fast: 15,
  power: 30,
  armor: 15,
  player: 15,
}

function classicRules() {
  const w = seedWorld(1)
  new Simulation(w, new Input())
  w.startGame('classic', 'modern', 0)
  return w.rules
}

function modernRules() {
  const w = seedWorld(1)
  new Simulation(w, new Input())
  w.startGame('hard', 'modern', 0)
  return w.rules
}

describe('classic bullet speed — faithful FC table via config', () => {
  it('classic bulletSpeedCps table equals the FC cells/sec values', () => {
    const r = classicRules()
    for (const k of ['basic', 'fast', 'power', 'armor', 'player'] as TankKind[]) {
      expect(r.bulletSpeedCps[k]).toBeCloseTo(FC_BULLET_CPS[k], 6)
    }
  })

  it('FC Power fires exactly twice as fast as every other tank (4 vs 2 px/frame)', () => {
    const r = classicRules()
    expect(r.bulletSpeedCps.power).toBeCloseTo(r.bulletSpeedCps.basic * 2, 6)
    expect(r.bulletSpeedCps.basic).toBeCloseTo(r.bulletSpeedCps.fast, 6)
    expect(r.bulletSpeedCps.basic).toBeCloseTo(r.bulletSpeedCps.armor, 6)
    expect(r.bulletSpeedCps.basic).toBeCloseTo(r.bulletSpeedCps.player, 6)
  })

  it('profileToStats yields the FC px/frame bullet speeds (px/tick) in classic', () => {
    const r = classicRules()
    for (const k of ['basic', 'fast', 'power', 'armor'] as TankKind[]) {
      const sp = profileToStats({} as never, k, 0, r).bulletSpeed
      expect(sp).toBeCloseTo(cpsToPxPerTick(FC_BULLET_CPS[k]), 6)
    }
  })

  it('classic fastBulletMult is 2.0 (1★ star → 2→4 px/frame, faithful FC)', () => {
    const r = classicRules()
    expect(r.fastBulletMult).toBeCloseTo(2.0, 6)
  })

  it('classic player base bullet is 15 cps, and the 1★ fastBullet jumps it to 30 cps', () => {
    const r = classicRules()
    expect(profileToStats({} as never, 'player', 0, r).bulletSpeed).toBeCloseTo(
      cpsToPxPerTick(15),
      6,
    )
    // The 1★ 'fastBullet' perk (cumulative) multiplies the base by fastBulletMult.
    expect(hasStarPerk(r, 1, 'fastBullet')).toBe(true)
    const base = profileToStats({} as never, 'player', 1, r).bulletSpeed
    expect(base * r.fastBulletMult).toBeCloseTo(cpsToPxPerTick(30), 6)
  })

  it('the fastBullet perk is cumulative — a 2★ player keeps the fast bullet (30 cps)', () => {
    const r = classicRules()
    // 2★ only *introduces* doubleShot, but the fast bullet earned at 1★ persists.
    expect(hasStarPerk(r, 2, 'fastBullet')).toBe(true)
    const base = profileToStats({} as never, 'player', 2, r).bulletSpeed
    expect(base * r.fastBulletMult).toBeCloseTo(cpsToPxPerTick(30), 6)
  })

  it('the in-flight fired bullet respects classic rules (power → 30 cps, with jitter)', () => {
    const r = classicRules()
    const fired = spawnBulletSpeedPxPerTick(
      'power',
      0,
      7,
      123,
      r.bulletSpeedCps,
      r.playerBulletSpeedPerStarCps,
    )
    // jitter is within ±5%, so the base (cpsToPxPerTick(30)) is recovered within 5%.
    expect(fired).toBeGreaterThan(cpsToPxPerTick(30) * 0.95)
    expect(fired).toBeLessThan(cpsToPxPerTick(30) * 1.05)
  })

  it('modern balanced bullet speed matches classic (both 15 cps, same ×4 ratio)', () => {
    const classic = classicRules()
    const modern = modernRules()
    // Modern now matches classic's balanced speed for comparable combat pace
    expect(modern.bulletSpeedCps.basic).toBeCloseTo(classic.bulletSpeedCps.basic, 6)
    // the bullet:tank speed RATIO is the same 4× in both (faithful geometry).
    expect(classic.bulletSpeedCps.basic / classic.speedCps.basic).toBeCloseTo(4, 6)
    expect(modern.bulletSpeedCps.basic / modern.speedCps.basic).toBeCloseTo(4, 6)
  })
})
