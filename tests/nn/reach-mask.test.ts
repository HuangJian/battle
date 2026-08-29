import { describe, it, expect } from 'bun:test'
import { World } from '../../src/game/World'
import { seedWorld } from '../helpers'
import { STAGES } from '../../src/config/stages'
import { GRID } from '../../src/constants'
import { findPath } from '../../src/ai/god/pathfind'
import { isPassable } from '../../src/utils/grid-search'
import {
  ReachMasker,
  selectGoal,
  isVertexStaticallyBlocked,
  REACH_UNREACHABLE,
} from '../../src/ai/goal/reach-mask'
import type { StageData } from '../../src/types'

/** 空场 + 经典基地 + 8 砖保护环（与 tests/battlement-carve-path.test.ts 同款）。 */
function ringArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    if (r === 23) row = row.slice(0, 11) + 'bbbb' + row.slice(15)
    if (r === 24 || r === 25) row = row.slice(0, 11) + 'b' + row.slice(12)
    if (r === 24 || r === 25) row = row.slice(0, 14) + 'b' + row.slice(15)
    tiles.push(row)
  }
  return { id: 9995, name: 'Ring Arena', tiles, enemies: ['basic'] }
}

/** 上下两半被整行砖墙隔开的 arena（row 12 一整行砖）——验证 carve 代价层。 */
function brickWallArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    if (r === 12) row = 'b'.repeat(GRID)
    tiles.push(row)
  }
  return { id: 9996, name: 'Brick Wall Arena', tiles, enemies: ['basic'] }
}

/** 逐元素 Object.is 比较（-0 / -Infinity 语义严格一致）。 */
function sameMask(a: Float32Array, b: Float32Array): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (!Object.is(a[i], b[i])) return false
  return true
}

function loadTiles(stage: StageData): World {
  const world = seedWorld(42)
  world.loadStageData(stage, 0)
  return world
}

describe('reach-mask（T3 子件 / §9.4）', () => {
  it('§9.4.0 第 25 行/列 51 格永久硬遮 −Infinity', () => {
    const world = loadTiles(ringArena())
    const rm = new ReachMasker()
    rm.compute(world.tileMap, 8, 24)
    const mask = rm.mask(0.5)
    let hardMasked = 0
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (r === 25 || c === 25) {
          expect(mask[r * GRID + c]).toBe(-Infinity)
          hardMasked++
        } else if (r * GRID + c === 0) {
          expect(mask[0]).toBe(0) // start 本身 walk=0
        }
      }
    }
    expect(hardMasked).toBe(51) // 26 + 26 − 1
  })

  it('carve 层：整行砖墙对面的格 k>0（mask 为负），同侧 k=0', () => {
    const world = loadTiles(brickWallArena())
    const rm = new ReachMasker()
    rm.compute(world.tileMap, 8, 20) // 砖墙下方
    const k = rm.k
    expect(k[20 * GRID + 8]).toBe(0) // 同侧 walk
    expect(k[14 * GRID + 8]).toBe(0) // 墙下仍是同侧
    expect(k[10 * GRID + 8]).toBe(2) // 墙上方：2×2 前缘两格都要凿 ⇒ k=2
    expect(k[2 * GRID + 8]).toBe(2) // 墙上方远处：最优路径仍只需凿 2 块
    const mask = rm.mask(0.5)
    expect(mask[20 * GRID + 8]).toBe(0)
    expect(mask[10 * GRID + 8]).toBe(-1)
    // λ 只重着色，不重跑 Dijkstra
    const mask2 = rm.mask(1.0)
    expect(mask2[10 * GRID + 8]).toBe(-2)
  })

  it('钢/基地/水足印 ⇒ −Infinity；砖足印不算静态阻断（E3 纪律 §6.6）', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) {
      let row = ''
      for (let c = 0; c < GRID; c++) row += '.'
      if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
      // 钢柱（2×2 足印完全堵死）：cols 4-5, rows 2-3
      if (r === 2 || r === 3) row = row.slice(0, 4) + 'ss' + row.slice(6)
      // 水域：cols 16-17, rows 2-3
      if (r === 2 || r === 3) row = row.slice(0, 16) + 'ww' + row.slice(18)
      tiles.push(row)
    }
    const world = loadTiles({ id: 9997, name: 'Static', tiles, enemies: ['basic'] })
    expect(isVertexStaticallyBlocked(world.tileMap, 4, 2)).toBe(true) // 钢
    expect(isVertexStaticallyBlocked(world.tileMap, 3, 2)).toBe(true) // 足印 3-4 列：col4 是钢 ⇒ 堵
    expect(isVertexStaticallyBlocked(world.tileMap, 6, 2)).toBe(false) // 足印 6-7 列：空地
    expect(isVertexStaticallyBlocked(world.tileMap, 16, 2)).toBe(true) // 水
    expect(isVertexStaticallyBlocked(world.tileMap, 12, 24)).toBe(true) // 基地本体
    expect(isVertexStaticallyBlocked(world.tileMap, 25, 25)).toBe(true) // 越界顶点
    expect(isVertexStaticallyBlocked(world.tileMap, 8, 10)).toBe(false) // 空地
    const rm = new ReachMasker()
    rm.compute(world.tileMap, 8, 20)
    const mask = rm.mask(0.5)
    expect(mask[2 * GRID + 4]).toBe(-Infinity) // 钢柱顶点
    expect(mask[2 * GRID + 16]).toBe(-Infinity) // 水面顶点
  })

  it('确定性：不同实例/不同调用顺序 ⇒ 逐字节一致', () => {
    const world = loadTiles(ringArena())
    const a = new ReachMasker()
    a.compute(world.tileMap, 8, 24)
    const aMask = Float32Array.from(a.mask(0.5))
    a.compute(world.tileMap, 3, 3)
    const aMask2 = Float32Array.from(a.mask(0.5))

    const b = new ReachMasker()
    b.compute(world.tileMap, 3, 3)
    const bMask2 = b.mask(0.5)
    b.compute(world.tileMap, 8, 24)
    const bMask = b.mask(0.5)

    expect(sameMask(aMask, bMask)).toBe(true)
    expect(sameMask(aMask2, bMask2)).toBe(true)
  })

  it('memo：同 revision+start 复用；地形变更（revision bump）后重算', () => {
    const world = loadTiles(brickWallArena())
    const rm = new ReachMasker()
    rm.compute(world.tileMap, 8, 20)
    expect(rm.k[10 * GRID + 8]).toBe(2) // 墙上方需凿 2 块（2×2 前缘）
    // 凿开 row12 的 (8,12)：revision bump ⇒ 前缘剩 (9,12) 一块 ⇒ k=1
    world.tileMap.set(8, 12, 'empty')
    rm.compute(world.tileMap, 8, 20)
    expect(rm.k[10 * GRID + 8]).toBe(1)
    // 再凿 (9,12)：前缘全开 ⇒ 纯行走可达 k=0
    world.tileMap.set(9, 12, 'empty')
    rm.compute(world.tileMap, 8, 20)
    expect(rm.k[10 * GRID + 8]).toBe(0)
  })

  it('与 findPath 交叉验证：k=0 ⟺ 纯行走可达；k<∞ ⟺ breakBrick 可达（真实关卡）', () => {
    // 用两关真实数据：首关 + Battlement（砖密原型，§1.1）
    const stageIdxs = [0, 33]
    for (const si of stageIdxs) {
      const stage = STAGES[si]
      if (!stage) continue
      const world = loadTiles(stage as StageData)
      // 起点取第一个静态可站顶点
      let sc = -1
      let sr = -1
      outer: for (let r = 0; r < GRID - 1; r++) {
        for (let c = 0; c < GRID - 1; c++) {
          if (isPassable(world.tileMap, c, r, false, false)) {
            sc = c
            sr = r
            break outer
          }
        }
      }
      expect(sc).toBeGreaterThanOrEqual(0)
      const rm = new ReachMasker()
      rm.compute(world.tileMap, sc, sr)
      const k = rm.k
      let walkAgree = 0
      let carveAgree = 0
      let checked = 0
      for (let r = 0; r < GRID - 1; r++) {
        for (let c = 0; c < GRID - 1; c++) {
          const from = { col: sc, row: sr }
          const to = { col: c, row: r }
          const walk = findPath(world.tileMap, from, to) !== null
          const carve = findPath(world.tileMap, from, to, { breakBrick: true }) !== null
          const ki = k[r * GRID + c]
          // findPath quick-reject 要求终点足印"当前可站"：brick 足印在 walk 下不可站
          // 但 breakBrick 下可站。交叉验证断言：k=0 ⟺ walk；k<∞ ⟺ carve。
          if ((ki === 0) === walk) walkAgree++
          if ((ki !== REACH_UNREACHABLE) === carve) carveAgree++
          checked++
        }
      }
      expect(walkAgree).toBe(checked)
      expect(carveAgree).toBe(checked)
    }
  })

  it('GC 断言：连续 compute（交替起点强制真算）1000 次 heapUsed 增量 < 1KB（评审 a2）', () => {
    const world = loadTiles(ringArena())
    const rm = new ReachMasker()
    // 预热（触发 V8/FFI 内部一次性分配）
    for (let i = 0; i < 20; i++) rm.compute(world.tileMap, i % 2 === 0 ? 8 : 9, 20)
    globalThis.gc?.()
    const before = process.memoryUsage().heapUsed
    for (let i = 0; i < 1000; i++) {
      rm.compute(world.tileMap, i % 2 === 0 ? 8 : 9, 20)
      rm.mask(0.5)
    }
    const after = process.memoryUsage().heapUsed
    expect(after - before).toBeLessThan(1024)
  })

  it('无效起点（足印被钢堵死）⇒ 全图不可达，compute 返回 false', () => {
    const tiles: string[] = []
    for (let r = 0; r < GRID; r++) {
      let row = ''
      for (let c = 0; c < GRID; c++) row += '.'
      if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
      // 完全围死 (8,20)：足印 rows 20-21 cols 8-9 + 四周钢
      if (r === 19) row = row.slice(0, 7) + 'ssss' + row.slice(11)
      if (r === 22) row = row.slice(0, 7) + 'ssss' + row.slice(11)
      if (r === 20 || r === 21) {
        row = row.slice(0, 7) + 's' + row.slice(8)
        row = row.slice(0, 10) + 's' + row.slice(11)
      }
      tiles.push(row)
    }
    const world = loadTiles({ id: 9998, name: 'Sealed', tiles, enemies: ['basic'] })
    const rm = new ReachMasker()
    // 起点足印本身是空地（坦克站在钢盒里）⇒ 合法但与外界隔绝
    const ok = rm.compute(world.tileMap, 8, 20)
    expect(ok).toBe(true)
    const mask = rm.mask(0.5)
    expect(mask[20 * GRID + 8]).toBe(0) // 起点 walk=0
    expect(mask[10 * GRID + 10]).toBe(-Infinity) // 盒外全部不可达
    // 起点足印直接压钢 ⇒ 无效起点，compute 返回 false
    const rm2 = new ReachMasker()
    expect(rm2.compute(world.tileMap, 7, 20)).toBe(false) // 足印 cols 7-8 含钢
    expect(rm2.mask(0.5)[10 * GRID + 10]).toBe(-Infinity)
  })
})

describe('selectGoal（§9.4.1 / §9.4.2）', () => {
  it('argmax(heat+mask)：硬遮跳过、carve 扣分生效', () => {
    const heat = new Float32Array(676)
    const mask = new Float32Array(676).fill(0)
    mask[100] = -Infinity
    mask[200] = -0.5 // carve k=1, λ=0.5
    heat[100] = 99 // 被硬遮，即使 heat 最大也不可选
    heat[200] = 2.0 // 2.0 − 0.5 = 1.5
    heat[300] = 1.0
    expect(selectGoal(heat, mask)).toBe(200)
  })

  it('平局取索引最小（行主序，§9.4.2 防漂移断言）', () => {
    const heat = new Float32Array(676)
    const mask = new Float32Array(676).fill(0)
    expect(selectGoal(heat, mask)).toBe(0) // 全 0 平局 → 索引 0
    heat[150] = 0.5
    heat[42] = 0.5
    expect(selectGoal(heat, mask)).toBe(42) // 严格 > ⇒ 平局保留先到的低索引
    // λ 改变 tie：heat[150]=1.0(k=1,mask=-0.5) vs heat[42]=0.5(k=0) ⇒ 平局 → 42
    const heat2 = new Float32Array(676)
    heat2[150] = 1.0
    heat2[42] = 0.5
    const mask2 = new Float32Array(676).fill(0)
    mask2[150] = -0.5
    expect(selectGoal(heat2, mask2)).toBe(42)
  })

  it('全遮 ⇒ 返回 −1（有序回退入口）', () => {
    const heat = new Float32Array(676)
    const mask = new Float32Array(676).fill(-Infinity)
    expect(selectGoal(heat, mask)).toBe(-1)
  })
})
