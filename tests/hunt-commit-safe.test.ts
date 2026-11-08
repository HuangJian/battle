import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-30 有条件追击承诺（huntCommitSafeTicks）。
 *
 * 背景：无条件 `huntCommitTicks=120` 在 hard 35×60 实测 −67（换目标是防御响应的
 * 必要行为）。本规则只在**基地不受威胁**时锁定追击目标 N 拍；基地一受威胁立刻释放。
 *
 * 用例锁定：
 *   1. mode 0 → 不写承诺（byte-identical）。
 *   2. 基地安全 → selectTarget 锁定当时的目标；更近的敌人出现也不换。
 *   3. 基地受威胁 → 承诺立刻释放，回落到自由选择。
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
}

describe('huntCommitSafeTicks', () => {
  it('mode 0 writes no commit', () => {
    const { world, input } = setupWorld({ huntCommitSafeTicks: 0 })
    positionPlayer(world, 10, 20)
    placeEnemy(world, 10, 15)
    input.selectTarget(input.playerCell())
    expect(input._huntSafeCommitId).toBe(-1)
  })

  it('locks the target while the base is safe', () => {
    const { world, input } = setupWorld({ huntCommitSafeTicks: 60 })
    positionPlayer(world, 10, 20)
    const a = placeEnemy(world, 10, 15) // 3 cells up — nearest
    placeEnemy(world, 14, 16) // 8 cells right
    const first = input.selectTarget(input.playerCell())
    expect(first).not.toBeNull()
    expect(input._huntSafeCommitId).toBe(a.id)
    // A much closer enemy appears — the commit must keep the original target.
    input.endFrame()
    placeEnemy(world, 11, 17)
    const second = input.selectTarget(input.playerCell())
    expect(second).not.toBeNull()
    expect(second!.col).toBe(first!.col)
    expect(second!.row).toBe(first!.row)
  })

  it('releases the commit as soon as the base is threatened', () => {
    const { world, input } = setupWorld({ huntCommitSafeTicks: 60 })
    positionPlayer(world, 10, 20)
    placeEnemy(world, 10, 15)
    input.selectTarget(input.playerCell())
    expect(input._huntSafeCommitId).toBeGreaterThanOrEqual(0)
    input.endFrame()
    placeEnemy(world, 12, 21) // base box
    input._baseUnderThreatCache = null
    expect(input.isBaseUnderThreat()).toBe(true)
    input.selectTarget(input.playerCell())
    expect(input._huntSafeCommitId).toBe(-1)
  })
})
