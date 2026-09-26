import type { Direction } from '../constants'

// ================================================================
// action-space.ts — move-head semantics, single source of truth.
//
// The NN move head has 5 slots (0..4). Historically slot 0 meant "keep the
// current heading" (the old `lastDir` held-action semantic), which made every
// explicit id EXCEPT the four directions unusable — in particular there was no
// way to express "stop" (plan/new-era-stop.plan.md).
//
// New era (B案): slot 0 = **STOP**. `null` is the movement command the physics
// already understands (`SimulationPlayer`: `dir === null ⇒ moving = false`).
// "Continue straight" is expressed by 1..4 (the tank's heading is observable in
// ch6 `self`), so no keep class is needed and the tensor layout is unchanged.
//
// Every mover — the in-sim policy (policy-input), the rollout collector, the
// eval executor and the replay recorder — MUST decode the sampled/argmax index
// through `decodeMove`, so "0 = keep" can never silently survive in one copy.
// A manifest gate test (tests/nn/action-space.test.ts) fails if a new string
// literal `=== 0 → lastDir` grows back in a mover.
// ================================================================

/** Move index → direction. Index i (1..4) maps to slot i-1. */
export const MOVE_DECODE: ReadonlyArray<Direction> = ['up', 'down', 'left', 'right']

/** Movement action count (0 = STOP, 1..4 = the four directions). Mirrors `MOVE_DIM`. */
export const MOVE_DIM = 5

/**
 * Decode a move-head index (0..4) into a movement command.
 *   0     → null  (STOP; `moving = false`)
 *   1..4  → up / down / left / right
 * Out-of-range indices decode to null (STOP) rather than throwing — a masked or
 * corrupted slot must degrade to "no movement", never to an undefined direction.
 */
export function decodeMove(idx: number): Direction | null {
  if (idx <= 0) return null
  return MOVE_DECODE[idx - 1] ?? null
}

/** Encode a direction back to its move index (1..4); null (stop) → 0. */
export function encodeMove(dir: Direction | null): number {
  if (dir === null) return 0
  return MOVE_DECODE.indexOf(dir) + 1
}
