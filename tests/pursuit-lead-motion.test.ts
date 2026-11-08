import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { updateEnemyMotion } from '../src/ai/god/EnemyMotion'
import { interceptLeadCellImpl } from '../src/ai/god/StrategyPlanner'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-31 运动感知预判（pursuitLeadMode = 2）。
 *
 * 与 mode 1（瞬时朝向 `t.dir`）不同：mode 2 只在敌人**连续 N 拍朝同一方向实测移动**
 * 时才预判，刚转向/静止时提前量归零——避免把玩家导向空地（mode 1 A/B −118 的根因）。
 *
 * 用例锁定：稳定同向 → 提前；静止 → 不提前；刚换向 → 不提前。
 */
function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  clearArena(world)
  void sim
  input.reset()
  return { world, input }
}

function stepEnemy(input: GodAIInput, e: { x: number }, dxCells: number): void {
  e.x += dxCells * CELL
  updateEnemyMotion(input)
}

describe('pursuitLeadMode 2 (measured motion)', () => {
  it('leads 2 cells after the enemy moves the same way for 2+ ticks', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 2, pursuitLeadCells: 2 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    input._enemies = [e]
    updateEnemyMotion(input)
    stepEnemy(input, e, 1)
    stepEnemy(input, e, 1)
    stepEnemy(input, e, 1) // stable = 2
    const c = input.tankCell(e)
    const out = interceptLeadCellImpl(input, { col: c.col, row: c.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(c.col + 2)
    expect(out!.row).toBe(c.row)
  })

  it('does not lead a stationary enemy', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 2, pursuitLeadCells: 2 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    input._enemies = [e]
    for (let i = 0; i < 4; i++) updateEnemyMotion(input) // never moves
    const c = input.tankCell(e)
    const out = interceptLeadCellImpl(input, { col: c.col, row: c.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(c.col)
  })

  it('does not lead right after a direction change', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 2, pursuitLeadCells: 2 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    input._enemies = [e]
    updateEnemyMotion(input)
    stepEnemy(input, e, 1)
    stepEnemy(input, e, 1)
    stepEnemy(input, e, 1) // stable 2 → would lead
    // Turn: one cell left resets the stable counter.
    stepEnemy(input, e, -1)
    const c = input.tankCell(e)
    const out = interceptLeadCellImpl(input, { col: c.col, row: c.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(c.col)
  })
})
