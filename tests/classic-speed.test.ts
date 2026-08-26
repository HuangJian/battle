import { seedWorld } from './helpers'
/**
 * Classic movement speed — faithful 1985 FC Battle City.
 *
 * Before this test, classic shared the SAME speed table as modern
 * (config/speed.ts) and only disabled jitter: classic's `GameplayRules` had no
 * speed field, and the table was a modern invention (differentiated per kind,
 * ~30% faster than FC). Now classic carries the FC table via `rules.speedCps`,
 * while modern keeps the differentiated table — fully config-driven, no modern
 * control logic leaks into classic.
 *
 * FC reference speeds (px/frame @60fps → cells/sec, CELL=16):
 *   Conversion FC px/frame → cells/sec uses a ×7.5 factor (see rules.ts note:
 *   FC px/frame ×60 ÷16 ×2). So: basic 0.5 → 3.75   fast 1.0 → 7.5
 *   power 0.5 → 3.75   armor 0.5 → 3.75   player T1 0.5 → 3.75   T4 1.0 → 7.5
 */

import { describe, it, expect } from 'bun:test'

import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { profileToStats } from '../src/config/combat'
import { cpsToPxPerTick } from '../src/config/speed'
import type { TankKind } from '../src/types'

const FC_CPS: Record<TankKind, number> = {
  basic: 3.75,
  fast: 7.5,
  power: 3.75,
  armor: 3.75,
  player: 3.75,
}

function classicRules() {
  const w = seedWorld(1)
  new Simulation(w, new Input())
  w.startGame('classic', 'modern', 0)
  return w.rules
}

describe('classic movement speed — faithful FC table via config', () => {
  it('classic speedCps table equals the FC cells/sec values', () => {
    const r = classicRules()
    for (const k of ['basic', 'fast', 'power', 'armor', 'player'] as TankKind[]) {
      expect(r.speedCps[k]).toBeCloseTo(FC_CPS[k], 6)
    }
  })

  it('FC basic/power/armor move at the SAME speed (FC has them equal)', () => {
    const r = classicRules()
    expect(r.speedCps.basic).toBeCloseTo(r.speedCps.power, 6)
    expect(r.speedCps.basic).toBeCloseTo(r.speedCps.armor, 6)
    // …and fast is exactly 2× the others (FC fast = 1.0, others = 0.5 px/frame).
    expect(r.speedCps.fast).toBeCloseTo(r.speedCps.basic * 2, 6)
  })

  it('profileToStats yields the FC px/frame speeds (px/tick) in classic', () => {
    const r = classicRules()
    for (const k of ['basic', 'fast', 'power', 'armor'] as TankKind[]) {
      const sp = profileToStats({} as never, k, 0, r).speed
      expect(sp).toBeCloseTo(cpsToPxPerTick(FC_CPS[k]), 6)
    }
  })

  it('classic player scales T1→T4 from 3.75 to 7.5 cps', () => {
    const r = classicRules()
    expect(profileToStats({} as never, 'player', 0, r).speed).toBeCloseTo(cpsToPxPerTick(3.75), 6)
    expect(profileToStats({} as never, 'player', 3, r).speed).toBeCloseTo(cpsToPxPerTick(7.5), 6)
  })

  it('modern balanced speed matches classic (both 3.75 cps, same baseline)', () => {
    const classic = classicRules()
    const modern = (() => {
      const w = seedWorld(1)
      new Simulation(w, new Input())
      w.startGame('hard', 'modern', 0)
      return w.rules
    })()
    // Modern now matches classic's balanced speed for comparable combat pace
    expect(modern.speedCps.basic).toBeCloseTo(classic.speedCps.basic, 6)
    // modern differentiates basic/power/armor; FC keeps them equal.
    expect(modern.speedCps.basic).not.toBeCloseTo(modern.speedCps.power, 6)
  })
})
