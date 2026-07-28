import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'

/**
 * Locks in the classic GameplayRules profile and the wiring that puts it on
 * the World (plan/classic-faithful-feel.md). classic is the faithful FC-1985
 * profile; every other difficulty must use DEFAULT_RULES unchanged.
 */
describe('GameplayRules — classic profile vs default', () => {
  it('RULES.classic differs from DEFAULT_RULES on every key dimension', () => {
    const c = RULES.classic
    const d = DEFAULT_RULES
    expect(c.combatModel).toBe('instant')
    expect(d.combatModel).toBe('pool')
    expect(c.fireModel).toBe('bulletCap')
    expect(d.fireModel).toBe('cooldown')
    expect(c.starModel).toBe('functional')
    expect(d.starModel).toBe('universal')
    expect(c.superDropChance).toBe(0)
    expect(d.superDropChance).toBeGreaterThan(0)
    expect(c.allowedPowerups).not.toContain('boat')
    expect(d.allowedPowerups).toContain('boat')
    expect(c.dropSchedule).toBe('fixed')
    expect(d.dropSchedule).toBe('modern')
    expect(c.fixedDropKillIndices).toEqual([4, 11, 18])
    expect(c.speedJitter).toBe(false)
    expect(d.speedJitter).toBe(true)
    expect(c.scoreModel).toBe('byKind')
    expect(d.scoreModel).toBe('flat')
    expect(c.spawnIntervalMs).toBe(1800)
    expect(d.spawnIntervalMs).toBe(1500)
  })

  it('startGame wires the faithful classic rules onto the World', () => {
    const w = new World()
    w.startGame('classic', 'modern', 0)
    expect(w.rules).toBe(RULES.classic)
    expect(w.rules.combatModel).toBe('instant')
    // Non-classic modes use DEFAULT_RULES (byte-identical to today).
    w.startGame('hard', 'modern', 0)
    expect(w.rules).toBe(DEFAULT_RULES)
  })

  it('snapshot rewind never clobbers world.rules (issue #3, by design)', () => {
    // Design decision: rules are set ONCE per run by startGame and are
    // constant for the run's lifetime, so restoreWorld deliberately does not
    // serialize/overwrite them — a rewind must leave the active profile
    // exactly as it was (see plan/classic-faithful-feel.md issue #3).
    const w = new World()
    w.startGame('classic', 'modern', 0)
    const snap = cloneWorld(w)
    // Simulate mid-run divergence of ordinary state, then rewind.
    w.score = 12345
    w.killCount = 17
    restoreWorld(w, snap)
    expect(w.score).toBe(0)
    expect(w.killCount).toBe(0)
    // The faithful profile survived the rewind untouched.
    expect(w.rules).toBe(RULES.classic)
    expect(w.rules.combatModel).toBe('instant')
    // And the snapshot itself carries no rules payload to drift out of sync.
    expect('rules' in snap).toBe(false)
  })
})
