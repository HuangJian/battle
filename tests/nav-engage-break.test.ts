import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { navEngageBreakImpl } from '../src/ai/god/think'
import { CELL } from '../src/constants'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-30 navigate 停滞打断（navEngageBreakMode）。
 *
 * 数据背景（nav-stall-audit，hard 35×60）：navigate 长段占 13.7% tick、段内击杀率
 * 0.33×；452 个「贴身空转」段中位 355 拍只开 7 拍火。规则：导航中若 aimDir 方向
 * ≤5 格内有敌人且射界干净、本拍没开火、基地不受威胁 ⇒ 停步开火。
 *
 * 用例锁定：
 *   1. mode 0 → 不动（byte-identical）。
 *   2. 命中条件 → 停步 + 开火 + 分支 navEngage。
 *   3. 敌人超距离 → 不打断。
 *   4. 基地受威胁 → 不打断（回防优先）。
 *   5. 非 navigate 分支 → 不打断。
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

function positionPlayer(
  world: World,
  col: number,
  row: number,
  dir: 'up' | 'down' | 'left' | 'right' = 'up',
): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.dir = dir
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
}

const ctxFor = (world: World, aimDir: 'up' | 'down' | 'left' | 'right' | null): DecisionContext => {
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

describe('navEngageBreakImpl', () => {
  it('mode 0 does nothing', () => {
    const { world, input } = setupWorld({ navEngageBreakMode: 0 })
    positionPlayer(world, 10, 6)
    placeEnemy(world, 10, 4)
    input._lastBranch = 'navigate'
    input._fire = false
    input._moveDir = 'up'
    expect(navEngageBreakImpl(input, ctxFor(world, 'up'))).toBe(false)
    expect(input._fire).toBe(false)
    expect(input._moveDir).toBe('up')
  })

  it('stops and fires at a close aligned enemy during navigate', () => {
    const { world, input } = setupWorld({ navEngageBreakMode: 1, navEngageBreakRangeCells: 5 })
    positionPlayer(world, 10, 6)
    placeEnemy(world, 10, 4)
    input._lastBranch = 'navigate'
    input._fire = false
    input._moveDir = 'up'
    expect(navEngageBreakImpl(input, ctxFor(world, 'up'))).toBe(true)
    expect(input._fire).toBe(true)
    expect(input._moveDir).toBeNull()
    expect(input._lastBranch).toBe('navEngage')
  })

  it('does not break for an enemy beyond the range', () => {
    const { world, input } = setupWorld({ navEngageBreakMode: 1, navEngageBreakRangeCells: 5 })
    positionPlayer(world, 10, 12)
    placeEnemy(world, 10, 5) // 7 cells up
    input._lastBranch = 'navigate'
    input._fire = false
    input._moveDir = 'up'
    expect(navEngageBreakImpl(input, ctxFor(world, 'up'))).toBe(false)
    expect(input._fire).toBe(false)
  })

  it('does not break while the base is under threat', () => {
    const { world, input } = setupWorld({ navEngageBreakMode: 1 })
    positionPlayer(world, 10, 6)
    placeEnemy(world, 10, 4)
    placeEnemy(world, 12, 20) // base box
    input._baseUnderThreatCache = null
    expect(input.isBaseUnderThreat()).toBe(true)
    input._lastBranch = 'navigate'
    input._fire = false
    input._moveDir = 'up'
    expect(navEngageBreakImpl(input, ctxFor(world, 'up'))).toBe(false)
  })

  it('does not break outside the navigate branch', () => {
    const { world, input } = setupWorld({ navEngageBreakMode: 1 })
    positionPlayer(world, 10, 6)
    placeEnemy(world, 10, 4)
    input._lastBranch = 'aggressive'
    input._fire = false
    input._moveDir = 'up'
    expect(navEngageBreakImpl(input, ctxFor(world, 'up'))).toBe(false)
  })
})
