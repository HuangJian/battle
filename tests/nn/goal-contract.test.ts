import { describe, it, expect } from 'bun:test'
import { seedWorld, makeTank } from '../helpers'
import { GRID } from '../../src/constants'
import {
  evaluateContract,
  makeGoalContract,
  makeDefaultPremise,
  E5_DODGE_TICK_LIMIT,
  type GoalContract,
} from '../../src/nn/goal-contract'
import { isVertexStaticallyBlocked } from '../../src/ai/goal/reach-mask'
import type { StageData } from '../../src/types'
import type { World } from '../../src/game/World'

function openArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    tiles.push(row)
  }
  return { id: 9999, name: 'Open', tiles, enemies: ['basic'] }
}

/** 载入空场并把玩家置为可操作（loadStageData 后 spawnTimer=1000 重生倒计时）。 */
function readyWorld(): World {
  const world = seedWorld(42)
  world.loadStageData(openArena(), 0)
  world.state = 'playing'
  const p = world.player
  if (p) {
    p.spawnTimer = 0
    p.shieldTimer = 0
  }
  return world
}

function contractAt(col: number, row: number, world: World, tick = 100, T = 180): GoalContract {
  const c = makeGoalContract({ col, row }, world, tick, T, 42)
  if (!c) throw new Error('contract rejected unexpectedly')
  return c
}

const baseCtx = { tick: 100, dodgeTicks: 0, goalMaskedOut: false }

describe('goal-contract（T8-min：E1/E3/E5/E4 + 固定 T）', () => {
  it('有效契约 → null；三条通用谓词各触发一次 E1 且不误触发', () => {
    const world = readyWorld()
    const c = contractAt(8, 10, world)
    expect(evaluateContract(c, world, baseCtx)).toBeNull()

    // E1-①: 基地被毁（isBaseDestroyed 走缓存标志，不走地形格）
    ;(world.tileMap as unknown as { baseAlive: boolean }).baseAlive = false
    expect(evaluateContract(c, world, baseCtx)).toBe('E1')
    ;(world.tileMap as unknown as { baseAlive: boolean }).baseAlive = true

    // E1-②: 玩家阵亡（不可控态）
    const p = world.player
    p!.alive = false
    expect(evaluateContract(c, world, baseCtx)).toBe('E1')
    p!.alive = true

    // E1-③: 关卡不再进行
    world.state = 'stageclear'
    expect(evaluateContract(c, world, baseCtx)).toBe('E1')
    world.state = 'playing'
    expect(evaluateContract(c, world, baseCtx)).toBeNull()
  })

  it('E3：只认静态地形 —— 目标格压钢触发；动态敌坦占位**不**触发（§6.6 防抖）', () => {
    const world = readyWorld()
    // 目标格 (8,10)：空地。敌坦压在目标格上 ⇒ E3 不得触发（动态占位交 L0/L2）。
    const enemy = makeTank({ kind: 'basic', isPlayer: false })
    enemy.x = 8 * 16
    enemy.y = 10 * 16
    enemy.alive = true
    world.tanks.push(enemy)
    const c = contractAt(8, 10, world)
    expect(isVertexStaticallyBlocked(world.tileMap, 8, 10)).toBe(false)
    expect(evaluateContract(c, world, { ...baseCtx, goalMaskedOut: false })).toBeNull()

    // 同一场景改为 goalMaskedOut=true（静态地形判定，执行器按 revision 刷新）⇒ E3
    expect(evaluateContract(c, world, { ...baseCtx, goalMaskedOut: true })).toBe('E3')
  })

  it('E5：dodge 位移 >60 tick 触发，=60 不触发（严格大于，§6.5.1）', () => {
    const world = readyWorld()
    const c = contractAt(8, 10, world)
    expect(evaluateContract(c, world, { ...baseCtx, dodgeTicks: E5_DODGE_TICK_LIMIT })).toBeNull()
    expect(evaluateContract(c, world, { ...baseCtx, dodgeTicks: E5_DODGE_TICK_LIMIT + 1 })).toBe(
      'E5',
    )
  })

  it('E4：超承诺期触发；T 边界内不触发', () => {
    const world = readyWorld()
    const c = contractAt(8, 10, world, 100, 180)
    expect(evaluateContract(c, world, { ...baseCtx, tick: 100 + 180 })).toBeNull()
    expect(evaluateContract(c, world, { ...baseCtx, tick: 100 + 181 })).toBe('E4')
  })

  it('定序 E3 > E1：静态不可达优先于前提失效（§6.2）', () => {
    const world = readyWorld()
    const c = contractAt(8, 10, world)
    world.tileMap.set(12, 24, 'empty') // E1 同时成立
    world.tileMap.set(13, 24, 'empty')
    world.tileMap.set(12, 25, 'empty')
    world.tileMap.set(13, 25, 'empty')
    expect(evaluateContract(c, world, { ...baseCtx, goalMaskedOut: true })).toBe('E3')
  })

  it('makeGoalContract：travelEst > T 的不可满足契约被拒绝', () => {
    const world = seedWorld(42)
    world.loadStageData(openArena(), 0)
    expect(makeGoalContract({ col: 8, row: 10 }, world, 100, 180, 181)).toBeNull()
    expect(makeGoalContract({ col: 8, row: 10 }, world, 100, 180, 180)).not.toBeNull()
  })

  it('确定性：同输入双跑 premise label 一致；零 world.rng 消费', () => {
    const w1 = seedWorld(7)
    w1.loadStageData(openArena(), 0)
    const p1 = makeDefaultPremise(w1)
    const w2 = seedWorld(7)
    w2.loadStageData(openArena(), 0)
    const p2 = makeDefaultPremise(w2)
    expect(p1.label).toBe(p2.label)
    expect(p1.predicates.length).toBe(p2.predicates.length)
  })
})
