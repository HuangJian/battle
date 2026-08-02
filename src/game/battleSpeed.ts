/**
 * battleSpeed.ts — live battle-speed control (督战模式 / supervise mode).
 *
 * Alt+> / Alt+< step the live game's sim-time scale up/down in real time
 * (see Game.adjustBattleSpeed). Pure helpers extracted here so the ladder
 * is unit-testable headlessly (AGENTS §8: no DOM in unit tests).
 *
 * Speed is a PRESENTATION/LOOP concern, not World state: it changes how many
 * fixed-timestep ticks the accumulator drains per wall-clock frame, never the
 * ticks themselves — determinism (AGENTS §2.3) is untouched. It therefore
 * lives on Game (like renderFpsCap), not on the World, and is never snapshotted.
 */

/** The speed ladder, ordered slowest → fastest. */
export const BATTLE_SPEEDS = [1, 1.5, 2, 4] as const

export type BattleSpeed = (typeof BATTLE_SPEEDS)[number]

/**
 * Step the current speed one notch up (+1) or down (−1) the ladder, clamped
 * at both ends. Returns the same speed when already at the edge.
 */
export function cycleBattleSpeed(current: number, dir: 1 | -1): BattleSpeed {
  const idx = BATTLE_SPEEDS.indexOf(current as BattleSpeed)
  const base = idx < 0 ? 0 : idx
  const next = Math.max(0, Math.min(BATTLE_SPEEDS.length - 1, base + dir))
  return BATTLE_SPEEDS[next]
}
