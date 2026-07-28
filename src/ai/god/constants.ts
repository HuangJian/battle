import type { TankKind, PowerUpType } from '../../types'

// ============================================================
// God AI shared constants (moved out of GodAIInput.ts during the
// §0.5 "split the God class" refactor — plan/God-AI-Curriculum).
// These are NOT tunable strategy params (those live in GodAIParams);
// they are game-rule-derived constants used across the sub-modules.
// ============================================================

/** T2a: max distance (in cells) at which to stop-and-aim.
 * At 15 cells (240px), the bullet takes ~120 ticks to arrive. An enemy
 * moving perpendicular at 1px/tick moves 120px = 7.5 cells in that time.
 * This is a balance: too short and the player rarely fires; too long and
 * the player wastes bullets on enemies that dodge. */
export const AIM_RANGE_CELLS = 15

/** T8: how far ahead to project a bullet's trajectory (entire field). */
export const BULLET_TRAJECTORY_MAX_CELLS = 26

/** T3: kind-based threat weights for target selection. */
export const KIND_THREAT_WEIGHT: Record<TankKind, number> = {
  power: 4,
  armor: 3,
  fast: 2,
  basic: 1,
  player: 0,
}

/** S5a: power-up collection priority (lower = higher priority). */
export const POWERUP_PRIORITY: Record<PowerUpType, number> = {
  bomb: 0,
  star: 1,
  freeze: 2,
  fence: 3,
  tank: 4,
  shield: 5,
  boat: 6,
  frenzy: 5,
  guard: 3,
  sacrifice: 5,
}
