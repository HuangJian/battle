import { describe, it, expect, beforeEach } from 'bun:test'
import { World } from '../src/game/World'
import {
  GodAIInput,
  DEFAULT_GOD_AI_PARAMS,
  setGodAIParamsOverride,
  godAIParamsOverride,
} from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'

/**
 * §228 M5 open-test hook: boot-time param overrides via URL query
 * (?fireLineDetour=1). The override is a launch configuration — merged once
 * at construction, never mutated, never snapshot-relevant, and it must NOT
 * leak into the shared DEFAULT_GOD_AI_PARAMS singleton (DECISIONS §98).
 */

describe('§228 godAIParamsOverride (M5 open-test hook)', () => {
  beforeEach(() => {
    setGodAIParamsOverride(null)
  })

  it('defaults to null — stock params, byte-identical', () => {
    expect(godAIParamsOverride()).toBeNull()
    const world = new World()
    world.rng = new RNG(1)
    const ai = new GodAIInput(world)
    // Constructor clones params (§98) — assert value equality with the
    // shared defaults (M5 shipped at 1 since §229; the knob is part of
    // stock defaults, untouched by the override).
    expect(ai.params.fireLineDetourMode).toBe(DEFAULT_GOD_AI_PARAMS.fireLineDetourMode)
    expect(ai.params.fireLineDetourMode).toBe(1)
  })

  it('merge applies only to new instances, never the shared singleton', () => {
    setGodAIParamsOverride({ fireLineDetourMode: 1 })
    const world = new World()
    world.rng = new RNG(1)
    const ai = new GodAIInput(world)
    expect(ai.params.fireLineDetourMode).toBe(1)
    // Shared defaults untouched (ship default is 1, §229).
    expect(DEFAULT_GOD_AI_PARAMS.fireLineDetourMode).toBe(1)
    // Per-instance base clone — no leak into the override itself.
    ai.params.fireLineDetourMode = 9
    const ai2 = new GodAIInput(world)
    expect(ai2.params.fireLineDetourMode).toBe(1)
  })

  it('clearing the override restores stock defaults', () => {
    setGodAIParamsOverride({ fireLineDetourMode: 1 })
    setGodAIParamsOverride(null)
    const world = new World()
    world.rng = new RNG(1)
    const ai = new GodAIInput(world)
    expect(ai.params.fireLineDetourMode).toBe(1)
  })
})
