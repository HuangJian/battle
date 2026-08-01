import type { GameState } from '../types'

/**
 * uiFlowGates.ts — pure state guards for the Control Center / recovery-screen
 * actions wired in Game.ts.
 *
 * These predicates ARE the regression surface of the MISSION FAILED (recovery)
 * screen fixes (2026-08-01): the Replay Browser, Lie-Back Win and Key Bindings
 * buttons were each silently dead / erroring there. The UI stack (UIManager /
 * ControlCenter) is DOM-bound, so the guards are extracted here as pure
 * functions to be regression-tested headlessly (AGENTS §8: no DOM in unit
 * tests unless the system under test requires it).
 */

/** May the Key Bindings panel open over this screen? The panel is a static
 *  modal; it needs a static screen underneath it (menu / paused / MISSION
 *  FAILED / classic game over). It can never cover the live world. */
export function canOpenControls(state: GameState): boolean {
  return state === 'menu' || state === 'paused' || state === 'recovery' || state === 'gameover'
}

/** May Lie-Back-Win (co-op) be toggled? Available from the menu, a paused
 *  game, and the MISSION FAILED (recovery) screen so co-op can be armed
 *  before retrying. Live play and terminal states fall through to no-op. */
export function canToggleCoop(state: GameState): boolean {
  return state === 'menu' || state === 'paused' || state === 'recovery'
}

/** Is the Replay Browser BLOCKED over this screen? Never. The browser is a
 *  fixed z-index-30 modal that layers over any screen; a live 'playing' game
 *  is auto-paused (and an active playback exited) by the caller before it
 *  opens.
 *
 *  This is the regression pin for `Game.openReplayBrowser`: the method once
 *  early-returned on 'recovery', silently killing the Control Center button
 *  on MISSION FAILED. `Game` consults THIS predicate as its guard, so the
 *  regression test is tied to production code — do not delete the guard call
 *  as "dead code"; a recovery block re-added inline in Game.ts would then
 *  slip past the tests. */
export function isReplayBrowserBlocked(_state: GameState): boolean {
  return false
}
