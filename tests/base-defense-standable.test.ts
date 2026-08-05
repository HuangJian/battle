import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { BASE_POS, GRID } from '../src/constants'

/**
 * §146 B — defensePosStandable: the default defense position (12, 24−offset)
 * sits on the base ring, which is brick on all 35 stages. A* (corridor and
 * breakBrick) returns an EMPTY path to a brick target cell, so every
 * rally-to-defense mechanism (emergency defense, §113 field retreat, §88
 * chokepoint hold, P4.2) routes the player into a dead end (measured on S8:
 * pocket (1,10) → (12,23) corridor=0 breakBrick=0). With the knob ON, an
 * unreachable default falls back to the nearest standable cell near the base.
 */

function buildAI(
  stageIdx: number,
  standable: number,
  playerAt: { col: number; row: number } | null = null,
): { ai: GodAIInput; w: World } {
  const w = new World()
  w.loadStageData(STAGES[stageIdx], stageIdx)
  const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, defensePosStandable: standable })
  ai.reset()
  if (playerAt && w.player) {
    w.player.x = playerAt.col * 16
    w.player.y = playerAt.row * 16
  }
  return { ai, w }
}

const standableCell = (c: number, r: number, w: World): boolean => {
  if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
  const t = w.tileMap.get(c, r)
  return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
}

describe('defensePosStandable (§146 B)', () => {
  it('knob OFF (default) → returns the base-ring cell unchanged (byte-identical)', () => {
    const { ai } = buildAI(7, 0, { col: 1, row: 10 }) // S8 Riverbed, player far
    const d = ai.getDefaultDefensePosition()
    expect(d.col).toBe(BASE_POS.col)
    expect(d.row).toBe(BASE_POS.row - DEFAULT_GOD_AI_PARAMS.defenseRowOffset)
  })

  it('knob ON with a brick default (S8) and player FAR → returns a standable cell near the base', () => {
    const { ai, w } = buildAI(7, 1, { col: 1, row: 10 }) // S8: default (12,23) brick, dist 25 > 8
    const d = ai.getDefaultDefensePosition()
    // The rally must be standable and close to the base (the scan box).
    expect(standableCell(d.col, d.row, w)).toBe(true)
    expect(Math.abs(d.col - BASE_POS.col) + Math.abs(d.row - BASE_POS.row)).toBeLessThanOrEqual(9)
    // The standable fallback picks the cell right above the base ring.
    expect(d.col).toBe(BASE_POS.col)
    expect(d.row).toBe(BASE_POS.row - 2) // (12,22) on S8
  })

  it('knob ON with the player NEAR the base → keeps the base-ring default (byte-identical near base)', () => {
    const { ai, w } = buildAI(7, 1, { col: 8, row: 24 }) // spawn, dist 4 ≤ 8
    const d = ai.getDefaultDefensePosition()
    expect(d.col).toBe(BASE_POS.col)
    expect(d.row).toBe(BASE_POS.row - DEFAULT_GOD_AI_PARAMS.defenseRowOffset)
    expect(standableCell(d.col, d.row, w)).toBe(false) // it IS the brick cell
  })
})
