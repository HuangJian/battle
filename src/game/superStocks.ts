import type { World } from './World'
import type { Tank } from '../types'

/**
 * Per-player super-item inventories (双打/躺赢 强力道具分家): the four super
 * kinds — guard / frenzy / sacrifice / rewind — live on the World as P1
 * fields (`guardStock` …) and P2 fields (`guardStock2` …), the `2` suffix
 * mirroring lives2 / score2 / playerLevel2. These helpers are the SINGLE
 * place that maps a tank to its inventory slot, so pickup
 * (PowerUpSystem.applyPowerUp), consumption (PlayerSystem / EnemiesSystem)
 * and the God AI (SuperItems) can never disagree about whose stock a count
 * belongs to. P2 = the tank that IS `world.player2` (human 双打, God AI
 * coop, or dual-spectate AI) — everything else is slot 1.
 *
 * Hot-path note (AGENTS §14.1): superStock/spend run per tick per player —
 * explicit field access only, no template-key indexing (no per-tick string
 * allocation).
 */
export type SuperKind = 'guard' | 'frenzy' | 'sacrifice' | 'rewind'

/** Player slot owning a tank: 2 exactly when it IS player2, else 1. */
export function playerSlotOf(w: World, p: Tank | null): 1 | 2 {
  return p === w.player2 ? 2 : 1
}

/** Read one player's inventory count for a super kind. */
export function superStock(w: World, p: Tank | null, kind: SuperKind): number {
  if (p === w.player2) {
    if (kind === 'guard') return w.guardStock2
    if (kind === 'frenzy') return w.frenzyStock2
    if (kind === 'sacrifice') return w.sacrificeStock2
    return w.rewindStock2
  }
  if (kind === 'guard') return w.guardStock
  if (kind === 'frenzy') return w.frenzyStock
  if (kind === 'sacrifice') return w.sacrificeStock
  return w.rewindStock
}

/** Add one picked-up super item to the COLLECTOR's inventory. */
export function addSuperStock(w: World, p: Tank | null, kind: SuperKind): void {
  if (p === w.player2) {
    if (kind === 'guard') w.guardStock2++
    else if (kind === 'frenzy') w.frenzyStock2++
    else if (kind === 'sacrifice') w.sacrificeStock2++
    else w.rewindStock2++
  } else {
    if (kind === 'guard') w.guardStock++
    else if (kind === 'frenzy') w.frenzyStock++
    else if (kind === 'sacrifice') w.sacrificeStock++
    else w.rewindStock++
  }
}

/** Consume one super item from the OWNER's inventory (caller gates on > 0). */
export function spendSuperStock(w: World, p: Tank | null, kind: SuperKind): void {
  if (p === w.player2) {
    if (kind === 'guard') w.guardStock2--
    else if (kind === 'frenzy') w.frenzyStock2--
    else if (kind === 'sacrifice') w.sacrificeStock2--
    else w.rewindStock2--
  } else {
    if (kind === 'guard') w.guardStock--
    else if (kind === 'frenzy') w.frenzyStock--
    else if (kind === 'sacrifice') w.sacrificeStock--
    else w.rewindStock--
  }
}

/** Empty the OWNER's inventory for one super kind (同归于尽 releases all). */
export function clearSuperStock(w: World, p: Tank | null, kind: SuperKind): void {
  if (p === w.player2) {
    if (kind === 'guard') w.guardStock2 = 0
    else if (kind === 'frenzy') w.frenzyStock2 = 0
    else if (kind === 'sacrifice') w.sacrificeStock2 = 0
    else w.rewindStock2 = 0
  } else {
    if (kind === 'guard') w.guardStock = 0
    else if (kind === 'frenzy') w.frenzyStock = 0
    else if (kind === 'sacrifice') w.sacrificeStock = 0
    else w.rewindStock = 0
  }
}

/** Combined stock of BOTH players (rail header `×N` total). */
export function superStockTotal(w: World): number {
  return (
    w.guardStock +
    w.frenzyStock +
    w.sacrificeStock +
    w.rewindStock +
    w.guardStock2 +
    w.frenzyStock2 +
    w.sacrificeStock2 +
    w.rewindStock2
  )
}
