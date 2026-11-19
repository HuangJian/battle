/**
 * turn-gate.ts — pure "will this turn commit?" predicate.
 *
 * Mirrors SimulationCombat.applyTurnCooldown without mutating the tank. Shared
 * by the God-AI brains and the NN/inference layer.
 */
import { TICK_MS, TURN_SENTINEL_MS, type Direction } from '../constants'
import type { World } from '../game/World'
import type { Tank } from '../types'

export function turnWillCommit(
  w: World,
  p: Tank | null | undefined,
  dir: Direction | null,
  nowMs: number = w.frame * TICK_MS,
): boolean {
  if (!p) return true
  const cd = w.rules?.turnCooldownMs ?? 0
  // No turn cooldown at all ⇒ every turn is accepted.
  if (cd <= 0) return true
  const d = dir ?? p.dir
  // Path 1 / path 3: the facing ends up at `d` either way.
  if (d === p.prevMoveDir) return true
  // Path 2: cooldown expired ⇒ the turn is accepted.
  return nowMs - (p.lastTurnMs ?? TURN_SENTINEL_MS) >= cd
}
