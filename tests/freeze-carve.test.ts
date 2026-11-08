import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import {
  bulletLaneClearImpl,
  firstBlockerKindImpl,
  findEnemyDirectionImpl,
} from '../src/ai/god/FireControl'
import { evalAggro } from '../src/ai/god/candidates/Aggro'
import { CELL } from '../src/constants'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-29 冻结期两条针对性策略：
 *   A. freezeTargetLosMode — 冻结期优先「6px 弹道可通」的目标（不让隔砖/钢的
 *      最近目标霸占 aimDir，导致 stop-and-aim 每拍拒火）。
 *   B. freezeCarveMode — 冻结期瞄准走廊为空且第一块挡弹地形是砖时，沿瞄准方向
 *      推进+开火破砖；钢/基地不可挖，交给目标选择换人。
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

describe('bulletLaneClearImpl', () => {
  it('clear empty lane → true', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    const { x, y } = CENTER(10, 10)
    expect(bulletLaneClearImpl(input, x, y, 'up', 5 * CELL)).toBe(true)
  })

  it('brick in the lane → false', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    world.tileMap.set(11, 8, 'brick') // player center is col 11
    const { x, y } = CENTER(10, 10)
    expect(bulletLaneClearImpl(input, x, y, 'up', 5 * CELL)).toBe(false)
  })

  it('steel and base also block', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    world.tileMap.set(11, 7, 'steel')
    const { x, y } = CENTER(10, 10)
    expect(bulletLaneClearImpl(input, x, y, 'up', 5 * CELL)).toBe(false)
  })
})

describe('firstBlockerKindImpl', () => {
  it('reports brick / steel / none', () => {
    const { world, input } = setupWorld()
    positionPlayer(world, 10, 10)
    const { x, y } = CENTER(10, 10)
    expect(firstBlockerKindImpl(input, x, y, 'up', 8)).toBe('none')
    world.tileMap.set(11, 8, 'brick')
    expect(firstBlockerKindImpl(input, x, y, 'up', 8)).toBe('brick')
    world.tileMap.set(11, 8, 'steel')
    expect(firstBlockerKindImpl(input, x, y, 'up', 8)).toBe('steel')
  })
})

describe('freezeTargetLosMode', () => {
  it('prefers a farther enemy with a clear lane over a nearer walled one', () => {
    const { world, input } = setupWorld({ freezeTargetLosMode: 1 })
    positionPlayer(world, 10, 10)
    placeEnemy(world, 10, 8) // 2 cells up, behind a brick
    world.tileMap.set(11, 9, 'brick')
    placeEnemy(world, 14, 10) // 4 cells right, clear lane
    world.freezeTimer = 5000
    const { x, y } = CENTER(10, 10)
    expect(findEnemyDirectionImpl(input, x, y)).toBe('right')
  })

  it('mode 0 keeps the default (nearest wins)', () => {
    const { world, input } = setupWorld({ freezeTargetLosMode: 0 })
    positionPlayer(world, 10, 10)
    placeEnemy(world, 10, 8)
    world.tileMap.set(11, 9, 'brick')
    placeEnemy(world, 14, 10)
    world.freezeTimer = 5000
    const { x, y } = CENTER(10, 10)
    expect(findEnemyDirectionImpl(input, x, y)).toBe('up')
  })
})

describe('freezeCarveMode wiring', () => {
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

  it('fires to carve when the first blocker is brick', () => {
    const { world, input } = setupWorld({ freezeCarveMode: 1, freezeCarveRangeCells: 8 })
    positionPlayer(world, 10, 6)
    world.tileMap.set(11, 5, 'brick') // wall between the player and the enemy
    placeEnemy(world, 11, 3)
    world.freezeTimer = 5000
    input.aggressive = true
    const ok = evalAggro(input, ctxFor(world, 'up'))
    expect(ok).toBe(true)
    expect(input._lastBranch).toBe('freezeCarve')
    expect(input._fire).toBe(true)
  })

  it('does not carve steel', () => {
    const { world, input } = setupWorld({ freezeCarveMode: 1 })
    positionPlayer(world, 10, 6)
    world.tileMap.set(11, 5, 'steel')
    placeEnemy(world, 11, 3)
    world.freezeTimer = 5000
    input.aggressive = true
    evalAggro(input, ctxFor(world, 'up'))
    expect(input._lastBranch).not.toBe('freezeCarve')
  })

  it('mode 0 never carves', () => {
    const { world, input } = setupWorld({ freezeCarveMode: 0 })
    positionPlayer(world, 10, 6)
    world.tileMap.set(11, 5, 'brick')
    placeEnemy(world, 11, 3)
    world.freezeTimer = 5000
    input.aggressive = true
    evalAggro(input, ctxFor(world, 'up'))
    expect(input._lastBranch).not.toBe('freezeCarve')
  })
})
