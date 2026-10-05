/**
 * policy-extra.test.ts — `POLICY_EXTRA` 9 维方向直方图的单测
 * （plan/policy-spatial-head.plan.md §3.2）。
 *
 * 体例仿 `tests/sim/danger-metrics.test.ts`：**独立重实现对账**（本文件不看生产
 * helper 的实现，只按口径自己写一遍），再加方位/镜像/边界用例与「纯函数不改 World」
 * 的断言。任何一侧改口径都会让逐值对账变红。
 *
 * 口径（plan §3.2 冻结）：
 *   0..3 前/后/左/右 威胁计数（19px 带 + 逼近/朝向 + 遮挡），归一 min(n,4)/4；
 *   4..7 前/后/左/右 命中距离（clamp01(px/832)；无 ⇒ 1.5 哨兵）；
 *   8    包夹度 = min(左,右,4)/4。
 * 方位 = 玩家相对（前 = 炮口朝向）；mirrorX = 左右两对互换。
 */
import { describe, expect, it } from 'bun:test'
import { BULLET, CELL, GRID, TANK, type Direction } from '../../src/constants'
import {
  POLICY_EXTRA_COUNT_CAP,
  POLICY_EXTRA_DIM,
  POLICY_EXTRA_HIT_DIST_NORM,
  POLICY_EXTRA_HIT_NONE,
  POLICY_EXTRA_MIRROR_SWAPS,
  POLICY_EXTRA_NAMES,
  computePolicyExtra,
} from '../../src/nn/policy-extra'
import type { Bullet } from '../../src/types'
import type { World } from '../../src/game/World'
import { makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'

const PCOL = 5
const PROW = 8
const FIELD_PX = GRID * CELL

function worldWithPlayer(seed = 1, dir: Direction = 'up'): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  positionPlayer(w, PCOL, PROW, dir)
  emptyField(w)
  return w
}

function emptyField(w: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
}

function center(w: World): { cx: number; cy: number } {
  const p = w.player!
  return { cx: p.x + p.w / 2, cy: p.y + p.h / 2 }
}

function compute(w: World): Float32Array {
  const out = new Float32Array(POLICY_EXTRA_DIM)
  computePolicyExtra(w, out)
  return out
}

/** 以中心坐标放一颗敌弹（默认逼近）。 */
function pushBullet(
  w: World,
  cx: number,
  cy: number,
  dir: Direction,
  over: Partial<Bullet> = {},
): void {
  w.bullets.push(makeBullet({ x: cx - BULLET / 2, y: cy - BULLET / 2, dir, ...over }))
}

// ── 独立重实现（刻意不 import 生产内部实现）─────────────────────────────
const OPP: Record<Direction, Direction> = { up: 'down', down: 'up', left: 'right', right: 'left' }
const LOF: Record<Direction, Direction> = { up: 'left', left: 'down', down: 'right', right: 'up' }

function relIdx(side: Direction, facing: Direction): number {
  if (side === facing) return 0
  if (side === OPP[facing]) return 1
  if (side === LOF[facing]) return 2
  return 3
}

function indepExtra(w: World): Float32Array {
  const out = new Float32Array(POLICY_EXTRA_DIM)
  const p = w.player
  if (!p || !p.alive) return out
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const BAND = TANK / 2 + BULLET / 2
  const colLow = Math.floor(p.x / CELL)
  const colHigh = Math.floor((p.x + p.w - 1) / CELL)
  const rowLow = Math.floor(p.y / CELL)
  const rowHigh = Math.floor((p.y + p.h - 1) / CELL)
  const blocks = (c: number, r: number): boolean => {
    const t = w.tileMap.get(c, r)
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
  const counts = [0, 0, 0, 0]
  const dists = [Infinity, Infinity, Infinity, Infinity]
  // 子弹：同轴 + 逼近 + 无遮挡
  for (const b of w.bullets) {
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bx - pcx) < BAND : Math.abs(by - pcy) < BAND
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && by < pcy) ||
      (b.dir === 'up' && by > pcy) ||
      (b.dir === 'right' && bx < pcx) ||
      (b.dir === 'left' && bx > pcx)
    if (!approaching) continue
    if (occluded(Math.floor(bx / CELL), Math.floor(by / CELL), vertical)) continue
    const side: Direction = vertical ? (by < pcy ? 'up' : 'down') : bx < pcx ? 'left' : 'right'
    counts[relIdx(side, p.dir)]++
  }
  // 坦克：威胁计数（炮口朝玩家）+ 4 向命中距离（无朝向要求）
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const tVertical = t.dir === 'up' || t.dir === 'down'
    const tAligned = tVertical ? Math.abs(tx - pcx) < BAND : Math.abs(ty - pcy) < BAND
    const tToward =
      (t.dir === 'down' && ty < pcy) ||
      (t.dir === 'up' && ty > pcy) ||
      (t.dir === 'right' && tx < pcx) ||
      (t.dir === 'left' && tx > pcx)
    if (tAligned && tToward && !occluded(Math.floor(tx / CELL), Math.floor(ty / CELL), tVertical)) {
      const side: Direction = tVertical ? (ty < pcy ? 'up' : 'down') : tx < pcx ? 'left' : 'right'
      counts[relIdx(side, p.dir)]++
    }
    for (let r = 0; r < 4; r++) {
      const d: Direction =
        r === 0 ? p.dir : r === 1 ? OPP[p.dir] : r === 2 ? LOF[p.dir] : OPP[LOF[p.dir]]
      const vertical = d === 'up' || d === 'down'
      const aligned = vertical ? Math.abs(tx - pcx) < BAND : Math.abs(ty - pcy) < BAND
      if (!aligned) continue
      const inFront =
        (d === 'down' && ty > pcy) ||
        (d === 'up' && ty < pcy) ||
        (d === 'right' && tx > pcx) ||
        (d === 'left' && tx < pcx)
      if (!inFront) continue
      if (occluded(Math.floor(tx / CELL), Math.floor(ty / CELL), vertical)) continue
      const dist = vertical ? Math.abs(ty - pcy) : Math.abs(tx - pcx)
      if (dist < dists[r]) dists[r] = dist
    }
  }
  const cap = 4
  for (let i = 0; i < 4; i++) out[i] = Math.min(counts[i], cap) / cap
  for (let i = 0; i < 4; i++)
    out[4 + i] = isFinite(dists[i]) ? Math.min(dists[i] / FIELD_PX, 1) : 1.5
  out[8] = Math.min(counts[2], counts[3], cap) / cap
  return out
}

// ── 确定性伪随机（xorshift32；测试自带，不依赖 world.rng）──────────────
function lcg(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s ^= s << 13
    s >>>= 0
    s ^= s >> 17
    s ^= s << 5
    s >>>= 0
    return s / 0x100000000
  }
}

function assertClose(a: Float32Array, b: Float32Array, label: string): void {
  for (let i = 0; i < a.length; i++) {
    expect(Math.abs(a[i] - b[i]), `${label}[${i}]: ${a[i]} vs ${b[i]}`).toBeLessThanOrEqual(1e-6)
  }
}

describe('POLICY_EXTRA：常量与布局契约', () => {
  it('维度/名字/哨兵/互换对 = 冻结值', () => {
    expect(POLICY_EXTRA_DIM).toBe(9)
    expect(POLICY_EXTRA_NAMES.length).toBe(9)
    expect(POLICY_EXTRA_HIT_NONE).toBe(1.5)
    expect(POLICY_EXTRA_HIT_DIST_NORM).toBe(GRID * CELL)
    expect(POLICY_EXTRA_COUNT_CAP).toBe(4)
    // 左右互换：威胁计数与命中距离各一对；前/后与包夹度不在表内（镜像不变）
    expect([...POLICY_EXTRA_MIRROR_SWAPS].map((p) => [...p])).toEqual([
      [2, 3],
      [6, 7],
    ])
  })
})

describe('POLICY_EXTRA：方位语义（玩家相对）', () => {
  it('朝上：世界四向的逼近弹各落对应桶', () => {
    const w = worldWithPlayer(1, 'up')
    const { cx, cy } = center(w)
    const out = compute(w)
    // 空场：计数 0、距离全 1.5、包夹 0
    expect([...out.slice(0, 4)]).toEqual([0, 0, 0, 0])
    expect([...out.slice(4, 8)]).toEqual([1.5, 1.5, 1.5, 1.5])
    expect(out[8]).toBe(0)
    // 正上方朝下走近战弹 ⇒ 前
    pushBullet(w, cx, cy - 3 * CELL, 'down')
    expect(compute(w)[0]).toBe(0.25)
    // 正下方朝上 ⇒ 后
    w.bullets.length = 0
    pushBullet(w, cx, cy + 3 * CELL, 'up')
    expect(compute(w)[1]).toBe(0.25)
    // 正左方朝右 ⇒ 左
    w.bullets.length = 0
    pushBullet(w, cx - 3 * CELL, cy, 'right')
    expect(compute(w)[2]).toBe(0.25)
    // 正右方朝左 ⇒ 右
    w.bullets.length = 0
    pushBullet(w, cx + 3 * CELL, cy, 'left')
    expect(compute(w)[3]).toBe(0.25)
  })

  it('朝右：世界左方威胁 = 我的后方（相对坐标不是世界坐标）', () => {
    const w = worldWithPlayer(2, 'right')
    const { cx, cy } = center(w)
    // 敌人在世界左侧、朝右瞄准我 ⇒ 我在其前方 ⇒ 威胁来自世界 left
    // 面朝右时「我的左」= 上、「我的右」= 下、后方 = 世界 left
    pushBullet(w, cx - 3 * CELL, cy, 'right')
    const out = compute(w)
    expect(out[1]).toBe(0.25) // back
    expect(out[0]).toBe(0)
  })

  it('包夹度 = min(左,右)/4，且左右无关前后', () => {
    const w = worldWithPlayer(3, 'up')
    const { cx, cy } = center(w)
    pushBullet(w, cx - 3 * CELL, cy, 'right')
    pushBullet(w, cx + 3 * CELL, cy, 'left')
    const out = compute(w)
    expect(out[2]).toBe(0.25)
    expect(out[3]).toBe(0.25)
    expect(out[8]).toBe(0.25)
  })

  it('计数归一化 cap：单向 5 颗逼近弹 ⇒ 1.0（不越界）', () => {
    const w = worldWithPlayer(4, 'up')
    const { cx, cy } = center(w)
    for (let i = 0; i < 5; i++) pushBullet(w, cx, cy - (2 + i) * CELL, 'down')
    const out = compute(w)
    expect(out[0]).toBe(1)
  })
})

describe('POLICY_EXTRA：命中距离（4 向）', () => {
  it('正前方无遮挡敌人 ⇒ 距离 = 轴向 px/416；敌人不在弹道/被墙挡 ⇒ 哨兵', () => {
    const w = worldWithPlayer(5, 'up')
    // 敌人 4 格前（同列）——无论其朝向如何，都算「我朝前能打到」
    const e = placeEnemy(w, PCOL, PROW - 4, 'basic', 'down')
    const expectDist = (4 * CELL) / FIELD_PX
    const out = compute(w)
    expect(out[4]).toBeCloseTo(expectDist, 6)
    expect(out[5]).toBe(1.5)
    expect(out[6]).toBe(1.5)
    expect(out[7]).toBe(1.5)
    // 敌人朝上（背对我）不影响「能打到」几何
    e.dir = 'up'
    expect(compute(w)[4]).toBeCloseTo(expectDist, 6)
    // 中间插墙 ⇒ 前向哨兵
    const col = Math.floor((w.player!.x + w.player!.w / 2) / CELL)
    w.tileMap.set(col, PROW - 2, 'brick')
    expect(compute(w)[4]).toBe(1.5)
  })

  it('多个敌人取最近；偏轴 2 格（>19px）不算同线', () => {
    const w = worldWithPlayer(6, 'up')
    placeEnemy(w, PCOL, PROW - 6, 'basic', 'down')
    placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    expect(compute(w)[4]).toBeCloseTo((3 * CELL) / FIELD_PX, 6)
    // 把近敌挪出带（+2 格 = 32px > 19px）⇒ 取远敌
    w.tanks.length = 0
    placeEnemy(w, PCOL, PROW - 6, 'basic', 'down')
    placeEnemy(w, PCOL + 2, PROW - 3, 'basic', 'down')
    expect(compute(w)[4]).toBeCloseTo((6 * CELL) / FIELD_PX, 6)
  })

  it('生成中敌（spawnTimer>0）与死敌不算', () => {
    const w = worldWithPlayer(7, 'up')
    const e1 = placeEnemy(w, PCOL, PROW - 3, 'basic', 'down')
    e1.spawnTimer = 500
    const e2 = placeEnemy(w, PCOL, PROW - 5, 'basic', 'down')
    e2.alive = false
    expect(compute(w)[4]).toBe(1.5)
  })
})

describe('POLICY_EXTRA：镜像（左右互换）', () => {
  it('整体镜像场景：前/后/包夹度不变，左右两对互换', () => {
    const w1 = worldWithPlayer(11, 'up')
    const c1 = center(w1)
    pushBullet(w1, c1.cx - 3 * CELL, c1.cy, 'right') // 左威胁
    pushBullet(w1, c1.cx, c1.cy - 5 * CELL, 'down') // 前威胁
    placeEnemy(w1, PCOL - 4, PROW, 'basic', 'right') // 左敌人（可打到）
    const a = compute(w1)

    const w2 = worldWithPlayer(12, 'up')
    // 镜像：x' = FIELD_PX - (x + w)，方向 left<->right 互换，墙格列 c' = GRID-1-c。
    // ⚠ 坦克 32px 跨 2 格（CELL=16），像素镜像 ≠ 格镜像——两边都必须按像素翻。
    const mx = (x: number, wid: number): number => FIELD_PX - (x + wid)
    const p1 = w1.player!
    const p2 = w2.player!
    p2.x = mx(p1.x, p1.w)
    const c2 = center(w2)
    pushBullet(w2, mx(c1.cx - 3 * CELL - BULLET / 2, BULLET) + BULLET / 2, c1.cy, 'left')
    pushBullet(w2, c2.cx, c2.cy - 5 * CELL, 'down')
    const e = placeEnemy(w2, 0, PROW, 'basic', 'left')
    e.x = mx((PCOL - 4) * CELL, TANK)
    const b = compute(w2)

    expect(b[0]).toBeCloseTo(a[0], 6) // 前不变
    expect(b[1]).toBeCloseTo(a[1], 6) // 后不变
    expect(b[2]).toBeCloseTo(a[3], 6) // 左 = 原右
    expect(b[3]).toBeCloseTo(a[2], 6) // 右 = 原左
    expect(b[4]).toBeCloseTo(a[4], 6)
    expect(b[5]).toBeCloseTo(a[5], 6)
    expect(b[6]).toBeCloseTo(a[7], 6)
    expect(b[7]).toBeCloseTo(a[6], 6)
    expect(b[8]).toBeCloseTo(a[8], 6) // 包夹度不变
  })
})

describe('POLICY_EXTRA：对拍与纯函数', () => {
  it('随机场景 × 60：与独立重实现逐值一致（含遮挡/多源/多朝向）', () => {
    const rand = lcg(20261005)
    for (let s = 0; s < 60; s++) {
      const w = worldWithPlayer(100 + s, 'up')
      const p = w.player!
      // 随机把玩家放到场地内、随机朝向
      const dirs: Direction[] = ['up', 'down', 'left', 'right']
      positionPlayer(
        w,
        2 + Math.floor(rand() * (GRID - 4)),
        2 + Math.floor(rand() * (GRID - 4)),
        dirs[Math.floor(rand() * 4)],
      )
      // 随机墙（brick/steel/water/empty）
      for (let k = 0; k < 30; k++) {
        const c = Math.floor(rand() * GRID)
        const r = Math.floor(rand() * GRID)
        const t = rand()
        w.tileMap.set(c, r, t < 0.5 ? 'empty' : t < 0.75 ? 'brick' : t < 0.9 ? 'steel' : 'water')
      }
      // 随机敌人
      const nEnemies = Math.floor(rand() * 6)
      for (let k = 0; k < nEnemies; k++) {
        const e = placeEnemy(
          w,
          Math.floor(rand() * GRID),
          Math.floor(rand() * GRID),
          'basic',
          dirs[Math.floor(rand() * 4)],
        )
        if (rand() < 0.15) e.spawnTimer = 100
        if (rand() < 0.1) e.alive = false
        // 让位置最小程度贴近真实：随机的坦克位置可能与玩家重叠，重叠即「同格」
        // ——两边实现都按同一几何处理，对拍仍然成立。
      }
      // 随机敌弹（中心坐标）
      const nBullets = Math.floor(rand() * 5)
      const pcx = p.x + p.w / 2
      const pcy = p.y + p.h / 2
      for (let k = 0; k < nBullets; k++) {
        pushBullet(
          w,
          pcx + (rand() - 0.5) * 8 * CELL,
          pcy + (rand() - 0.5) * 8 * CELL,
          dirs[Math.floor(rand() * 4)],
        )
      }
      const before = { x: p.x, y: p.y, dir: p.dir, frame: w.frame }
      assertClose(compute(w), indepExtra(w), `seed ${100 + s}`)
      expect(p.x).toBe(before.x)
      expect(p.y).toBe(before.y)
      expect(p.dir).toBe(before.dir)
      expect(w.frame).toBe(before.frame)
    }
  })

  it('无玩家 / 玩家死亡 ⇒ 全 0（不含 1.5 哨兵）', () => {
    const empty = seedWorld(21)
    const out1 = compute(empty)
    expect([...out1]).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0])
    const w = worldWithPlayer(22)
    w.player!.alive = false
    const out2 = compute(w)
    expect([...out2]).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0])
  })
})
