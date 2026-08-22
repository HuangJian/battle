import { CELL, GRID, POPUP_DURATION_MS } from '../constants'
import { killScore } from '../config/score'
import { genId } from './World'
import type { World } from './World'
import type { Tank } from '../types'

// ================================================================
// Kill Pipeline — shared "enemy dies → accounting" steps
//
// Extracted from four copy-pasted sites (plan/refactor.agy.md §2.1):
// bulletHitsTank, updateMines, triggerSacrificeAoE, applyPowerUp('bomb').
// Called ONLY from Simulation mixins — One-Author invariant (AGENTS §2.1)
// is preserved because the caller is always the Simulation.
// ================================================================

/** Options for {@link recordEnemyKill}. */
export interface KillCreditOptions {
  /** Route the score to P2's pool (Lie-Back-Win God-AI kills). Default false. */
  toScore2?: boolean
  /**
   * Decrement `enemiesRemaining`. Extra (balance-spawn) tanks sit outside the
   * per-stage count and pass false. Default true.
   */
  countsTowardStage?: boolean
}

/**
 * Account one enemy death: roll its score, credit the killer's pool, bump
 * counters, and float the score popup.
 *
 * Does NOT flip `alive`, spawn explosions, push events, or roll item drops —
 * those stay at the call sites, which own their differing semantics.
 *
 * @returns the scored points (`gained`), for callers that need them.
 */
export function recordEnemyKill(w: World, victim: Tank, opts: KillCreditOptions = {}): number {
  const gained = killScore(
    w.difficultyKey,
    victim.aiState?.level,
    w.stageIndex,
    w.rules,
    victim.kind,
  )
  if (opts.toScore2) {
    w.score2 += gained
  } else {
    w.score += gained
  }
  w.killCount++
  if (opts.countsTowardStage !== false) w.enemiesRemaining--
  w.addPopup({
    id: genId(),
    x: victim.x,
    y: victim.y,
    text: String(gained),
    timer: POPUP_DURATION_MS,
  })
  return gained
}

/**
 * Destroy every brick wall inside the square window of cells touched by a
 * circle at pixel center (cx,cy) with pixel radius radiusPx. Shared verbatim
 * by mine detonation and sacrifice AoE (plan §2.1 — byte-identical loops).
 */
export function destroyBrickAoE(w: World, cx: number, cy: number, radiusPx: number): void {
  const c0 = Math.max(0, Math.floor((cx - radiusPx) / CELL))
  const c1 = Math.min(GRID - 1, Math.floor((cx + radiusPx) / CELL))
  const r0 = Math.max(0, Math.floor((cy - radiusPx) / CELL))
  const r1 = Math.min(GRID - 1, Math.floor((cy + radiusPx) / CELL))
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) {
      if (w.tileMap.get(c, r) === 'brick') {
        w.tileMap.destroy(c, r)
      }
    }
  }
}
