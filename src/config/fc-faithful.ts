import type { TankKind } from '../types'

/**
 * Faithful 1985 FC *Battle City* per-kind values — the `classic` mode profile.
 *
 * These are DELIBERATELY kept separate from `config/tank-spec.ts`, which is the
 * modern *balance* single-source of truth (refactor.trae.md §2.1 / §2.2). The
 * modern game uses balanced per-kind ratios; `classic` reproduces the original
 * FC numbers exactly. **Do NOT "tidy" these into the modern balance** — they
 * are a faithful-behavior contract, not a tuning table.
 *
 * `rules.ts` `RULES.classic` references these constants; the literal numbers
 * live ONLY here so a future tuning pass cannot accidentally "correct" the FC
 * faithful values into the modern spread.
 */

/** Faithful FC movement speeds (cells/sec). Conversion FC px/frame @60fps →
 * px/sec (×60) → FC tiles/sec (÷16, tile=16px) → project cells/sec (×2 because
 * 1 FC tile = 1 FC tank = 16px while 1 project cell = 0.5 project tank, and both
 * fields are 13 tanks wide). Net factor = ×7.5. Thus basic 0.5 px/frame → 3.75
 * cps, fast 1.0 → 7.5 cps; player T1 3.75 → T4 7.5. */
export const FC_FAITHFUL_SPEED_CPS: Record<TankKind, number> = {
  basic: 3.75,
  fast: 7.5,
  power: 3.75,
  armor: 3.75,
  player: 3.75,
}

/** Player per-star linear speed growth in classic (cells/sec). */
export const FC_FAITHFUL_PLAYER_SPEED_PER_STAR_CPS = 1.25

/** Faithful FC bullet speeds (cells/sec). Same ×7.5 px/frame→cps factor as
 * movement. FC bullets are 2 px/frame (slow) for basic/fast/armor/player and
 * 4 px/frame (fast) for Power — NOT a per-kind 1.05/0.95/0.90 spread like
 * modern. Player growth is perk-driven: base 2 px/frame (15 cps); the 1★
 * 'fastBullet' star jumps it to 4 px/frame (30 cps) via fastBulletMult (2.0),
 * and that fast bullet stays for every higher star level (FC keeps the fast
 * bullet once earned). Hence playerBulletSpeedPerStarCps is 0 here. */
export const FC_FAITHFUL_BULLET_SPEED_CPS: Record<TankKind, number> = {
  basic: 15,
  fast: 15,
  power: 30,
  armor: 15,
  player: 15,
}

/** Player per-star bullet-speed growth in classic (cells/sec). 0 — growth is
 * perk-driven via fastBulletMult, not a linear per-star add. */
export const FC_FAITHFUL_PLAYER_BULLET_SPEED_PER_STAR_CPS = 0

/** Faithful FC kill scores by enemy kind. */
export const FC_FAITHFUL_SCORE_BY_KIND: Partial<Record<TankKind, number>> = {
  basic: 100,
  fast: 200,
  power: 300,
  armor: 400,
}
