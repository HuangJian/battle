import { describe, it, expect } from 'bun:test'
import { Game, needsCoopGodInputRebuild } from '../src/game/Game'
import { Input, type InputLike, DEFAULT_KEYS } from '../src/game/Input'
import { AutoFireInput } from '../src/game/AutoFireInput'
import { GodAIInput } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { makeCoopWorld } from './helpers'

/**
 * Mode-input normalization (2p-review R2-P1 + R2-P2-1): Game.normalizeModeInputs
 * collapses the input wiring to EXACTLY the restored world's mode. Two holes
 * from round 2:
 *
 *  - the coop branch reused an existing `godInput` without checking WHO it was
 *    bound to — a leftover spectate P1 AI (controlledTank = w.player) became
 *    the P2 driver, so P2 silently shadowed P1's every decision;
 *  - it unconditionally rebuilt AutoFireInput, reviving auto-fire for a player
 *    who had already taken over firing (a manual rewind mid-coop re-armed it).
 *
 * Game's constructor needs a canvas 2D context (unavailable headless), so the
 * tests drive the real normalizeModeInputs method on an instance built via
 * Object.create(Game.prototype) with only the fields the method touches — the
 * wiring side effect (wireLiveInputs) is spied. World/AI construction is real.
 */

/** Minimal Game-shaped harness: real World/Input/AI, spied wiring. */
function makeFakeGame(world: ReturnType<typeof makeCoopWorld>): Game & { wired: number } {
  const g = Object.create(Game.prototype) as Game & { wired: number }
  g.wired = 0
  g.world = world
  g.input = new Input({ ...DEFAULT_KEYS })
  g.godInput = null
  g.godInput2 = null
  g.autoFireInput = null
  g.audio = { player2Id: -1 } as Game['audio']
  // The real wireLiveInputs touches pads/simulation composites — not needed
  // to verify the normalization decision; spy that it runs exactly once.
  g.wireLiveInputs = () => {
    g.wired++
  }
  return g
}

/** A P1-bound God AI (default controlledTank = w.player) — the spectate shape. */
function spectateP1Ai(world: ReturnType<typeof makeCoopWorld>): GodAIInput {
  const ai = new GodAIInput(world, undefined, new RNG(7))
  ai.reset()
  return ai
}

/** A P2-bound God AI — the coop shape. */
function coopP2Ai(world: ReturnType<typeof makeCoopWorld>): GodAIInput {
  const ai = new GodAIInput(world, undefined, new RNG(7), (w) => w.player2)
  ai.reset()
  return ai
}

describe('needsCoopGodInputRebuild (pure) — R2-P1', () => {
  it('rebuilds null (never armed) and P1-bound AIs, keeps a P2-bound AI', () => {
    const world = makeCoopWorld(42)
    expect(needsCoopGodInputRebuild(null)).toBe(true)
    expect(needsCoopGodInputRebuild(spectateP1Ai(world))).toBe(true)
    expect(needsCoopGodInputRebuild(coopP2Ai(world))).toBe(false)
  })
})

describe('normalizeModeInputs coop branch — R2-P1 stale-spectate hole', () => {
  it('replaces a stale spectate P1 AI with a fresh P2-bound driver', () => {
    const world = makeCoopWorld(42)
    world.seed = 42
    world.coop = true
    const g = makeFakeGame(world)
    // Spectate-armed BEFORE the coop restore: P1's AI sits in godInput.
    g.godInput = spectateP1Ai(world)
    expect(g.godInput!.controlledTank(world)).toBe(world.player) // P1-bound
    g.audio.player2Id = -1

    g.normalizeModeInputs()

    expect(g.godInput).not.toBeNull()
    expect(g.godInput!.isPlayer2()).toBe(true)
    expect(g.godInput!.controlledTank(world)).toBe(world.player2)
    expect(g.godInput2).toBeNull()
    expect(g.autoFireInput).not.toBeNull() // P1 auto-fires in coop
    expect(g.audio.player2Id).toBe(world.player2!.id)
    expect(g.wired).toBe(1)
  })

  it('keeps an ALREADY P2-bound AI (no wasteful rebuild on every restore)', () => {
    const world = makeCoopWorld(42)
    world.seed = 42
    world.coop = true
    const g = makeFakeGame(world)
    const existing = coopP2Ai(world)
    g.godInput = existing

    g.normalizeModeInputs()

    expect(g.godInput).toBe(existing) // same object — the AI keeps its state
    expect(g.godInput!.controlledTank(world)).toBe(world.player2)
  })
})

describe('normalizeModeInputs auto-fire handling — R2-P2-1', () => {
  it('preserves an existing (disarmed) AutoFireInput across a same-session restore', () => {
    const world = makeCoopWorld(42)
    world.seed = 42
    world.coop = true
    const g = makeFakeGame(world)
    // A player who already pressed fire: the decorator disarmed permanently.
    const firingInner: InputLike = {
      getMoveDirection: () => null,
      isFiring: () => true,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const disarmed = new AutoFireInput(firingInner)
    expect(disarmed.isFiring()).toBe(true) // disarms (human took over)
    g.autoFireInput = disarmed

    g.normalizeModeInputs()

    // Same object — auto-fire was NOT magically re-armed by the restore
    // (the pre-fix `new AutoFireInput` would have produced a fresh armed one).
    expect(g.autoFireInput).toBe(disarmed)
  })

  it('creates a fresh armed AutoFireInput when none exists (fresh coop session)', () => {
    const world = makeCoopWorld(42)
    world.seed = 42
    world.coop = true
    const g = makeFakeGame(world)
    g.autoFireInput = null // spectate already cleared it — cross-mode switch

    g.normalizeModeInputs()

    expect(g.autoFireInput).toBeInstanceOf(AutoFireInput)
  })
})
