/**
 * policy-extra.ts — `POLICY_EXTRA`：37 维方向直方图（v1 = 9 维 → v2 = 37 维，obs-v5）。
 *
 * 只喂给走位/开火两个策略头（**不进 `scalars`**）。obs-v5 起 **value 头也吃同一份 `pf`**
 * （含 extra）——「critic 输入集不动」的旧否决已被
 * `DECISIONS.md §2026-10-12-goalnn-obs-v5-critic-superset` 推翻（plan §附录 B）。
 *
 * 布局（**0–8 是 v1 原样，序号与语义一字不动**；9–36 为 v2 追加；plan §2.2 冻结）：
 *   0..3   前/后/左/右 威胁计数（19px 命中带 + 判墙体遮挡）—— `min(n,4)/4`
 *   4..7   前/后/左/右 「第一个能打到的敌人」距离 —— `clamp01(px/416)`；打不到 ⇒ 1.5
 *   8      包夹度 = `min(左,右,4)/4`
 *   9..12  各向连续可走**地形**格数 /8（`corridorCells`；坦克不算）
 *   13..16 各向最近逼近**无遮挡**敌弹 TTI /60；无弹 ⇒ 1.5
 *   17..20 各向同轴（19px）+ 无遮挡敌数（**不要求瞄准**）`min(n,4)/4`
 *   21..24 各向首个**阻弹**地形距离 px/416；无阻弹或出界 ⇒ 1.5（坦克不挡）
 *   25..27 老家上/左/右逼近敌数 `min(n,4)/4`（**世界坐标**，转身不变）
 *   28..30 老家各侧环墙完好比例（上 /4、左/右 /2）
 *   31     最近威胁敌到基地 TTI /300；无 ⇒ 1.5
 *   32..33 该威胁敌相对**玩家**的单位向量（X 在 mirrorX 下取负）
 *   34..36 各侧最近敌车曼哈顿格距 /12；无敌 ⇒ 1.5（与 s38 同一份敌车口径）
 *
 * 方位语义 = **玩家相对**（前 = 炮口朝向；后 = 反向；左/右 = 逆/顺 90°）。
 * 25–36 是**世界坐标**族（老家不会转身）：威胁源的「来自哪边」按世界方位分类后再映射到
 * 相对系；镜像由 swap/取负表处理。
 *
 * mirrorX：`POLICY_EXTRA_MIRROR_SWAPS` 九对互换 + `POLICY_EXTRA_MIRROR_NEG` 取负。
 * TS 侧入口 = `mirrorSwap`（本文件）；python 侧 = `worker/data/mirror.py::mirror_x`
 * （共用同一对表，双端指纹与 A4/A4b 对账钉住）。
 *
 * 热路径纪律（AGENTS §14）：零分配（模块级复用缓冲 + 纯局部标量）、
 * `world.allTanks` 每 encode **取一次**传引用（玩家族与基地族共用）、不读 rng、不写 World。
 *
 * 谓词复用（plan §2.5 P3）：`bulletLaneDist` / `bulletInFrontDist`（utils/helpers）与
 * `laneOccluded` / `alignedScan` / `corridorCells` / `firstBulletBlockerPx` / `baseSideCode`
 * / `sampleBasePressure`（danger-metrics）——与威胁/暴露指标同族口径，不存在
 * 「训练侧说危险、eval 侧说不危险」的分叉。
 */

import { BASE_POS, CELL, GRID, type Direction } from '../constants'
import type { World } from '../game/World'
import { BULLET_LANE_MISS, bulletInFrontDist, bulletLaneDist } from '../utils/helpers'
import { RING_CELLS } from '../ai/god/ThreatBudget'
import {
  BASE_CENTER_X,
  BASE_CENTER_Y,
  BASE_PRESSURE_RADIUS,
  SIDE_DOWN,
  SIDE_LEFT,
  SIDE_RIGHT,
  SIDE_UP,
  THREAT_ALIGN_BAND_PX,
  alignedScan,
  baseSideCode,
  corridorCells,
  firstBulletBlockerPx,
  laneOccluded,
} from './danger-metrics'

/** `POLICY_EXTRA` 维度（布局契约，见文件头）。 */
export const POLICY_EXTRA_DIM = 57
/** 计数维上限（归一化分母；威胁含敌弹，单向可能 > 4，不 clamp 会越界）。 */
export const POLICY_EXTRA_COUNT_CAP = 4
/** 「该相对方向打不到敌人 / 没有目标」的哨兵值（> 1，与 clamp01 后的合法域可区分）。 */
export const POLICY_EXTRA_HIT_NONE = 1.5
/** 命中距离归一化分母：场地轴向跨度（26 格 × 16px = 416px；轴向距离天然有界于它）。 */
export const POLICY_EXTRA_HIT_DIST_NORM = GRID * CELL
/** `corr*` 的归一化分母 = 连续可走格数上限（满走读 1.0）。 */
export const POLICY_EXTRA_CORR_CAP = 8
/** `tti*` 归一化分母（tick；60 tick = 1s）。 */
export const POLICY_EXTRA_TTI_NORM = 60
/** `baseThreatTTI` 归一化分母（tick；300 tick = 5s）。 */
export const POLICY_EXTRA_BASE_TTI_NORM = 300
/** `baseEnemyDist*` 归一化分母（格）＝ s38 的同一半径 ⇒ `min(三侧)=(1−s38)×12` 恒等式。 */
export const POLICY_EXTRA_BASE_DIST_NORM = BASE_PRESSURE_RADIUS
/** 环墙各侧总格数（`RING_CELLS` = 上 4 + 左 2 + 右 2 = 8，与 s6 的分母同源）。 */
export const POLICY_EXTRA_RING_UP_TOTAL = 4
export const POLICY_EXTRA_RING_SIDE_TOTAL = 2

/** 维名（**逐字镜像** nn-training/common/schema.py::POLICY_EXTRA_LAYOUT；进指纹）。 */
export const POLICY_EXTRA_NAMES = [
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
  // ---- v6 追加（plan §4-B2/B3/B4；0–36 序号与语义不动）----
  'threatTankFront', // 37
  'threatTankBack',
  'threatTankLeft',
  'threatTankRight', // 40
  'prevActionUp', // 41
  'prevActionDown',
  'prevActionLeft',
  'prevActionRight', // 44
  'lastHitUp', // 45
  'lastHitDown',
  'lastHitLeft',
  'lastHitRight', // 48
  'enemyFacingUp', // 49
  'enemyFacingDown',
  'enemyFacingLeft',
  'enemyFacingRight', // 52
  'leadHitFront', // 53
  'leadHitBack',
  'leadHitLeft',
  'leadHitRight', // 56
] as const
if (POLICY_EXTRA_NAMES.length !== POLICY_EXTRA_DIM) {
  throw new Error(`POLICY_EXTRA_NAMES must have ${POLICY_EXTRA_DIM} entries`)
}

/**
 * mirrorX 的**互换对**（不是取负）：左右语义维两两交换，前/后与包夹度不变。
 *
 * 为什么叫 swap 而不是 plan §4-S0b 所称的 `EXTRA_X_INDICES`：这些维是计数/距离，镜像语义
 * 是「左右互换」而非标量族的「x 分量取负」。表须与
 * `nn-training/common/schema.py::EXTRA_MIRROR_SWAPS` 逐字一致（双端指纹钉住）。
 *
 * 末段的三对（25–36 族）是**世界坐标**维：镜像把世界整体翻转 ⇒ 老家的 L/R 互换、U 自守。
 */
export const POLICY_EXTRA_MIRROR_SWAPS: ReadonlyArray<readonly [number, number]> = [
  [2, 3], // 左/右威胁计数
  [6, 7], // 左/右命中距离
  [11, 12], // corr 左/右
  [15, 16], // tti 左/右
  [19, 20], // laneE 左/右
  [23, 24], // blk 左/右
  [26, 27], // baseThreat 左/右
  [29, 30], // baseRing 左/右
  [35, 36], // baseEnemyDist 左/右
  [39, 40], // v6 threatTank 左/右
  [43, 44], // v6 prevAction 左/右
  [47, 48], // v6 lastHitDir 左/右
  [51, 52], // v6 enemyFacing 左/右
  [55, 56], // v6 leadHit 左/右
]

/**
 * mirrorX 的**取负**维（相对向量的 x 分量）：换不出「符号」这件事，故与 swap 表分家
 * （plan §2.4：指纹末段冻结为 `dim|names|swaps|neg` 四字段）。
 * 对标量族是 `SCALAR_X_INDICES=[15,18,29,33]`（obs-encoder.ts），两侧同族不同表。
 */
export const POLICY_EXTRA_MIRROR_NEG: readonly number[] = [32]

/** 反向朝向。 */
const OPPOSITE: Record<Direction, Direction> = {
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
}
/** 朝向的**左侧**世界方位（屏幕坐标 y 向下：右→上→左→下 的逆时针序）。 */
const LEFT_OF: Record<Direction, Direction> = {
  up: 'left',
  left: 'down',
  down: 'right',
  right: 'up',
}

/** 相对方位索引：0 前 / 1 后 / 2 左 / 3 右（`side` 为源的世界方位）。 */
function relIndex(side: Direction, facing: Direction): 0 | 1 | 2 | 3 {
  if (side === facing) return 0
  if (side === OPPOSITE[facing]) return 1
  if (side === LEFT_OF[facing]) return 2
  return 3
}

/** 世界方位 → 世界方位码（与 danger-metrics 的侧码同序）。 */
const WORLD_SIDE_CODE: Record<Direction, number> = {
  up: SIDE_UP,
  down: SIDE_DOWN,
  left: SIDE_LEFT,
  right: SIDE_RIGHT,
}

// ---- 模块级复用缓冲（§14.1：encode 热路径零分配）----
/** B4：威胁源拆族——v6 起 0–3 只含**敌弹**，37–40 只含**敌车炮口**。 */
const _bulletCounts = [0, 0, 0, 0]
const _tankCounts = [0, 0, 0, 0]
/** 4 向命中距离（px；Infinity = 尚未找到）。 */
const _dists = [Infinity, Infinity, Infinity, Infinity]
/** 4 向最近逼近弹 TTI（tick；Infinity = 无弹）。 */
const _tti = [Infinity, Infinity, Infinity, Infinity]
/** 4 向同轴敌数（不要求瞄准）。 */
const _lane = [0, 0, 0, 0]
/** 4 个相对朝向对应的世界朝向（前/后/左/右）。 */
const _dirs: Direction[] = ['up', 'up', 'up', 'up']
/** 老家三侧最近敌车格距（Infinity = 该侧无敌人）。 */
const _near = [Infinity, Infinity, Infinity, Infinity]
/** `alignedScan` 输出缓冲（[total, weight, up, down, left, right]）。 */
const _scan = new Float64Array(6)

/** 世界方位 → one-hot 下标（up/down/left/right ≡ 0..3）。 */
const DIR_ORDER: readonly Direction[] = ['up', 'down', 'left', 'right']

/**
 * B3 `leadHitD[d]`：沿世界方位 `dir` 开火，**到达时**能否命中（计划§4-B3）。
 *
 * 条件：轴向距 ≤ 13 格、线性外推 t*=轴向距/弹速 后的横向偏差 < 19px、
 * 路径无阻弹地形、排除生成中的敌。敌人转向不可预知 ⇒ 用 live 速度外推
 * （停着的车横向不动 = 恒速假设的退化情形）。
 *
 * 零分配、只读 World。
 */
function leadHitForDir(world: World, dir: Direction, pcx: number, pcy: number): number {
  const p = world.player
  if (!p) return 0
  const vertical = dir === 'up' || dir === 'down'
  const sign = dir === 'down' || dir === 'right' ? 1 : -1
  const bulletSpeed = p.bulletSpeed > 0 ? p.bulletSpeed : 4
  const maxAxial = 13 * CELL
  const shooterCol = Math.floor(pcx / CELL)
  const shooterRow = Math.floor(pcy / CELL)
  const tileMap = world.tileMap
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const axial = (vertical ? ty - pcy : tx - pcx) * sign
    if (axial <= 0 || axial > maxAxial) continue
    const lateral = vertical ? tx - pcx : ty - pcy
    const vLat = vertical ? t.vx : t.vy
    const tStar = axial / bulletSpeed
    if (Math.abs(lateral + vLat * tStar) >= THREAT_ALIGN_BAND_PX) continue
    const colLow = Math.floor(t.x / CELL)
    const colHigh = Math.floor((t.x + t.w - 1) / CELL)
    const rowLow = Math.floor(t.y / CELL)
    const rowHigh = Math.floor((t.y + t.h - 1) / CELL)
    const occluded = laneOccluded(
      tileMap,
      shooterCol,
      shooterRow,
      vertical,
      vertical ? rowLow : colLow,
      vertical ? rowHigh : colHigh,
    )
    if (!occluded) return 1
  }
  return 0
}

/** 距离 → [0,1]；无目标 ⇒ 1.5 哨兵。 */
function hitNorm(dist: number): number {
  if (!isFinite(dist)) return POLICY_EXTRA_HIT_NONE
  const v = dist / POLICY_EXTRA_HIT_DIST_NORM
  return v > 1 ? 1 : v
}

/**
 * 计算 `POLICY_EXTRA`（写入 `out`，长度 37；重复调用复用缓冲，零分配）。
 *
 * 无玩家 / 玩家死亡 ⇒ **全 0**（与 obs/scalars 的「无玩家即 0」同语义；1.5 哨兵只在
 * 「有玩家、该方向确实没有目标」时出现，两者不混淆）。
 *
 * 无基地 ⇒ 基地族（25–36）按类走各自的无目标约定：计数/环墙 = 0、距离/TTI = 1.5、
 * 单位向量 = (0,0)。**这 12 维在无基地场上是恒常量**（计划 §1.2 已知：G0 只证明空旷场，
 * 基地语义留专场重验）。
 */
export function computePolicyExtra(world: World, out: Float32Array): void {
  out.fill(0)
  const p = world.player
  if (!p || !p.alive) return

  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const facing = p.dir
  const tileMap = world.tileMap
  // 玩家身体的像素精确格跨度（遮挡判据；与 danger-metrics 同规）。
  const pColLow = Math.floor(p.x / CELL)
  const pColHigh = Math.floor((p.x + p.w - 1) / CELL)
  const pRowLow = Math.floor(p.y / CELL)
  const pRowHigh = Math.floor((p.y + p.h - 1) / CELL)

  for (let i = 0; i < 4; i++) {
    _bulletCounts[i] = 0
    _tankCounts[i] = 0
    _dists[i] = Infinity
    _tti[i] = Infinity
    _lane[i] = 0
    _near[i] = Infinity
  }
  _dirs[0] = facing
  _dirs[1] = OPPOSITE[facing]
  _dirs[2] = LEFT_OF[facing]
  _dirs[3] = OPPOSITE[LEFT_OF[facing]]

  // ---- ①②③：威胁计数（0..3）、4 向命中距离与 4 向最近弹 TTI 共用两趟扫描 ----
  const bullets = world.bullets
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    // 同轴（19px）+ 逼近；返回值 = 轴向距离（px）——正好是 TTI 的分子。
    const along = bulletLaneDist(b.dir, bx, by, pcx, pcy, THREAT_ALIGN_BAND_PX)
    if (along === BULLET_LANE_MISS) continue
    const vertical = b.dir === 'up' || b.dir === 'down'
    const occluded = laneOccluded(
      tileMap,
      Math.floor(bx / CELL),
      Math.floor(by / CELL),
      vertical,
      vertical ? pRowLow : pColLow,
      vertical ? pRowHigh : pColHigh,
    )
    if (occluded) continue
    // 源的世界方位：竖直弹在上/下方，水平弹在左/右侧。
    const side: Direction = vertical ? (by < pcy ? 'up' : 'down') : bx < pcx ? 'left' : 'right'
    const r = relIndex(side, facing)
    _bulletCounts[r]++
    // TTI（tick）= 轴向距离 / 弹速（px/tick）。速度非正（域外）按 1 计 ⇒ 读数饱和为 1.0。
    const tti = along / (b.speed > 0 ? b.speed : 1)
    if (tti < _tti[r]) _tti[r] = tti
  }

  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2

    // ① 「在瞄我」：炮口朝向玩家（bulletInFrontDist 的调用约定：玩家是被问的点）。
    if (bulletInFrontDist(t.dir, pcx, pcy, tx, ty, THREAT_ALIGN_BAND_PX) !== BULLET_LANE_MISS) {
      const vertical = t.dir === 'up' || t.dir === 'down'
      const occluded = laneOccluded(
        tileMap,
        Math.floor(tx / CELL),
        Math.floor(ty / CELL),
        vertical,
        vertical ? pRowLow : pColLow,
        vertical ? pRowHigh : pColHigh,
      )
      if (!occluded) {
        const side: Direction = vertical ? (ty < pcy ? 'up' : 'down') : tx < pcx ? 'left' : 'right'
        _tankCounts[relIndex(side, facing)]++
      }
    }

    // ② 「能打到」：同一趟坦克扫描里对 4 个相对朝向各查一次（射线复用）。
    //    r: 0 前 / 1 后 / 2 左 / 3 右 —— 世界朝向由 facing 派生（_dirs）。
    for (let r = 0; r < 4; r++) {
      const worldDir = _dirs[r]
      const vertical = worldDir === 'up' || worldDir === 'down'
      const aligned = vertical
        ? Math.abs(tx - pcx) < THREAT_ALIGN_BAND_PX
        : Math.abs(ty - pcy) < THREAT_ALIGN_BAND_PX
      if (!aligned) continue
      const inFront =
        (worldDir === 'down' && ty > pcy) ||
        (worldDir === 'up' && ty < pcy) ||
        (worldDir === 'right' && tx > pcx) ||
        (worldDir === 'left' && tx < pcx)
      if (!inFront) continue
      const occluded = laneOccluded(
        tileMap,
        Math.floor(tx / CELL),
        Math.floor(ty / CELL),
        vertical,
        vertical ? pRowLow : pColLow,
        vertical ? pRowHigh : pColHigh,
      )
      if (occluded) continue
      const dist = vertical ? Math.abs(ty - pcy) : Math.abs(tx - pcx)
      if (dist < _dists[r]) _dists[r] = dist
    }
  }

  // ---- ④ 同轴敌数（**不要求瞄准**）：alignedScan 的世界方位桶 → 玩家相对桶 ----
  // 与 alignedEnemyCount 同一份谓词（Σ四桶 ≡ 该扫描的总数）。
  alignedScan(world, pcx, pcy, pColLow, pColHigh, pRowLow, pRowHigh, false, _scan, tanks)
  _lane[relIndex('up', facing)] += _scan[2]
  _lane[relIndex('down', facing)] += _scan[3]
  _lane[relIndex('left', facing)] += _scan[4]
  _lane[relIndex('right', facing)] += _scan[5]

  // ---- ⑤⑥ 走廊（corr）与首阻弹距离（blk）：4 个相对朝向各一条射线 ----
  for (let r = 0; r < 4; r++) {
    const k = corridorCells(world, p, _dirs[r], POLICY_EXTRA_CORR_CAP)
    out[9 + r] = Math.min(k, POLICY_EXTRA_CORR_CAP) / POLICY_EXTRA_CORR_CAP
    const bpx = firstBulletBlockerPx(world, pcx, pcy, _dirs[r])
    out[21 + r] = bpx < 0 ? POLICY_EXTRA_HIT_NONE : Math.min(bpx / POLICY_EXTRA_HIT_DIST_NORM, 1)
  }

  // ---- ⑦⑧⑨⑩⑪ 基地族（25–36）：对齐桶 + 环墙 + 指定威胁敌 + 三侧最近格距 ----
  if (tileMap.hasBase()) {
    alignedScan(
      world,
      BASE_CENTER_X,
      BASE_CENTER_Y,
      BASE_POS.col,
      BASE_POS.col + 1,
      BASE_POS.row,
      BASE_POS.row + 1,
      false,
      _scan,
      tanks,
    )
    const cap = POLICY_EXTRA_COUNT_CAP
    out[25] = Math.min(_scan[2], cap) / cap // 上（D 向被网格边界封死）
    out[26] = Math.min(_scan[4], cap) / cap // 左
    out[27] = Math.min(_scan[5], cap) / cap // 右

    // 环墙完好（与 s6 同口径：brick/steel 算完好；RING_CELLS = 上 4 + 左 2 + 右 2）。
    let up = 0
    let left = 0
    let right = 0
    for (let i = 0; i < RING_CELLS.length; i++) {
      const c = RING_CELLS[i]
      const tile = tileMap.get(c.col, c.row)
      const intact = tile === 'brick' || tile === 'steel' ? 1 : 0
      if (c.row < BASE_POS.row) up += intact
      else if (c.col < BASE_POS.col) left += intact
      else right += intact
    }
    out[28] = up / POLICY_EXTRA_RING_UP_TOTAL
    out[29] = left / POLICY_EXTRA_RING_SIDE_TOTAL
    out[30] = right / POLICY_EXTRA_RING_SIDE_TOTAL

    // 一趟扫描出：指定威胁敌（距基地最近的敌车；`strict <` 首个最小者胜）＋三侧最近格距。
    let bestCells = -1
    let bestX = 0
    let bestY = 0
    let bestSpeed = 1
    for (let i = 0; i < tanks.length; i++) {
      const t = tanks[i]
      if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
      const col = Math.floor((t.x + t.w / 2) / CELL)
      const row = Math.floor((t.y + t.h / 2) / CELL)
      const cells = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
      const side = baseSideCode(col, row)
      if (side >= 0 && cells < _near[side]) _near[side] = cells
      if (bestCells < 0 || cells < bestCells) {
        bestCells = cells
        bestX = t.x + t.w / 2
        bestY = t.y + t.h / 2
        bestSpeed = t.speed
      }
    }

    // 31–33：到基地的 TTI + 该敌相对玩家的单位向量。
    if (bestCells >= 0) {
      const speed = bestSpeed > 0 ? bestSpeed : 1
      out[31] = Math.min((bestCells * CELL) / speed / POLICY_EXTRA_BASE_TTI_NORM, 1)
      const dx = bestX - pcx
      const dy = bestY - pcy
      const d = Math.hypot(dx, dy)
      if (d > 0) {
        out[32] = dx / d
        out[33] = dy / d
      }
    } else {
      out[31] = POLICY_EXTRA_HIT_NONE
    }

    // 34–36：上/左/右 各侧最近敌车格距 /12；该侧无敌 ⇒ 1.5。
    out[34] = distNorm(_near[WORLD_SIDE_CODE.up])
    out[35] = distNorm(_near[WORLD_SIDE_CODE.left])
    out[36] = distNorm(_near[WORLD_SIDE_CODE.right])
  }

  // ---- v6：最近敌炮口朝向（B2）与提前量可命中（B3）----
  let nearD = Infinity
  let nearDir: Direction = 'up'
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    const d = dx * dx + dy * dy
    if (d < nearD) {
      nearD = d
      nearDir = t.dir
    }
  }
  if (nearD < Infinity) out[49 + DIR_ORDER.indexOf(nearDir)] = 1

  // prevAction / lastHitDir 的 world-方向 one-hot（镜像由 swap 表处理）。
  if (p.lastActionDir) out[41 + DIR_ORDER.indexOf(p.lastActionDir)] = 1
  if (p.lastHitDir) out[45 + DIR_ORDER.indexOf(p.lastHitDir)] = 1

  for (let r = 0; r < 4; r++) out[53 + r] = leadHitForDir(world, _dirs[r], pcx, pcy)

  // ---- 写缓冲（0–8 与 v1 逐位同式；只追加不改写） ----
  const cap = POLICY_EXTRA_COUNT_CAP
  out[0] = Math.min(_bulletCounts[0], cap) / cap
  out[1] = Math.min(_bulletCounts[1], cap) / cap
  out[2] = Math.min(_bulletCounts[2], cap) / cap
  out[3] = Math.min(_bulletCounts[3], cap) / cap
  out[4] = hitNorm(_dists[0])
  out[5] = hitNorm(_dists[1])
  out[6] = hitNorm(_dists[2])
  out[7] = hitNorm(_dists[3])
  for (let r = 0; r < 4; r++) {
    out[37 + r] = Math.min(_tankCounts[r], cap) / cap
    out[13 + r] = isFinite(_tti[r])
      ? Math.min(_tti[r] / POLICY_EXTRA_TTI_NORM, 1)
      : POLICY_EXTRA_HIT_NONE
    out[17 + r] = Math.min(_lane[r], cap) / cap
  }
  // 包夹度 = 左右**两族合计**（拆族不改变这个粗读数的语义）。
  const leftAll = _bulletCounts[2] + _tankCounts[2]
  const rightAll = _bulletCounts[3] + _tankCounts[3]
  out[8] = Math.min(leftAll, rightAll, cap) / cap

  // ---- B4 窗口：护盾期威胁族/包夹度全部置零（真无敌，物理上无后果）。
  // 冰冻期**不**置零：在飞的敌弹照飞，并且解冻瞬间的危险是真实信号（plan §4-B4）。
  if ((p.shieldTimer ?? 0) > 0) {
    for (let r = 0; r < 4; r++) {
      out[r] = 0
      out[13 + r] = 0
      out[37 + r] = 0
    }
    out[8] = 0
  }
}

/** 基地侧最近格距 → [0,1]；该侧无敌 ⇒ 1.5 哨兵。 */
function distNorm(cells: number): number {
  if (!isFinite(cells)) return POLICY_EXTRA_HIT_NONE
  const v = cells / POLICY_EXTRA_BASE_DIST_NORM
  return v > 1 ? 1 : v
}

/**
 * TS 侧 mirrorX 的 **extra 半边**（plan §3-S3：`mirrorSwap`）。
 *
 * 与 `nn-training/worker/data/mirror.py::mirror_x` 的第 4 个返回值**同表同序**：
 * 先按 `POLICY_EXTRA_MIRROR_SWAPS` 九对互换，再把 `POLICY_EXTRA_MIRROR_NEG` 取负。
 * 原地改写（`extra` 由调用方拥有）。
 *
 * 为什么在 `src/nn` 而不是测试文件里：它必须与 python 侧共用同一对表，双端对账（A4/A4b）
 * 才有意义；生产增广仍在 python（`dataset.py`），TS 侧消费者 = A4/A4b + `spatial-probe-*`。
 */
export function mirrorSwap(extra: Float32Array): void {
  for (let i = 0; i < POLICY_EXTRA_MIRROR_SWAPS.length; i++) {
    const [a, b] = POLICY_EXTRA_MIRROR_SWAPS[i]
    const tmp = extra[a]
    extra[a] = extra[b]
    extra[b] = tmp
  }
  for (let i = 0; i < POLICY_EXTRA_MIRROR_NEG.length; i++) {
    const k = POLICY_EXTRA_MIRROR_NEG[i]
    extra[k] = -extra[k]
  }
}
