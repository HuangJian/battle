import { describe, it, expect } from 'bun:test'
import { planModeInputs } from '../src/game/modeInputs'

/**
 * Mode-input normalization decision table (2p-review P1-2): coop / 督战
 * spectate / 双打 twoPlayer are mutually exclusive owners of the shared
 * player slots. Whenever the mode flag changes — a toggle, a snapshot
 * restore, a recovery — the input wiring must collapse to EXACTLY the target
 * mode's decorators; a residual coop autoFireInput would make P1 auto-fire in
 * a restored two-player game. The rule lives here (pure) so the table is
 * regression-tested headlessly; Game.normalizeModeInputs applies it.
 */

describe('planModeInputs — mode ⇒ which input decorators survive', () => {
  it('coop keeps P2 God AI + P1 auto-fire, and clears spectate residue', () => {
    const plan = planModeInputs({
      coop: true,
      spectate: true, // stale flag from a previous spectate session
      spectateDual: true,
      twoPlayer: false,
    })
    expect(plan).toEqual({
      keepGodInput: true,
      keepSpectateGodInput: false,
      keepGodInput2: false,
      keepAutoFire: true,
    })
  })

  it('spectate keeps P1 God AI (+ the second AI when dual) and NEVER auto-fire', () => {
    const single = planModeInputs({
      coop: false,
      spectate: true,
      spectateDual: false,
      twoPlayer: false,
    })
    expect(single).toEqual({
      keepGodInput: false,
      keepSpectateGodInput: true,
      keepGodInput2: false,
      keepAutoFire: false,
    })
    const dual = planModeInputs({
      coop: false,
      spectate: true,
      spectateDual: true,
      twoPlayer: false,
    })
    expect(dual).toEqual({
      keepGodInput: false,
      keepSpectateGodInput: true,
      keepGodInput2: true,
      keepAutoFire: false,
    })
  })

  it('twoPlayer keeps NO AI decorators — P2 is the second HUMAN keyboard', () => {
    const plan = planModeInputs({
      coop: false,
      spectate: false,
      spectateDual: false,
      twoPlayer: true,
    })
    expect(plan).toEqual({
      keepGodInput: false,
      keepSpectateGodInput: false,
      keepGodInput2: false,
      keepAutoFire: false,
    })
  })

  it('plain (no mode) keeps nothing', () => {
    const plan = planModeInputs({
      coop: false,
      spectate: false,
      spectateDual: false,
      twoPlayer: false,
    })
    expect(plan).toEqual({
      keepGodInput: false,
      keepSpectateGodInput: false,
      keepGodInput2: false,
      keepAutoFire: false,
    })
  })
})
