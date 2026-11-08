import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findUrgentPowerUpTargetImpl } from '../src/ai/god/StrategyPlanner'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-10-31 用户规则：freeze / bomb 豁免出生带门（highPickupSpawnRowExempt）。
 *
 * 实测（hard 35×60，A* 口径）：A* 4–8 格的冻/炸掉落 175 个，拾取 0 个；
 * 其中 94 个正好落在 `pickupPrioritySpawnRowMax = 3` 的顶部出生带里。
 * 本旋钮让这两种道具无视出生带门（其它门——敌近 / 路线危险——仍然生效）。
 *
 * 用例锁定：默认不豁免；开启后 freeze/bomb 在顶部 3 行可被选为紧急目标；star 不受影响。
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
  p.spawnTimer = 0
}

function addItem(world: World, type: string, col: number, row: number): void {
  world.powerUps.push({
    id: world.powerUps.length + 1,
    type,
    x: col * CELL,
    y: row * CELL,
    w: CELL,
    h: CELL,
    alive: true,
    despawnTimer: 20000,
  } as any)
}

describe('highPickupSpawnRowExempt', () => {
  it('default: top-band freeze is not an urgent target', () => {
    const { world, input } = setupWorld({ highPickupSpawnRowExempt: 0 })
    positionPlayer(world, 10, 6)
    addItem(world, 'freeze', 10, 2)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'high')).toBeNull()
  })

  it('exempt: top-band freeze becomes an urgent target', () => {
    const { world, input } = setupWorld({ highPickupSpawnRowExempt: 1 })
    positionPlayer(world, 10, 6)
    addItem(world, 'freeze', 10, 2)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'high')).not.toBeNull()
  })

  it('exempt: top-band bomb becomes an urgent target', () => {
    const { world, input } = setupWorld({ highPickupSpawnRowExempt: 1 })
    positionPlayer(world, 10, 6)
    addItem(world, 'bomb', 10, 2)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'high')).not.toBeNull()
  })

  it('danger gate still blocks a near-freeze when an enemy is closer to it', () => {
    const { world, input } = setupWorld({ highPickupSpawnRowExempt: 1 })
    positionPlayer(world, 10, 10)
    addItem(world, 'freeze', 10, 6)
    placeEnemy(world, 10, 4) // closer to the item than the player
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'high')).toBeNull()
  })

  it('highPickupDangerExempt lifts the route-danger gate for freeze', () => {
    const { world, input } = setupWorld({
      highPickupSpawnRowExempt: 1,
      highPickupDangerExempt: 1,
    })
    positionPlayer(world, 10, 10)
    addItem(world, 'freeze', 10, 6)
    placeEnemy(world, 10, 4)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'high')).not.toBeNull()
  })

  it('danger exempt does not apply to star', () => {
    const { world, input } = setupWorld({ highPickupDangerExempt: 1 })
    positionPlayer(world, 10, 10)
    addItem(world, 'star', 10, 6)
    placeEnemy(world, 10, 4)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'all')).toBeNull()
  })

  it('exempt does not apply to star', () => {
    const { world, input } = setupWorld({ highPickupSpawnRowExempt: 1 })
    positionPlayer(world, 10, 6)
    addItem(world, 'star', 10, 2)
    const p = world.player!
    expect(findUrgentPowerUpTargetImpl(input, p.x + p.w / 2, p.y + p.h / 2, 'all')).toBeNull()
  })
})
