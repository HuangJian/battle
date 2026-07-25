import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { profileToStats, resolveProfile } from '../src/config/combat'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../src/config/difficulty'
import { CELL } from '../src/constants'
import type { TankKind } from '../src/types'

/**
 * Regression guard for the bug:
 *   "Adjusting difficulty must NOT enhance enemy combat power (armor / speed /
 *    bullet speed / HP). It may only make enemies smarter via DIFFICULTY_AI."
 *
 * Per DECISIONS.md (Tactical Intelligence Framework): difficulty scales the AI
 * (dodge / prediction / reaction / aggression / commander chance) — never the
 * tank stats. This test locks that contract in so a combat multiplier can't
 * silently reappear.
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

describe('Difficulty does not scale enemy combat power', () => {
  it('DifficultyConfig carries no enemy combat multipliers', () => {
    for (const key of DIFFICULTY_KEYS) {
      // The combat multipliers were intentionally removed from DifficultyConfig.
      // Cast through `unknown` so we can assert the deleted keys are absent.
      const d = DIFFICULTIES[key] as unknown as Record<string, unknown>
      expect(d.enemyHpMult).toBeUndefined()
      expect(d.enemySpeedMult).toBeUndefined()
      expect(d.enemyFireMult).toBeUndefined()
    }
  })

  it('every enemy kind has identical combat stats across all difficulties', () => {
    // Baseline (classic) stats per kind.
    const baseline = new Map<Exclude<TankKind, 'player'>, ReturnType<typeof profileToStats>>()
    for (const kind of ENEMY_KINDS) baseline.set(kind, profileToStats(resolveProfile(kind)))

    for (const key of DIFFICULTY_KEYS) {
      const world = new World()
      world.startGame(key, 'modern', 0)
      for (const kind of ENEMY_KINDS) {
        const t = world.createTank(kind, 8 * CELL, 8 * CELL, 'down')
        const base = baseline.get(kind)!
        // Enemy HP/armor must equal the archetype's fixed maxHp — NOT scaled by
        // difficulty (this is what the bug violated: chaos gave 2× HP).
        expect(t.hp).toBe(base.maxHp)
        expect(t.maxHp).toBe(base.maxHp)
        // Speed / bullet speed / fire cadence are archetype-fixed too.
        expect(t.speed).toBe(base.speed)
        expect(t.bulletSpeed).toBe(base.bulletSpeed)
        expect(t.fireCooldown).toBe(base.fireCooldown)
        expect(t.bulletPower).toBe(base.bulletPower)
      }
    }
  })
})
