import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { interceptLeadCellImpl } from '../src/ai/god/StrategyPlanner'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-30 预判拦截（pursuitLeadMode）。
 *
 * 背景：同速尾随追「敌人当前格」永远缩不近距离（nav-stall 段首 7 格、段尾 7 格）。
 * 规则：追击终点改为「敌人当前格 + 朝向 × pursuitLeadCells」，提前点不可通行
 * （钢/水/基地/越界）时逐格退让，最终回落到当前格。
 *
 * 用例锁定：mode 0 不变；可通行时提前；遇钢逐格退让；非敌人目标不变。
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

describe('interceptLeadCellImpl', () => {
  it('mode 0 returns the target unchanged', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 0 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    const cell = input.tankCell(e)
    const out = interceptLeadCellImpl(input, { col: cell.col, row: cell.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(cell.col)
    expect(out!.row).toBe(cell.row)
  })

  it('leads by 2 cells along the enemy facing', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 1, pursuitLeadCells: 2 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    const cell = input.tankCell(e)
    const out = interceptLeadCellImpl(input, { col: cell.col, row: cell.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(cell.col + 2)
    expect(out!.row).toBe(cell.row)
  })

  it('backs off one cell when the lead cell is steel', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 1, pursuitLeadCells: 2 })
    const e = placeEnemy(world, 10, 10, 'basic', 'right')
    const cell = input.tankCell(e)
    world.tileMap.set(cell.col + 2, cell.row, 'steel')
    const out = interceptLeadCellImpl(input, { col: cell.col, row: cell.row })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(cell.col + 1)
  })

  it('leaves non-enemy targets untouched', () => {
    const { world, input } = setupWorld({ pursuitLeadMode: 1, pursuitLeadCells: 2 })
    positionPlayer(world, 10, 20)
    const out = interceptLeadCellImpl(input, { col: 12, row: 22 })
    expect(out).not.toBeNull()
    expect(out!.col).toBe(12)
    expect(out!.row).toBe(22)
  })
})

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.spawnTimer = 0
}
