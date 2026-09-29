/**
 * danger-metrics.test.ts — metrics v8 危险暴露判定的单测（plan/x20-dodge-avoidance §2）。
 *
 * 体例仿 `pickup-dist-metric.test.ts`：**独立重实现**对账（本文件不看生产 helper 的实现，
 * 只按口径自己写一遍），再加边界用例与「纯函数不改 World」的断言。
 *
 * 口径（**2026-09-29 metrics v9 改定义**，plan/geo-threat-instrumentation.plan.md §1.2）：
 *   ① 敌弹/敌车：同轴 **< 19px**（= 坦克半宽 16 + 子弹半高 3，物理判据）+ 弹逼近 /
 *      炮口朝玩家；
 *   ② **无半径上限**（lane 是 lane，不论远近）；
 *   ③ **判墙体遮挡**（沿源自己的轴 raycast，`TileMap.blocksBullet` 阻弹）。
 * 旧口径（±0.75 格 / ≤6 格 / 不判遮挡）的读数与 v9 **不可比**（plan §1.2 祖父条款）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { BULLET, CELL, GRID, TANK, type Direction } from '../../src/constants'
import {
  DANGER_HP_THRESHOLD,
  DMG_FIRST_WINDOW_TICKS,
  THREAT_ALIGN_BAND,
  THREAT_ALIGN_BAND_PX,
  THREAT_RADIUS_CELLS,
  inThreatLane,
  playerHpRatio,
  threatLaneExempt,
  threatLaneSources,
} from '../../src/nn/danger-metrics'
import type { Bullet } from '../../src/types'
import type { World } from '../../src/game/World'
import { makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'

const PCOL = 5
const PROW = 5

/** 建一个「有玩家、无敌人」的干净世界（startGame 才建玩家坦克）。 */
function worldWithPlayer(seed = 1): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  positionPlayer(w, PCOL, PROW)
  return w
}

/** 玩家中心（px）。 */
function center(w: World): { cx: number; cy: number } {
  const p = w.player!
  return { cx: p.x + p.w / 2, cy: p.y + p.h / 2 }
}

/** 以中心坐标放一颗弹（避免依赖 BULLET/TANK 的尺寸关系）。 */
function bulletAtCenter(
  cx: number,
  cy: number,
  dir: Direction,
  over: Partial<Bullet> = {},
): Bullet {
  return makeBullet({ x: cx - BULLET / 2, y: cy - BULLET / 2, dir, ...over })
}

/**
 * 独立重实现（**刻意不 import 生产内部实现**）：只按口径读 World 原始字段。
 * 任何一侧改口径都会让下面的逐值对账变红。
 */
function indepThreatSources(w: World): number {
  const p = w.player
  if (!p || !p.alive) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const BAND = TANK / 2 + BULLET / 2 // 19px（物理判据，不是调参）
  // 身体的像素精确格跨度（与生产侧同口径：骑线时可能跨 3 格）。
  const colLow = Math.floor(p.x / CELL)
  const colHigh = Math.floor((p.x + p.w - 1) / CELL)
  const rowLow = Math.floor(p.y / CELL)
  const rowHigh = Math.floor((p.y + p.h - 1) / CELL)
  const blocks = (col: number, row: number): boolean => {
    const t = w.tileMap.get(col, row)
    return t === 'brick' || t === 'steel' || t === 'base'
  }
  /** 沿源自己的轴向玩家走到玩家身体的近边，中间有无阻弹格。 */
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
  /** 同轴 + 朝向玩家 + 无半径上限 + 无遮挡。 */
  const counts = (ex: number, ey: number, dir: Direction): boolean => {
    const vertical = dir === 'up' || dir === 'down'
    const aligned = vertical ? Math.abs(ex - pcx) < BAND : Math.abs(ey - pcy) < BAND
    if (!aligned) return false
    const toward =
      (dir === 'down' && ey < pcy) ||
      (dir === 'up' && ey > pcy) ||
      (dir === 'right' && ex < pcx) ||
      (dir === 'left' && ex > pcx)
    if (!toward) return false
    return !occluded(Math.floor(ex / CELL), Math.floor(ey / CELL), vertical)
  }
  let n = 0
  for (const b of w.bullets) {
    if (!b.alive || b.allegiance !== 'enemy') continue
    if (counts(b.x + b.w / 2, b.y + b.h / 2, b.dir)) n++
  }
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    if (counts(t.x + t.w / 2, t.y + t.h / 2, t.dir)) n++
  }
  return n
}

/** 把整张场地清成空地（**合成世界**，plan §3）：让「同轴/带宽」用例只测谓词本身，
 *  遮挡另设专项（显式放墙）。不带它的话关卡自带地形会让断言随关卡漂移。 */
function emptyField(w: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
}

describe('danger-metrics：玩家 hp 比例', () => {
  it('hp/maxHp，越界夹取；无玩家/未建玩家 = 0', () => {
    const w = worldWithPlayer()
    const p = w.player!
    p.maxHp = 200
    p.hp = 100
    expect(playerHpRatio(w)).toBe(0.5)
    p.hp = 260 // 越界（理论上不会，但必须夹取，否则奖励项可能 >1）
    expect(playerHpRatio(w)).toBe(1)
    p.hp = -5
    expect(playerHpRatio(w)).toBe(0)
    expect(playerHpRatio(w)).toBe(playerHpRatio(w)) // 纯函数

    const empty = seedWorld(2)
    expect(playerHpRatio(empty)).toBe(0)
    const w2 = worldWithPlayer()
    w2.player!.alive = false
    w2.player!.hp = 0
    expect(playerHpRatio(w2)).toBe(0)
  })

  it('阈值与窗常量是冻结值（0.4 / 600）', () => {
    // 与 goal-mask 的撤退阈值、paired §5.1 的 dmgFirst600 窗一致；改它们 = 改实验口径。
    expect(DANGER_HP_THRESHOLD).toBe(0.4)
    expect(DMG_FIRST_WINDOW_TICKS).toBe(600)
    // v9：旧带宽常量仍在（dodge-l0/decision-trace 三处镜像按旧值各自实现，不得静默改），
    // 但 `inThreatLane` 已改用**物理值** 19px（坦克半宽 16 + 子弹半高 3）。
    expect(THREAT_ALIGN_BAND).toBe(CELL * 0.75)
    expect(THREAT_RADIUS_CELLS).toBe(6)
    expect(THREAT_ALIGN_BAND_PX).toBe(TANK / 2 + BULLET / 2)
    expect(THREAT_ALIGN_BAND_PX).toBe(19)
  })
})

describe('danger-metrics：弹道/炮口线威胁（弹）', () => {
  it('同轴逼近 = 威胁（无半径上限）；远离/偏轴 = 不威胁', () => {
    const w = worldWithPlayer()
    emptyField(w)
    const { cx, cy } = center(w)
    const push = (b: Bullet): void => {
      w.bullets.push(b)
    }
    const sources = (): number => threatLaneSources(w)

    // 正上方 3 格、朝下（逼近）
    push(bulletAtCenter(cx, cy - 3 * CELL, 'down'))
    expect(sources()).toBe(1)
    expect(inThreatLane(w)).toBe(true)

    // 同格同轴但朝上（远离）——不算
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 3 * CELL, 'up'))
    expect(sources()).toBe(0)

    // 正上方 **7 格**（旧口径超 6 格半径 ⇒ 漏判；v9 无半径上限 ⇒ 算）
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 7 * CELL, 'down'))
    expect(sources()).toBe(1)

    // **远离旧半径**：把玩家挪到场地中部，弹在顶行（距离 13 格 > 旧 6 格）
    // —— v9 无半径上限 ⇒ 照样算在线（远端噪声由剂量侧承担，观测不预过滤）。
    positionPlayer(w, 12, 12)
    emptyField(w)
    w.bullets.length = 0
    const mid = center(w)
    push(bulletAtCenter(mid.cx, 0 * CELL + 3, 'down'))
    expect(sources()).toBe(1)
    // 复原（后续断言都用 (5,5) 的 cx/cy）
    w.bullets.length = 0
    positionPlayer(w, PCOL, PROW)
    emptyField(w)

    // 偏轴 1 格（16px）= **仍在 19px 带内** ⇒ 算在线。这正是 §60 的发现：
    // 坦克 2 格宽、格子只有 16px，旧 12px 带把「中心线差一行」的炮弹全漏了。
    w.bullets.length = 0
    push(bulletAtCenter(cx + CELL, cy - 3 * CELL, 'down'))
    expect(sources()).toBe(1)

    // 偏轴 2 格（32px > 19px）⇒ 出带（这一发真打不到我）
    w.bullets.length = 0
    push(bulletAtCenter(cx + 2 * CELL, cy - 3 * CELL, 'down'))
    expect(sources()).toBe(0)

    // 正右方 2 格朝左（逼近）
    w.bullets.length = 0
    push(bulletAtCenter(cx + 2 * CELL, cy, 'left'))
    expect(sources()).toBe(1)

    // 玩家自己的弹 / 友军弹不算
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 3 * CELL, 'down', { isPlayer: true, allegiance: 'player' }))
    push(bulletAtCenter(cx, cy - 2 * CELL, 'down', { isPlayer: false, allegiance: 'ally' }))
    expect(sources()).toBe(0)

    // 两颗来弹 = 2（计数而非布尔）
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 3 * CELL, 'down'))
    push(bulletAtCenter(cx + 2 * CELL, cy, 'left'))
    expect(sources()).toBe(2)

    // 已死的弹不算
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 3 * CELL, 'down', { alive: false }))
    expect(sources()).toBe(0)
  })

  it('带宽 19px 边界：16px 偏轴在带内（打得中），32px 出带', () => {
    // §60：旧 12px 带漏判「中心线差一行」的炮弹（人类 4.07% → 8.19%）。
    // 19px = 16(坦克半宽) + 3(子弹半高)，即「这一发打不打得中我」的精确判据。
    const w = worldWithPlayer()
    emptyField(w)
    const { cx, cy } = center(w)
    w.bullets.push(bulletAtCenter(cx + 16, cy - 3 * CELL, 'down')) // 16 < 19 ⇒ 在线
    expect(threatLaneSources(w)).toBe(1)
    w.bullets.length = 0
    w.bullets.push(bulletAtCenter(cx + 20, cy - 3 * CELL, 'down')) // 20 > 19 ⇒ 出带
    expect(threatLaneSources(w)).toBe(0)
  })

  it('障碍 raycast：同线有墙 ⇒ 不算在线（墙在别处不影响）', () => {
    const w = worldWithPlayer()
    emptyField(w)
    const { cx, cy } = center(w)
    // 弹自己的单格线（弹道线）的列：中心 96px ⇒ 格 6。
    const col = Math.floor(cx / CELL)
    // 弹在玩家正上方 4 格：先无墙 = 在线
    w.bullets.push(bulletAtCenter(cx, cy - 4 * CELL, 'down'))
    expect(threatLaneSources(w)).toBe(1)
    // 在弹与玩家身体之间（行 4）插一格砖 ⇒ 弹打不到玩家 ⇒ 不算在线
    w.tileMap.set(col, 4, 'brick')
    expect(threatLaneSources(w)).toBe(0)
    // 那格砖挪到**旁边一列**：不影响本弹道 ⇒ 仍在线
    w.tileMap.set(col, 4, 'empty')
    w.tileMap.set(col + 2, 4, 'brick')
    expect(threatLaneSources(w)).toBe(1)
    // 钢同理（blocksBullet = brick/steel/base）；水**不**阻弹 ⇒ 仍在线
    w.tileMap.set(col + 2, 4, 'empty')
    w.tileMap.set(col, 4, 'water')
    expect(threatLaneSources(w)).toBe(1)
    // 玩家身体那一格上的墙不算遮挡（不可能共存；确认边界不会自伤）
    w.tileMap.set(col, 4, 'empty')
    w.tileMap.set(col, 5, 'brick')
    expect(threatLaneSources(w)).toBe(1)
  })
})

describe('danger-metrics：炮口线（敌车）', () => {
  it('同轴 + 朝玩家 = 威胁（无半径上限）；背对/未入场/友军 = 不威胁', () => {
    const w = worldWithPlayer()
    emptyField(w)
    const sources = (): number => threatLaneSources(w)

    // 同列 3 格、朝下（朝玩家）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(sources()).toBe(1)
    w.tanks.length = 0

    // 同列但朝上（背对玩家）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'up')
    expect(sources()).toBe(0)
    w.tanks.length = 0

    // 同列 7 格（旧口径超半径 ⇒ 漏判；v9 算）——注意行号可为 0，距离用像素算
    placeEnemy(w, PCOL, PROW - 7, 'basic', 'down')
    expect(sources()).toBe(1)
    w.tanks.length = 0

    // 未入场（spawnTimer > 0）
    const t = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    t.spawnTimer = 1
    expect(sources()).toBe(0)
    t.spawnTimer = 0
    w.tanks.length = 0

    // 友军（ally）不是敌人
    const ally = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    ally.allegiance = 'ally'
    expect(sources()).toBe(0)
    w.tanks.length = 0

    // 障碍 raycast：炮口朝向玩家的同列上有砖 ⇒ 打不到 ⇒ 不算在线
    const e = placeEnemy(w, PCOL, PROW - 4, 'basic', 'down')
    expect(sources()).toBe(1)
    const line = Math.floor((e.x + e.w / 2) / CELL)
    w.tileMap.set(line, 4, 'brick')
    expect(sources()).toBe(0)
    w.tileMap.set(line, 4, 'empty')
    w.tanks.length = 0
  })

  it('玩家阵亡/不在场 ⇒ 一律无威胁', () => {
    const w = worldWithPlayer()
    const { cx, cy } = center(w)
    w.bullets.push(bulletAtCenter(cx, cy - 2 * CELL, 'down'))
    placeEnemy(w, PCOL, PROW - 2, 'basic', 'down')
    expect(threatLaneSources(w)).toBeGreaterThan(0)

    w.player!.alive = false
    expect(threatLaneSources(w)).toBe(0)
    expect(inThreatLane(w)).toBe(false)
  })
})

describe('danger-metrics：冻/盾豁免（v9 穿越税族的加法列判据）', () => {
  it('freezeTimer>0 或 player.shieldTimer>0 ⇒ 豁免；两者都 0 ⇒ 不豁免', () => {
    const w = worldWithPlayer()
    expect(threatLaneExempt(w)).toBe(false)
    w.freezeTimer = 1
    expect(threatLaneExempt(w)).toBe(true)
    w.freezeTimer = 0
    w.player!.shieldTimer = 120
    expect(threatLaneExempt(w)).toBe(true)
    w.player!.shieldTimer = 0
    expect(threatLaneExempt(w)).toBe(false)
    // 无玩家（未 startGame）⇒ 不豁免（纯读、不抛）
    expect(threatLaneExempt(seedWorld(9))).toBe(false)
  })
})

describe('danger-metrics：独立重实现对账 + 纯函数', () => {
  it('10 种构型逐值一致（生产实现 vs 本文件独立重实现）', () => {
    const builds: Array<(w: World) => void> = [
      () => {},
      (w) => {
        const { cx, cy } = center(w)
        w.bullets.push(bulletAtCenter(cx, cy - 2 * CELL, 'down'))
      },
      (w) => {
        const { cx, cy } = center(w)
        w.bullets.push(bulletAtCenter(cx + 3 * CELL, cy, 'left'))
      },
      (w) => {
        const { cx, cy } = center(w)
        w.bullets.push(bulletAtCenter(cx, cy + 5 * CELL, 'up'))
      },
      (w) => {
        const { cx, cy } = center(w)
        w.bullets.push(bulletAtCenter(cx + CELL, cy - 2 * CELL, 'down'))
      },
      (w) => {
        placeEnemy(w, PCOL, PROW - 4, 'basic', 'down')
      },
      (w) => {
        placeEnemy(w, PCOL + 3, PROW, 'basic', 'left')
      },
      (w) => {
        placeEnemy(w, PCOL, PROW + 2, 'basic', 'down')
      },
      (w) => {
        const { cx, cy } = center(w)
        w.bullets.push(bulletAtCenter(cx, cy - 6 * CELL, 'down'))
        placeEnemy(w, PCOL, PROW - 2, 'basic', 'down')
        w.bullets.push(bulletAtCenter(cx + 4 * CELL, cy, 'left'))
      },
      (w) => {
        const p = w.player!
        p.hp = Math.floor(p.maxHp * 0.3)
        placeEnemy(w, PCOL - 1, PROW, 'basic', 'right')
      },
    ]
    for (let i = 0; i < builds.length; i++) {
      const w = worldWithPlayer(100 + i)
      builds[i](w)
      expect(threatLaneSources(w)).toBe(indepThreatSources(w))
      expect(inThreatLane(w)).toBe(indepThreatSources(w) > 0)
    }
  })

  it('纯函数：不改 World（连读两次结果相同、世界字段不变）', () => {
    const w = worldWithPlayer(7)
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    const before = JSON.stringify({ t: w.tanks.length, b: w.bullets.length, hp: w.player!.hp })
    const a = threatLaneSources(w)
    const b = threatLaneSources(w)
    const h = playerHpRatio(w)
    expect(a).toBe(b)
    expect(h).toBe(playerHpRatio(w))
    expect(JSON.stringify({ t: w.tanks.length, b: w.bullets.length, hp: w.player!.hp })).toBe(
      before,
    )
  })

  it('口径常数与 dodge-l0 未漂移（源码哨兵）', () => {
    // L0 保底层与 metrics 列共享同一反应半径/带宽；任一侧改动必须同步（改这里=改口径）。
    const src = readFileSync(join(import.meta.dir, '..', '..', 'src', 'nn', 'dodge-l0.ts'), 'utf8')
    expect(src).toContain('const DODGE_RADIUS = 6 * CELL')
    expect(src).toContain('const ALIGN_BAND = CELL * 0.75')
  })
})
