import type { PowerUpType } from '../types'

/**
 * Super power-up (强力道具) configuration — DECISIONS.md §31.
 *
 * When ANY power-up drop occurs (elite kill / every-10-kills / every-5000-pts /
 * bonus enemy), there is a `SUPER_POWERUP_DROP_CHANCE` probability that the
 * dropped item is a super power-up instead of a normal one. A super drop rolls
 * equally among `SUPER_POWERUP_TYPES`.
 *
 * Super power-ups are accumulated into an inventory on pickup (not applied
 * instantly): 天降神兵/狂暴宣泄 are released actively (F5/F6), 同归于尽 releases
 * passively when the player loses a life.
 *
 * Phase split (user decision): Phase 1 shipped 同归于尽 + 狂暴宣泄. 天降神兵
 * (guard) joined the pool in Phase 2 once the ally AI + third-faction
 * collision landed — it is now a real, droppable super power-up.
 */
export const SUPER_POWERUP_DROP_CHANCE = 0.1

/** All three 强力道具 are in the drop pool now that Phase 2 is complete. */
export const SUPER_POWERUP_TYPES: PowerUpType[] = ['frenzy', 'sacrifice', 'guard']

/** 狂暴宣泄: number of shells fired during one activation. */
export const FRENZY_SHOTS = 20

/** 同归于尽: base blast radius (cells) at inventory count = 1; +1 cell per extra. */
export const SACRIFICE_BASE_RADIUS_CELLS = 5
