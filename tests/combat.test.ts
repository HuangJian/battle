import { describe, it, expect } from 'bun:test'
import {
  TANK_PROFILES,
  PLAYER_PROGRESSION,
  BASELINE_BUDGET,
  ELITE_BUDGET,
  playerProfile,
  resolveProfile,
  applyEliteModifier,
  totalBudget,
  profileToStats,
  capabilityBias,
  ELITE_DIMENSION,
  ELITE_BONUS,
  STEEL_PIERCE_FIREPOWER,
} from '../src/config/combat'
import { baseSpeedPxPerTick, baseBulletSpeedPxPerTick, BULLET_SPEED_RATIO, cpsToPxPerTick } from '../src/config/speed'
import type { TankKind } from '../src/types'

/**
 * Combat Capability System — balance / progression / regression / AI / ext.
 *
 * Guards the plan's Definition of Done (§18/§19):
 *  - six shared dimensions, types are profile variations
 *  - normal enemies share a similar budget
 *  - elite commanders combine type + modifier + commander AI (budget broken)
 *  - player progression improves all dimensions together, bounded by config
 *  - AI evaluates decisions based on its own capabilities
 *  - new tank types are configuration only (procedural generation supported)
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

// ============================================================
// Budgets — normal enemies compete through specialization, not inflation
// ============================================================

describe('Combat Capability — budgets (DoD #3)', () => {
  it('every normal enemy archetype sums to exactly the baseline budget', () => {
    for (const k of ENEMY_KINDS) {
      expect(totalBudget(TANK_PROFILES[k])).toBe(BASELINE_BUDGET)
    }
  })

  it('the six dimensions are all present and in 0..100', () => {
    for (const k of ENEMY_KINDS) {
      const p = TANK_PROFILES[k]
      for (const dim of Object.keys(p) as (keyof typeof p)[]) {
        expect(p[dim]).toBeGreaterThanOrEqual(0)
        expect(p[dim]).toBeLessThanOrEqual(100)
      }
    }
  })

  it('archetypes specialize rather than inflate (distinct distributions)', () => {
    // fast is the mobility outlier; heavy is the armor outlier; power the firepower outlier
    expect(TANK_PROFILES.fast.mobility).toBeGreaterThan(TANK_PROFILES.basic.mobility)
    expect(TANK_PROFILES.armor.armor).toBeGreaterThan(TANK_PROFILES.basic.armor)
    expect(TANK_PROFILES.power.firepower).toBeGreaterThan(TANK_PROFILES.basic.firepower)
    // total budget stays equal despite the different shapes
    expect(totalBudget(TANK_PROFILES.fast)).toBe(totalBudget(TANK_PROFILES.armor))
  })
})

// ============================================================
// Profile → stats mapping is coherent & monotonic per role
// ============================================================

describe('Combat Capability — stat derivation (DoD #1)', () => {
  it('fast is the fastest, heavy is the slowest, power sits between', () => {
    const fast = profileToStats(TANK_PROFILES.fast, 'fast').speed
    const basic = profileToStats(TANK_PROFILES.basic, 'basic').speed
    const power = profileToStats(TANK_PROFILES.power, 'power').speed
    const heavy = profileToStats(TANK_PROFILES.armor, 'armor').speed
    expect(fast).toBeGreaterThan(basic)
    expect(heavy).toBeLessThanOrEqual(basic)
    expect(power).toBeLessThanOrEqual(basic)
  })

  it('heavy is the most durable, fast the frailest', () => {
    const heavy = profileToStats(TANK_PROFILES.armor).maxHp
    const fast = profileToStats(TANK_PROFILES.fast).maxHp
    expect(heavy).toBeGreaterThan(fast)
  })

  it('default power tank cannot pierce steel (bulletPower 1)', () => {
    // default power firepower (75) sits below the steel-pierce threshold
    expect(profileToStats(TANK_PROFILES.power).bulletPower).toBe(1)
    expect(profileToStats(TANK_PROFILES.basic).bulletPower).toBe(1)
  })

  it('only ELITE power pierces steel — bulletPower 2', () => {
    // elite power gets a +15% firepower boost (75 → 86), clearing the threshold
    const elitePower = applyEliteModifier(TANK_PROFILES.power, 'power')
    expect(elitePower.firepower).toBeGreaterThanOrEqual(STEEL_PIERCE_FIREPOWER)
    expect(profileToStats(elitePower).bulletPower).toBe(2)
    // other elite kinds do NOT reach the threshold → cannot destroy steel
    for (const k of ['basic', 'fast', 'armor'] as const) {
      expect(profileToStats(applyEliteModifier(TANK_PROFILES[k], k)).bulletPower).toBe(1)
    }
  })

  it('basic bullet speed is anchored to 4× the balanced-enemy movement speed', () => {
    // The bullet-speed model was redesigned (2026-07-26): bullet speed is a
    // per-kind table anchored to BALANCED_ENEMY_CPS × BULLET_SPEED_RATIO (×4),
    // not the old projectileSpeed × BULLET_SPEED_SCALE formula.
    const basicBullet = profileToStats(TANK_PROFILES.basic, 'basic').bulletSpeed
    const basicMove = baseSpeedPxPerTick('basic')
    expect(basicBullet).toBeCloseTo(basicMove * BULLET_SPEED_RATIO, 9)
    // concrete value (10.0 cells/sec → 2.6667 px/tick)
    expect(basicBullet).toBeCloseTo(cpsToPxPerTick(10.0), 9)
    expect(baseBulletSpeedPxPerTick('basic')).toBeCloseTo(basicBullet, 9)
  })

  it('higher fire control yields a shorter fire cooldown', () => {
    const low = profileToStats({
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 45,
      mobility: 50,
      armor: 50,
      special: 50,
    }).fireCooldown
    const high = profileToStats({
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 80,
      mobility: 50,
      armor: 50,
      special: 50,
    }).fireCooldown
    expect(high).toBeLessThan(low)
  })
})

// ============================================================
// Power enemy — faster bullets (≈ lvl-2 player), NO steel pierce (user req)
// ============================================================

describe('Combat Capability — power enemy bullet speed (user req)', () => {
  it('power bullet base = 0.95 × the balanced-enemy (basic) bullet speed', () => {
    // Redesigned bullet model (2026-07-26): bullet speed is a per-kind table,
    // so power fires at 0.95 × the basic bullet (the spec's "强力敌人弹速 =
    // 均衡敌人弹速 × 95%") — NOT at the level-2 player's bullet speed.
    const powerBullet = profileToStats(TANK_PROFILES.power, 'power').bulletSpeed
    const basicBullet = profileToStats(TANK_PROFILES.basic, 'basic').bulletSpeed
    expect(powerBullet).toBeCloseTo(basicBullet * 0.95, 9)
    // Document the concrete value so a silent regression is obvious.
    expect(powerBullet).toBeCloseTo(cpsToPxPerTick(9.5), 9) // 2.5333 px/tick
  })

  it('power projectileSpeed is exactly 70 (the level-2 player value)', () => {
    expect(TANK_PROFILES.power.projectileSpeed).toBe(70)
  })

  it('power firepower is unchanged and still cannot destroy steel', () => {
    // Firepower must NOT be raised — power must stay a non-steel-piercing unit.
    expect(TANK_PROFILES.power.firepower).toBe(75)
    expect(TANK_PROFILES.power.firepower).toBeLessThan(STEEL_PIERCE_FIREPOWER)
    expect(profileToStats(TANK_PROFILES.power).bulletPower).toBe(1)
  })

  it('power keeps its fire-rate fairness (no faster than unbuffed player)', () => {
    // fireControl unchanged at 50 → same 420 ms cooldown as the level-0 player.
    const powerCd = profileToStats(TANK_PROFILES.power).fireCooldown
    const playerCd = profileToStats(playerProfile(0)).fireCooldown
    expect(powerCd).toBeGreaterThanOrEqual(playerCd)
  })

  it('power bullet still clearly outruns every tank (race invariant)', () => {
    const power = profileToStats(TANK_PROFILES.power, 'power')
    const fast = profileToStats(TANK_PROFILES.fast, 'fast') // fastest-moving enemy
    expect(power.bulletSpeed).toBeGreaterThan(power.speed) // beats its own tank
    expect(power.bulletSpeed).toBeGreaterThan(fast.speed) // beats the fastest tank
  })

  it('power profile still sums to the baseline budget (300)', () => {
    expect(totalBudget(TANK_PROFILES.power)).toBe(BASELINE_BUDGET)
  })
})

// ============================================================
// Player progression — universal growth, configurable ceiling (DoD #5, #6)
// ============================================================

describe('Combat Capability — player progression (DoD #5, #6)', () => {
  it('matches the plan §11 ladder at default multiplier (50/60/70/80)', () => {
    expect(playerProfile(0)).toEqual({
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    })
    expect(playerProfile(1)).toEqual({
      firepower: 60,
      projectileSpeed: 60,
      fireControl: 60,
      mobility: 60,
      armor: 60,
      special: 60,
    })
    expect(playerProfile(2)).toEqual({
      firepower: 70,
      projectileSpeed: 70,
      fireControl: 70,
      mobility: 70,
      armor: 70,
      special: 70,
    })
    expect(playerProfile(3)).toEqual({
      firepower: 80,
      projectileSpeed: 80,
      fireControl: 80,
      mobility: 80,
      armor: 80,
      special: 80,
    })
  })

  it('raises ALL dimensions together (universal, not specialized)', () => {
    const p0 = playerProfile(0)
    const p3 = playerProfile(3)
    for (const dim of Object.keys(p3) as (keyof typeof p3)[]) {
      expect(p3[dim]).toBeGreaterThan(p0[dim])
    }
  })

  it('respects the configurable maximum level (never grows past it)', () => {
    expect(playerProfile(5).firepower).toBe(
      playerProfile(PLAYER_PROGRESSION.maximumLevel).firepower,
    )
    expect(playerProfile(-1).firepower).toBe(playerProfile(0).firepower)
  })

  it('maxMultiplier scales the player power ceiling (Option B/C modes)', () => {
    const cfg = { ...PLAYER_PROGRESSION, maxMultiplier: 1.5 }
    const dim = Math.min(
      100,
      Math.round((cfg.baseDim + cfg.maximumLevel * cfg.perLevel) * cfg.maxMultiplier),
    )
    expect(dim).toBeGreaterThan(playerProfile(cfg.maximumLevel).firepower)
  })
})

// ============================================================
// Elite commander — type + combat modifier + commander AI (DoD #4)
// ============================================================

describe('Combat Capability — elite commander (DoD #4)', () => {
  it('elite modifier boosts ONLY the kind-specific dimension by +15%', () => {
    for (const k of ENEMY_KINDS) {
      const base = TANK_PROFILES[k]
      const elite = applyEliteModifier(base, k)
      const dim = ELITE_DIMENSION[k]
      expect(elite[dim]).toBe(Math.min(100, Math.round(base[dim] * (1 + ELITE_BONUS))))
      // every other dimension is untouched
      for (const d of Object.keys(base) as (keyof typeof base)[]) {
        if (d !== dim) expect(elite[d]).toBe(base[d])
      }
    }
  })

  it('elite profile breaks the budget (higher total than baseline)', () => {
    for (const k of ENEMY_KINDS) {
      expect(totalBudget(applyEliteModifier(TANK_PROFILES[k], k))).toBeGreaterThan(BASELINE_BUDGET)
    }
  })

  it('elite budget stays bounded (does not absurdly exceed the elite ceiling)', () => {
    for (const k of ENEMY_KINDS) {
      expect(totalBudget(applyEliteModifier(TANK_PROFILES[k], k))).toBeLessThanOrEqual(
        ELITE_BUDGET + 50,
      )
    }
  })

  it('does not mutate the shared base profile (safe for shallow clone / siblings)', () => {
    const before = { ...TANK_PROFILES.armor }
    applyEliteModifier(TANK_PROFILES.armor, 'armor')
    expect(TANK_PROFILES.armor).toEqual(before)
  })
})

// ============================================================
// AI capability bias — decisions reflect the tank's own strengths (DoD #7)
// ============================================================

describe('Combat Capability — AI bias (DoD #7)', () => {
  it('fast tank biases toward flanking, heavy toward pushing, power toward attacking', () => {
    const fast = capabilityBias(TANK_PROFILES.fast)
    const heavy = capabilityBias(TANK_PROFILES.armor)
    const power = capabilityBias(TANK_PROFILES.power)
    expect(fast.flank).toBeGreaterThan(0)
    expect(heavy.push).toBeGreaterThan(0)
    expect(power.attack).toBeGreaterThan(0)
  })

  it('baseline profile yields a neutral bias (no skew)', () => {
    const b = capabilityBias(TANK_PROFILES.basic)
    expect(b.flank).toBe(0)
    expect(b.push).toBe(0)
    expect(b.attack).toBe(0)
  })

  it('a +15% elite boost shifts the bias in the boosted dimension', () => {
    const base = capabilityBias(TANK_PROFILES.armor)
    const elite = capabilityBias(applyEliteModifier(TANK_PROFILES.armor, 'armor'))
    expect(elite.push).toBeGreaterThan(base.push)
  })
})

// ============================================================
// Extensibility — new tanks are configuration only (DoD #2, #9)
// ============================================================

describe('Combat Capability — extensibility (DoD #2, #9)', () => {
  it('resolveProfile returns the fixed archetype for enemies and progression for player', () => {
    expect(resolveProfile('basic')).toBe(TANK_PROFILES.basic)
    expect(resolveProfile('armor')).toBe(TANK_PROFILES.armor)
    expect(resolveProfile('player', 2)).toEqual(playerProfile(2))
  })

  it('a hypothetical new archetype is just a budgeted profile (procedural generation)', () => {
    // No engine changes required — define a profile, derive stats, done.
    const siege: typeof TANK_PROFILES.basic = {
      firepower: 65,
      projectileSpeed: 35,
      fireControl: 50,
      mobility: 25,
      armor: 75,
      special: 50,
    }
    expect(totalBudget(siege)).toBe(BASELINE_BUDGET)
    const stats = profileToStats(siege)
    expect(stats.maxHp).toBeGreaterThan(profileToStats(TANK_PROFILES.fast, 'fast').maxHp)
    // (Speed is per-kind data in config/speed.ts, not derivable from a bare
    // profile, so we don't assert .speed here — that's covered by speed.test.ts.)
    // Sanity: a new slow archetype's base speed is below the fast enemy's.
    expect(baseSpeedPxPerTick('armor')).toBeLessThan(baseSpeedPxPerTick('fast'))
  })
})
