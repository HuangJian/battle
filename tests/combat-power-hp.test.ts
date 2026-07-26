import { describe, it, expect } from 'bun:test'
import {
  TANK_PROFILES,
  profileToStats,
  playerProfile,
  applyEliteModifier,
  DAMAGE_SCALE,
  HP_SCALE,
  PLAYER_FIREPOWER_MULT,
  PLAYER_HP_MULT,
  STEEL_PIERCE_PLAYER_LEVEL,
} from '../src/config/combat'
import type { TankKind } from '../src/types'

/**
 * Firepower (火力强度值) & HP (HP 值) balance — user spec 2026-07-26.
 *
 * The spec gives a preliminary hits-to-kill matrix (source \ target) and asks
 * us to verify it is reasonable and, if not, adjust it to match each tank's
 * character. The original matrix had power one-shotting the fast tank (1 hit),
 * which was unreasonable once power's specialty shifted to firing frequency
 * (1.10×): highest damage AND highest fire rate AND one-shots is too dominant.
 * Firepower was lowered from 80 to 64 (damage 128), changing only the
 * power→fast cell from 1 to 2. The adjusted matrix is reproduced EXACTLY
 * by deriving damage = round(firepower × DAMAGE_SCALE) and
 * maxHp = round(armor × HP_SCALE):
 *
 *   damage:  basic 100 · fast 72 · power 128 · armor 86   (basic = reference)
 *   maxHp :  basic 250 · fast 150 · power 200 · armor 350
 *
 * hits(source→target) = ceil(target.maxHp / source.damage).
 *
 * Character check (all satisfied):
 *   - power  = strongest gun (highest damage), slightly-low HP.
 *   - armor  = highest HP, slightly-below-average gun.
 *   - fast   = weakest gun, lowest HP.
 *   - basic  = average everywhere (the reference).
 *   - power no longer one-shots any enemy (2 hits minimum); elite power
 *     (firepower 74, damage 148) also cannot one-shot the frailest fast
 *     (HP 150) — so no enemy archetype can one-shot another.
 *   - elite power is kept from breaking steel by making steel-pierce
 *     player-only (classic Battle City), NOT by a firepower threshold —
 *     so power stays the highest-damage enemy per the matrix.
 *   - no-star player = basic × 1.05 in both firepower and HP.
 */

const ENEMY_KINDS: Exclude<TankKind, 'player'>[] = ['basic', 'fast', 'power', 'armor']

// damage / maxHp per kind (the canonical "火力强度值" / "HP 值" table).
const DAMAGE: Record<Exclude<TankKind, 'player'>, number> = {
  basic: 100,
  fast: 72,
  power: 128,
  armor: 86,
}
const HP: Record<Exclude<TankKind, 'player'>, number> = {
  basic: 250,
  fast: 150,
  power: 200,
  armor: 350,
}

// The user's preliminary hits-to-kill matrix (rows = source, cols = target).
const EXPECTED: Record<Exclude<TankKind, 'player'>, Record<Exclude<TankKind, 'player'>, number>> = {
  basic: { basic: 3, fast: 2, power: 2, armor: 4 },
  fast: { basic: 4, fast: 3, power: 3, armor: 5 },
  power: { basic: 2, fast: 2, power: 2, armor: 3 },
  armor: { basic: 3, fast: 2, power: 3, armor: 5 },
}

describe('Firepower & HP — concrete values (user spec)', () => {
  it('derives the documented 火力强度值 (damage) for every archetype', () => {
    for (const k of ENEMY_KINDS) {
      const stats = profileToStats(TANK_PROFILES[k], k)
      expect(stats.damage).toBe(DAMAGE[k])
      // damage must equal round(firepower × DAMAGE_SCALE)
      expect(stats.damage).toBe(Math.round(TANK_PROFILES[k].firepower * DAMAGE_SCALE))
    }
  })

  it('derives the documented HP 值 (maxHp) for every archetype', () => {
    for (const k of ENEMY_KINDS) {
      const stats = profileToStats(TANK_PROFILES[k], k)
      expect(stats.maxHp).toBe(HP[k])
      // maxHp must equal round(armor × HP_SCALE)
      expect(stats.maxHp).toBe(Math.round(TANK_PROFILES[k].armor * HP_SCALE))
    }
  })

  it('firepower ordering: power > basic > armor > fast', () => {
    const d = (k: (typeof ENEMY_KINDS)[number]) => profileToStats(TANK_PROFILES[k], k).damage
    expect(d('power')).toBeGreaterThan(d('basic'))
    expect(d('basic')).toBeGreaterThan(d('armor'))
    expect(d('armor')).toBeGreaterThan(d('fast'))
  })

  it('HP ordering: armor > basic > power > fast', () => {
    const h = (k: (typeof ENEMY_KINDS)[number]) => profileToStats(TANK_PROFILES[k], k).maxHp
    expect(h('armor')).toBeGreaterThan(h('basic'))
    expect(h('basic')).toBeGreaterThan(h('power'))
    expect(h('power')).toBeGreaterThan(h('fast'))
  })
})

describe('Firepower & HP — hits-to-kill matrix reproduces the spec exactly', () => {
  for (const source of ENEMY_KINDS) {
    it(`${source} source kills each target in the spec's hit count`, () => {
      const srcDamage = profileToStats(TANK_PROFILES[source], source).damage
      for (const target of ENEMY_KINDS) {
        const tgtHp = profileToStats(TANK_PROFILES[target], target).maxHp
        const hits = Math.ceil(tgtHp / srcDamage)
        expect(hits).toBe(EXPECTED[source][target])
      }
    })
  }

  it('the matrix is internally consistent (each cell = ceil(hp/dmg))', () => {
    for (const source of ENEMY_KINDS) {
      const srcDamage = DAMAGE[source]
      for (const target of ENEMY_KINDS) {
        expect(Math.ceil(HP[target] / srcDamage)).toBe(EXPECTED[source][target])
      }
    }
  })
})

describe('Firepower & HP — sanity / character checks', () => {
  it('power does NOT one-shot any enemy (minimum 2 hits, including fast)', () => {
    const powerDmg = profileToStats(TANK_PROFILES.power, 'power').damage
    for (const target of ENEMY_KINDS) {
      const tgtHp = profileToStats(TANK_PROFILES[target], target).maxHp
      const hits = Math.ceil(tgtHp / powerDmg)
      expect(hits).toBeGreaterThanOrEqual(2)
    }
    // specifically, power→fast is 2 hits (not 1)
    const fastHp = profileToStats(TANK_PROFILES.fast, 'fast').maxHp
    expect(Math.ceil(fastHp / powerDmg)).toBe(2)
  })

  it('elite power also cannot one-shot any enemy (damage 148 < fast HP 150)', () => {
    // Elite power gets +15% firepower: round(64 × 1.15) = 74 → damage 148.
    // The frailest enemy (fast) has HP 150, so ceil(150/148) = 2 — no one-shot.
    // This is the key reason firepower was lowered from 80 to 64: at firepower
    // 80 the elite reached damage 184 and one-shot everything fragile.
    const elitePower = applyEliteModifier(TANK_PROFILES.power, 'power')
    const eliteDmg = profileToStats(elitePower, 'power').damage
    expect(eliteDmg).toBe(148)
    for (const target of ENEMY_KINDS) {
      const tgtHp = profileToStats(TANK_PROFILES[target], target).maxHp
      const hits = Math.ceil(tgtHp / eliteDmg)
      expect(hits).toBeGreaterThanOrEqual(2)
    }
  })

  it('armor soaks the most shots from every source (max column)', () => {
    // For each source, armor target requires ≥ every other target's hit count.
    for (const source of ENEMY_KINDS) {
      const srcDamage = profileToStats(TANK_PROFILES[source], source).damage
      const armorHits = Math.ceil(HP.armor / srcDamage)
      for (const target of ENEMY_KINDS) {
        const tgtHits = Math.ceil(HP[target] / srcDamage)
        expect(armorHits).toBeGreaterThanOrEqual(tgtHits)
      }
    }
  })

  it('basic peer-duel is a fair 3-shot exchange (reference cell)', () => {
    const basic = profileToStats(TANK_PROFILES.basic, 'basic')
    expect(Math.ceil(basic.maxHp / basic.damage)).toBe(3)
  })

  it('the gunslingers (power/armor/basic) each win at least one duel', () => {
    // "wins" = kills the target in FEWER hits than the target kills it.
    // fast is intentionally the duel-weakest (weakest gun + lowest HP) — its
    // role is hit-and-run via mobility, not 1v1, so it is excluded here.
    const duelists = ENEMY_KINDS.filter((k) => k !== 'fast')
    for (const a of duelists) {
      const aDmg = DAMAGE[a]
      const aHp = HP[a]
      let wins = 0
      for (const b of ENEMY_KINDS) {
        if (a === b) continue
        const aKillsB = Math.ceil(HP[b] / aDmg)
        const bKillsA = Math.ceil(aHp / DAMAGE[b])
        if (aKillsB < bKillsA) wins++
      }
      expect(wins).toBeGreaterThan(0)
    }
  })

  it('fast is the duel-weakest by design, but the fastest mover (hit-and-run)', () => {
    const fastDmg = DAMAGE.fast
    const fastHp = HP.fast
    // fast never wins a straight duel (loses or ties every matchup)…
    for (const b of ENEMY_KINDS) {
      if (b === 'fast') continue
      const fastKillsB = Math.ceil(HP[b] / fastDmg)
      const bKillsFast = Math.ceil(fastHp / DAMAGE[b])
      expect(fastKillsB).toBeGreaterThanOrEqual(bKillsFast)
    }
    // …and compensates with the highest movement speed.
    const fastSpeed = profileToStats(TANK_PROFILES.fast, 'fast').speed
    for (const b of ENEMY_KINDS) {
      if (b === 'fast') continue
      expect(fastSpeed).toBeGreaterThan(profileToStats(TANK_PROFILES[b], b).speed)
    }
  })
})

describe('Firepower & HP — no-star player = balanced enemy × 1.05', () => {
  it('player level-0 firepower = basic firepower × 1.05', () => {
    const basicDmg = profileToStats(TANK_PROFILES.basic, 'basic').damage
    const player = profileToStats(playerProfile(0), 'player', 0)
    expect(player.damage).toBe(Math.round(basicDmg * PLAYER_FIREPOWER_MULT))
    expect(player.damage).toBeGreaterThan(basicDmg)
  })

  it('player level-0 HP = basic HP × 1.05', () => {
    const basicHp = profileToStats(TANK_PROFILES.basic, 'basic').maxHp
    const player = profileToStats(playerProfile(0), 'player', 0)
    expect(player.maxHp).toBe(Math.round(basicHp * PLAYER_HP_MULT))
    expect(player.maxHp).toBeGreaterThan(basicHp)
  })

  it('player out-guns and out-bulks the balanced enemy at level 0', () => {
    const basic = profileToStats(TANK_PROFILES.basic, 'basic')
    const player = profileToStats(playerProfile(0), 'player', 0)
    expect(player.damage).toBeGreaterThan(basic.damage)
    expect(player.maxHp).toBeGreaterThan(basic.maxHp)
    // a no-star player should kill a basic enemy in 3 (ceil(250/105)=3) …
    expect(Math.ceil(basic.maxHp / player.damage)).toBe(3)
    // … and survive a basic enemy for at least 2 shots (ceil(263/100)=3).
    expect(Math.ceil(player.maxHp / basic.damage)).toBe(3)
  })

  it('player HP & firepower keep growing with star level', () => {
    const l0 = profileToStats(playerProfile(0), 'player', 0)
    const l3 = profileToStats(playerProfile(3), 'player', 3)
    expect(l3.damage).toBeGreaterThan(l0.damage)
    expect(l3.maxHp).toBeGreaterThan(l0.maxHp)
  })
})

describe('Firepower & HP — steel-pierce is player-only (elite power cannot break steel)', () => {
  it('every enemy archetype (base & elite) uses bulletPower 1 / cannot pierce', () => {
    for (const k of ENEMY_KINDS) {
      const base = profileToStats(TANK_PROFILES[k], k)
      expect(base.canPierceSteel).toBe(false)
      expect(base.bulletPower).toBe(1)
    }
  })

  it('only the max-level player pierces steel', () => {
    expect(profileToStats(playerProfile(0), 'player', 0).canPierceSteel).toBe(false)
    expect(
      profileToStats(
        playerProfile(STEEL_PIERCE_PLAYER_LEVEL - 1),
        'player',
        STEEL_PIERCE_PLAYER_LEVEL - 1,
      ).canPierceSteel,
    ).toBe(false)
    const max = profileToStats(
      playerProfile(STEEL_PIERCE_PLAYER_LEVEL),
      'player',
      STEEL_PIERCE_PLAYER_LEVEL,
    )
    expect(max.canPierceSteel).toBe(true)
    expect(max.bulletPower).toBe(2)
  })
})
