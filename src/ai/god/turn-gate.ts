/**
 * A2 — committed-facing fire gate (plan/godai-post-a1-retune.plan.md §3.1).
 *
 * WHY THIS EXISTS
 * ---------------
 * A1 (`SimulationCombat.tryFire` now adjudicates the pending turn BEFORE
 * deriving the bullet direction) made "a bullet always leaves along the
 * tank's *committed* facing" a hard rule. Before A1, the God AI relied on the
 * opposite: it set `_moveDir`, then fired in the same tick and the bullet flew
 * along `_moveDir` even when the engine rejected the turn (instant snap-aim).
 * After A1 that invariant only holds on the tick where the turn is actually
 * accepted, so a "turn + fire in the same tick" request inside the turn
 * cooldown window now emits a bullet along the OLD facing — a wasted shot in
 * the wrong lane (measured: hard win rate 77.7% → 58.7%, §1.4 of the plan).
 *
 * `turnWillCommit` is the missing predicate: *will the bullet emitted this
 * tick actually fly along `dir`?* Feeding it into the fire decision turns
 * "a wasted off-axis shot" into "a correct shot 1..k ticks later".
 *
 * EXACTNESS
 * ---------
 * `SimulationCombat.applyTurnCooldown` (the function `tryFire` calls) has
 * three paths, and the post-adjudication facing is one of them:
 *
 *   1. `tank.dir === tank.prevMoveDir`            → accept, facing unchanged
 *   2. cooldown expired (`now - lastTurnMs ≥ cd`) → accept, facing = dir
 *   3. cooldown active                            → reject, facing = prevMoveDir
 *
 * `dir` is written into `tank.dir` before `tryFire` runs (see
 * `PlayerSystem.updatePlayerTank`), so with `d` = the requested direction the
 * emitted bullet flies along `d` in exactly two cases:
 *
 *   • `d === p.prevMoveDir` — either path 1 (already there) or path 3, which
 *     reverts ONTO `d`. (The "turn back to where I just was" case.)
 *   • the cooldown has expired — path 1 or 2.
 *
 * Hence the whole predicate collapses to two clauses — no frame guesswork, no
 * approximation. It reads only World/tank state already visible to the AI, so
 * it consumes no RNG and is snapshot/replay-safe (AGENTS §2.2 / §2.3).
 */
import { TICK_MS, TURN_SENTINEL_MS, type Direction } from '../../constants'
import type { World } from '../../game/World'
import type { Tank } from '../../types'

/**
 * True when a shot fired this tick leaves along `dir`.
 *
 * `dir === null` means "no turn requested this tick" — the tank keeps its
 * committed facing, and the candidates aim via `_moveDir ?? p.dir`, so the
 * shot is aligned by construction.
 *
 * `nowMs` defaults to the same clock `tryFire` uses (`w.frame * TICK_MS`);
 * callers that already computed it may pass it in to stay allocation-free.
 */
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
