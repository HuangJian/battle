import type { TankKind } from '../types'

/**
 * Per-kind design values — the SINGLE SOURCE OF TRUTH for balance per-kind
 * numbers (refactor.trae.md §2.1 / §2.2).
 *
 * Adding a new tank = extend the `TankKind` union (types.ts) + add ONE row
 * here. The `Record<TankKind>` tables in speed.ts / fire-rate.ts /
 * god/constants.ts are derived VIEWS via `specField`, so they need no
 * hand-editing. This collapses the old "7-file / 11-table" fan-out
 * (MANIFEST §6 tension) to ~2 edit points.
 *
 * Classic *faithful* per-kind values (speed / bullet-speed / score / hits /
 * bullets) are intentionally NOT here — they live in config/fc-faithful.ts
 * so the 1985 FC numbers are never "tidyed" into the modern balance. Do not
 * merge the two registries.
 */
export interface TankSpec {
  /** Movement speed, cells/sec (normal terrain). */
  speedCps: number
  /** Bullet speed, cells/sec. */
  bulletSpeedCps: number
  /** Fire-frequency multiplier vs the balanced enemy (1.0 = reference). */
  fireFreqMult: number
  /** God-AI target-selection threat weight. */
  threatWeight: number
}

export const TANK_SPEC: Record<TankKind, TankSpec> = {
  basic: { speedCps: 3.75, bulletSpeedCps: 15.0, fireFreqMult: 1.0, threatWeight: 1 },
  fast: { speedCps: 4.5, bulletSpeedCps: 15.75, fireFreqMult: 1.05, threatWeight: 2 },
  power: { speedCps: 3.5625, bulletSpeedCps: 14.25, fireFreqMult: 1.1, threatWeight: 4 },
  armor: { speedCps: 3.1875, bulletSpeedCps: 13.5, fireFreqMult: 0.9, threatWeight: 3 },
  player: { speedCps: 3.9375, bulletSpeedCps: 15.75, fireFreqMult: 1.05, threatWeight: 0 },
}

/**
 * Derive a `Record<TankKind, X>` from one TankSpec field. Centralized so the
 * only per-kind edit points are `TankKind` (types.ts) and `TANK_SPEC` above.
 */
export function specField<K extends keyof TankSpec>(k: K): Record<TankKind, TankSpec[K]> {
  return {
    player: TANK_SPEC.player[k],
    basic: TANK_SPEC.basic[k],
    fast: TANK_SPEC.fast[k],
    power: TANK_SPEC.power[k],
    armor: TANK_SPEC.armor[k],
  }
}
