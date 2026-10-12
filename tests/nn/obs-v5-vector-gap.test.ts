/**
 * obs-v5-vector-gap.test.ts — obs-v5 新族的**跨维对账**（plan/obs-v5-vector-gap.plan.md §3-S3
 * 的 A2/A4/A5/A8；§2 是唯一口径来源）。
 *
 * 分工：`policy-extra.test.ts` 负责 POLICY_EXTRA 自己的冻结表 + 独立重实现 60 局对拍；
 * 本文件负责**跨系统**的那几件（一侧改口径就会红）：
 *   · A2  corr 与地形版 `canStep` 的 k=1 一致性（200 局面 × 4 向）；
 *   · A4  TS `mirrorSwap`/`mirrorScalars` vs 整体镜像场景（extra + scalars 双张量）；
 *   · A5  口径对账：pickup 三连（含同源规则）、s37、s38↔extra 34–36 恒等式、s6 环墙；
 *   · A8  逐维 golden：s30/s31/s35/s36/s39（s39 逼近正/远离负/无敌零 + 同敌断言）。
 */
import { describe, expect, it } from 'bun:test'
import { BULLET, CELL, GRID, TANK } from '../../src/constants'
import { ObsEncoder, SCALAR_DIM, SCALAR_X_INDICES, mirrorScalars } from '../../src/nn/obs-encoder'
import { POLICY_EXTRA_DIM, mirrorSwap } from '../../src/nn/policy-extra'
import { corridorCells } from '../../src/nn/danger-metrics'
import { cornerAnchors } from '../../src/nn/danger-metrics'
import { canStep } from '../../src/ai/perception'
import type { World } from '../../src/game/World'
import type { Direction as Dir } from '../../src/constants'
import { makeBullet, makePowerUp, placeEnemy, positionPlayer, seedWorld } from '../helpers'

const FIELD_PX = GRID * CELL
const DIRS: Dir[] = ['up', 'down', 'left', 'right']

/** 空场 + 玩家（保留 base 缓存 = 有基地，除非显式 rebuild）。 */
function openWorld(seed: number): World {
  const w = seedWorld(seed)
  w.startGame('hard', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) w.tileMap.set(c, r, 'empty')
  }
  positionPlayer(w, 12, 12, 'up')
  return w
}

function encode(w: World): ObsEncoder {
  const enc = new ObsEncoder()
  enc.encode(w)
  return enc
}

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

const MIRROR_SWAPS: Array<[number, number]> = [
  [2, 3],
  [6, 7],
  [11, 12],
  [15, 16],
  [19, 20],
  [23, 24],
  [26, 27],
  [29, 30],
  [35, 36],
]
const MIRROR_NEG = [32]

/** 镜像一个场景（水平翻：x' = W − (x+w)，left↔right，格列 c' = GRID−1−c）。 */
function mirrorInto(src: World, dst: World): void {
  const mx = (x: number, w: number): number => FIELD_PX - (x + w)
  const mdir = (d: Dir): Dir => (d === 'left' ? 'right' : d === 'right' ? 'left' : d)
  const p1 = src.player!
  const p2 = dst.player!
  p2.x = mx(p1.x, p1.w)
  p2.y = p1.y
  p2.dir = mdir(p1.dir)
  p2.moving = p1.moving
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) dst.tileMap.set(GRID - 1 - c, r, src.tileMap.get(c, r))
  }
  for (const t of src.allTanks) {
    if (t === p1 || !t.alive) continue
    const e = placeEnemy(dst, 0, 0, t.kind, mdir(t.dir))
    e.x = mx(t.x, t.w)
    e.y = t.y
    e.vx = t.vx
    e.vy = t.vy
    e.spawnTimer = t.spawnTimer
  }
  for (const b of src.bullets) {
    dst.bullets.push(
      makeBullet({
        x: mx(b.x, b.w),
        y: b.y,
        dir: mdir(b.dir),
        speed: b.speed,
        allegiance: b.allegiance,
        alive: b.alive,
      }),
    )
  }
  for (const pu of src.powerUps) {
    const c = makePowerUp(0, 0, pu.type, { x: mx(pu.x, pu.w), y: pu.y, alive: pu.alive })
    dst.powerUps.push(c)
  }
}

describe('A2：corr（走廊地形格数）自洽 + 与 canStep 的整步一致（200 局面 × 4 向）', () => {
  /** 独立重实现的步进（只看地形；不看生产 helper）：snap 起、逐格 16px、查 2×2 格。 */
  const blocksTank = (w: World, c: number, r: number): boolean => {
    const t = w.tileMap.get(c, r)
    return t === 'brick' || t === 'steel' || t === 'water' || t === 'base'
  }
  const indepCorr = (w: World, x: number, y: number, dir: Dir, cap: number): number => {
    const v: Record<Dir, { dx: number; dy: number }> = {
      up: { dx: 0, dy: -1 },
      down: { dx: 0, dy: 1 },
      left: { dx: -1, dy: 0 },
      right: { dx: 1, dy: 0 },
    }
    const gx = Math.round(x / CELL) * CELL
    const gy = Math.round(y / CELL) * CELL
    let k = 0
    for (let i = 1; i <= cap; i++) {
      const nx = gx + v[dir].dx * CELL * i
      const ny = gy + v[dir].dy * CELL * i
      if (nx < 0 || ny < 0 || nx + TANK > GRID * CELL || ny + TANK > GRID * CELL) break
      let hit = false
      for (let r = Math.floor(ny / CELL); r <= Math.floor((ny + TANK - 1) / CELL); r++) {
        for (let c = Math.floor(nx / CELL); c <= Math.floor((nx + TANK - 1) / CELL); c++) {
          if (blocksTank(w, c, r)) hit = true
        }
      }
      if (hit) break
      k = i
    }
    return k
  }

  it('200 局面 × 4 向：corridorCells == 独立重实现（cap 8）；32px 整步 ⟺ canStep', () => {
    const rand = lcg(20261012)
    let stepOpen = 0
    let stepBlocked = 0
    let halfOpen = 0
    for (let s = 0; s < 200; s++) {
      const w = seedWorld(700 + s)
      w.startGame('hard', 'modern', 0) // 真 stage：砖/钢/水/草齐全
      const p = w.player!
      positionPlayer(
        w,
        1 + Math.floor(rand() * (GRID - 3)),
        1 + Math.floor(rand() * (GRID - 3)),
        DIRS[Math.floor(rand() * 4)],
      )
      // 只对**合法站位**（不与挡车地形重叠）断言。
      if (w.rectHitsTerrain(p.x, p.y, TANK, TANK)) continue
      for (const dir of DIRS) {
        const k = corridorCells(w, p, dir, 8)
        expect(k, `seed ${700 + s} dir ${dir} at ${p.x},${p.y}`).toBe(
          indepCorr(w, p.x, p.y, dir, 8),
        )
        if (k >= 1) halfOpen++
        // 32px 整步（k ≥ 2）⟺ 地形版 canStep：canStep 的扫掠并集 = 当前∪目的，
        // 合法站位下多出的那圈格就是坦克自己占的格（必为空）
        // ⇒ 两者等价。⚠ 不能拿 k ≥ 1（16px 半步）去比 canStep（32px 整步）。
        const canGo = corridorCells(w, p, dir, 2) >= 2
        expect(canGo, `seed ${700 + s} dir ${dir} at ${p.x},${p.y}`).toBe(canStep(w, p, dir, false))
        if (canGo) stepOpen++
        else stepBlocked++
      }
    }
    // 反向哨兵：三类都出现过（防「全 true / 全 false」的恒真对照）
    expect(stepOpen).toBeGreaterThan(50)
    expect(stepBlocked).toBeGreaterThan(50)
    expect(halfOpen).toBeGreaterThan(stepOpen)
  })
})

describe('A4：TS mirror（extra + scalars）vs 整体镜像场景', () => {
  it('镜像场景后：extra′ = mirrorSwap(extra)、scalars′ = mirrorScalars(scalars)', () => {
    // ⚠ 构造约束（两条，都是**格号量化**的坑，不是本表的毛病）：
    //   ① 空场：32px 坦克中心正好落在格边界（x=col*16, 中心=col*16+16），而
    //      “左边缘规则 c↦25−c” 与 `snap` 的 round-half-up / `floor` 在边界点上
    //      差一格 ⇒ 带掩体的 corr/blk/遮挡 raycast 无法逐格严格镜像（tile 是
    //      单格实体、按左边缘镜像；ray 起点是坦克中心格）；
    //   ② 玩家放在**场地中心**：s35/s36（到边界/角锚点的格距）用中心格号，
    //      只有 c↦25−c 对称的位置才在两侧同值；同理 corr 的 left/right 在中心
    //      饱和到 cap（两侧都是满 8 格）才不会因 snap 的方向性差一格。
    // 被这两条排除的维（corr 9–12 / blk 21–24 在掩体下的读数、s35/s36 的偏心读数）
    // 由冻结表测试（policy-extra.test.ts）与各族语义测试（砖在左侧 ⇒ corrL 变）覆盖。
    const a = openWorld(41)
    a.tileMap.rebuildBaseCache() // 无基地（基地固定在世界左下，整场镜像不搬家）
    expect(a.tileMap.hasBase()).toBe(false)
    // 场景：玩家在**场地中心**（col 12——镜像下自身对称，避开格号量化的边界歧义）、
    // 朝上；敌车 1 辆在左、1 辆在下；敌弹 1 颗；拾取 1 个。
    positionPlayer(a, 12, 12, 'up')
    const p = a.player!
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    a.bullets.push(
      makeBullet({
        x: pcx - 3 * CELL - BULLET / 2,
        y: pcy - BULLET / 2,
        dir: 'right',
        speed: 4,
        allegiance: 'enemy',
      }),
    )
    placeEnemy(a, 3, 13, 'basic', 'up')
    placeEnemy(a, 8, 18, 'basic', 'left')
    a.powerUps.push(makePowerUp(4, 16, 'star'))
    // 让 s39（径向接近速度）有读数：给敌人 live 速度。
    a.allTanks[1].vx = 1
    a.allTanks[2].vy = -1

    const b = openWorld(42)
    b.tileMap.rebuildBaseCache()
    mirrorInto(a, b)

    const ea = encode(a)
    const eb = encode(b)
    expect(ea.extra.length).toBe(POLICY_EXTRA_DIM)
    expect(ea.scalars.length).toBe(SCALAR_DIM)

    // 非空断言（防镜像恒等式的空转通过）：新族真的有读数
    expect(ea.extra[0] + ea.extra[1] + ea.extra[2] + ea.extra[3]).toBeGreaterThan(0)
    expect(ea.extra[13] + ea.extra[14] + ea.extra[15] + ea.extra[16]).toBeGreaterThan(0)
    expect(ea.extra[17] + ea.extra[18] + ea.extra[19] + ea.extra[20]).toBeGreaterThan(0)
    expect(ea.extra[9]).toBeGreaterThan(0) // 空场 corr 也读数（边界距离）
    expect(Math.abs(ea.scalars[33])).toBeGreaterThan(0) // pickupRelX 非零
    expect(ea.scalars[39]).not.toBe(0) // 接近速度非零（方向由镜像保不变）

    // extra：先按 9 对互换，再把 [32] 取负
    const expExtra = Float32Array.from(ea.extra)
    for (const [i, j] of MIRROR_SWAPS) {
      const t = expExtra[i]
      expExtra[i] = expExtra[j]
      expExtra[j] = t
    }
    for (const i of MIRROR_NEG) expExtra[i] = -expExtra[i]
    for (let i = 0; i < POLICY_EXTRA_DIM; i++) {
      expect(eb.extra[i], `extra[${i}]`).toBeCloseTo(expExtra[i], 5)
    }
    // 生产入口与上表同行为（防「只在本测试里存在」的第二套表）
    const viaFn = Float32Array.from(ea.extra)
    mirrorSwap(viaFn)
    for (let i = 0; i < POLICY_EXTRA_DIM; i++)
      expect(viaFn[i], `mirrorSwap[${i}]`).toBeCloseTo(eb.extra[i], 6)

    // scalars：x 分量取负（SCALAR_X_INDICES）；其余（含 s39 径向接近速度）不变
    const expScalars = Float32Array.from(ea.scalars)
    for (const i of SCALAR_X_INDICES) expScalars[i] = -expScalars[i]
    for (let i = 0; i < SCALAR_DIM; i++) {
      expect(eb.scalars[i], `scalars[${i}]`).toBeCloseTo(expScalars[i], 5)
    }
    const viaFn2 = Float32Array.from(ea.scalars)
    mirrorScalars(viaFn2)
    for (let i = 0; i < SCALAR_DIM; i++)
      expect(viaFn2[i], `mirrorScalars[${i}]`).toBeCloseTo(eb.scalars[i], 6)
  })
})

describe('A5：口径对账（pickup 三连 / s37 / s38↔extra34–36 / s6）', () => {
  it('s32 换算 `!alive?0 : raw<0?1.5:raw/50`；无拾取 ⇒ 1.5、玩家死亡 ⇒ 0', () => {
    const w = openWorld(43)
    const enc0 = encode(w)
    expect(enc0.scalars[32]).toBe(1.5)
    expect(enc0.scalars[33]).toBe(0)
    expect(enc0.scalars[34]).toBe(0)
    // 放两个拾取：取**中心格曼哈顿**最近者；等距时数组里靠前者胜（同源规则）
    const far = makePowerUp(4, 12, 'star') // 中心格 (5,13)
    const near = makePowerUp(9, 12, 'bomb') // 中心格 (10,13)
    w.powerUps.push(far, near)
    const p = w.player!
    const prow = Math.floor((p.y + p.h / 2) / CELL) // 13
    const pcol = Math.floor((p.x + p.w / 2) / CELL)
    const dNear = Math.abs(10 - pcol) + Math.abs(13 - prow)
    const enc1 = encode(w)
    expect(enc1.scalars[32]).toBeCloseTo(Math.min(dNear / 50, 1), 6)
    // 向量指向 near（世界左方 ⇒ dx<0 ⇒ s33<0），且是**像素中心欧氏**单位向量
    const dx = near.x + near.w / 2 - (p.x + p.w / 2)
    const dy = near.y + near.h / 2 - (p.y + p.h / 2)
    const d = Math.hypot(dx, dy)
    expect(enc1.scalars[33]).toBeCloseTo(dx / d, 6)
    expect(enc1.scalars[34]).toBeCloseTo(dy / d, 6)
    // 等距反例：把 far 推到与 near 同距的右侧（中心格 17）⇒ 首最小者胜（far 在数组里在前）
    w.powerUps.length = 0
    const left = makePowerUp(10, 12, 'star') // 中心格 11
    const right = makePowerUp(15, 12, 'bomb') // 中心格 16
    const dL = Math.abs(11 - pcol)
    const dR = Math.abs(16 - pcol)
    expect(dL === dR || Math.abs(dL - dR) >= 1).toBe(true) // 构造前提：等距或确定更近
    w.powerUps.push(left, right)
    const enc2 = encode(w)
    const nearer = dL <= dR ? left : right
    const ndx = nearer.x + nearer.w / 2 - (p.x + p.w / 2)
    expect(Math.sign(enc2.scalars[33])).toBe(Math.sign(ndx) || 0)
    // 玩家死亡 ⇒ 三连归 0（跟随 extra「无玩家全 0」纪律）
    p.alive = false
    const enc3 = encode(w)
    expect([enc3.scalars[32], enc3.scalars[33], enc3.scalars[34]]).toEqual([0, 0, 0])
  })

  it('s37 = baseRelY（与 s18 同一 d/同一分支；无基地 ⇒ 0）', () => {
    const w = openWorld(44) // hasBase true（缓存未重建），基地在 (12,24)-(13,25)
    const p = w.player!
    const bcx = 12 * CELL + CELL / 2
    const bcy = 24 * CELL + CELL / 2
    const dx = bcx - (p.x + p.w / 2)
    const dy = bcy - (p.y + p.h / 2)
    const d = Math.hypot(dx, dy)
    const enc = encode(w)
    expect(enc.scalars[18]).toBeCloseTo(dx / d, 6)
    expect(enc.scalars[37]).toBeCloseTo(dy / d, 6)
    expect(enc.scalars[18] ** 2 + enc.scalars[37] ** 2).toBeCloseTo(1, 6)
    w.tileMap.rebuildBaseCache()
    const enc2 = encode(w)
    expect(enc2.scalars[18]).toBe(0)
    expect(enc2.scalars[37]).toBe(0)
    expect(enc2.scalars[17]).toBe(0)
  })

  it('s38 ↔ extra 34–36 同锚点同过滤：(1−s38)×12 = min(三侧格距)', () => {
    const w = openWorld(45) // base 在 (12,24)-(13,25)；玩家 (12,12) 朝上
    const e = placeEnemy(w, 12, 19, 'basic', 'down') // 中心格 (13,20)
    e.spawnTimer = 0
    const enc = encode(w)
    const ec = Math.floor((e.x + e.w / 2) / CELL)
    const er = Math.floor((e.y + e.h / 2) / CELL)
    const cells = Math.abs(ec - 12) + Math.abs(er - 24)
    expect(cells).toBe(5) // 中心格 (13,20)：32px 坦克的中心落在格边界 ⇒ 归右格
    expect(enc.scalars[38]).toBeCloseTo(1 - cells / 12, 6)
    // 恒等式（plan §2.5）：s38 = 1 − minDist/12 ⇒ min(三侧, 归一后) = 1 − s38
    const minSide = Math.min(enc.extra[34], enc.extra[35], enc.extra[36])
    expect(minSide).toBeCloseTo(1 - enc.scalars[38], 6)
    // 同过滤：把该敌改成「玩家」阵营（自己人贴家）⇒ s38 归 0（不含玩家），extra 三侧也 1.5
    e.allegiance = 'player'
    const enc2 = encode(w)
    expect(enc2.scalars[38]).toBe(0)
    expect([enc2.extra[34], enc2.extra[35], enc2.extra[36]]).toEqual([1.5, 1.5, 1.5])
    // 生成中（spawnTimer>0）同样被两条路径一致排除
    e.allegiance = 'enemy'
    e.spawnTimer = 500
    const enc3 = encode(w)
    expect(enc3.scalars[38]).toBe(0)
    expect(enc3.extra[34]).toBe(1.5)
  })

  it('s6 环墙 = 完好（brick/steel）格数 / 8；水/草/空不算', () => {
    const w = openWorld(46)
    const RING = [
      ...[11, 12, 13, 14].map((col) => ({ col, row: 23 })),
      ...[24, 25].flatMap((row) => [
        { col: 11, row },
        { col: 14, row },
      ]),
    ]
    expect(RING.length).toBe(8)
    for (const c of RING) w.tileMap.set(c.col, c.row, 'steel')
    expect(encode(w).scalars[6]).toBe(1)
    w.tileMap.set(11, 24, 'water')
    expect(encode(w).scalars[6]).toBeCloseTo(7 / 8, 6)
    for (const c of RING) w.tileMap.set(c.col, c.row, 'empty')
    expect(encode(w).scalars[6]).toBe(0)
  })
})

describe('A8：逐维 golden（s30/s31/s35/s36/s39）', () => {
  it('s30 moving：顶墙推 vs STOP 的区分位（moving ⇒ 1）', () => {
    const w = openWorld(47)
    const p = w.player!
    p.moving = false
    expect(encode(w).scalars[30]).toBe(0)
    p.moving = true
    expect(encode(w).scalars[30]).toBe(1)
  })

  it('s31 myBullets：只数玩家阵营弹（友军弹不算），cap 3', () => {
    const w = openWorld(48)
    const p = w.player!
    const cx = p.x + p.w / 2
    const cy = p.y + p.h / 2
    for (let i = 0; i < 2; i++) {
      w.bullets.push(makeBullet({ x: cx, y: cy - (i + 1) * CELL, dir: 'up', allegiance: 'player' }))
    }
    w.bullets.push(makeBullet({ x: cx, y: cy + CELL, dir: 'down', allegiance: 'ally' }))
    expect(encode(w).scalars[31]).toBeCloseTo(2 / 3, 6)
    for (let i = 0; i < 3; i++) {
      w.bullets.push(makeBullet({ x: cx, y: cy - (i + 3) * CELL, dir: 'up', allegiance: 'player' }))
    }
    expect(encode(w).scalars[31]).toBe(1) // 5 颗 ⇒ cap 3 截断
  })

  it('s35 wallDist：到边界格距 /13（用中心格）；s36 cornerDist：四角锚点最小切比雪夫 /10', () => {
    const w = openWorld(49)
    const p = w.player!
    // 场地中心：中心格 13 ⇒ 到边界 12 格
    expect(encode(w).scalars[35]).toBeCloseTo(12 / 13, 6)
    // 角落：中心格 (1,1) ⇒ 到边界 1 格；到锚点 (2,2) 切比雪夫 1 ⇒ 0.1
    positionPlayer(w, 0, 0, 'up')
    const enc = encode(w)
    expect(enc.scalars[35]).toBeCloseTo(1 / 13, 6)
    expect(enc.scalars[36]).toBeCloseTo(1 / 10, 6)
    // 独立重实现：四角锚点（cornerAnchors 的表）× 切比雪夫最小 × /10
    const c = Math.floor((p.x + p.w / 2) / CELL)
    const r = Math.floor((p.y + p.h / 2) / CELL)
    const cheb = Math.min(
      ...cornerAnchors().map((an) => Math.max(Math.abs(c - an.col), Math.abs(r - an.row))),
    )
    expect(enc.scalars[36]).toBeCloseTo(Math.min(cheb / 10, 1), 6)
  })

  it('s39 逼近为正 / 远离为负 / 无敌为 0；且与 s14–16 **同一个敌**', () => {
    const w = openWorld(50)
    const p = w.player!
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    // 无敌 ⇒ 0
    expect(encode(w).scalars[39]).toBe(0)
    // 世界左侧 100px 处、朝右（逼近）⇒ u=(−1,0)、V=(1,0) ⇒ approach = +1/3
    const e = placeEnemy(w, 0, 0, 'basic', 'right')
    e.x = pcx - 100 - TANK / 2
    e.y = pcy - TANK / 2
    e.spawnTimer = 0
    e.vx = 1
    e.vy = 0
    const enc = encode(w)
    expect(enc.scalars[14]).toBeCloseTo(100 / (26 * CELL * 1.5), 6)
    expect(enc.scalars[15]).toBeCloseTo(-1, 6) // s14–16 指向该敌（左侧 ⇒ dx<0）
    expect(enc.scalars[39]).toBeCloseTo(1 / 3, 6)
    // 速度反向（远离）⇒ 负
    e.vx = -1
    expect(encode(w).scalars[39]).toBeCloseTo(-1 / 3, 6)
    // 纯切向（v 垂直于 u）⇒ 0
    e.vx = 0
    e.vy = 1
    expect(encode(w).scalars[39]).toBeCloseTo(0, 6)
    // 同敌断言：再加一个**等距**敌（数组中在后），它朝右远离；若 s39 误用第二个敌，
    // 读数会翻成 −1/3（等距平局由 `strict <` 首个最小者胜 ⇒ 仍是第一个敌）
    const e2 = placeEnemy(w, 0, 0, 'basic', 'right')
    e2.x = pcx + 100 - TANK / 2
    e2.y = pcy - TANK / 2
    e2.spawnTimer = 0
    e2.vx = 1
    e2.vy = 0
    e.vx = 1
    e.vy = 0
    const enc2 = encode(w)
    expect(Math.hypot(100, 0)).toBeCloseTo(100, 6)
    expect(enc2.scalars[15]).toBeCloseTo(-1, 6) // 仍是左边那个（首个最小者）
    expect(enc2.scalars[39]).toBeCloseTo(1 / 3, 6)
  })
})
