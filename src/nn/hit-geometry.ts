/**
 * hit-geometry.ts —— 命中方位分类（metrics v9 的 `backHits/sideHits/frontHitsExempt/farHits`）。
 *
 * plan/geo-threat-instrumentation.plan.md §1.1 的**唯一实现**。两个导出器 + 单测共用，
 * 避免「训练侧说背刺、eval 侧说正面」的口径分叉（与 `danger-metrics.ts` 同一条纪律）。
 *
 * 坐标系（与 census/telemetry 同构）：
 *   格坐标 = 坦克**中心格** `floor((x + w/2) / CELL)`（32px 体按中心点算；跨格骑线
 *   误差由 2 格 scrum 区吸收，不逐像素）。行号向下递增。
 *   朝向单位向量：`up(0,-1) / down(0,+1) / left(-1,0) / right(+1,0)`。
 *
 * 记 V = **玩家格 − 受害敌格**（`dx = pc.col − vc.col`，`dy = pc.row − vc.row`），
 * F = 受害敌朝向。判定**按序**（先命中先得）：
 *   0. `min(|dx|,|dy|) > 2` ⇒ FAR。轴线开火物理上不可能命中 ⇒ 那是「开火后目标自己走进
 *      弹道」的流弹（**不是**炸弹/地雷：爆炸类击杀不推 `enemy_hit`，见下）
 *      （61 局实测 103/1221 = 8.4%）。**不定价**，只作「非瞄准份额」监视列。
 *   1. `dx == 0 && dy == 0` ⇒ SIDE（同格贴身，不分前后）。
 *   2. `max(|dx|,|dy|) ≤ 2 && min(|dx|,|dy|) ≥ 1` ⇒ SIDE（近身对角 scrum 区；
 *      主轴投影会把它误判成 front —— 这正是本步先于第 3 步的理由）。
 *   3. 否则主轴投影：`|dx| ≥ |dy|` 看 x，否则看 y；`F·V > 0` ⇒ FRONT，`< 0` ⇒ BACK，
 *      `= 0` ⇒ SIDE（垂直）。
 *
 * ⚠ **第 3 步的平局分支是死代码**（2026-09-29 评审 H3 修正，原文写错了）：
 *   `|dx| == |dy|` 的构型必被第 0 步（`min > 2` ⇒ FAR）或第 2 步（`max ≤ 2 ∧ min ≥ 1`
 *   ⇒ SIDE）吃掉 —— 例如 `(2,2)` 走第 2 步得 SIDE（**不是**「列优先得 front」），
 *   `(3,3)` 走第 0 步得 FAR。故 `|dx| ≥ |dy|` 里的 `≥` 与 `>` 在本函数中不可区分，
 *   保留 `≥` 只为与既有 resim 口径的字面写法一致。
 *
 * 纯函数、零分配（AGENTS §14.1–14.2）、不读 rng、不写 World。
 */
import { CELL, DIR_VECTORS, GRID, TANK, type Direction } from '../constants'
import type { Bullet } from '../types'

/** 命中方位（数值枚举：热路径零字符串分配）。 */
export const HIT_FRONT = 0
export const HIT_BACK = 1
export const HIT_SIDE = 2
export const HIT_FAR = 3
export type HitGeometry = typeof HIT_FRONT | typeof HIT_BACK | typeof HIT_SIDE | typeof HIT_FAR

/** scrum 半径（格）：2 格内的近身对角一律算侧击。pin 值；改则剂量连带重算（plan §1.1）。 */
export const SCRUM_RADIUS_CELLS = 2

/** 打包格键 `col * GRID + row`（当 Map 键用，避免为每次开火分配对象）。 */
export function cellKey(col: number, row: number): number {
  return col * GRID + row
}

/** 打包格键 → 列。 */
export function keyCol(key: number): number {
  return Math.floor(key / GRID)
}

/** 打包格键 → 行。 */
export function keyRow(key: number): number {
  return key % GRID
}

/** 任意盒（坦克/子弹）的**中心格**键；与 census/telemetry 的格口径同构。 */
export function centerCellKey(x: number, y: number, w: number, h: number): number {
  return cellKey(Math.floor((x + w / 2) / CELL), Math.floor((y + h / 2) / CELL))
}

/**
 * 从**开火当拍**的子弹反推 shooter 的中心格键。
 *
 * 弹的生成式（`SimulationCombat.tryFire`）：
 *   `bullet.center = tank.center + dirVec × (TANK / 2)`
 * ⇒ 减去这半格偏移即得 shooter 的中心点，**无需**在遥测侧维护「新生弹首见」表——
 * 开火事件的弹坐标本身就是开火时刻的真值，晚一步采样只会更差（评审 M3）。
 */
export function fireOriginCellKey(bullet: Bullet): number {
  const v = DIR_VECTORS[bullet.dir]
  const bx = bullet.x + bullet.w / 2 - v.dx * (TANK / 2)
  const by = bullet.y + bullet.h / 2 - v.dy * (TANK / 2)
  return cellKey(Math.floor(bx / CELL), Math.floor(by / CELL))
}

/** 朝向单位向量的轴分量：x 轴 = 左右，y 轴 = 上下。 */
function axisSign(dir: Direction, axis: 'x' | 'y'): number {
  const v = DIR_VECTORS[dir]
  return axis === 'x' ? v.dx : v.dy
}

/**
 * 命中方位分类（`V = 玩家格 − 受害敌格`，F = 受害敌朝向）。
 *
 * 冻/盾豁免**不在这里**判：本函数只给 raw 几何（含 FAR 判定），豁免是导出器侧对
 * 非 FAR 结果的加法覆盖（`frontHitsExempt`），见 plan §1.1「冻/盾映射」。
 */
export function classifyHit(
  pCol: number,
  pRow: number,
  vCol: number,
  vRow: number,
  vDir: Direction,
): HitGeometry {
  const dx = pCol - vCol
  const dy = pRow - vRow
  const adx = dx < 0 ? -dx : dx
  const ady = dy < 0 ? -dy : dy

  // 0. 轴向开火打不到 ⇒ 只能是**开火后目标自己走进弹道**（流弹效应）。
  //    ⚠ 本列**不是**「非瞄准份额」的全量：地雷/牺牲等爆炸类击杀**不推 `enemy_hit`**
  //    （全仓唯一推点 = `SimulationCombat.bulletHitsTank`）⇒ 它们不落任何方位桶。
  if ((adx < ady ? adx : ady) > SCRUM_RADIUS_CELLS) return HIT_FAR
  // 1. 同格贴身。
  if (dx === 0 && dy === 0) return HIT_SIDE
  // 2. 近身对角 scrum 区。
  const max = adx > ady ? adx : ady
  const min = adx < ady ? adx : ady
  if (max <= SCRUM_RADIUS_CELLS && min >= 1) return HIT_SIDE

  // 3. 主轴投影（平局分支不可达，见文件头 ⚠）。
  if (adx >= ady) {
    const s = axisSign(vDir, 'x') * dx
    return s > 0 ? HIT_FRONT : s < 0 ? HIT_BACK : HIT_SIDE
  }
  const s = axisSign(vDir, 'y') * dy
  return s > 0 ? HIT_FRONT : s < 0 ? HIT_BACK : HIT_SIDE
}
