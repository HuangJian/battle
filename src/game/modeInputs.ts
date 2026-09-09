/**
 * Mode-input normalization decision table (2p-review P1-2).
 *
 * coop / 督战 spectate / 双打 twoPlayer are mutually exclusive owners of the
 * shared player slots. Each mode arms a specific set of input decorators:
 *
 *   coop      → godInput (P2 God AI) + autoFireInput (P1 keyboard auto-fires)
 *   spectate  → godInput (P1 God AI) + godInput2 when dual; NEVER autoFire
 *   twoPlayer → no AI decorators at all (P2 is the second HUMAN keyboard)
 *   plain     → no decorators
 *
 * The Game wiring must collapse to EXACTLY the target mode's set whenever the
 * mode flag changes (toggle, snapshot restore, recovery) — a residual
 * autoFireInput from a previous coop session makes P1 auto-fire in a restored
 * two-player game. This pure function is the single decision source so the
 * rule is regression-testable headlessly (AGENTS §8); Game applies it.
 */
export interface ModeInputPlan {
  /** P2's God AI input survives (coop). */
  keepGodInput: boolean
  /** P1's God AI input survives (spectate). */
  keepSpectateGodInput: boolean
}

/** Pure decision — which decorators the world's CURRENT mode requires.
 *
 *  Note (2p-review R2-P2-2): only the two DRIVER objects the decision table
 *  itself must keep are columns here. The 督战双玩家 second AI (godInput2) and
 *  the auto-fire decorator are NOT columns: the former is re-derived from
 *  world.spectateDual inside `rearmSpectateGodInput()`, the latter follows the
 *  branch structure directly — dead columns in a pure decision table are a
 *  lie waiting to rot. */
export function planModeInputs(world: {
  coop: boolean
  spectate: boolean
  spectateDual: boolean
  twoPlayer: boolean
}): ModeInputPlan {
  if (world.coop) {
    return { keepGodInput: true, keepSpectateGodInput: false }
  }
  if (world.spectate) {
    return { keepGodInput: false, keepSpectateGodInput: true }
  }
  // twoPlayer and plain: no AI decorators survive.
  return { keepGodInput: false, keepSpectateGodInput: false }
}
