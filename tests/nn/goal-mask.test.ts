import { describe, it, expect } from 'bun:test'
import { World } from '../../src/game/World'
import { bfsDistance, goalMoveMask, pickHeuristicGoal, GoalSteering } from '../../src/nn/goal-mask'
import { GRID, CELL, BASE_POS } from '../../src/constants'
import type { Tank } from '../../src/types'

/** 26×26 全空场 + 玩家在 (10,10)（x=160,y=160）。 */
function makeWorld(): World {
  const w = new World()
  for (let r = 0; r < GRID; r++) for (let c = 0; c < GRID; c++) w.tileMap.grid[r][c] = 'empty'
  w.player = {
    x: 10 * CELL,
    y: 10 * CELL,
    alive: true,
    hp: 200,
    maxHp: 263,
    isPlayer: true,
    allegiance: 'ally',
  } as unknown as Tank
  return w
}

function enemyAt(col: number, row: number): Tank {
  return {
    x: col * CELL,
    y: row * CELL,
    alive: true,
    isPlayer: false,
    allegiance: 'enemy',
    hp: 100,
    maxHp: 250,
  } as unknown as Tank
}

describe('bfsDistance', () => {
  it('目标右侧：向右距离递减，向左递增', () => {
    const w = makeWorld()
    const d = bfsDistance(w, 15, 10)
    expect(d[10 * GRID + 10]).toBe(5) // 玩家 (10,10) 到 (15,10)
    expect(d[10 * GRID + 11]).toBe(4)
    expect(d[10 * GRID + 9]).toBe(6)
  })

  it('确定性：同输入两次输出逐位一致', () => {
    const w = makeWorld()
    const a = bfsDistance(w, 8, 20)
    const b = bfsDistance(w, 8, 20)
    expect(Array.from(a)).toEqual(Array.from(b))
  })

  it('目标被钢墙围死（不可达）→ 全 -1', () => {
    const w = makeWorld()
    // 目标 (15,10) 四邻全 steel
    w.tileMap.grid[9][15] = 'steel'
    w.tileMap.grid[11][15] = 'steel'
    w.tileMap.grid[10][14] = 'steel'
    w.tileMap.grid[10][16] = 'steel'
    const d = bfsDistance(w, 15, 10)
    expect(d[15 * GRID + 15]).toBe(-1)
  })
})

describe('goalMoveMask', () => {
  it('目标在右侧：允许 stay+right，禁 up/down/left', () => {
    const w = makeWorld()
    const m = goalMoveMask(w, 15, 10)
    expect(m).toEqual([1, 0, 0, 0, 1]) // [stay, up, down, left, right]
  })

  it('玩家不可操作 → 全 1（不干预）', () => {
    const w = makeWorld()
    ;(w.player as Tank).alive = false
    const m = goalMoveMask(w, 15, 10)
    expect(m).toEqual([1, 1, 1, 1, 1])
  })

  it('目的地不可达 → 全 1（回退本能）', () => {
    const w = makeWorld()
    w.tileMap.grid[9][15] = 'steel'
    w.tileMap.grid[11][15] = 'steel'
    w.tileMap.grid[10][14] = 'steel'
    w.tileMap.grid[10][16] = 'steel'
    const m = goalMoveMask(w, 15, 10)
    expect(m).toEqual([1, 1, 1, 1, 1])
  })
})

describe('pickHeuristicGoal', () => {
  it('血量 <40% → 撤退到基地上方', () => {
    const w = makeWorld()
    ;(w.player as Tank).hp = 80 // < 263*0.4
    const g = pickHeuristicGoal(w)
    expect(g.col).toBe(BASE_POS.col)
    expect(g.row).toBe(Math.max(0, BASE_POS.row - 2))
  })

  it('血量健康 → 追最近存活敌', () => {
    const w = makeWorld()
    ;(w as any).tanks = [enemyAt(12, 10), enemyAt(22, 22)]
    const g = pickHeuristicGoal(w)
    expect(g).toEqual({ col: 12, row: 10 })
  })

  it('场清 → 回基地上方', () => {
    const w = makeWorld()
    ;(w as any).tanks = []
    const g = pickHeuristicGoal(w)
    expect(g.col).toBe(BASE_POS.col)
  })
})

describe('GoalSteering 承诺', () => {
  it('承诺期内保持同一目标（god 源无 God 时回退启发式）', () => {
    const w = makeWorld()
    ;(w as any).tanks = [enemyAt(20, 10)]
    const s = new GoalSteering('heuristic', null, 240)
    const m0 = s.maskForTick(w, 0)
    expect(s.currentGoal).toEqual({ col: 20, row: 10 })
    // 目标在右侧：allow right
    expect(m0[4]).toBe(1)
    // 240 tick 内不重选（目标仍 20,10）
    s.maskForTick(w, 100)
    expect(s.currentGoal).toEqual({ col: 20, row: 10 })
    // 承诺期过后重选
    s.maskForTick(w, 241)
    expect(s.currentGoal).not.toBeNull()
  })
})
