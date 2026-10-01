/**
 * exposure-levers.test.ts — aim-dodge-levers 批次 P1 共享实现的单测（plan §7.1）。
 *
 * 覆盖：
 *   · `src/nn/shield-window.ts` 粘性 tracker（3000 / POWERUP_DURATION_MS / 1500 / 衰减 / 叠加 / 清零 / reset）；
 *   · `exposureExempt` 与 `threatLaneExempt` 的**双口径 fixture**（§3.6 ①b：出生盾段旧宽谓词 ++ 而新窄谓词照计）；
 *   · `enemyBulletLaneWeightTick`：19px 带 ∧ 玩家在弹前 ∧ 切比雪夫 x ≤ 5 ⇒ 6−x；豁免 gate；
 *   · `enclWeightTick`：n ≥ 2 门槛、中心格切比雪夫、窗口、遮挡（与 `alignedEnemyCount` 同谓词）；
 *   · `cornerWeightTick`：GRID 派生锚点、中心格、d ≤ 3；关卡 `enemy_spawns` 一致性断言（c04/c20）；
 *   · 常量哨兵（K_* / 锚点 M / 19px），阈值从 `src/constants.ts` 派生，改常量必须连带重测。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  BULLET,
  CELL,
  GRID,
  POWERUP_DURATION_MS,
  RESPAWN_SHIELD_MS,
  STAR_SHIELD_GRACE_MS,
} from '../../src/constants'
import {
  CORNER_ANCHOR_MARGIN,
  K_CORNER,
  K_ENCL,
  K_HURT,
  THREAT_ALIGN_BAND_PX,
  alignedEnemyCount,
  cornerAnchors,
  cornerWeightTick,
  enclWeightTick,
  enemyBulletLaneWeightTick,
  exposureExempt,
  threatLaneExempt,
} from '../../src/nn/danger-metrics'
import {
  SHIELD_CLASS_NONE,
  SHIELD_CLASS_POWERUP,
  SHIELD_CLASS_SPAWN,
  SHIELD_CLASS_STARLIKE,
  createShieldWindow,
} from '../../src/nn/shield-window'
import type { World } from '../../src/game/World'
import type { Bullet } from '../../src/types'
import { makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'

const PCOL = 5
const PROW = 5

/** 有玩家、无敌人的干净世界（startGame 才建坦克；与 danger-metrics.test 同规）。 */
function worldWithPlayer(seed = 1): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  positionPlayer(w, PCOL, PROW)
  return w
}

/** 清空全部地块（合成世界；让带宽/距离断言不随关卡地形漂移）。 */
function emptyField(w: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
}

/** 玩家中心（px）与中心格。 */
function center(w: World): { cx: number; cy: number; cc: number; cr: number } {
  const p = w.player!
  const cx = p.x + p.w / 2
  const cy = p.y + p.h / 2
  return { cx, cy, cc: Math.floor(cx / CELL), cr: Math.floor(cy / CELL) }
}

/** 以中心坐标放一颗弹（避免依赖尺寸关系）。 */
function bulletAtCenter(
  cx: number,
  cy: number,
  dir: Bullet['dir'],
  over: Partial<Bullet> = {},
): Bullet {
  return makeBullet({ x: cx - BULLET / 2, y: cy - BULLET / 2, dir, ...over })
}

describe('shield-window：盾道具窗粘性 tracker（豁免机制 A）', () => {
  it('三类增量 + 粘性 + 清零 + reset', () => {
    const w = worldWithPlayer()
    const t = createShieldWindow()
    expect(t.classOf()).toBe(SHIELD_CLASS_NONE)
    expect(t.powerupActive()).toBe(false)

    // 出生/复活盾（= RESPAWN_SHIELD_MS）⇒ SPAWN，不算豁免
    w.player!.shieldTimer = RESPAWN_SHIELD_MS
    t.update(w)
    expect(t.classOf()).toBe(SHIELD_CLASS_SPAWN)
    expect(t.powerupActive()).toBe(false)

    // 拾取（+= POWERUP_DURATION_MS；出生盾期间拾取读数 3000+20000 ⇒ 仍归盾道具，优先级）
    w.player!.shieldTimer = RESPAWN_SHIELD_MS + POWERUP_DURATION_MS
    t.update(w)
    expect(t.classOf()).toBe(SHIELD_CLASS_POWERUP)
    expect(t.powerupActive()).toBe(true)

    // 衰减穿过 RESPAWN/STAR 两条边界：类保持（粘性——盾道具尾段像出生盾，不许翻类）
    w.player!.shieldTimer = RESPAWN_SHIELD_MS - 100
    t.update(w)
    expect(t.powerupActive()).toBe(true)
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS - 1
    t.update(w)
    expect(t.powerupActive()).toBe(true)

    // 归零清类
    w.player!.shieldTimer = 0
    t.update(w)
    expect(t.classOf()).toBe(SHIELD_CLASS_NONE)
    expect(t.powerupActive()).toBe(false)

    // 星盾 grace（= STAR_SHIELD_GRACE_MS）⇒ STARLIKE，不算豁免
    const t2 = createShieldWindow()
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS
    t2.update(w)
    expect(t2.classOf()).toBe(SHIELD_CLASS_STARLIKE)
    expect(t2.powerupActive()).toBe(false)
    // 星盾 tail 上拾盾 ⇒ 盾道具
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS + POWERUP_DURATION_MS
    t2.update(w)
    expect(t2.classOf()).toBe(SHIELD_CLASS_POWERUP)

    // reset（跨局卫生）
    t2.reset()
    expect(t2.classOf()).toBe(SHIELD_CLASS_NONE)
    expect(t2.powerupActive()).toBe(false)
  })

  it('叠加拾取（40000）与无玩家都不抛', () => {
    const w = worldWithPlayer()
    const t = createShieldWindow()
    w.player!.shieldTimer = POWERUP_DURATION_MS * 2
    t.update(w)
    expect(t.powerupActive()).toBe(true)
    // 未 startGame（无玩家）⇒ timer 视作 0，不抛
    const t3 = createShieldWindow()
    t3.update(seedWorld(9))
    expect(t3.classOf()).toBe(SHIELD_CLASS_NONE)
  })

  it('双口径 fixture：出生盾段旧宽谓词真、新 A 谓词假；冻期两者皆真（§3.6 ①b）', () => {
    const w = worldWithPlayer()
    const t = createShieldWindow()
    w.player!.shieldTimer = RESPAWN_SHIELD_MS
    t.update(w)
    expect(threatLaneExempt(w)).toBe(true) // 宽：任意 shieldTimer > 0
    expect(exposureExempt(w, t)).toBe(false) // 窄：出生/复活盾不计入
    w.freezeTimer = 1
    expect(threatLaneExempt(w)).toBe(true)
    expect(exposureExempt(w, t)).toBe(true) // 冻期两口径都豁免
    w.freezeTimer = 0
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS
    const t2 = createShieldWindow()
    t2.update(w)
    expect(threatLaneExempt(w)).toBe(true)
    expect(exposureExempt(w, t2)).toBe(false) // 星盾 grace 同样不计入
  })

  it('阈值哨兵：分类边界 === src/constants.ts 常量', () => {
    expect(POWERUP_DURATION_MS).toBeGreaterThan(RESPAWN_SHIELD_MS)
    expect(RESPAWN_SHIELD_MS).toBeGreaterThan(STAR_SHIELD_GRACE_MS)
    // 低一档的增量不许冒充高类（+常量派生 ⇒ 调参自动跟随）
    const w = worldWithPlayer()
    const t = createShieldWindow()
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS
    t.update(w)
    expect(t.classOf()).toBe(SHIELD_CLASS_STARLIKE)
    const t2 = createShieldWindow()
    w.player!.shieldTimer = RESPAWN_SHIELD_MS
    t2.update(w)
    expect(t2.classOf()).toBe(SHIELD_CLASS_SPAWN)
  })
})

describe('enemyBulletLaneWeightTick：敌弹火线承伤逐拍权重（hurtWeight 项）', () => {
  it('19px 带 ∧ 玩家在弹前 ∧ 切比雪夫 ≤5 ⇒ 6−x；出带/背离/超窗 ⇒ 0', () => {
    const w = worldWithPlayer()
    emptyField(w)
    const t = createShieldWindow()
    t.update(w)
    const { cx, cy, cc, cr } = center(w)
    expect([cc, cr]).toEqual([6, 6])

    // 正上方 3 格、朝下 ⇒ x=3 ⇒ 3
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx, cy - 3 * CELL, 'down'), t)).toBe(3)
    // 5 格（窗口上界）⇒ 1；6 格 ⇒ 0
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx, cy - 5 * CELL, 'down'), t)).toBe(1)
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx, cy - 6 * CELL, 'down'), t)).toBe(0)
    // 同轴但在玩家后方（弹朝上 = 远离）⇒ 0
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx, cy - 3 * CELL, 'up'), t)).toBe(0)
    // 偏轴 1 格（16px < 19px）⇒ 仍在带内，x = max(1,3) = 3
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx + CELL, cy - 3 * CELL, 'down'), t)).toBe(
      3,
    )
    // 偏轴 2 格（32px）⇒ 出带 ⇒ 0
    expect(
      enemyBulletLaneWeightTick(w, bulletAtCenter(cx + 2 * CELL, cy - 3 * CELL, 'down'), t),
    ).toBe(0)
    // 正右方 2 格朝左（逼近）⇒ x=2 ⇒ 4
    expect(enemyBulletLaneWeightTick(w, bulletAtCenter(cx + 2 * CELL, cy, 'left'), t)).toBe(4)
    // 已死弹 ⇒ 0（调用方只喂存活弹，这是防误用）
    expect(
      enemyBulletLaneWeightTick(w, bulletAtCenter(cx, cy - 3 * CELL, 'down', { alive: false }), t),
    ).toBe(0)
    // 无玩家 ⇒ 0
    const empty = seedWorld(2)
    expect(enemyBulletLaneWeightTick(empty, bulletAtCenter(cx, cy - 3 * CELL, 'down'), t)).toBe(0)
  })

  it('豁免 gate：冻期不新增；盾道具窗不新增；出生/星盾照计（窄口径）', () => {
    const w = worldWithPlayer()
    emptyField(w)
    const { cx, cy } = center(w)
    const b = bulletAtCenter(cx, cy - 3 * CELL, 'down')
    const t = createShieldWindow()
    t.update(w)
    expect(enemyBulletLaneWeightTick(w, b, t)).toBe(3)

    w.freezeTimer = 1
    expect(enemyBulletLaneWeightTick(w, b, t)).toBe(0)
    w.freezeTimer = 0

    w.player!.shieldTimer = POWERUP_DURATION_MS
    t.update(w)
    expect(t.powerupActive()).toBe(true)
    expect(enemyBulletLaneWeightTick(w, b, t)).toBe(0)

    // 出生盾/星盾不是豁免（窄口径）——用新 tracker 重新分类
    w.player!.shieldTimer = RESPAWN_SHIELD_MS
    const t2 = createShieldWindow()
    t2.update(w)
    expect(enemyBulletLaneWeightTick(w, b, t2)).toBe(3)
    w.player!.shieldTimer = STAR_SHIELD_GRACE_MS
    const t3 = createShieldWindow()
    t3.update(w)
    expect(enemyBulletLaneWeightTick(w, b, t3)).toBe(3)
  })
})

describe('enclWeightTick：被包围距离加权（Σ max(0,5−d)，n ≥ 2）', () => {
  it('n<2 ⇒ 0；n=2 ⇒ 中心格切比雪夫加权；d ≥ 5 贡献 0', () => {
    const w = worldWithPlayer()
    emptyField(w)
    // 单敌对齐 ⇒ 0（门槛 n≥2）
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(1)
    expect(enclWeightTick(w)).toBe(0)
    // 第二敌同列更近（中心格 d=2）⇒ Σ (5−3)+(5−2) = 5
    placeEnemy(w, PCOL, PROW - 2, 'basic', 'down')
    expect(enclWeightTick(w)).toBe(5)
    expect(enclWeightTick(w)).toBeGreaterThan(0)
    // 观测侧仍与计数一致（共用谓词）
    expect(alignedEnemyCount(w)).toBe(2)
  })

  it('窗口：d=5 的敌计 n 但贡献 0；远离的敌不计 n', () => {
    const w = worldWithPlayer()
    emptyField(w)
    // 中心格 (6,1) ⇒ d=5 ⇒ 贡献 0；中心格 (6,5) ⇒ d=1 ⇒ 4
    placeEnemy(w, PCOL, PROW - 5, 'basic', 'down')
    placeEnemy(w, PCOL, PROW - 1, 'basic', 'down')
    expect(alignedEnemyCount(w)).toBe(2)
    expect(enclWeightTick(w)).toBe(4)
  })

  it('遮挡与计数同步（brick 挡掉一个 ⇒ n<2 ⇒ 加权归零）', () => {
    const w = worldWithPlayer()
    emptyField(w)
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down') // 中心格 (6,3)，d=3
    placeEnemy(w, PCOL, PROW - 2, 'basic', 'down') // 中心格 (6,4)，d=2
    expect(enclWeightTick(w)).toBe(5)
    // 在 (6,4) 放砖：挡掉上面那颗（弹道列 6、row 4）；下面那颗到玩家之间无格
    w.tileMap.set(6, PROW - 1, 'brick')
    expect(alignedEnemyCount(w)).toBe(1)
    expect(enclWeightTick(w)).toBe(0)
  })
})

describe('cornerWeightTick：角落权重（GRID 派生锚点，中心格，d ≤ 3）', () => {
  it('锚点 === (2,2)/(22,2)/(22,22)/(2,22)；d=0..3 ⇒ 4..1；d=4 ⇒ 0', () => {
    expect(cornerAnchors()).toEqual([
      { col: 2, row: 2 },
      { col: 22, row: 2 },
      { col: 22, row: 22 },
      { col: 2, row: 22 },
    ])
    const w = worldWithPlayer()
    emptyField(w)
    positionPlayer(w, 1, 1) // 中心格 (2,2) ⇒ d=0
    expect(cornerWeightTick(w)).toBe(K_CORNER)
    positionPlayer(w, 2, 1) // 中心格 (3,2) ⇒ d=1
    expect(cornerWeightTick(w)).toBe(3)
    positionPlayer(w, 3, 1) // 中心格 (4,2) ⇒ d=2
    expect(cornerWeightTick(w)).toBe(2)
    positionPlayer(w, 4, 1) // 中心格 (5,2) ⇒ d=3
    expect(cornerWeightTick(w)).toBe(1)
    positionPlayer(w, 5, 1) // 中心格 (6,2) ⇒ d=4（窗外）
    expect(cornerWeightTick(w)).toBe(0)
    // 中心格不是左上格：骑线位置（左上 col=0 ⇒ 中心 col=1）离角更近
    positionPlayer(w, 0, 0) // 中心格 (1,1) ⇒ d=1 到 (2,2)
    expect(cornerWeightTick(w)).toBe(3)
    // 无玩家 ⇒ 0
    expect(cornerWeightTick(seedWorld(9))).toBe(0)
  })

  it('关卡 enemy_spawns 一致性断言（c04 / c20 四角 spawn === 派生锚点）', () => {
    const anchors = cornerAnchors()
    for (const file of ['ladder-c04.jsonc', 'ladder-c20-lives1.jsonc']) {
      const raw = readFileSync(
        join(import.meta.dir, '..', '..', 'nn-training', 'levels', file),
        'utf8',
      )
      const parsed = JSON.parse(raw.replace(/,\s*([}\]])/g, '$1')) as {
        stages: Array<{ enemy_spawns: Array<{ col: number; row: number }> }>
      }
      for (const stage of parsed.stages) {
        for (const a of anchors) {
          const hit = stage.enemy_spawns.some((s) => s.col === a.col && s.row === a.row)
          expect(`${file}:${a.col},${a.row}:${hit}`).toBe(`${file}:${a.col},${a.row}:true`)
        }
      }
    }
  })
})

describe('常量哨兵（改常量 = 改口径）', () => {
  it('K_* = 6/5/4；锚点 M = 2；带宽 19px', () => {
    expect(K_HURT).toBe(6)
    expect(K_ENCL).toBe(5)
    expect(K_CORNER).toBe(4)
    expect(CORNER_ANCHOR_MARGIN).toBe(2)
    expect(THREAT_ALIGN_BAND_PX).toBe(19)
  })

  it('纯函数：连读两次同值、世界字段不变', () => {
    const w = worldWithPlayer(7)
    emptyField(w)
    const t = createShieldWindow()
    t.update(w)
    const before = JSON.stringify({
      tanks: w.tanks.length,
      bullets: w.bullets.length,
      hp: w.player!.hp,
    })
    const a = enclWeightTick(w)
    const b = enclWeightTick(w)
    const c = cornerWeightTick(w)
    const d = cornerWeightTick(w)
    expect(a).toBe(b)
    expect(c).toBe(d)
    expect(
      JSON.stringify({ tanks: w.tanks.length, bullets: w.bullets.length, hp: w.player!.hp }),
    ).toBe(before)
  })
})
