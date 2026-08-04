import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'

/**
 * M4 round-2 (DECISIONS §115, SHIPPED 2026-08-04): the full-corpus CMA-ES
 * search tuned 14 params for the POOL combat model (hard/chaos) and wrote
 * them into DEFAULT_GOD_AI_PARAMS. classic ('instant') measured -2.4pp under
 * those defaults (91.0% → 88.6%), so GodAIInput.reset() restores the pre-M4
 * values via CLASSIC_MODEL_PARAMS when world.rules.combatModel === 'instant'.
 *
 * These tests lock the three invariants:
 *   1. DEFAULT carries the M4-tuned values (the shipped operating point).
 *   2. reset() on classic restores the pre-M4 values (91% gate byte-identical).
 *   3. An EXPLICIT override of an M4 param is respected even on classic
 *      (restore only fires when the param is still at its DEFAULT value).
 *   4. reset() on a pool difficulty (hard) keeps the M4 values.
 */
describe('m4-release-restore', () => {
  it('DEFAULT_GOD_AI_PARAMS carries the M4-tuned values', () => {
    expect(DEFAULT_GOD_AI_PARAMS.replanInterval).toBe(1) // 50 → 1 (search)
    expect(DEFAULT_GOD_AI_PARAMS.threatRangeCells).toBe(23) // 10 → 23
    expect(DEFAULT_GOD_AI_PARAMS.campTimeoutTicks).toBe(20) // 90 → 20
    expect(DEFAULT_GOD_AI_PARAMS.outnumberedFieldDistCells).toBe(26) // 15 → 26
    expect(DEFAULT_GOD_AI_PARAMS.aimError).toBeCloseTo(0.0303, 4) // game-feel: NOT tuned
    expect(DEFAULT_GOD_AI_PARAMS.suboptimalPathProb).toBe(0) // game-feel: NOT tuned
  })

  it('CLASSIC_MODEL_PARAMS restores pre-M4 values on classic (instant)', () => {
    const world = new World()
    world.rng = new RNG(42)
    const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
    world.startGame('classic', 'modern', 0)
    input.reset()
    expect(world.rules.combatModel).toBe('instant')
    // Restored to the pre-M4 shipped values. NOTE: stage adaptation runs ON
    // TOP of the restore (same as pre-M4), so we assert only keys that S0's
    // terrain does NOT adapt: baseRaceRangeCells widens to 14 on this open
    // stage (§60), replanInterval adapts on brick-dense stages, etc.
    expect(input.params.replanInterval).toBe(50)
    expect(input.params.threatRangeCells).toBe(10)
    expect(input.params.campTimeoutTicks).toBe(90)
    expect(input.params.outnumberedFieldDistCells).toBe(15)
    expect(input.params.defenseColSpread).toBe(5)
    expect(input.params.outnumberedEnemyCount).toBe(3)
    expect(input.params.t8MaxInterceptDistCells).toBe(8)
    expect(input.params.baseWallScanRadius).toBe(3)
    expect(input.params.powerupMaxDivertDistance).toBe(16)
    expect(input.params.endgameEnemyThreshold).toBe(6)
  })

  it('pool difficulty (hard) keeps the M4-tuned defaults', () => {
    const world = new World()
    world.rng = new RNG(42)
    const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
    world.startGame('hard', 'modern', 0)
    input.reset()
    expect(world.rules.combatModel).toBe('pool')
    expect(input.params.replanInterval).toBe(1)
    expect(input.params.threatRangeCells).toBe(23)
    expect(input.params.campTimeoutTicks).toBe(20)
  })

  it('explicit M4 override wins even on classic (restore is default-only)', () => {
    const world = new World()
    world.rng = new RNG(42)
    // Caller explicitly overrides replanInterval — must survive the restore.
    const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, replanInterval: 7 })
    world.startGame('classic', 'modern', 0)
    input.reset()
    expect(world.rules.combatModel).toBe('instant')
    expect(input.params.replanInterval).toBe(7)
    // A non-overridden M4 param IS restored.
    expect(input.params.threatRangeCells).toBe(10)
  })

  it('SKILLED_HUMAN_PARAMS aimError floor is unaffected by M4 (game-feel guard)', async () => {
    const { SKILLED_HUMAN_PARAMS } = await import('../src/ai/god/params')
    // Human proxy is always weaker than God: aimError ≥ 0.15 regardless of
    // God's (untuned) 0.0303.
    expect(SKILLED_HUMAN_PARAMS.aimError).toBeGreaterThanOrEqual(0.15)
    expect(SKILLED_HUMAN_PARAMS.aimError).toBeGreaterThan(DEFAULT_GOD_AI_PARAMS.aimError)
  })
})
