import { describe, it, expect } from 'bun:test'
import { STAGES } from '../src/config/stages'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../src/ai/GodAIInput'
import { World } from '../src/game/World'
import { RNG } from '../src/utils/RNG'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { BASE_POS, GRID } from '../src/constants'
import { findPath } from '../src/ai/god/pathfind'

/**
 * A8 — 集合点必须是「能站的格」(DECISIONS §2026-10-21-godai-a8-defense-cell).
 *
 * `getDefaultDefensePosition()` 返回 `(BASE_POS.col, BASE_POS.row − defenseRowOffset)`
 * = (12, 23)。实测 35/35 关该格都是基地环砖，`findPath` 在 corridor 与
 * dig-through-brick 两种模式下**都返回空路径** —— 所有「回防」指令都把导航层
 * 指向一个永远到不了的目标 ⇒ followPath 返回 null、directMove 对着环砖盲走破砖。
 *
 * 修复：不可站（或可站但四邻全封 = 环内单格气泡）时沿基地列向上找、再在基地
 * 周边 7×10 盒内就近取可站格；35 关全部落到 (12,22)。
 */

const standable = (t: string | undefined): boolean =>
  t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'

function makeInput(stageIdx: number, params: GodAIParams = DEFAULT_GOD_AI_PARAMS) {
  const world = new World()
  world.rng.reseed(1)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  const input = new GodAIInput(world, params, new RNG(1))
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()
  return { world, input }
}

describe('A8 — defense cell is standable and reachable', () => {
  it('ships ON by default', () => {
    expect(DEFAULT_GOD_AI_PARAMS.defenseCellResolve).toBe(1)
  })

  it('the legacy raw cell is a brick on every stage (the defect)', () => {
    let bricks = 0
    for (let s = 0; s < STAGES.length; s++) {
      const { world } = makeInput(s, { ...DEFAULT_GOD_AI_PARAMS, defenseCellResolve: 0 })
      const raw = { col: BASE_POS.col, row: BASE_POS.row - DEFAULT_GOD_AI_PARAMS.defenseRowOffset }
      if (!standable(world.tileMap.get(raw.col, raw.row))) bricks++
    }
    expect(bricks).toBe(STAGES.length)
  })

  it('resolves to a standable cell with an open neighbour on every stage', () => {
    for (let s = 0; s < STAGES.length; s++) {
      const { world, input } = makeInput(s)
      const def = input.getDefaultDefensePosition()
      const t = world.tileMap.get(def.col, def.row)
      expect(standable(t), `s${s + 1} ${STAGES[s].name} def=(${def.col},${def.row}) t=${t}`).toBe(
        true,
      )
      const open =
        standable(world.tileMap.get(def.col - 1, def.row)) ||
        standable(world.tileMap.get(def.col + 1, def.row)) ||
        standable(world.tileMap.get(def.col, def.row - 1)) ||
        standable(world.tileMap.get(def.col, def.row + 1))
      expect(open, `s${s + 1} ${STAGES[s].name} def is sealed`).toBe(true)
      expect(def.col >= 0 && def.col < GRID && def.row >= 0 && def.row < GRID).toBe(true)
    }
  })

  it('has an A* route (dig mode) from the spawn cell, unlike the raw cell', () => {
    for (let s = 0; s < STAGES.length; s++) {
      const { world, input } = makeInput(s)
      const from = input.playerCell()
      const raw = { col: BASE_POS.col, row: BASE_POS.row - DEFAULT_GOD_AI_PARAMS.defenseRowOffset }
      const rawDig = findPath(world.tileMap, from, raw, { breakBrick: true })
      const fixed = input.getDefaultDefensePosition()
      const dig = findPath(world.tileMap, from, fixed, { breakBrick: true })
      // The raw brick cell has NO route at all …
      expect(rawDig?.length ?? 0, `s${s + 1} raw dig route`).toBe(0)
      // … the resolved cell always does.
      expect(
        dig?.length ?? 0,
        `s${s + 1} ${STAGES[s].name} fixed=(${fixed.col},${fixed.row})`,
      ).toBeGreaterThan(0)
    }
  })

  it('caches per stage and recomputes after reset()', () => {
    const { input } = makeInput(0)
    const a = input.getDefaultDefensePosition()
    expect(input._defCellValid).toBe(true)
    const b = input.getDefaultDefensePosition()
    expect(b).toEqual(a)
    // Returns a fresh object — callers mutate the result.
    expect(b).not.toBe(a)
    input.reset()
    expect(input._defCellValid).toBe(false)
    expect(input.getDefaultDefensePosition()).toEqual(a)
  })
})
