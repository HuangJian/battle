import { describe, it, expect } from 'bun:test'
import { iceGlideAdjust } from '../src/ai/god/Navigator'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS } from '../src/ai/god/params'

/**
 * §145 ice glide control — 冰上滑行控制.
 *
 * The ice physics model (SimulationCombat.updateMovement) treats a reverse
 * input as an acceleration TOWARD the opposite direction (ICE_ACCEL_TRACTION
 * 0.35) — a real reversal, not a brake. Reversing at a path waypoint makes the
 * tank overshoot, the Math.round player-cell flickers across a cell boundary,
 * the A*-path cache (keyed on playerCell) flips, and the AI oscillates
 * (measured on S24 Labyrinth seed 23: moveDir up/down flapping for ~6 ticks).
 *
 * The fix: when gliding on ice and the chosen move direction is the reverse of
 * the glide axis, emit null (release the stick) so the glide decays at
 * ICE_DECEL_TRACTION instead of reversing. Pure function — deterministic.
 */

describe('iceGlideAdjust (pure function)', () => {
  it('reversing against the glide axis on ice → null (brake by release)', () => {
    // Gliding right, told to go left — the old behaviour reversed (real
    // reversal, overshoot); the fix releases the stick.
    expect(iceGlideAdjust('left', true, 1.0, 0, 0.3)).toBeNull()
    // Gliding down, told to go up.
    expect(iceGlideAdjust('up', true, 0, 1.2, 0.3)).toBeNull()
    // Gliding up, told to go down.
    expect(iceGlideAdjust('down', true, 0, -1.0, 0.3)).toBeNull()
    // Gliding left, told to go right.
    expect(iceGlideAdjust('right', true, -0.9, 0, 0.3)).toBeNull()
  })

  it('moving along the glide axis on ice → keep the direction', () => {
    expect(iceGlideAdjust('right', true, 1.0, 0, 0.3)).toBe('right')
    expect(iceGlideAdjust('down', true, 0, 1.2, 0.3)).toBe('down')
    expect(iceGlideAdjust('left', true, -0.9, 0, 0.3)).toBe('left')
  })

  it('perpendicular turn on ice → keep the direction (axis-lock decays the old axis)', () => {
    // Gliding right, turn up — the sim's axis-lock lets the old axis decay
    // while the new one accelerates; the turn itself is valid, do not block it.
    expect(iceGlideAdjust('up', true, 1.0, 0, 0.3)).toBe('up')
    expect(iceGlideAdjust('left', true, 0, 1.0, 0.3)).toBe('left')
  })

  it('below the min glide speed → no intervention (just started / nearly stopped)', () => {
    expect(iceGlideAdjust('left', true, 0.1, 0, 0.3)).toBe('left')
    expect(iceGlideAdjust('up', true, 0, 0.29, 0.3)).toBe('up')
    expect(iceGlideAdjust('left', true, 0, 0, 0.3)).toBe('left')
  })

  it('not on ice → never intervenes (byte-identical on ground)', () => {
    expect(iceGlideAdjust('left', false, 1.0, 0, 0.3)).toBe('left')
    expect(iceGlideAdjust('up', false, 0, 1.2, 0.3)).toBe('up')
    expect(iceGlideAdjust(null, false, 1.0, 0, 0.3)).toBeNull()
  })

  it('null moveDir → null (no-op)', () => {
    expect(iceGlideAdjust(null, true, 1.0, 0, 0.3)).toBeNull()
  })

  it('at exactly the min speed → intervenes (≥ threshold)', () => {
    expect(iceGlideAdjust('left', true, 0.3, 0, 0.3)).toBeNull()
  })
})

describe('§145 param defaults', () => {
  it('iceGlideControl defaults to 0 (OFF, byte-identical) and classic restores 0', () => {
    expect(DEFAULT_GOD_AI_PARAMS.iceGlideControl).toBe(0)
    expect(DEFAULT_GOD_AI_PARAMS.iceGlideMinSpeed).toBe(0.3)
    expect(CLASSIC_MODEL_PARAMS.iceGlideControl).toBe(0)
  })
})
