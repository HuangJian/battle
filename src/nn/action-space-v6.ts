import type { Direction } from '../constants'

/**
 * action-space-v6.ts — B′ B1 action contract (plan/nn-capability-upgrade.plan.md).
 *
 * v6 replaces the legacy 5-move/2-fire pair with three independent heads:
 *   hold  2 slots : 0 = advance, 1 = turn-only (no advance)
 *   move  4 slots : up / down / left / right (no STOP class)
 *   fire  3 slots : 0 = no shot, 1 = before turn, 2 = after turn
 *
 * This file is additive: the legacy `action-space.ts` constants stay untouched
 * so archived 5/2 checkpoints keep loading. `ModelLike.usesHoldFire3` is the
 * switch (presence of a `hold_head.*` param set in the weights JSON).
 */
export const HOLD_DIM = 2
export const MOVE4_DIM = 4
export const FIRE3_DIM = 3
export const MASK_V6_DIM = HOLD_DIM + MOVE4_DIM + FIRE3_DIM // 9

/** Move-label semantics tag for v6 corpora (twin of schema.py once B1 lands). */
export const MOVE_LABEL_SEMANTICS_V6 = 'hold_move4_fire3'

export const MOVE4_DECODE: ReadonlyArray<Direction> = ['up', 'down', 'left', 'right']

/** Decode a v6 move index (0..3). Out-of-range clamps to `up` (no STOP class). */
export function decodeMove4(idx: number): Direction {
  return MOVE4_DECODE[idx] ?? MOVE4_DECODE[0]
}

/** Encode a direction to its v6 move index (0..3). */
export function encodeMove4(dir: Direction): number {
  const i = MOVE4_DECODE.indexOf(dir)
  return i >= 0 ? i : 0
}
