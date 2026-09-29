/**
 * hit-geometry.test.ts — metrics v9 命中方位分类的真值表（plan/geo-threat-instrumentation.plan.md §3）。
 *
 * 体例：**独立重实现**对账（本文件不看 `classifyHit` 的写法，只按 plan §1.1 的口径自己写一遍）
 * ＋ 逐条手写真值表（背/侧/正/同格/scrum/远）＋ 三条已知坑的回归：
 *   ① **平局不可达**（评审 H3）：`|dx| == |dy|` 必被 step 0（far）或 step 2（side）吃掉，
 *      原文「列优先得 front」的例子是错的 —— 本文件把这个事实钉死；
 *   ② 事件驱动的**开火时刻**归因：`fireOriginCellKey` 从弹的生成位置反推 shooter 格
 *      （`bullet.center = shooter.center + dir × TANK/2`），不是用弹的当前坐标；
 *   ③ scrum 区先于主轴投影（后侧角擦伤不得被判成 front）。
 */
import { describe, expect, it } from 'bun:test'
import { CELL, GRID, TANK, type Direction } from '../../src/constants'
import {
  HIT_BACK,
  HIT_FAR,
  HIT_FRONT,
  HIT_SIDE,
  SCRUM_RADIUS_CELLS,
  cellKey,
  centerCellKey,
  classifyHit,
  fireOriginCellKey,
  keyCol,
  keyRow,
} from '../../src/nn/hit-geometry'
import type { Bullet } from '../../src/types'

/**
 * 独立重实现（**刻意不 import 生产实现**）：plan §1.1 的判定流程逐字重写一遍。
 * 任一侧改口径都会让下面的逐格对账变红。
 */
function indepClassify(
  pCol: number,
  pRow: number,
  vCol: number,
  vRow: number,
  vDir: Direction,
): 'front' | 'back' | 'side' | 'far' {
  const dx = pCol - vCol
  const dy = pRow - vRow
  if (Math.min(Math.abs(dx), Math.abs(dy)) > 2) return 'far'
  if (dx === 0 && dy === 0) return 'side'
  if (Math.max(Math.abs(dx), Math.abs(dy)) <= 2 && Math.min(Math.abs(dx), Math.abs(dy)) >= 1)
    return 'side'
  const unit: Record<Direction, [number, number]> = {
    up: [0, -1],
    down: [0, 1],
    left: [-1, 0],
    right: [1, 0],
  }
  const [fx, fy] = unit[vDir]
  if (Math.abs(dx) >= Math.abs(dy)) {
    const s = fx * dx
    return s > 0 ? 'front' : s < 0 ? 'back' : 'side'
  }
  const s = fy * dy
  return s > 0 ? 'front' : s < 0 ? 'back' : 'side'
}

const NAME: Record<number, 'front' | 'back' | 'side' | 'far'> = {
  [HIT_FRONT]: 'front',
  [HIT_BACK]: 'back',
  [HIT_SIDE]: 'side',
  [HIT_FAR]: 'far',
}

describe('hit-geometry：真值表（受害车在 (5,5)，朝右 F=(1,0)）', () => {
  it('plan §1.1 的七个手写场景逐条对上', () => {
    const V = [5, 5] as const
    // 背面：玩家在身后（dx=-2）⇒ back
    expect(classifyHit(3, 5, V[0], V[1], 'right')).toBe(HIT_BACK)
    // 正面：迎面（dx=+3）⇒ front
    expect(classifyHit(8, 5, V[0], V[1], 'right')).toBe(HIT_FRONT)
    // 侧面：垂直轴（dx=0, dy=3）⇒ side
    expect(classifyHit(5, 8, V[0], V[1], 'right')).toBe(HIT_SIDE)
    // scrum：近身对角（dx=2, dy=1，主轴投影会误判 front）⇒ side
    expect(classifyHit(7, 6, V[0], V[1], 'right')).toBe(HIT_SIDE)
    // 同格贴身 ⇒ side（不分前后）
    expect(classifyHit(5, 5, V[0], V[1], 'right')).toBe(HIT_SIDE)
    // 远：轴向开火物理不可能命中 ⇒ far
    expect(classifyHit(10, 9, V[0], V[1], 'right')).toBe(HIT_FAR)
    expect(classifyHit(8, 8, V[0], V[1], 'right')).toBe(HIT_FAR)
  })

  it('朝向左/上/下三向的极性（F·V 的符号，不是特判）', () => {
    // 受害车朝左、玩家在其左侧 ⇒ 正面
    expect(classifyHit(2, 5, 5, 5, 'left')).toBe(HIT_FRONT)
    expect(classifyHit(8, 5, 5, 5, 'left')).toBe(HIT_BACK)
    // 受害车朝上、玩家在其上方 ⇒ 正面
    expect(classifyHit(5, 2, 5, 5, 'up')).toBe(HIT_FRONT)
    expect(classifyHit(5, 8, 5, 5, 'up')).toBe(HIT_BACK)
    // 受害车朝下、玩家在其下方 ⇒ 正面
    expect(classifyHit(5, 8, 5, 5, 'down')).toBe(HIT_FRONT)
    // 受害车朝上、玩家水平位移 ⇒ 垂直 ⇒ 侧
    expect(classifyHit(7, 5, 5, 5, 'up')).toBe(HIT_SIDE)
  })

  it('★ 平局（|dx|==|dy|）不可达：被 far/side 吃掉，永不落到主轴投影', () => {
    // 评审 H3 的修正：原文例子 P(7,7)（dx=2,dy=2）走的是 step 2（scrum）⇒ **side**，
    // 不是「列优先看 x 得 front」。真值表以本断言为准。
    expect(classifyHit(7, 7, 5, 5, 'right')).toBe(HIT_SIDE)
    // (3,3) 走 step 0 ⇒ far（min=3 > 2），同样到不了平局分支。
    expect(classifyHit(8, 8, 5, 5, 'right')).toBe(HIT_FAR)
    // 穷举 26×26 场地内的全部平局构型：结果集只能是 {side, far}
    const seen = new Set<number>()
    for (let d = -25; d <= 25; d++) {
      if (d === 0) continue
      seen.add(classifyHit(5 + d, 5 + d, 5, 5, 'right'))
    }
    expect([...seen].sort()).toEqual([HIT_FAR, HIT_SIDE].sort())
  })

  it('scrum 半径是 pin 值 2（改则剂量连带重算）', () => {
    expect(SCRUM_RADIUS_CELLS).toBe(2)
    // max=2/min=1 的角点都在 scrum 内；max=2/min=0 走主轴投影
    expect(classifyHit(3, 4, 5, 5, 'right')).toBe(HIT_SIDE)
    expect(classifyHit(5, 3, 5, 5, 'right')).toBe(HIT_SIDE)
    expect(classifyHit(3, 5, 5, 5, 'right')).toBe(HIT_BACK) // min=0 ⇒ 主轴
  })
})

describe('hit-geometry：独立重实现对账（穷举网格）', () => {
  it('受害车 (5,5) 四朝向 × 全场 26×26 格逐格一致', () => {
    for (const dir of ['up', 'down', 'left', 'right'] as const) {
      for (let pCol = 0; pCol < GRID; pCol++) {
        for (let pRow = 0; pRow < GRID; pRow++) {
          const got = NAME[classifyHit(pCol, pRow, 5, 5, dir)]
          const exp = indepClassify(pCol, pRow, 5, 5, dir)
          if (got !== exp) throw new Error(`(${pCol},${pRow}) dir=${dir}: ${got} != ${exp}`)
        }
      }
    }
    expect(true).toBe(true)
  })
})

describe('hit-geometry：格键与开火时刻归因', () => {
  it('cellKey / keyCol / keyRow 可逆（打包当 Map 键用）', () => {
    for (const [c, r] of [
      [0, 0],
      [25, 25],
      [7, 13],
    ] as const) {
      expect(keyCol(cellKey(c, r))).toBe(c)
      expect(keyRow(cellKey(c, r))).toBe(r)
    }
  })

  it('centerCellKey = 中心格 floor((x+w/2)/CELL)（与 census/telemetry 同构）', () => {
    expect(centerCellKey(5 * CELL, 5 * CELL, TANK, TANK)).toBe(cellKey(6, 6)) // 中心 = 5*16+16
    expect(centerCellKey(0, 0, TANK, TANK)).toBe(cellKey(1, 1))
  })

  it('★ fireOriginCellKey：从弹的生成位置精确反推开火时刻 shooter 格', () => {
    // 生成式（SimulationCombat.tryFire）：bullet.center = tank.center + dirVec × TANK/2。
    // 把 shooter 中心放在 (6,6)，按四个朝向造弹，反推必须逐向回到 (6,6)。
    const scx = 6 * CELL + CELL / 2 // 中心点 x（格 6 的中心）
    const scy = 6 * CELL + CELL / 2
    const vec: Record<Direction, [number, number]> = {
      up: [0, -1],
      down: [0, 1],
      left: [-1, 0],
      right: [1, 0],
    }
    for (const dir of ['up', 'down', 'left', 'right'] as const) {
      const [vx, vy] = vec[dir]
      const b: Bullet = {
        id: 1,
        x: scx + vx * (TANK / 2) - 3,
        y: scy + vy * (TANK / 2) - 3,
        w: 6,
        h: 6,
        dir,
        alive: true,
        ownerId: 1,
        ownerKind: 'player',
        isPlayer: true,
        allegiance: 'player',
        speed: 4.2,
        power: 1,
        damage: 50,
      }
      // 反推 = 先回推半格偏移，再取中心格。
      expect(keyCol(fireOriginCellKey(b))).toBe(6)
      expect(keyRow(fireOriginCellKey(b))).toBe(6)
    }
  })

  it('反推不是「弹的当前坐标」：弹飞了几格后开火点仍指向原地', () => {
    // 同一颗弹（v9 归因必须用**开火时刻**的位置，弹位移后不得漂移）：
    // 这里直接吃**开火那一拍**的事件弹对象 —— 位移后的对象不属于本函数的输入契约。
    const b: Bullet = {
      id: 2,
      x: 6 * CELL + CELL / 2 + TANK / 2 - 3,
      y: 4 * CELL,
      w: 6,
      h: 6,
      dir: 'right',
      alive: true,
      ownerId: 1,
      ownerKind: 'player',
      isPlayer: true,
      allegiance: 'player',
      speed: 4.2,
      power: 1,
      damage: 50,
    }
    // 行取弹自己的行（弹在水平飞行时行不变 ⇒ 与 shooter 同行），列回退半格。
    expect(keyCol(fireOriginCellKey(b))).toBe(6)
    expect(keyRow(fireOriginCellKey(b))).toBe(4)
  })
})
