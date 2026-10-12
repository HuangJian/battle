/**
 * policy-extra.test.ts — `POLICY_EXTRA` **37 维**方向直方图的单测
 * （plan/obs-v5-vector-gap.plan.md §2.2/§2.5；v1 的 9 维版见 git 历史）。
 *
 * 体例仿 `tests/sim/danger-metrics.test.ts`：**独立重实现对账**（本文件不看生产
 * helper 的实现，只按 plan §2.2 的表 + §2.5 的公式自己写一遍），再加方位/镜像/边界
 * 用例与「纯函数不改 World」的断言。任何一侧改口径都会让逐值对账变红。
 *
 * 布局（plan §2.2 冻结；F/B/L/R = 玩家相对，前 = 炮口朝向）：
 *   0..3   前/后/左/右 威胁计数（19px 带 + 瞄我 + 遮挡），`min(n,4)/4`；
 *   4..7   前/后/左/右 命中距离（clamp01(px/416)；打不到 ⇒ 1.5 哨兵）；
 *   8      包夹度 = `min(左,右,4)/4`；
 *   9..12  各向连续可走**地形**格数 /8（步进 1 格、查左上 TANK 矩形、出界即停）；
 *   13..16 各向最近逼近无遮挡敌弹 TTI /60；无弹 ⇒ 1.5；
 *   17..20 各向同轴 19px + 无遮挡敌数（**不要求瞄准**）`min(n,4)/4`；
 *   21..24 各向首个**阻弹**地形距离 px/416（坦克不挡；brick/steel/base 挡）；无 ⇒ 1.5；
 *   25..27 老家上/左/右逼近敌数 `min(n,4)/4`（**世界坐标**，转身不变）；
 *   28..30 老家环墙各侧完好比例（上 /4、左/右 /2）；
 *   31     指定威胁敌（距基地最近）到基地 TTI /300；无 ⇒ 1.5；
 *   32..33 该敌相对**玩家**的单位向量（x 在 mirrorX 下取负）；
 *   34..36 各侧最近敌车曼哈顿格距 /12；该侧无敌 ⇒ 1.5。
 */
import { describe, expect, it } from 'bun:test'
import { BASE_POS, BULLET, CELL, GRID, TANK, type Direction } from '../../src/constants'
import {
  POLICY_EXTRA_BASE_DIST_NORM,
  POLICY_EXTRA_BASE_TTI_NORM,
  POLICY_EXTRA_COUNT_CAP,
  POLICY_EXTRA_CORR_CAP,
  POLICY_EXTRA_DIM,
  POLICY_EXTRA_HIT_DIST_NORM,
  POLICY_EXTRA_HIT_NONE,
  POLICY_EXTRA_MIRROR_NEG,
  POLICY_EXTRA_MIRROR_SWAPS,
  POLICY_EXTRA_NAMES,
  POLICY_EXTRA_TTI_NORM,
  computePolicyExtra,
  mirrorSwap,
} from '../../src/nn/policy-extra'
import { RING_CELLS } from '../../src/ai/god/ThreatBudget'
import type { Bullet, Tank } from '../../src/types'
import type { World } from '../../src/game/World'
import { makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'

const PCOL = 5
const PROW = 8
const FIELD_PX = GRID * CELL
/** obs 侧 19px 命中带 = 坦克半宽 16 + 子弹半高 3（plan §2.5）。 */
const BAND = TANK / 2 + BULLET / 2

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

/** 玩家**身体左上格**行（`placeEnemy` 的 row 参数口径；玩家中心行可能差一格）。 */
function r0Of(w: World): number {
  return Math.floor(w.player!.y / CELL)
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
const VEC: Record<Direction, { dx: number; dy: number }> = {
  up: { dx: 0, dy: -1 },
  down: { dx: 0, dy: 1 },
  left: { dx: -1, dy: 0 },
  right: { dx: 1, dy: 0 },
}

function relIdx(side: Direction, facing: Direction): number {
  if (side === facing) return 0
  if (side === OPP[facing]) return 1
  if (side === LOF[facing]) return 2
  return 3
}

/** 相对朝向（0 前 / 1 后 / 2 左 / 3 右）→ 世界朝向。 */
function relDir(facing: Direction, r: number): Direction {
  return r === 0 ? facing : r === 1 ? OPP[facing] : r === 2 ? LOF[facing] : OPP[LOF[facing]]
}

/** 阻弹地形（plan §2.5：brick/steel/base 挡，水/草/冰过）。 */
function blocksBulletAt(w: World, c: number, r: number): boolean {
  const t = w.tileMap.get(c, r)
  return t === 'brick' || t === 'steel' || t === 'base'
}

/** 阻车地形（brick/steel/water/base 挡，与 TileMap.blocksTank 同）。 */
function blocksTankAt(w: World, c: number, r: number): boolean {
  const t = w.tileMap.get(c, r)
  return t === 'brick' || t === 'steel' || t === 'water' || t === 'base'
}

/** 源格 (sc,sr) 到目标体跨格 [cLow..cHigh]/[rLow..rHigh] 的轴向遮挡（raycast 不含端点）。 */
function occludedSpan(
  w: World,
  sc: number,
  sr: number,
  vertical: boolean,
  cLow: number,
  cHigh: number,
  rLow: number,
  rHigh: number,
): boolean {
  if (vertical) {
    if (sr < rLow) {
      for (let r = sr + 1; r < rLow; r++) if (blocksBulletAt(w, sc, r)) return true
    } else {
      for (let r = sr - 1; r > rHigh; r--) if (blocksBulletAt(w, sc, r)) return true
    }
    return false
  }
  if (sc < cLow) {
    for (let c = sc + 1; c < cLow; c++) if (blocksBulletAt(w, c, sr)) return true
  } else {
    for (let c = sc - 1; c > cHigh; c--) if (blocksBulletAt(w, c, sr)) return true
  }
  return false
}

/** 矩形（左上 x,y，TANK×TANK）是否压到阻车地形。 */
function rectBlocked(w: World, x: number, y: number): boolean {
  const c0 = Math.floor(x / CELL)
  const c1 = Math.floor((x + TANK - 1) / CELL)
  const r0 = Math.floor(y / CELL)
  const r1 = Math.floor((y + TANK - 1) / CELL)
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) if (blocksTankAt(w, c, r)) return true
  }
  return false
}

/** corr：`snap(tank)` 起、逐格步进 CELL、查左上 TANK 矩形；出界或撞地形即停。 */
function indepCorr(w: World, tank: Tank, dir: Direction): number {
  const v = VEC[dir]
  const gx = Math.round(tank.x / CELL) * CELL
  const gy = Math.round(tank.y / CELL) * CELL
  let k = 0
  for (let i = 1; i <= POLICY_EXTRA_CORR_CAP; i++) {
    const nx = gx + v.dx * CELL * i
    const ny = gy + v.dy * CELL * i
    if (nx < 0 || ny < 0 || nx + TANK > FIELD_PX || ny + TANK > FIELD_PX) break
    if (rectBlocked(w, nx, ny)) break
    k = i
  }
  return k
}

/** blk：玩家中心格沿该向首个阻弹格的距离（px）；出界/无阻 ⇒ -1（调用方转 1.5）。 */
function indepBlk(w: World, px: number, py: number, dir: Direction): number {
  const v = VEC[dir]
  const c0 = Math.floor(px / CELL)
  const r0 = Math.floor(py / CELL)
  for (let i = 1; i <= GRID; i++) {
    const c = c0 + v.dx * i
    const r = r0 + v.dy * i
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return -1
    if (blocksBulletAt(w, c, r)) return i * CELL
  }
  return -1
}

/** 基地侧判定（世界坐标，plan §2.5）：先行后列；2×2 足印内 ⇒ -1。 */
function indepBaseSide(col: number, row: number): 'U' | 'D' | 'L' | 'R' | null {
  if (row < BASE_POS.row) return 'U'
  if (row > BASE_POS.row + 1) return 'D'
  if (col < BASE_POS.col) return 'L'
  if (col > BASE_POS.col + 1) return 'R'
  return null
}

function indepExtra(w: World): Float32Array {
  const out = new Float32Array(POLICY_EXTRA_DIM)
  const p = w.player
  if (!p || !p.alive) return out
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const colLow = Math.floor(p.x / CELL)
  const colHigh = Math.floor((p.x + p.w - 1) / CELL)
  const rowLow = Math.floor(p.y / CELL)
  const rowHigh = Math.floor((p.y + p.h - 1) / CELL)
  const counts = [0, 0, 0, 0]
  const dists = [Infinity, Infinity, Infinity, Infinity]
  const tti = [Infinity, Infinity, Infinity, Infinity]
  const lane = [0, 0, 0, 0]
  const occluded = (sc: number, sr: number, vertical: boolean): boolean =>
    occludedSpan(w, sc, sr, vertical, colLow, colHigh, rowLow, rowHigh)

  // ── ①②③ 子弹：威胁计数 + 4 向 TTI ──
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
    const r = relIdx(side, p.dir)
    counts[r]++
    const along = vertical ? Math.abs(by - pcy) : Math.abs(bx - pcx)
    const t = along / (b.speed > 0 ? b.speed : 1)
    if (t < tti[r]) tti[r] = t
  }
  // ── 坦克：威胁计数（瞄我）+ 4 向命中距离 ──
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
      const d = relDir(p.dir, r)
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
    // ── ④ 同轴敌数（不要求瞄准；同列优先分桶 ⇒ Σ 桶 ≡ 总数）──
    const sameCol = Math.abs(tx - pcx) < BAND
    const sameRow = Math.abs(ty - pcy) < BAND
    if (!sameCol && !sameRow) continue
    if (occluded(Math.floor(tx / CELL), Math.floor(ty / CELL), sameCol)) continue
    const side: Direction = sameCol ? (ty < pcy ? 'up' : 'down') : tx < pcx ? 'left' : 'right'
    lane[relIdx(side, p.dir)]++
  }

  const cap = POLICY_EXTRA_COUNT_CAP
  for (let i = 0; i < 4; i++) out[i] = Math.min(counts[i], cap) / cap
  for (let i = 0; i < 4; i++)
    out[4 + i] = isFinite(dists[i]) ? Math.min(dists[i] / POLICY_EXTRA_HIT_DIST_NORM, 1) : 1.5
  out[8] = Math.min(counts[2], counts[3], cap) / cap
  for (let r = 0; r < 4; r++) {
    out[9 + r] = indepCorr(w, p, relDir(p.dir, r)) / POLICY_EXTRA_CORR_CAP
    out[13 + r] = isFinite(tti[r]) ? Math.min(tti[r] / POLICY_EXTRA_TTI_NORM, 1) : 1.5
    out[17 + r] = Math.min(lane[r], cap) / cap
    const bpx = indepBlk(w, pcx, pcy, relDir(p.dir, r))
    out[21 + r] = bpx < 0 ? 1.5 : Math.min(bpx / POLICY_EXTRA_HIT_DIST_NORM, 1)
  }

  // ── ⑤ 基地族（25–36）：无基地 ⇒ 全 0（本函数对无基地世界早退）──
  if (!w.tileMap.hasBase()) return out
  const bcx = (BASE_POS.col + 1) * CELL
  const bcy = (BASE_POS.row + 1) * CELL
  const bColLow = BASE_POS.col
  const bColHigh = BASE_POS.col + 1
  const bRowLow = BASE_POS.row
  const bRowHigh = BASE_POS.row + 1
  let bUp = 0
  let bLeft = 0
  let bRight = 0
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const sameCol = Math.abs(tx - bcx) < BAND
    const sameRow = Math.abs(ty - bcy) < BAND
    if (!sameCol && !sameRow) continue
    const vertical: boolean = sameCol
    if (
      occludedSpan(
        w,
        Math.floor(tx / CELL),
        Math.floor(ty / CELL),
        vertical,
        bColLow,
        bColHigh,
        bRowLow,
        bRowHigh,
      )
    )
      continue
    if (sameCol) {
      if (ty < bcy) bUp++
      // D 向（ty > bcy）几何不可达（基地底行 = 25）⇒ 不计入任何侧
    } else if (tx < bcx) bLeft++
    else bRight++
  }
  out[25] = Math.min(bUp, cap) / cap
  out[26] = Math.min(bLeft, cap) / cap
  out[27] = Math.min(bRight, cap) / cap
  let ringUp = 0
  let ringLeft = 0
  let ringRight = 0
  for (const cell of RING_CELLS) {
    const t = w.tileMap.get(cell.col, cell.row)
    const ok = t === 'brick' || t === 'steel' ? 1 : 0
    if (cell.row < BASE_POS.row) ringUp += ok
    else if (cell.col < BASE_POS.col) ringLeft += ok
    else ringRight += ok
  }
  out[28] = ringUp / 4
  out[29] = ringLeft / 2
  out[30] = ringRight / 2
  // 指定威胁敌 = 中心格曼哈顿距基地左上格最近（`strict <` 首个最小者胜）
  let bestCells = -1
  let bestX = 0
  let bestY = 0
  let bestSpeed = 1
  const near: Record<'U' | 'D' | 'L' | 'R', number> = {
    U: Infinity,
    D: Infinity,
    L: Infinity,
    R: Infinity,
  }
  for (const t of w.allTanks) {
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const cells = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const side = indepBaseSide(col, row)
    if (side && cells < near[side]) near[side] = cells
    if (bestCells < 0 || cells < bestCells) {
      bestCells = cells
      bestX = t.x + t.w / 2
      bestY = t.y + t.h / 2
      bestSpeed = t.speed
    }
  }
  if (bestCells >= 0) {
    out[31] = Math.min(
      (bestCells * CELL) / (bestSpeed > 0 ? bestSpeed : 1) / POLICY_EXTRA_BASE_TTI_NORM,
      1,
    )
    const dx = bestX - pcx
    const dy = bestY - pcy
    const d = Math.hypot(dx, dy)
    if (d > 0) {
      out[32] = dx / d
      out[33] = dy / d
    }
  } else {
    out[31] = 1.5
  }
  for (const [side, idx] of [
    ['U', 34],
    ['L', 35],
    ['R', 36],
  ] as const) {
    const cells = near[side]
    out[idx] = isFinite(cells) ? Math.min(cells / POLICY_EXTRA_BASE_DIST_NORM, 1) : 1.5
  }
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
  it('维度/名字/哨兵/互换对/取负表 = 冻结值（plan §2.2/§2.4）', () => {
    expect(POLICY_EXTRA_DIM as number).toBe(37)
    expect(POLICY_EXTRA_NAMES.length).toBe(37)
    expect([...POLICY_EXTRA_NAMES]).toEqual([
      'threatFront',
      'threatBack',
      'threatLeft',
      'threatRight',
      'hitDistFront',
      'hitDistBack',
      'hitDistLeft',
      'hitDistRight',
      'pincer',
      'corrF',
      'corrB',
      'corrL',
      'corrR',
      'ttiF',
      'ttiB',
      'ttiL',
      'ttiR',
      'laneEF',
      'laneEB',
      'laneEL',
      'laneER',
      'blkF',
      'blkB',
      'blkL',
      'blkR',
      'baseThreatU',
      'baseThreatL',
      'baseThreatR',
      'baseRingU',
      'baseRingL',
      'baseRingR',
      'baseThreatTTI',
      'baseThreatRelX',
      'baseThreatRelY',
      'baseEnemyDistU',
      'baseEnemyDistL',
      'baseEnemyDistR',
    ])
    expect(POLICY_EXTRA_HIT_NONE).toBe(1.5)
    expect(POLICY_EXTRA_HIT_DIST_NORM).toBe(GRID * CELL)
    expect(POLICY_EXTRA_COUNT_CAP).toBe(4)
    expect(POLICY_EXTRA_CORR_CAP).toBe(8)
    expect(POLICY_EXTRA_TTI_NORM).toBe(60)
    expect(POLICY_EXTRA_BASE_TTI_NORM).toBe(300)
    expect(POLICY_EXTRA_BASE_DIST_NORM).toBe(12)
    // mirrorX：9 对互换（v1 两对 + 新族七对）；D 向与向量 x 维不在交换表内。
    expect([...POLICY_EXTRA_MIRROR_SWAPS].map((p) => [...p])).toEqual([
      [2, 3],
      [6, 7],
      [11, 12],
      [15, 16],
      [19, 20],
      [23, 24],
      [26, 27],
      [29, 30],
      [35, 36],
    ])
    expect([...POLICY_EXTRA_MIRROR_NEG]).toEqual([32])
  })

  it('mirrorSwap：按冻结表九对互换 + [32] 取负（与 python mirror_x 同表）', () => {
    const extra = Float32Array.from({ length: POLICY_EXTRA_DIM }, (_, i) => i + 1)
    const exp = Float32Array.from(extra)
    for (const [a, b] of [
      [2, 3],
      [6, 7],
      [11, 12],
      [15, 16],
      [19, 20],
      [23, 24],
      [26, 27],
      [29, 30],
      [35, 36],
    ]) {
      const t = exp[a]
      exp[a] = exp[b]
      exp[b] = t
    }
    exp[32] = -exp[32]
    mirrorSwap(extra)
    expect(Array.from(extra)).toEqual(Array.from(exp))
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
    // 敌弹在世界左侧、朝右逼近我 ⇒ 威胁来自世界 left
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

describe('POLICY_EXTRA：corr（9–12）走廊地形格数 /8', () => {
  it('空旷场 4 向 = 1.0（满 8 格）；4 格外挡砖 ⇒ (4−1)/8', () => {
    const w = worldWithPlayer(8, 'up')
    // 场地中心（col 12/row 12）：四向都离边界 ≥8 格（col 5/row 8 处朝左只剩 5 格）。
    positionPlayer(w, 12, 12, 'up')
    const out = compute(w)
    for (let r = 0; r < 4; r++) expect(out[9 + r]).toBe(1)
    // 玩家身体格跨 cols 12–13 / rows 12–13（32px / 16px）。朝左：第 2 步目标矩形 = cols 10–11，
    // 第 3 步 = cols 9–10 ⇒ 把砖放在 col 9 才在第 3 步首次撞上 ⇒ 走满 2 格 = 2/8。
    const p = w.player!
    const c0 = Math.floor((p.x + p.w / 2) / CELL) // 中心正好落格边界 ⇒ 归右格（col 13）
    const r0 = Math.floor((p.y + p.h / 2) / CELL)
    w.tileMap.set(c0 - 4, r0, 'brick')
    const out2 = compute(w)
    expect(out2[9 + 2]).toBeCloseTo(2 / 8, 6) // corrL（面朝上时左 = 世界 left）
    expect(out2[9 + 0]).toBe(1) // 前方不受影响
    expect(out2[9 + 3]).toBe(1) // 右侧（世界 right）不受影响
  })

  it('水面挡车（与 blk 的阻弹口径不同）：水在 3 格外 ⇒ corr 减、blk 不减', () => {
    const w = worldWithPlayer(9, 'up')
    const p = w.player!
    const c0 = Math.floor((p.x + p.w / 2) / CELL)
    const r0 = Math.floor((p.y + p.h / 2) / CELL)
    // 第 1 步目标矩形 = rows 7–8 ⇒ 水放 row 6 才在第 2 步撞上（走满 1 格）。
    w.tileMap.set(c0, r0 - 3, 'water')
    const out = compute(w)
    expect(out[9]).toBeCloseTo(1 / 8, 6) // 前：走 1 格即被水挡住
    expect(out[21]).toBe(1.5) // 子弹穿水 ⇒ 无阻弹
  })
})

describe('POLICY_EXTRA：tti（13–16）与 laneE（17–20）', () => {
  it('tti = 轴向距离 / 弹速 /60；远离/无弹 ⇒ 1.5', () => {
    const w = worldWithPlayer(10, 'up')
    const { cx, cy } = center(w)
    // 3 格前（48px）朝下、speed 4 ⇒ tti = 12 tick ⇒ 12/60 = 0.2
    pushBullet(w, cx, cy - 3 * CELL, 'down', { speed: 4 })
    const out = compute(w)
    expect(out[13]).toBeCloseTo(0.2, 6)
    expect(out[14]).toBe(1.5)
    // 同位置的**远离**弹（朝上）不算 TTI（也不算威胁计数）
    w.bullets.length = 0
    pushBullet(w, cx, cy - 3 * CELL, 'up', { speed: 4 })
    expect(compute(w)[13]).toBe(1.5)
  })

  it('laneE 不要求瞄准：背对我的同轴敌也算；威胁计数（0–3）不算', () => {
    const w = worldWithPlayer(11, 'up')
    const p = w.player!
    const r0 = Math.floor((p.y + p.h / 2) / CELL)
    // 世界左侧同轴敌，炮口朝上（背对/不瞄我）
    placeEnemy(w, PCOL - 3, r0, 'basic', 'up')
    const out = compute(w)
    expect(out[17 + 2]).toBeCloseTo(0.25, 6) // laneEL（面朝上 ⇒ 左 = 世界 left）
    expect(out[2]).toBe(0) // threatLeft：没瞄我 ⇒ 0
    // hitDistLeft：同轴 + 在我左半平面 ⇒ 有距离（「能不能打到」纯几何，不看朝向）
    expect(out[4 + 2]).toBeCloseTo((3 * CELL) / FIELD_PX, 6)
  })

  it('laneE 遮挡：中间隔砖 ⇒ 不计（与威胁计数同一份 raycast）', () => {
    const w = worldWithPlayer(12, 'up')
    // 砖必须落在**源格行**上（源行 = 敌车中心格行，不一定是玩家中心行）。
    const e = placeEnemy(w, PCOL - 4, r0Of(w), 'basic', 'up')
    const srcRow = Math.floor((e.y + e.h / 2) / CELL)
    w.tileMap.set(PCOL - 2, srcRow, 'brick')
    const out = compute(w)
    expect(out[17 + 2]).toBe(0)
  })
})

describe('POLICY_EXTRA：blk（21–24）首阻弹地形', () => {
  it('无阻弹 ⇒ 1.5；2 格外砖 ⇒ 2×CELL/416；钢同挡、草/水不挡', () => {
    const w = worldWithPlayer(13, 'up')
    const p = w.player!
    const c0 = Math.floor((p.x + p.w / 2) / CELL)
    const r0 = Math.floor((p.y + p.h / 2) / CELL)
    expect(compute(w)[21]).toBe(1.5)
    w.tileMap.set(c0, r0 - 2, 'brick')
    expect(compute(w)[21]).toBeCloseTo((2 * CELL) / FIELD_PX, 6)
    w.tileMap.set(c0, r0 - 2, 'steel')
    expect(compute(w)[21]).toBeCloseTo((2 * CELL) / FIELD_PX, 6)
    w.tileMap.set(c0, r0 - 2, 'forest')
    expect(compute(w)[21]).toBe(1.5)
    w.tileMap.set(c0, r0 - 2, 'water')
    expect(compute(w)[21]).toBe(1.5)
  })
})

describe('POLICY_EXTRA：基地族（25–36）', () => {
  it('无基地（rebuildBaseCache 后 basePos=null）⇒ 25–36 按类走无目标约定（全 0）', () => {
    const w = worldWithPlayer(14, 'up')
    w.tileMap.rebuildBaseCache()
    expect(w.tileMap.hasBase()).toBe(false)
    const out = compute(w)
    for (let i = 25; i <= 36; i++) expect(out[i], `out[${i}]`).toBe(0)
  })

  it('有基地 + 上方对齐敌：baseThreatU / 环墙 / 指定敌 TTI+向量 / 三侧格距', () => {
    const w = worldWithPlayer(15, 'up')
    // 基地 2×2 在 (12,24)-(13,25)；环墙默认被 emptyField 清空 ⇒ 全部完好率 0。
    // 敌车放 cell (12,19)：中心 (208,320) ⇒ 与基地同列、在基地上方 4 个中心格。
    const e = placeEnemy(w, 12, 19, 'basic', 'down')
    e.spawnTimer = 0
    const out = compute(w)
    expect(out[25]).toBe(0.25) // baseThreatU（世界上方，我面朝上也不影响）
    expect(out[26]).toBe(0)
    expect(out[27]).toBe(0)
    expect(out[28]).toBe(0) // 环墙被清空 ⇒ 0/4
    expect(out[29]).toBe(0)
    expect(out[30]).toBe(0)
    // 指定敌 = 距基地左上格（12,24）曼哈顿最近的敌车。⚠ 敌车 32px 宽、中心正好落在
    // 格边界上（col*16+TANK/2）⇒ 中心格归**右**格（13）——不是放置参数里的 12。
    const ec = Math.floor((e.x + e.w / 2) / CELL)
    const er = Math.floor((e.y + e.h / 2) / CELL)
    const cells = Math.abs(ec - BASE_POS.col) + Math.abs(er - BASE_POS.row)
    expect([ec, er, cells]).toEqual([13, 20, 5])
    expect(out[31]).toBeCloseTo(Math.min((cells * CELL) / e.speed / 300, 1), 6)
    const p = w.player!
    const dx = e.x + e.w / 2 - (p.x + p.w / 2)
    const dy = e.y + e.h / 2 - (p.y + p.h / 2)
    const d = Math.hypot(dx, dy)
    expect(out[32]).toBeCloseTo(dx / d, 6)
    expect(out[33]).toBeCloseTo(dy / d, 6)
    expect(out[34]).toBeCloseTo(Math.min(cells / 12, 1), 6) // baseEnemyDistU（>1 截断在 1）
    expect(out[35]).toBe(1.5)
    expect(out[36]).toBe(1.5)
    // 拍：把该敌移到基地左下方（世界 L 侧，中心格 (9,25)）⇒ U 消失、L 出现
    e.x = 8 * CELL
    e.y = 24 * CELL
    const out2 = compute(w)
    expect(out2[25]).toBe(0)
    expect(out2[26]).toBe(0.25)
    expect(out2[34]).toBe(1.5)
    expect(out2[35]).toBeCloseTo(4 / 12, 6)
  })

  it('环墙完好比例：RING_CELLS 上 4 / 左 2 / 右 2（brick 与 steel 都算完好）', () => {
    const w = worldWithPlayer(16, 'up')
    // 分组的**优先级**是冻结语义：先看行（row < 24 ⇒ 上），再看列 —— 顶部那一行的
    // 四格（cols 11–14）全部归「上」，即使它们的 col 在基地左侧/右侧之外。
    const upCells = RING_CELLS.filter((c) => c.row < BASE_POS.row)
    const leftCells = RING_CELLS.filter((c) => c.row >= BASE_POS.row && c.col < BASE_POS.col)
    const rightCells = RING_CELLS.filter((c) => c.row >= BASE_POS.row && c.col > BASE_POS.col)
    expect([upCells.length, leftCells.length, rightCells.length]).toEqual([4, 2, 2])
    for (const cell of RING_CELLS) {
      w.tileMap.set(cell.col, cell.row, cell.col === BASE_POS.col ? 'brick' : 'steel')
    }
    const out = compute(w)
    expect(out[28]).toBe(1)
    expect(out[29]).toBe(1)
    expect(out[30]).toBe(1)
    // 打掉左侧一格 ⇒ 左 = 1/2；上侧不受影响
    w.tileMap.set(leftCells[0].col, leftCells[0].row, 'empty')
    const out2 = compute(w)
    expect(out2[29]).toBe(0.5)
    expect(out2[28]).toBe(1)
    // 打掉顶部最左一格（col < 12 但 row < 24）⇒ 归「上」不归「左」：上 = 3/4、左不变
    w.tileMap.set(upCells[0].col, upCells[0].row, 'empty')
    const out3 = compute(w)
    expect(out3[28]).toBe(0.75)
    expect(out3[29]).toBe(0.5)
  })

  it('环墙被砖/钢外的地形占位 ⇒ 不计（草/水/空）', () => {
    const w = worldWithPlayer(17, 'up')
    for (const cell of RING_CELLS) w.tileMap.set(cell.col, cell.row, 'forest')
    const out = compute(w)
    expect(out[28]).toBe(0)
    expect(out[29]).toBe(0)
    expect(out[30]).toBe(0)
  })

  it('生成中的敌车不算基地威胁（spawnTimer>0）', () => {
    const w = worldWithPlayer(18, 'up')
    const e = placeEnemy(w, 12, 19, 'basic', 'down')
    e.spawnTimer = 500
    const out = compute(w)
    expect(out[25]).toBe(0)
    expect(out[31]).toBe(1.5)
    expect(out[34]).toBe(1.5)
  })
})

describe('POLICY_EXTRA：镜像（左右互换）', () => {
  it('整体镜像场景：前/后/包夹度不变，左右两对互换（v1 九维语义）', () => {
    const w1 = worldWithPlayer(21, 'up')
    const c1 = center(w1)
    pushBullet(w1, c1.cx - 3 * CELL, c1.cy, 'right') // 左威胁
    pushBullet(w1, c1.cx, c1.cy - 5 * CELL, 'down') // 前威胁
    placeEnemy(w1, PCOL - 4, PROW, 'basic', 'right') // 左敌人（可打到）
    const a = compute(w1)

    const w2 = worldWithPlayer(22, 'up')
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
    const rand = lcg(20261012)
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
      // 随机墙（brick/steel/water/empty）——base 族会读到这些格；`hasBase()` 保持 true
      // （set() 不重建基地缓存，与生产一致：缓存只在 loadStage/快照恢复时重建）。
      for (let k = 0; k < 30; k++) {
        const c = Math.floor(rand() * GRID)
        const r = Math.floor(rand() * GRID)
        const t = rand()
        w.tileMap.set(c, r, t < 0.5 ? 'empty' : t < 0.75 ? 'brick' : t < 0.9 ? 'steel' : 'water')
      }
      // 随机敌人（含基地族要读的 `allTanks`）
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

  it('无玩家 / 玩家死亡 ⇒ 37 维全 0（不含 1.5 哨兵）', () => {
    const empty = seedWorld(31)
    expect([...compute(empty)]).toEqual(new Array(POLICY_EXTRA_DIM).fill(0))
    const w = worldWithPlayer(32)
    placeEnemy(w, 12, 19, 'basic', 'down') // 有基地威胁也照样全 0
    w.player!.alive = false
    expect([...compute(w)]).toEqual(new Array(POLICY_EXTRA_DIM).fill(0))
  })

  it('哨兵语义可分：1.5 = 「有玩家但该向无目标」，0 = 「无玩家」（两者不混淆）', () => {
    const w = worldWithPlayer(33, 'up')
    const live = compute(w)
    expect(live[4]).toBe(1.5) // 无敌人 ⇒ hitDist 哨兵
    expect(live[21]).toBe(1.5) // 无阻弹 ⇒ blk 哨兵
    expect(live[31]).toBe(1.5) // 无基地威胁敌 ⇒ baseThreatTTI 哨兵
    expect(live[34]).toBe(1.5) // 该侧无敌 ⇒ baseEnemyDist 哨兵
    const dead = worldWithPlayer(34, 'up')
    dead.player!.alive = false
    const d = compute(dead)
    for (const i of [4, 21, 31, 34]) expect(d[i], `dead[${i}]`).toBe(0)
  })
})
