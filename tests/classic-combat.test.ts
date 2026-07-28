import { describe, it, expect } from 'bun:test'
import { profileToStats, resolveProfile } from '../src/config/combat'
import { RULES, DEFAULT_RULES } from '../src/config/rules'

/**
 * Classic 'instant' combat model (plan Phase 1). Every bullet deals a flat
 * `referenceDamage` (100); HP = hitsToKill × referenceDamage. This makes the
 * fast enemy (whose pool-model damage would be only 72) one-shot too — the
 * issue #2 fix.
 */
describe('Classic combat — instant TTK model', () => {
  const classic = RULES.classic

  it('basic and player die in one hit; damage is flat referenceDamage', () => {
    const basic = profileToStats(resolveProfile('basic'), 'basic', 0, classic)
    expect(basic.maxHp).toBe(100)
    expect(basic.damage).toBe(100)

    const player = profileToStats(resolveProfile('player'), 'player', 0, classic)
    expect(player.maxHp).toBe(100)
    expect(player.damage).toBe(100)
  })

  it('armor takes exactly 4 hits (400 HP)', () => {
    const armor = profileToStats(resolveProfile('armor'), 'armor', 0, classic)
    expect(armor.maxHp).toBe(400)
    expect(armor.damage).toBe(100) // flat — same as every other shooter
  })

  it('fast enemy also one-shots (flat damage fixes issue #2)', () => {
    const fast = profileToStats(resolveProfile('fast'), 'fast', 0, classic)
    expect(fast.maxHp).toBe(100)
    expect(fast.damage).toBe(100)
  })

  it('power enemy one-shots the player (HP 100)', () => {
    const power = profileToStats(resolveProfile('power'), 'power', 0, classic)
    expect(power.maxHp).toBe(100)
    expect(power.damage).toBe(100)
  })

  it('modern default still uses the pool model (regression)', () => {
    const basic = profileToStats(resolveProfile('basic'), 'basic', 0, DEFAULT_RULES)
    expect(basic.maxHp).toBe(250) // 50 armor × 5
    expect(basic.damage).toBe(100) // 50 firepower × 2
  })
})
