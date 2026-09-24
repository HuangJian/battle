/**
 * danger-metrics.test.ts — metrics v8 危险暴露判定的单测（plan/x20-dodge-avoidance §2）。
 *
 * 体例仿 `pickup-dist-metric.test.ts`：**独立重实现**对账（本文件不看生产 helper 的实现，
 * 只按口径自己写一遍），再加边界用例与「纯函数不改 World」的断言。
 * 口径冻结在 `src/nn/danger-metrics.ts` 头注释：
 *   ① 敌弹：同轴 ±0.75 格 + 逼近 + ≤6 格；② 敌车：同轴 ±0.75 格 + 炮口朝玩家 + ≤6 格。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { BULLET, CELL, type Direction } from '../../src/constants'
import {
  DANGER_HP_THRESHOLD,
  DMG_FIRST_WINDOW_TICKS,
  THREAT_ALIGN_BAND,
  THREAT_RADIUS_CELLS,
  inThreatLane,
  playerHpRatio,
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
  /** 同轴 + 朝向玩家 + 半径内。 */
  const counts = (ex: number, ey: number, dir: Direction): boolean => {
    const vertical = dir === 'up' || dir === 'down'
    const aligned = vertical ? Math.abs(ex - pcx) < CELL * 0.75 : Math.abs(ey - pcy) < CELL * 0.75
    if (!aligned) return false
    const toward =
      (dir === 'down' && ey < pcy) ||
      (dir === 'up' && ey > pcy) ||
      (dir === 'right' && ex < pcx) ||
      (dir === 'left' && ex > pcx)
    if (!toward) return false
    const dist = vertical ? Math.abs(ey - pcy) : Math.abs(ex - pcx)
    return dist <= 6 * CELL
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
    expect(THREAT_RADIUS_CELLS).toBe(6)
    expect(THREAT_ALIGN_BAND).toBe(CELL * 0.75)
  })
})

describe('danger-metrics：弹道/炮口线威胁（弹）', () => {
  it('同轴逼近且 ≤6 格 = 威胁；远离/超半径/偏轴 = 不威胁', () => {
    const w = worldWithPlayer()
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

    // 正上方 7 格（超 6 格半径）
    w.bullets.length = 0
    push(bulletAtCenter(cx, cy - 7 * CELL, 'down'))
    expect(sources()).toBe(0)

    // 偏轴 1 格（>0.75 格带宽）
    w.bullets.length = 0
    push(bulletAtCenter(cx + CELL, cy - 3 * CELL, 'down'))
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
})

describe('danger-metrics：炮口线（敌车）', () => {
  it('同轴 + 朝玩家 + ≤6 格 = 威胁；背对/超半径/未入场/友军 = 不威胁', () => {
    const w = worldWithPlayer()
    const sources = (): number => threatLaneSources(w)

    // 同列 3 格、朝下（朝玩家）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(sources()).toBe(1)
    w.tanks.length = 0

    // 同列但朝上（背对玩家）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'up')
    expect(sources()).toBe(0)
    w.tanks.length = 0

    // 同列 7 格（超半径）——注意行号可为 0，距离用像素算
    placeEnemy(w, PCOL, PROW - 7, 'basic', 'down')
    expect(sources()).toBe(0)
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
