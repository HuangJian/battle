import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { BASE_POS, GRID } from '../src/constants'

/**
 * §146 B → A8 退役守卫。
 *
 * `defensePosStandable` 是 §146B 时代的**收窄版**修法：只在「玩家距基地 > 8 格」
 * 时把不可站的默认集合点换成盒扫可站格。它保留了近基场景的错误行为（近基时仍返回
 * 环砖格 (12,23)），因此机制上没修干净 —— A8（DECISIONS
 * §2026-10-21-godai-a8-defense-cell）用无条件的可站解析取代它，35/35 关落到 (12,22)。
 *
 * 这里的断言守的是「退役」本身：旧旋钮不再有任何效果，旧行为只能通过
 * `defenseCellResolve: 0`（A/B 基线）复现。
 */

function buildAI(
  stageIdx: number,
  overrides: Partial<GodAIParams>,
  playerAt?: { col: number; row: number },
) {
  const w = new World()
  w.loadStageData(STAGES[stageIdx], stageIdx)
  const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, ...overrides })
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

describe('A8 取代 §146B defensePosStandable', () => {
  it('legacy raw cell is only reachable with defenseCellResolve: 0', () => {
    const { ai } = buildAI(7, { defenseCellResolve: 0 }, { col: 1, row: 10 }) // S8 Riverbed, far
    const d = ai.getDefaultDefensePosition()
    expect(d.col).toBe(BASE_POS.col)
    expect(d.row).toBe(BASE_POS.row - DEFAULT_GOD_AI_PARAMS.defenseRowOffset)
  })

  it('the retired knob changes nothing any more (near OR far)', () => {
    for (const playerAt of [
      { col: 1, row: 10 }, // far — the only case §146B used to handle
      { col: 8, row: 24 }, // near the base — the case §146B deliberately left broken
    ]) {
      const base = buildAI(7, {}, playerAt).ai.getDefaultDefensePosition()
      const retired = buildAI(
        7,
        { defensePosStandable: 1 },
        playerAt,
      ).ai.getDefaultDefensePosition()
      expect(retired).toEqual(base)
    }
  })

  it('A8 resolves the same standable cell near and far (no distance gate)', () => {
    const near = buildAI(7, {}, { col: 8, row: 24 })
    const far = buildAI(7, {}, { col: 1, row: 10 })
    const dNear = near.ai.getDefaultDefensePosition()
    const dFar = far.ai.getDefaultDefensePosition()
    expect(dNear).toEqual(dFar)
    expect(standableCell(dNear.col, dNear.row, near.w)).toBe(true)
    expect(dNear.col).toBe(BASE_POS.col)
    expect(dNear.row).toBe(BASE_POS.row - 2) // (12,22) on S8
  })
})
