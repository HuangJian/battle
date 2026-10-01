/**
 * metrics-v10-exposure.test.ts — metrics v10 四族差距列的判定单测
 * （plan/metrics-v10-gap-columns.plan.md §3）。
 *
 * 覆盖 v10 新增的三个共享谓词（`src/nn/danger-metrics.ts`，两个导出器同一实现）：
 *   · `alignedEnemyCount`   —— 被包围计数（19px 带 + 轴遮挡 + 已激活 + 存活）；
 *   · `nearestEnemyDistPx`  —— 最近敌距（px，哨兵 −1）；
 *   · `damageClusterStats`  —— 伤害成簇（连击数 / 120t 窗内最大承伤）。
 *
 * 体例仿 `tests/sim/danger-metrics.test.ts`：**独立重实现**对账（本文件不看生产 helper
 * 的实现，只按口径自己写一遍）+ 边界用例 + 常数冻结值。
 *
 * 口径（§71 ②b 实测定案，勿改）：
 *   ① 带宽 `THREAT_ALIGN_BAND_PX` = 19px（= 坦克半宽 16 + 子弹半高 3 = 「这一发打得中我」）；
 *      **不是** 12px 旧带（实测读数腰斩且翻符号）、**不是** naive「中心格同行列」（直接归零）；
 *   ② 不带朝向、不带半径上限（lane 是 lane）；③ 判墙体遮挡（`TileMap.blocksBullet`）。
 * 夹具纪律（plan §3 / v9 M4）：地形**显式清空**，否则测到的是「那一格砖」。
 */
import { describe, expect, it } from 'bun:test'
import { join } from 'node:path'
import { CELL, GRID, TANK } from '../../src/constants'
import {
  DMG_BURST_TICKS,
  ENEMY_DIST_SENTINEL,
  NEAR_ENEMY_BAND_PX,
  THREAT_ALIGN_BAND,
  THREAT_ALIGN_BAND_PX,
  alignedEnemyCount,
  damageClusterStats,
  nearestEnemyDistPx,
} from '../../src/nn/danger-metrics'
import type { World } from '../../src/game/World'
import { placeEnemy, positionPlayer, seedWorld } from '../helpers'

const PCOL = 5
const PROW = 5

/** 建一个「有玩家、无敌人」的干净世界（startGame 才建玩家坦克）。 */
function worldWithPlayer(seed = 1): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  positionPlayer(w, PCOL, PROW)
  emptyField(w)
  return w
}

/** 把整张场地清成空地：让「带宽/同行列」用例只测谓词本身（遮挡另设专项）。 */
function emptyField(w: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
}

/** 格 → 中心像素（与 `placeEnemy` 的 `col*CELL` 左上角约定配套）。 */
function cellCenter(col: number, row: number): { cx: number; cy: number } {
  return { cx: col * CELL + TANK / 2, cy: row * CELL + TANK / 2 }
}

/** 玩家中心（px）。 */
function playerCenter(w: World): { cx: number; cy: number } {
  const p = w.player!
  return { cx: p.x + p.w / 2, cy: p.y + p.h / 2 }
}

/**
 * 独立重实现（**刻意不 import 生产内部实现**）：只按口径读 World 原始字段。
 * 任何一侧改口径都会让下面的逐值对账变红。
 */
function indepAlignedCount(w: World): number {
  const p = w.player
  if (!p || !p.alive) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const band = TANK / 2 + 3 // 19px：坦克半宽 + 子弹半高（BULLET=6 ⇒ /2=3）
  const colLow = Math.floor(p.x / CELL)
  const colHigh = Math.floor((p.x + p.w - 1) / CELL)
  const rowLow = Math.floor(p.y / CELL)
  const rowHigh = Math.floor((p.y + p.h - 1) / CELL)
  const blocks = (col: number, row: number): boolean => {
    const t = w.tileMap.get(col, row)
    return t === 'brick' || t === 'steel' || t === 'base'
  }
  const occluded = (sc: number, sr: number, vertical: boolean): boolean => {
    if (vertical) {
      if (sr < rowLow) {
        for (let r = sr + 1; r < rowLow; r++) if (blocks(sc, r)) return true
      } else {
        for (let r = sr - 1; r > rowHigh; r--) if (blocks(sc, r)) return true
      }
      return false
    }
    if (sc < colLow) {
      for (let c = sc + 1; c < colLow; c++) if (blocks(c, sr)) return true
    } else {
      for (let c = sc - 1; c > colHigh; c--) if (blocks(c, sr)) return true
    }
    return false
  }
  let n = 0
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const sameCol = Math.abs(tx - pcx) < band
    const sameRow = Math.abs(ty - pcy) < band
    if (!sameCol && !sameRow) continue
    const vertical = sameCol
    if (occluded(Math.floor(tx / CELL), Math.floor(ty / CELL), vertical)) continue
    n++
  }
  return n
}

/** 独立重实现：最近敌距（两遍扫描、显式排序语义 ⇒ 与生产实现路径不同）。 */
function indepNearestDist(w: World): number {
  const p = w.player
  if (!p || !p.alive) return -1
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const ds: number[] = []
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    ds.push(Math.hypot(t.x + t.w / 2 - pcx, t.y + t.h / 2 - pcy))
  }
  return ds.length === 0 ? -1 : Math.min(...ds)
}

/** 独立重实现：伤害成簇（双指针滑窗 + 逐笔连击判定，与生产的 O(n²) 写法不同路）。 */
function indepCluster(
  ticks: readonly number[],
  amounts: readonly number[],
): { bursts: number; max120: number } {
  let bursts = 0
  for (let i = 1; i < ticks.length; i++) {
    if (ticks[i] - ticks[i - 1] <= DMG_BURST_TICKS) bursts++
  }
  let best = 0
  let j = 0
  let sum = 0
  for (let i = 0; i < ticks.length; i++) {
    while (j < ticks.length && ticks[j] - ticks[i] <= DMG_BURST_TICKS) {
      sum += amounts[j]
      j++
    }
    if (sum > best) best = sum
    sum -= amounts[i]
  }
  return { bursts, max120: best }
}

describe('metrics v10：常量为冻结值（与 §71 面板逐字一致）', () => {
  it('近敌带 4 格 / 连击窗 120t / 哨兵 −1 / 对齐带 19px', () => {
    expect(NEAR_ENEMY_BAND_PX).toBe(4 * CELL)
    expect(NEAR_ENEMY_BAND_PX).toBe(64)
    expect(DMG_BURST_TICKS).toBe(120)
    expect(ENEMY_DIST_SENTINEL).toBe(-1)
    // 19px 是**物理值**（坦克半宽 16 + 子弹半高 3），不是调参 ⇒ 与旧 12px 带并列断言。
    expect(THREAT_ALIGN_BAND_PX).toBe(TANK / 2 + 3)
    expect(THREAT_ALIGN_BAND_PX).toBe(19)
    expect(THREAT_ALIGN_BAND).toBe(12)
  })
})

describe('alignedEnemyCount：带宽（19px，§71 ②b 定案）', () => {
  it('同列 / 同行算；偏轴 1 格（16px）在带内、2 格（32px）出带；双向都偏 = 不算', () => {
    const w = worldWithPlayer()
    const { cx, cy } = playerCenter(w)

    // 同列 3 格上方（无半径上限 ⇒ 「远处也在同一条线上」照算）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(1)
    w.tanks.length = 0

    // 同行 4 格右侧（朝向无关：被包围只看几何，不看炮口）
    placeEnemy(w, PCOL + 4, PROW, 'basic', 'up')
    expect(alignedEnemyCount(w)).toBe(1)
    w.tanks.length = 0

    // 偏轴 1 格 = 16px < 19px ⇒ **在带内**（旧 12px 带会漏掉这一整族，§71 ②b：读数腰斩翻符号）
    const one = cellCenter(PCOL + 1, PROW - 3)
    expect(Math.abs(one.cx - cx)).toBe(16)
    placeEnemy(w, PCOL + 1, PROW - 3, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(1)
    w.tanks.length = 0

    // 偏轴 2 格 = 32px > 19px ⇒ 出带（这一发打不到我）
    const two = cellCenter(PCOL + 2, PROW - 3)
    expect(Math.abs(two.cx - cx)).toBe(32)
    placeEnemy(w, PCOL + 2, PROW - 3, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(0)
    w.tanks.length = 0

    // 对角线两轴都出带 ⇒ 不算；对角但横向 16px（仍可能在弹道上）⇒ 算（几何真值，不是「中心格」）
    placeEnemy(w, PCOL + 2, PROW + 2, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(0)
    w.tanks.length = 0
    placeEnemy(w, PCOL + 1, PROW - 1, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(1)
    w.tanks.length = 0

    // 玩家自身的像素中心与 naive「中心格」不同口径：格 (6,4) 的中心列 ≈ (6+1)*16 = 112 ⇒ 16px 偏轴
    expect(Math.floor(cx / CELL)).toBe(6)
    expect(cy).toBe(96)
  })
})

describe('alignedEnemyCount：激活 / 存活 / 阵营 / 玩家', () => {
  it('未激活（spawnTimer>0）· 阵亡 · 友军 都不算；多敌计数', () => {
    const w = worldWithPlayer()
    const live = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(1)

    // 出生保护期的车还不是暴露源（否则「刚出生在玩家旁边」会被算成被包围）
    live.spawnTimer = 1
    expect(alignedEnemyCount(w)).toBe(0)
    live.spawnTimer = 0

    // 阵亡的不算
    live.alive = false
    expect(alignedEnemyCount(w)).toBe(0)
    live.alive = true

    // 友军（ally）不是敌人
    live.allegiance = 'ally'
    expect(alignedEnemyCount(w)).toBe(0)
    live.allegiance = 'enemy'

    // 多敌计数 + 互斥分档的原料（1 / 2 / 3+）
    placeEnemy(w, PCOL + 1, PROW, 'basic', 'left')
    placeEnemy(w, PCOL, PROW + 3, 'basic', 'up')
    placeEnemy(w, PCOL - 1, PROW - 5, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(4)

    // 玩家阵亡 / 不在场 ⇒ 0（与 dangerTicks/threatTicks 同规：死亡帧不算暴露）
    w.player!.alive = false
    expect(alignedEnemyCount(w)).toBe(0)
    expect(alignedEnemyCount(seedWorld(9))).toBe(0)
  })
})

describe('alignedEnemyCount：轴遮挡（无阻弹地形才算包围）', () => {
  it('同列/同行中间有砖 ⇒ 不算；水不阻弹 ⇒ 照算；旁列砖不影响', () => {
    const w = worldWithPlayer()

    // 同列（敌在 (5,2)，其弹道单格线 = 列 6 —— 与玩家像素跨度的 5–6 对齐）
    const e = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    const lineCol = Math.floor((e.x + e.w / 2) / CELL)
    expect(lineCol).toBe(6)
    expect(alignedEnemyCount(w)).toBe(1)
    w.tileMap.set(lineCol, PROW - 1, 'brick') // 夹在敌与我之间
    expect(alignedEnemyCount(w)).toBe(0)
    w.tileMap.set(lineCol, PROW - 1, 'water') // 水不阻弹
    expect(alignedEnemyCount(w)).toBe(1)
    w.tileMap.set(lineCol + 2, PROW - 1, 'brick') // 旁列砖不挡本弹道
    expect(alignedEnemyCount(w)).toBe(1)
    w.tileMap.set(lineCol, PROW - 1, 'empty')
    w.tileMap.set(lineCol + 2, PROW - 1, 'empty')
    w.tanks.length = 0

    // 同行（敌在 (9,5)：其弹道行 = 行 6）
    const h = placeEnemy(w, PCOL + 4, PROW, 'basic', 'left')
    const lineRow = Math.floor((h.y + h.h / 2) / CELL)
    expect(lineRow).toBe(6)
    expect(alignedEnemyCount(w)).toBe(1)
    w.tileMap.set(PCOL + 2, lineRow, 'steel')
    expect(alignedEnemyCount(w)).toBe(0)
    w.tileMap.set(PCOL + 2, lineRow, 'empty')
    expect(alignedEnemyCount(w)).toBe(1)
  })
})

describe('alignedEnemyCount：独立重实现对账 + 纯函数', () => {
  it('12 种构型逐值一致（生产实现 vs 本文件独立重实现）', () => {
    const builds: Array<(w: World) => void> = [
      () => {},
      (w) => {
        placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL + 1, PROW - 3, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL + 2, PROW - 3, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL + 4, PROW, 'basic', 'up')
      },
      (w) => {
        placeEnemy(w, PCOL, PROW + 4, 'basic', 'up')
      },
      (w) => {
        placeEnemy(w, PCOL - 2, PROW - 2, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
        placeEnemy(w, PCOL + 1, PROW, 'basic', 'left')
        placeEnemy(w, PCOL, PROW + 3, 'basic', 'up')
      },
      (w) => {
        const e = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
        e.spawnTimer = 1
      },
      (w) => {
        const e = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
        e.alive = false
      },
      (w) => {
        const e = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
        e.allegiance = 'ally'
      },
      (w) => {
        placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
        w.tileMap.set(6, PROW - 1, 'brick')
        placeEnemy(w, PCOL + 3, PROW, 'basic', 'left')
      },
    ]
    for (let i = 0; i < builds.length; i++) {
      const w = worldWithPlayer(100 + i)
      builds[i](w)
      expect(alignedEnemyCount(w)).toBe(indepAlignedCount(w))
    }
  })

  it('纯函数：不改 World（连读两次同值、世界不变量不变）', () => {
    const w = worldWithPlayer(7)
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    const before = JSON.stringify(w.tileMap.grid)
    expect(alignedEnemyCount(w)).toBe(alignedEnemyCount(w))
    expect(JSON.stringify(w.tileMap.grid)).toBe(before)
  })
})

describe('nearestEnemyDistPx：最近敌距与哨兵', () => {
  it('无敌/玩家不在场 ⇒ 哨兵 −1；取最近者；欧氏 px', () => {
    const w = worldWithPlayer()
    expect(nearestEnemyDistPx(w)).toBe(ENEMY_DIST_SENTINEL)

    // 正上方 3 格：中心距 = 3*16 = 48px（纯垂直，与 sqrt 实现同值）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(nearestEnemyDistPx(w)).toBe(48)

    // 再来一个更远的（同行 4 格 = 64px）⇒ 仍取 48
    placeEnemy(w, PCOL + 4, PROW, 'basic', 'up')
    expect(nearestEnemyDistPx(w)).toBe(48)

    // 更近的（偏轴 1 格 + 上 1 格 ⇒ sqrt(16²+16²) ≈ 22.63）
    placeEnemy(w, PCOL + 1, PROW - 1, 'basic', 'down')
    expect(nearestEnemyDistPx(w)).toBeCloseTo(Math.hypot(16, 16), 10)
    expect(nearestEnemyDistPx(w)).toBe(indepNearestDist(w))
  })

  it('未激活 / 阵亡 / 友军 不算（否则出生保护会被算成「敌在身边」）', () => {
    const w = worldWithPlayer()
    const e = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    e.spawnTimer = 1
    expect(nearestEnemyDistPx(w)).toBe(ENEMY_DIST_SENTINEL)
    e.spawnTimer = 0
    e.alive = false
    expect(nearestEnemyDistPx(w)).toBe(ENEMY_DIST_SENTINEL)
    e.alive = true
    e.allegiance = 'ally'
    expect(nearestEnemyDistPx(w)).toBe(ENEMY_DIST_SENTINEL)

    e.allegiance = 'enemy'
    expect(nearestEnemyDistPx(w)).toBe(48)
    w.player!.alive = false
    expect(nearestEnemyDistPx(w)).toBe(ENEMY_DIST_SENTINEL)
  })

  it('独立重实现对账：8 种构型逐值一致（含「无存活敌」哨兵）', () => {
    const builds: Array<(w: World) => void> = [
      () => {},
      (w) => {
        placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL + 4, PROW, 'basic', 'up')
      },
      (w) => {
        placeEnemy(w, PCOL - 3, PROW - 2, 'basic', 'right')
      },
      (w) => {
        placeEnemy(w, PCOL + 1, PROW + 1, 'basic', 'up')
        placeEnemy(w, PCOL - 5, PROW - 5, 'basic', 'down')
        placeEnemy(w, PCOL, PROW + 2, 'basic', 'up')
      },
      (w) => {
        const e = placeEnemy(w, PCOL, PROW - 1, 'basic', 'down')
        e.spawnTimer = 1
      },
      (w) => {
        const e = placeEnemy(w, PCOL, PROW - 1, 'basic', 'down')
        e.alive = false
        placeEnemy(w, PCOL + 3, PROW, 'basic', 'left')
      },
      (w) => {
        w.player!.alive = false
        placeEnemy(w, PCOL, PROW - 1, 'basic', 'down')
      },
    ]
    for (let i = 0; i < builds.length; i++) {
      const w = worldWithPlayer(200 + i)
      builds[i](w)
      expect(nearestEnemyDistPx(w)).toBe(indepNearestDist(w))
    }
  })
})

describe('damageClusterStats：伤害成簇（连击数 / 120t 窗内最大承伤）', () => {
  it('空序列 ⇒ 0/0；单笔自成窗；间隔 ≤120 才算连击', () => {
    expect(damageClusterStats([], [])).toEqual({ bursts: 0, max120: 0 })

    // 单笔：不成连击，但自己就是一个 120t 窗
    expect(damageClusterStats([100], [30])).toEqual({ bursts: 0, max120: 30 })

    // 间隔 119（≤120）= 连击
    expect(damageClusterStats([100, 219], [10, 20])).toEqual({ bursts: 1, max120: 30 })
    // 间隔 120（**边界包含**）= 连击
    expect(damageClusterStats([100, 220], [10, 20])).toEqual({ bursts: 1, max120: 30 })
    // 间隔 121 > 120 = 不连击；两窗各自独立 ⇒ max 仍是单笔
    expect(damageClusterStats([100, 221], [10, 20])).toEqual({ bursts: 0, max120: 20 })
  })

  it('max120 是「任意起点滑窗」的最大累积，不是全局总和', () => {
    // 三笔各 10，两两间隔 60 ⇒ 全部落在同一 120t 窗内（120-0=120 ≤ 120）
    expect(damageClusterStats([0, 60, 120], [10, 10, 10])).toEqual({ bursts: 2, max120: 30 })
    // 三笔各 10，间隔 200 ⇒ 每笔自成窗，互不叠加
    expect(damageClusterStats([0, 200, 400], [10, 10, 10])).toEqual({ bursts: 0, max120: 10 })
    // 大额单笔 vs 小额连击：取最大窗，不是最大单笔
    expect(damageClusterStats([0, 130, 260], [50, 1, 1])).toEqual({ bursts: 0, max120: 50 })
    // 窗起点在中间：只有后两笔能叠（0→130 超窗），两笔间隔 120 ⇒ 连击 1
    expect(damageClusterStats([0, 130, 250], [5, 40, 40])).toEqual({ bursts: 1, max120: 80 })
    // 连击跨窗：0 与 119 叠、119 与 238 叠，但 0 与 238 不叠 ⇒ 窗口从 119 起 = 70
    expect(damageClusterStats([0, 119, 238], [30, 40, 30])).toEqual({ bursts: 2, max120: 70 })
  })

  it('两列都单调不减（Φ 逐行差分非负的原料）', () => {
    const ticks = [10, 40, 90, 200, 260, 500, 520, 700]
    const amounts = [3, 7, 1, 20, 5, 2, 9, 4]
    let prev = damageClusterStats([], [])
    for (let n = 1; n <= ticks.length; n++) {
      const cur = damageClusterStats(ticks.slice(0, n), amounts.slice(0, n))
      expect(cur.bursts).toBeGreaterThanOrEqual(prev.bursts)
      expect(cur.max120).toBeGreaterThanOrEqual(prev.max120)
      prev = cur
    }
  })

  it('独立重实现对账：9 组序列逐值一致（双指针滑窗 vs O(n²)）', () => {
    const cases: Array<[number[], number[]]> = [
      [[], []],
      [[0], [7]],
      [
        [0, 1, 2, 3, 4],
        [1, 1, 1, 1, 1],
      ],
      [
        [0, 120, 240, 360],
        [10, 10, 10, 10],
      ],
      [
        [0, 121, 242],
        [10, 10, 10],
      ],
      [
        [600, 700, 710, 900, 1200],
        [4, 30, 2, 40, 50],
      ],
      [
        [1, 2, 130, 131, 132, 300],
        [100, 1, 1, 1, 1, 2],
      ],
      [
        [50, 50, 50],
        [1, 2, 4],
      ],
      [[12900], [1]],
    ]
    for (const [ticks, amounts] of cases) {
      expect(damageClusterStats(ticks, amounts)).toEqual(indepCluster(ticks, amounts))
    }
  })

  it('零分配：不修改入参（只读切片）', () => {
    const ticks = [0, 60, 120]
    const amounts = [1, 2, 3]
    const t0 = ticks.slice()
    const a0 = amounts.slice()
    damageClusterStats(ticks, amounts)
    expect(ticks).toEqual(t0)
    expect(amounts).toEqual(a0)
  })
})

describe('metrics v10：导出器接线（源码哨兵）', () => {
  it('held head（stop/fire）来自动作头、每个决策步更新一次，不看实弹事件', async () => {
    // §1 的输入级口径：训练侧 = action 头（idx0 移动 / idx1 开火），held 语义 = 决策步内保持
    // （`ScriptedInput` 无时钟脉冲）；**不读** `bullet_fired`（实弹会被冷却/弹量上限门掉）。
    // 逐 tick 行为在 `tests/export-rl-rollout-metrics.test.ts` 的行级用例里；这里钉住「接线」。
    const src = await Bun.file(
      join(import.meta.dir, '..', '..', 'tools', 'sim', 'export-rl-rollout.ts'),
    ).text()
    expect(src).toContain('fireHeld = fr.idx === 1')
    expect(src).toContain('stopHeld = aMove === 0')
    expect(src).toContain('if (stopHeld) tel.stopTicks++')
    expect(src).toContain('if (fireHeld) tel.fireHeldTicks++')
    expect(src).toContain('if (!world.player.moving) tel.idleTicks++')
    // 累计块只在「玩家存活」分支内（与 dangerTicks/threatTicks 同规：死亡帧不算暴露）。
    expect(src).toContain('if (world.player?.alive) {')
    // 两列都从 held head 累加、不从 `playerShots`（实弹）派生 —— 实弹被冷却/弹量上限门掉，
    // 与 §71 面板的「开火输出」口径必须同义（否则列与探针不可比）。
    expect(src).toContain('if (fireHeld) tel.fireHeldTicks++')
  })
})
