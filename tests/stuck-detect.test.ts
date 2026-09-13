/**
 * obs v3 sN4 / reward stuckTicks 共享判定的单测（dsf A4 同源 + ms F3 约束）。
 *
 * 语义锚 = 导出器历史实现（tools/sim/export-rl-rollout.ts）：
 *   停滞 tick = 玩家存活 且 中心 cell 不变 且 本 tick 未命中敌车（enemy_hit）。
 *   注意：cell 是 16px 粒度——慢速穿格（<16px/tick）也会连续同 cell ⇒ 累计，
 *   这是既有 reward 语义，共享实现必须逐字节复刻（不做"更好"的语义改写）。
 *
 * 覆盖：
 *   1. 纯函数（isStuckTick / nextStuckTicks / playerCenterCell）边界；
 *   2. Simulation 端到端（传送式确定性断言）；
 *   3. 快照 round-trip + 旧快照缺字段兼容（ms F3）；
 *   4. 决定性（§2.3）：同 seed 双跑序列逐 tick 一致。
 */
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
import { isStuckTick, nextStuckTicks, playerCenterCell } from '../src/game/stuck-detect'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { seedWorld } from './helpers'

/** 无 DOM 的最小 InputLike：方向/开火可编程（§14 无关——测试专用，不进 src/）。 */
class FakeInput implements InputLike {
  dir: Direction | null = null
  firing = false
  getMoveDirection(): Direction | null {
    return this.dir
  }
  isFiring(): boolean {
    return this.firing
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {
    this.dir = null
    this.firing = false
  }
}

/** 把玩家中心 cell 放到 (col,row)（placePlayer 惯例：x = col*16-8）。 */
function placePlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * 16 - 8
  p.y = row * 16 - 8
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.alive = true
}

function makeSim(seed = 42): { world: World; sim: Simulation; input: FakeInput } {
  const world = seedWorld(seed)
  const input = new FakeInput()
  const sim = new Simulation(world, input as never)
  world.startGame('hard', 'modern', 0)
  return { world, sim, input }
}

describe('stuck-detect 纯函数', () => {
  it('中心 cell 锚 = (x+16)/16（格锚点）', () => {
    const t = { x: 192, y: 192, alive: true } as never // (192+16)/16 = 13 → cell 13
    expect(playerCenterCell(t)).toEqual({ col: 13, row: 13 })
    expect(playerCenterCell(null)).toBe(null)
  })

  it('cell 不变 + 未命中 = 停滞；任一破坏 = 非停滞', () => {
    const c = { col: 12, row: 12 }
    expect(isStuckTick(true, c, c, false)).toBe(true)
    expect(isStuckTick(true, c, c, true)).toBe(false) // 本 tick 命中敌
    expect(isStuckTick(true, c, { col: 13, row: 12 }, false)).toBe(false) // 移动
    expect(isStuckTick(false, c, c, false)).toBe(false) // 死亡
    expect(isStuckTick(true, c, null, false)).toBe(false) // 首 tick / 快照恢复
  })

  it('nextStuckTicks：累计与清零', () => {
    const c = { col: 5, row: 5 }
    expect(nextStuckTicks(0, true, c, c, false)).toBe(1)
    expect(nextStuckTicks(299, true, c, c, false)).toBe(300)
    expect(nextStuckTicks(299, true, c, c, true)).toBe(0) // 命中敌 → 清零
    expect(nextStuckTicks(50, true, { col: 6, row: 5 }, c, false)).toBe(0)
  })
})

describe('Simulation 端到端（传送式确定性）', () => {
  it('idle 玩家：首 tick 0（prev=null），之后逐 tick 累计', () => {
    const { world, sim, input } = makeSim()
    input.dir = null
    placePlayer(world, 12, 12)
    const series: number[] = []
    for (let i = 0; i < 5; i++) {
      sim.tick()
      series.push(world.stuckTicks)
    }
    expect(series).toEqual([0, 1, 2, 3, 4])
  })

  it('跨 tick 移动（传送换 cell）：cell 变化 ⇒ 清零', () => {
    const { world, sim } = makeSim()
    placePlayer(world, 12, 12)
    sim.tick() // prev=(12,12), stuck=0
    sim.tick() // idle: stuck=1
    placePlayer(world, 14, 12) // 传送换 cell
    sim.tick()
    expect(world.stuckTicks).toBe(0) // cell 变化 ⇒ 清零
  })

  it('命中敌车标记（enemy_hit 同源）消费后清零：idle 但命中 ⇒ 不累计', () => {
    const { world, sim } = makeSim()
    placePlayer(world, 12, 12)
    sim.tick() // stuck=0, prev 置位
    world.playerHitEnemyThisTick = true // 模拟本 tick 玩家子弹命中敌车
    sim.tick()
    expect(world.stuckTicks).toBe(0) // 命中敌 ⇒ 清零
    expect(world.playerHitEnemyThisTick).toBe(false) // 消费后复位
    sim.tick()
    expect(world.stuckTicks).toBe(1) // 下一 tick 恢复累计
  })

  it('同 seed 双跑序列逐 tick 一致（§2.3 决定性，ms F3 golden 前提）', () => {
    const run = (): number[] => {
      const { world, sim, input } = makeSim(7)
      input.dir = null
      placePlayer(world, 12, 12)
      const s: number[] = []
      for (let i = 0; i < 60; i++) {
        sim.tick()
        s.push(world.stuckTicks)
      }
      return s
    }
    expect(run()).toEqual(run())
  })
})

describe('快照 round-trip（WorldSerializer）', () => {
  it('stuckTicks / prevStuckCell 入快照并恢复', () => {
    const { world, sim } = makeSim()
    placePlayer(world, 12, 12)
    for (let i = 0; i < 6; i++) sim.tick()
    expect(world.stuckTicks).toBeGreaterThan(0)
    const snap = cloneWorld(world)
    const before = { stuck: world.stuckTicks, cell: world.prevStuckCell }
    for (let i = 0; i < 5; i++) sim.tick() // 推进改变状态
    restoreWorld(world, snap)
    expect(world.stuckTicks).toBe(before.stuck)
    expect(world.prevStuckCell).toEqual(before.cell)
  })

  it('旧快照（无 stuckTicks/prevStuckCell 字段）恢复默认 0/null（ms F3 兼容）', () => {
    const { world, sim } = makeSim()
    placePlayer(world, 12, 12)
    for (let i = 0; i < 6; i++) sim.tick()
    const snap = cloneWorld(world) as unknown as Record<string, unknown>
    delete snap.stuckTicks
    delete snap.prevStuckCell
    world.stuckTicks = 999
    restoreWorld(world, snap as never)
    expect(world.stuckTicks).toBe(0)
    expect(world.prevStuckCell).toBe(null)
    expect(world.playerHitEnemyThisTick).toBe(false)
  })
})
