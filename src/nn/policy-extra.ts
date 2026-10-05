/**
 * policy-extra.ts — `POLICY_EXTRA`：9 维方向直方图（plan/policy-spatial-head.plan.md §3.2）。
 *
 * 只喂给走位/开火两个策略头（**不进 `scalars`**、不吃 fc/value——critic 输入集不动），
 * 用来回答「威胁在哪边」：全局平均把位置抹掉之后，方向量是网络唯一能直接读到方位的入口。
 *
 * 布局（v1，冻结；改一个就要重审 plan §3）：
 *   0..3  前/后/左/右 各有几个威胁在瞄我（19px 命中带 + 判墙体遮挡）—— 归一 `min(n,4)/4`
 *   4..7  前/后/左/右 四个朝向各自「第一个能打到的敌人」的距离 —— `clamp01(px / GRID*CELL)`；
 *         打不到 ⇒ 哨兵 **1.5**（越界可区分，不另加有效性位）
 *   8     包夹度 = `min(左,右,4)/4`（左右同时被瞄是合取项，RL 小样本学合取吃力，直接喂）
 *
 * 方位语义 = **玩家相对**（与 plan §1.2 的注记一致：塔的分区是世界坐标、本向量是
 * 玩家相对坐标——「我左边那个分区」由网络自行组合）：
 *   前 = 玩家炮口朝向；后 = 反向；左/右 = 朝向的逆/顺 90°。
 *   威胁源的「来自哪边」按**源相对玩家的世界方位**分类，再映射到玩家相对系。
 *   mirrorX：左右两对维互换（`POLICY_EXTRA_MIRROR_SWAPS`），前/后/包夹度不变
 *   （镜像把朝向也翻了 left↔right，故相对语义自洽）。
 *
 * 热路径纪律（AGENTS §14）：零分配（纯局部标量）、子弹一趟 + 坦克一趟（威胁计数与
 * 4 向命中距离共用同一趟坦克扫描）、不读 rng、不写 World。
 *
 * 谓词复用：`bulletLaneDist` / `bulletInFrontDist`（src/utils/helpers.ts）与
 * `laneOccluded`（danger-metrics.ts，本计划导出）——与威胁/暴露指标同族口径，
 * 不存在「训练侧说危险、eval 侧说不危险」的分叉。
 */

import { CELL, GRID, type Direction } from '../constants'
import type { World } from '../game/World'
import { BULLET_LANE_MISS, bulletInFrontDist, bulletLaneDist } from '../utils/helpers'
import { THREAT_ALIGN_BAND_PX, laneOccluded } from './danger-metrics'

/** `POLICY_EXTRA` 维度（布局契约，见文件头）。 */
export const POLICY_EXTRA_DIM = 9
/** 计数维上限（归一化分母；威胁含敌弹，单向可能 > 4，不 clamp 会越界）。 */
export const POLICY_EXTRA_COUNT_CAP = 4
/** 「该相对方向打不到敌人」的哨兵值（> 1，与 clamp01 后的合法域可区分）。 */
export const POLICY_EXTRA_HIT_NONE = 1.5
/** 命中距离归一化分母：场地轴向跨度（26 格 × 16px = 416px；轴向距离天然有界于它）。 */
export const POLICY_EXTRA_HIT_DIST_NORM = GRID * CELL

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
] as const
if (POLICY_EXTRA_NAMES.length !== POLICY_EXTRA_DIM) {
  throw new Error(`POLICY_EXTRA_NAMES must have ${POLICY_EXTRA_DIM} entries`)
}

/**
 * mirrorX 的**互换对**（不是取负）：左右语义维两两交换，前/后与包夹度不变。
 *
 * 为什么叫 swap 而不是 plan §4-S0b 所称的 `EXTRA_X_INDICES`：这些维是计数/距离，
 * 镜像语义是「左右互换」而非标量族的「x 分量取负」。表须与
 * `nn-training/common/schema.py::EXTRA_MIRROR_SWAPS` 逐字一致（双端指纹钉住）。
 */
export const POLICY_EXTRA_MIRROR_SWAPS: ReadonlyArray<readonly [number, number]> = [
  [2, 3], // 左/右威胁计数
  [6, 7], // 左/右命中距离
]

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

/**
 * 计算 `POLICY_EXTRA`（写入 `out`，长度 9；重复调用复用缓冲，零分配）。
 *
 * 无玩家 / 玩家死亡 ⇒ 全 0（与 obs/scalars 的「无玩家即 0」同语义；1.5 哨兵只在
 * 「有玩家、该方向确实没有可命中敌人」时出现，两者不混淆）。
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

  // ---- ①②：威胁计数（子弹一趟 + 坦克一趟；0..3）与 4 向命中距离共用坦克扫描 ----
  const counts = [0, 0, 0, 0] // 前/后/左/右
  // 4 向命中距离（px；Infinity = 尚未找到）
  let dFront = Infinity
  let dBack = Infinity
  let dLeft = Infinity
  let dRight = Infinity

  const bullets = world.bullets
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    // 同轴（19px）+ 逼近（bulletLaneDist 内含方向判定）。
    if (bulletLaneDist(b.dir, bx, by, pcx, pcy, THREAT_ALIGN_BAND_PX) === BULLET_LANE_MISS) {
      continue
    }
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
    counts[relIndex(side, facing)]++
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
        counts[relIndex(side, facing)]++
      }
    }

    // ② 「能打到」：同一趟坦克扫描里对 4 个相对朝向各查一次（射线复用）。
    //    r: 0 前 / 1 后 / 2 左 / 3 右 —— 世界朝向由 facing 派生。
    for (let r = 0; r < 4; r++) {
      const worldDir: Direction =
        r === 0
          ? facing
          : r === 1
            ? OPPOSITE[facing]
            : r === 2
              ? LEFT_OF[facing]
              : OPPOSITE[LEFT_OF[facing]]
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
      if (r === 0 && dist < dFront) dFront = dist
      else if (r === 1 && dist < dBack) dBack = dist
      else if (r === 2 && dist < dLeft) dLeft = dist
      else if (r === 3 && dist < dRight) dRight = dist
    }
  }

  // ---- 写缓冲 ----
  const cap = POLICY_EXTRA_COUNT_CAP
  out[0] = Math.min(counts[0], cap) / cap
  out[1] = Math.min(counts[1], cap) / cap
  out[2] = Math.min(counts[2], cap) / cap
  out[3] = Math.min(counts[3], cap) / cap
  out[4] = hitNorm(dFront)
  out[5] = hitNorm(dBack)
  out[6] = hitNorm(dLeft)
  out[7] = hitNorm(dRight)
  out[8] = Math.min(counts[2], counts[3], cap) / cap // 包夹度 = min(左,右)
}

/** 距离 → [0,1]；无目标 ⇒ 1.5 哨兵。 */
function hitNorm(dist: number): number {
  if (!isFinite(dist)) return POLICY_EXTRA_HIT_NONE
  const v = dist / POLICY_EXTRA_HIT_DIST_NORM
  return v > 1 ? 1 : v
}
