import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findFreezeAlignDirImpl } from '../src/ai/god/StrategyPlanner'
import { evalAggro } from '../src/ai/god/candidates/Aggro'
import { CELL } from '../src/constants'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-29 冻结期横移对齐（freezeAlignMode）。
 *
 * 满长低杀冻结窗口里 52% 的拍是「有 aimDir + 路径无遮挡 + fire=false」：
 * 敌人横向偏 1–2 格，6px 弹道走廊打不到；导航回退又指向敌人本体格（不可达），
 * 导致整个窗口原地掉头。修法：冻结期横移一格把敌人放回走廊。
 *
 * 这些用例锁定：
 *   1. 瞄准轴前方、横向偏 1 格 → 返回横移方向（左右对称）。
 *   2. 敌人在背后 / 横向偏 >2 格 / 超出射程 → null。
 *   3. 水平瞄准对称成立。
 *   4. 集成：走廊为空时 evalAggro 提交 freezeAlign 并横移。
 *   5. 走廊有敌人时不得劫持（交给 stop-and-aim）。
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

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
}

const CENTER = (col: number, row: number) => ({ x: col * CELL + CELL, y: row * CELL + CELL })

describe('findFreezeAlignDirImpl', () => {
  it('enemy 1 cell right & 3 up while aiming up → right', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 6)
    placeEnemy(world, 11, 3)
    const { x, y } = CENTER(10, 6)
    expect(findFreezeAlignDirImpl(input, x, y, 'up', 8)).toBe('right')
  })

  it('enemy 1 cell left while aiming up → left', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 6)
    placeEnemy(world, 9, 3)
    const { x, y } = CENTER(10, 6)
    expect(findFreezeAlignDirImpl(input, x, y, 'up', 8)).toBe('left')
  })

  it('enemy behind the aim axis → null', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 6)
    placeEnemy(world, 11, 9) // below while aiming up
    const { x, y } = CENTER(10, 6)
    expect(findFreezeAlignDirImpl(input, x, y, 'up', 8)).toBeNull()
  })

  it('lateral offset beyond 2 cells → null', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 6)
    placeEnemy(world, 13, 3) // 3 cells right
    const { x, y } = CENTER(10, 6)
    expect(findFreezeAlignDirImpl(input, x, y, 'up', 8)).toBeNull()
  })

  it('beyond range → null', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 12)
    placeEnemy(world, 11, 2) // 10 cells up, range 8
    const { x, y } = CENTER(10, 12)
    expect(findFreezeAlignDirImpl(input, x, y, 'up', 8)).toBeNull()
  })

  it('horizontal aim is symmetric (right aim, enemy 1 cell down) → down', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 6, 10)
    placeEnemy(world, 10, 11)
    const { x, y } = CENTER(6, 10)
    expect(findFreezeAlignDirImpl(input, x, y, 'right', 8)).toBe('down')
  })
})

describe('evalAggro freezeAlign wiring', () => {
  const ctxFor = (world: World, aimDir: 'up' | 'down' | 'left' | 'right'): DecisionContext => {
    const p = world.player!
    return {
      w: world,
      p,
      pcx: p.x + p.w / 2,
      pcy: p.y + p.h / 2,
      onCooldown: false,
      aimDir,
      threat: null,
      shielded: false,
    }
  }

  it('sidesteps when the corridor is empty during freeze', () => {
    const { world, input } = setupWorld({ freezeAlignMode: 1, freezeAlignRangeCells: 8 })
    positionPlayer(world, 10, 6)
    const enemy = placeEnemy(world, 11, 3)
    enemy.x += 12 // 28px lateral offset: aimDir exists (<32), scanAhead corridor is empty
    world.freezeTimer = 5000
    input.aggressive = true
    const ok = evalAggro(input, ctxFor(world, 'up'))
    expect(ok).toBe(true)
    expect(input._moveDir).toBe('right')
    expect(input._lastBranch).toBe('freezeAlign')
  })

  it('does not hijack when the corridor already has the enemy', () => {
    const { world, input } = setupWorld({ freezeAlignMode: 1, freezeAlignRangeCells: 8 })
    positionPlayer(world, 10, 6)
    placeEnemy(world, 10, 3) // dead ahead — stop-and-aim can fire
    world.freezeTimer = 5000
    input.aggressive = true
    evalAggro(input, ctxFor(world, 'up'))
    expect(input._lastBranch).not.toBe('freezeAlign')
  })

  it('mode 0 never aligns', () => {
    const { world, input } = setupWorld({ freezeAlignMode: 0 })
    positionPlayer(world, 10, 6)
    const enemy = placeEnemy(world, 11, 3)
    enemy.x += 12
    world.freezeTimer = 5000
    input.aggressive = true
    evalAggro(input, ctxFor(world, 'up'))
    expect(input._lastBranch).not.toBe('freezeAlign')
  })
})
