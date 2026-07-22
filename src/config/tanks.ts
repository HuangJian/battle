import type { TankConfig, TankKind } from '../types'

/**
 * Tank metadata.
 *
 * Combat stats (hp / speed / bulletSpeed / bulletPower) are NO LONGER stored
 * here — they are derived from each tank's CombatProfile in `config/combat.ts`
 * (see the Combat Capability System). This file keeps only the static,
 * non-combat presentation/score metadata. Adding a new tank = a profile entry
 * in `combat.ts` plus (optionally) a metadata row here.
 */
export const TANK_CONFIGS: Record<TankKind, TankConfig> = {
  player: {
    kind: 'player',
    color: '#e8c840',
    score: 0,
    dropsBonus: false,
  },
  basic: {
    kind: 'basic',
    color: '#808080',
    score: 100,
    dropsBonus: false,
  },
  fast: {
    kind: 'fast',
    color: '#40c0c0',
    score: 200,
    dropsBonus: false,
  },
  power: {
    kind: 'power',
    color: '#c080c0',
    score: 300,
    dropsBonus: false,
  },
  armor: {
    kind: 'armor',
    color: '#c0c040',
    score: 400,
    dropsBonus: false,
  },
}
