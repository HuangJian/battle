import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { ENEMY_MODEL_DEFAULT_WINDOW_TICKS } from '../src/ai/god/EnemyModel'
import { clearArena, seedWorld } from './helpers'

/**
 * M3 EnemyModel wiring (2026-10-27 audit).
 *
 * The model had TWO independent wiring faults, and together they made
 * `enemyModelMode: 1` a dead switch (measured: 0/350 games different):
 *
 *   1. `enemyModelWindowTicks` ships at 0 while both the update site and every
 *      consumer gate on `window > 0` — a mode knob that does not arm its own
 *      mechanism;
 *   2. `reset()` resolves `_enemyModel.active` from `this.params` at its TOP,
 *      i.e. BEFORE the stage-adapted params are computed at the BOTTOM — so a
 *      window supplied by the caller's base params is the only value that can
 *      ever activate the model.
 *
 * These tests pin the contract: one knob means one mechanism.
 */

function make(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(11)
  world.startGame('hard', 'modern', 0)
  clearArena(world)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  input.reset()
  void new Simulation(world, new Input())
  return { world, input }
}

describe('M3 EnemyModel — enemyModelMode arms its own window', () => {
  it('mode 1 with the default table activates the model', () => {
    const { input } = make({ enemyModelMode: 1 })
    expect(input.params.enemyModelWindowTicks).toBe(ENEMY_MODEL_DEFAULT_WINDOW_TICKS)
    expect(input._enemyModel.active).toBe(true)
  })

  it('mode 0 keeps the model inert and the window untouched', () => {
    const { input } = make()
    expect(input.params.enemyModelWindowTicks).toBe(0)
    expect(input._enemyModel.active).toBe(false)
  })

  it('an explicit caller window wins over the default', () => {
    const { input } = make({ enemyModelMode: 1, enemyModelWindowTicks: 30 })
    expect(input.params.enemyModelWindowTicks).toBe(30)
    expect(input._enemyModel.active).toBe(true)
  })
})
