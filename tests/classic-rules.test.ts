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

  it('snapshot rewind preserves the run profile (difficulty travels in the snapshot)', () => {
    // The profile (rules/difficulty/theme) is now part of the snapshot via
    // difficultyKey/themeKey, so restoreWorld restores it atomically. A rewind
    // leaves the active profile exactly as it was when the snapshot was taken.
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
    expect(w.difficultyKey).toBe('classic')
    expect(w.rules).toBe(RULES.classic)
    expect(w.rules.combatModel).toBe('instant')
    // The snapshot stores the profile keys (not the rules object itself).
    expect(snap.difficultyKey).toBe('classic')
    expect('rules' in snap).toBe(false)
  })

  it('loading a classic save restores classic rules even when the World holds modern rules (bug fix)', () => {
    // Reproduces the reported bug: a classic save, when loaded into a World
    // whose current rules are the modern DEFAULT_RULES (e.g. the menu default),
    // must NOT silently apply modern rules. The snapshot carries its own
    // difficultyKey, so restoreWorld re-derives the faithful classic profile.
    const modern = new World()
    modern.startGame('relax', 'modern', 0)
    expect(modern.rules).toBe(DEFAULT_RULES) // sanity: modern World pre-load
    const modernSnap = cloneWorld(modern) // capture the modern save FIRST

    const classicWorld = new World()
    classicWorld.startGame('classic', 'modern', 0)
    const classicSnap = cloneWorld(classicWorld)

    // Load the classic save into the modern World.
    restoreWorld(modern, classicSnap)

    expect(modern.difficultyKey).toBe('classic')
    expect(modern.themeKey).toBe('modern')
    expect(modern.rules).toBe(RULES.classic)
    expect(modern.rules.combatModel).toBe('instant')
    expect(modern.rules.starModel).toBe('functional')
    expect(modern.rules.dropSchedule).toBe('fixed')

    // And a modern save loaded into a classic World restores modern rules too.
    const classic = new World()
    classic.startGame('classic', 'modern', 0)
    restoreWorld(classic, modernSnap)
    expect(classic.difficultyKey).toBe('relax')
    expect(classic.themeKey).toBe('modern')
    expect(classic.rules).toBe(DEFAULT_RULES)
    expect(classic.rules.combatModel).toBe('pool')
  })
})
