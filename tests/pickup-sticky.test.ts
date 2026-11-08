import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { evalPickupHigh } from '../src/ai/god/candidates/PickupHigh'
import { commitPowerupTail } from '../src/ai/god/candidates/shared'
import { CELL } from '../src/constants'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * 2026-11-01 拾取粘性（pickupCommitMode）。
 *
 * 数据：A* 4–8 格的冻/炸 76–86% 被选为目标却 0% 拾取——承诺被后续候选/防守打断。
 * 规则：commitPowerupTail 的目标在 pickupStickyTicks 内跨 tick 存活；基地受威胁、
 * 道具消失或超时释放。
 *
 * 用例：粘性优先于更近的新道具；道具消失即释放；基地受威胁即释放。
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

function addItem(
  world: World,
  type: string,
  col: number,
  row: number,
): { id: number; alive: boolean } {
  const pu = {
    id: world.powerUps.length + 1,
    type,
    x: col * CELL,
    y: row * CELL,
    w: CELL,
    h: CELL,
    alive: true,
    despawnTimer: 20000,
  } as any
  world.powerUps.push(pu)
  return pu
}

const ctxFor = (world: World): DecisionContext => {
  const p = world.player!
  return {
    w: world,
    p,
    pcx: p.x + p.w / 2,
    pcy: p.y + p.h / 2,
    onCooldown: false,
    aimDir: null,
    threat: null,
    shielded: false,
  }
}

describe('pickupCommitMode (sticky pickup)', () => {
  it('keeps the committed item even when a closer one appears', () => {
    const { world, input } = setupWorld({ pickupCommitMode: 1, pickupStickyTicks: 60 })
    positionPlayer(world, 10, 20)
    const a = addItem(world, 'freeze', 10, 14) // 6 cells up
    commitPowerupTail(input, ctxFor(world), { col: 10, row: 14 })
    expect(a.alive).toBe(true)
    // A closer bomb appears 1 cell to the right — the sticky commit must win.
    addItem(world, 'bomb', 11, 20)
    const ok = evalPickupHigh(input, ctxFor(world))
    expect(ok).toBe(true)
    expect(input._navIntentCol).toBe(10)
    expect(input._navIntentRow).toBe(14)
  })

  it('releases when the committed item disappears', () => {
    const { world, input } = setupWorld({ pickupCommitMode: 1, pickupStickyTicks: 60 })
    positionPlayer(world, 10, 20)
    const a = addItem(world, 'freeze', 10, 14)
    commitPowerupTail(input, ctxFor(world), { col: 10, row: 14 })
    a.alive = false
    evalPickupHigh(input, ctxFor(world))
    expect(input._pickupStickyCol).toBe(-1)
  })

  it('releases when the base is threatened', () => {
    const { world, input } = setupWorld({ pickupCommitMode: 1, pickupStickyTicks: 60 })
    positionPlayer(world, 10, 20)
    addItem(world, 'freeze', 10, 14)
    commitPowerupTail(input, ctxFor(world), { col: 10, row: 14 })
    placeEnemy(world, 12, 21) // base box
    input._baseUnderThreatCache = null
    evalPickupHigh(input, ctxFor(world))
    expect(input._pickupStickyCol).toBe(-1)
  })

  it('mode 0 never records a sticky commit', () => {
    const { world, input } = setupWorld({ pickupCommitMode: 0 })
    positionPlayer(world, 10, 20)
    addItem(world, 'freeze', 10, 14)
    commitPowerupTail(input, ctxFor(world), { col: 10, row: 14 })
    expect(input._pickupStickyCol).toBe(-1)
  })
})
