import type { TankConfig, TankKind } from '../types'
import { BULLET_SPEED } from '../constants'

/**
 * Tank configurations.
 * Adding a new tank type = adding one entry here.
 */
export const TANK_CONFIGS: Record<TankKind, TankConfig> = {
  player: {
    kind: 'player',
    hp: 1,
    speed: 2,
    bulletSpeed: BULLET_SPEED.player,
    bulletPower: 1,
    score: 0,
    color: '#e8c840',
    dropsBonus: false,
  },
  basic: {
    kind: 'basic',
    hp: 1,
    speed: 1,
    bulletSpeed: BULLET_SPEED.basic,
    bulletPower: 1,
    score: 100,
    color: '#808080',
    dropsBonus: false,
  },
  fast: {
    kind: 'fast',
    hp: 1,
    speed: 3,
    bulletSpeed: BULLET_SPEED.fast,
    bulletPower: 1,
    score: 200,
    color: '#40c0c0',
    dropsBonus: false,
  },
  power: {
    kind: 'power',
    hp: 1,
    speed: 2,
    bulletSpeed: BULLET_SPEED.power,
    bulletPower: 1,
    score: 300,
    color: '#c080c0',
    dropsBonus: false,
  },
  armor: {
    kind: 'armor',
    hp: 4,
    speed: 2,
    bulletSpeed: BULLET_SPEED.armor,
    bulletPower: 1,
    score: 400,
    color: '#c0c040',
    dropsBonus: false,
  },
}
