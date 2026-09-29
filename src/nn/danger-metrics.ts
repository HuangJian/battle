/**
 * danger-metrics.ts —— 「危险暴露」的共享判定（metrics v8 的 idx41–44 + v9 的穿越税族）。
 *
 * 为什么存在：41 列指标里没有任何一列能表达「当前有多危险」（audit §6.1：
 * 有 `playerLevel` 却没有 hp，有 `pickupDist` 却没有威胁）——观测侧看得到
 * （obs s19 hp、nearestEnemyDist），奖励侧用不到，于是生存只能事后罚款。
 * 本模块给出可落盘的判定，供两个导出器（export-rl-rollout / export-eval-game）
 * **共用同一实现**，避免「training 侧说危险、eval 侧说不危险」的口径分叉。
 *
 * 判定：
 *   `playerHpRatio(world)`      —— hp/maxHp（clamp01；与 obs s19 同源）
 *   `inThreatLane(world)`       —— 是否站在敌方弹道/炮口线上（见下）
 *   `threatLaneSources(world)`  —— 同上，返回计数（诊断/测试用）
 *   `threatLaneExempt(world)`   —— 冻/盾豁免（v9 穿越税族的 `*Exempt` 列用它）
 *   以及 `DANGER_HP_THRESHOLD`  —— 残血阈值 0.4（与 `src/nn/goal-mask.ts:154` 同源）
 *
 * `inThreatLane` 口径（**2026-09-29 metrics v9 改定义**，plan/geo-threat-instrumentation.plan.md
 * §1.2；旧口径 = 12px 带 + ≤6 格 + 不判遮挡）：
 *   ① 敌方**子弹**：与玩家同轴（中心距 < 19px）、逼近；或
 *   ② 敌方**坦克**：与玩家同轴（同 < 19px 带）、`dir` 朝向玩家。
 *   ③ **无半径上限**（lane 是 lane，不论远近；远端面朝的噪声由剂量侧承担）。
 *   ④ **判墙体遮挡**（沿源自己的轴 raycast，`TileMap.blocksBullet` 阻弹）。
 *
 * 带宽 19px 是**物理值**而非调参：坦克半宽 `TANK/2 = 16` + 子弹半高 `BULLET/2 = 3`
 * ⇒ 「这一发**打得中**我」的精确判据（`|Δ| < 16 + 3`）。旧 12px（`CELL*0.75`）中心线
 * 差一行的炮弹全漏（§60：人类 4.07% → 8.19%，被漏掉的那部分扛 55.6% 伤害）。
 *
 * ⚠ **新旧不可比**（用户拍板代价，plan §1.2 祖父条款）：L1/L1d2/L2b/dodge-l3 的
 * `threatTicks` 读数与「机制零位移」结论都是旧谓词下记录的，逐字复算不可能（逐 tick
 * 状态已丢）——verdict 维持原判但标注口径断代。常量的**旧值语义**保留在
 * `THREAT_ALIGN_BAND`（见下），使用处一律走新常量。
 *
 * ⚠ **口径变更的爆炸半径不止 metrics**（评审 B3，已拍板接受）：`inThreatLane` 同时是
 * **决策门**的输入（`src/nn/decision-gate.ts::threatOnsetEdge` → `decisionDue` 的
 * threat-ONSET 事件 rung，八处调用含部署链 `policy-input.ts`），也是
 * `src/nn/obs-encoder.ts::decisionTick` 的 condition 4。开了 `decision_events` 的课程
 * （`nn-training/curricula/h5e-events.jsonc`）其**决策点集合**随之变化，而
 * `decision_events` 不在 `corpus_identity_fp` 里 ⇒ 旧/新 shard 同血缘、D14 不拒收。
 * 这是本 plan 已知且接受的代价；改动本模块前请先读 plan §1.2 与 DECISIONS 对应条目。
 *
 * 已知简化：raycast 走**源自己的单格线**（弹/炮管的实际弹道线），玩家 2×2 体按中心格
 * ±1 格算近边。15×15 格场地内不会出现「斜向可打」的构型（本模块只处理轴对齐），
 * 故不做角度化。
 *
 * 纯函数、零分配、不读 rng、不写 World（AGENTS §2.3 / §14.1–14.2）。
 */
import { BULLET, CELL, TANK } from '../constants'
import type { World } from '../game/World'
import { TileMap } from '../game/TileMap'
import { BULLET_LANE_MISS, bulletInFrontDist, bulletLaneDist } from '../utils/helpers'

/** 触发半径（格）：**v9 起不再被 `inThreatLane` 使用**；保留给 dodge-l0 镜像与测试。 */
export const THREAT_RADIUS_CELLS = 6
/**
 * **旧**对齐带宽：±0.75 格（12px）。v9 起 `inThreatLane` **不用它** —— 使用处以
 * `THREAT_ALIGN_BAND_PX`（19px 物理值）为准。保留导出是因为 dodge-l0 /
 * simulation-runner / decision-trace 三处镜像仍按旧值各自实现，且测试钉着它。
 */
export const THREAT_ALIGN_BAND = CELL * 0.75
/** v9 生产带宽：坦克半宽 + 子弹半高 = 16 + 3 = 19px（「打得中」的精确判据）。 */
export const THREAT_ALIGN_BAND_PX = TANK / 2 + BULLET / 2
/** 残血阈值：hpRatio < 0.4（与 `src/nn/goal-mask.ts` 的撤退阈值同源）。 */
export const DANGER_HP_THRESHOLD = 0.4
/** 开局承伤窗（tick）：`dmgFirst600` 只累计 `tick < 本值` 的承伤。 */
export const DMG_FIRST_WINDOW_TICKS = 600

/** 玩家 hp/maxHp（clamp01）；玩家不在场/已死 = 0（与 obs s19 同口径）。 */
export function playerHpRatio(world: World): number {
  const p = world.player
  if (!p) return 0
  const max = p.maxHp > 0 ? p.maxHp : 1
  const r = p.hp / max
  return r < 0 ? 0 : r > 1 ? 1 : r
}

/**
 * 冻/盾豁免（v9）：敌人冻结（`freezeTimer > 0`，玩家吃冻结道具的效果期）或玩家星盾
 * 在身（`player.shieldTimer > 0`）时，玩家「在线上」不构成可归因的暴露。
 *
 * ⚠ **两者性质不同，别当同一件事**（引擎事实，2026-09-29 核）：
 *   · `shieldTimer > 0` = **真无敌** ⇒ 「命中也无所谓」是物理事实；
 *   · `freezeTimer > 0` 只停**敌方 AI 决策与移动**（`TacticalIntelligence.update` 提前
 *     return、`SimulationCombat` 把 vx/vy 清零并 `continue`）——**在飞的敌弹照飞**
 *     （没有任何子弹分支读 `freezeTimer`）⇒ 玩家仍会被冻前打出的弹命中。
 *   所以本函数给的是**定价假设**（「冻住这一拍不可归因」），不是「物理上打不到」。
 *   观测侧只负责把这个事实记成加法列；公式侧拿它当折扣时必须自担这个偏差。
 *
 * ⚠ 豁免**只**用于观测族的加法列（`onLaneExemptTicks`）与公式侧的净价项
 * （`-w*d(onLane) + w*d(onLaneExempt)`）；`threatTicks` **不带豁免**（保持无豁免
 * lineage，plan §1.2）。冻结/盾的翻转沿不可做成逐行 0/1 flag 再相乘 —— 那会在
 * 翻转拍打出幻影 ± 尖峰（plan §1.3 机制注）。
 */
export function threatLaneExempt(world: World): boolean {
  if (world.freezeTimer > 0) return true
  const p = world.player
  return !!p && (p.shieldTimer ?? 0) > 0
}

/**
 * 源（弹/炮口）到玩家之间是否有阻弹地形（v9 新增子项）。
 *
 * 走**源自己的单格线**（弹道线 = 源的垂直坐标决定的列/行），两端都不算：
 * 起点是源格，终点是玩家身体在来向上的**近边格**。中间任一格
 * `TileMap.blocksBullet` ⇒ 遮挡。
 *
 * ⚠ 玩家身体的格跨度按**像素**算（`floor(x/CELL)` … `floor((x+w-1)/CELL)`），不是
 * 「中心格 ±1」：32px 的身体骑线时可以跨 3 格（骑线位置由 scrum margin 吸收的是
 * **分类**，不是遮挡 — 遮挡问的是「这一发物理上能不能到我」，按像素算才是真值）。
 */
function laneOccluded(
  tileMap: TileMap,
  srcCol: number,
  srcRow: number,
  vertical: boolean,
  pLow: number,
  pHigh: number,
): boolean {
  if (vertical) {
    if (srcRow < pLow) {
      for (let r = srcRow + 1; r < pLow; r++) {
        if (TileMap.blocksBullet(tileMap.get(srcCol, r))) return true
      }
    } else {
      for (let r = srcRow - 1; r > pHigh; r--) {
        if (TileMap.blocksBullet(tileMap.get(srcCol, r))) return true
      }
    }
    return false
  }
  if (srcCol < pLow) {
    for (let c = srcCol + 1; c < pLow; c++) {
      if (TileMap.blocksBullet(tileMap.get(c, srcRow))) return true
    }
  } else {
    for (let c = srcCol - 1; c > pHigh; c--) {
      if (TileMap.blocksBullet(tileMap.get(c, srcRow))) return true
    }
  }
  return false
}

/**
 * 站在敌方弹道/炮口线上的威胁源数量（0 = 不在弹道上）。
 * 返回计数而非布尔：便于测试与诊断区分「刚进弹道」与「被交叉火力夹住」。
 *
 * 对齐/逼近（弹）与对齐/朝向（炮口）复用 `src/utils/helpers.ts` 的**共享谓词**
 * （`bulletLaneDist` / `bulletInFrontDist`），不再各写一份 inline 链。
 */
export function threatLaneSources(world: World): number {
  const p = world.player
  if (!p || !p.alive) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const tileMap = world.tileMap
  // 玩家身体的**像素精确**格跨度（遮挡判据用；分类用的中心格另算）。
  const pColLow = Math.floor(p.x / CELL)
  const pColHigh = Math.floor((p.x + p.w - 1) / CELL)
  const pRowLow = Math.floor(p.y / CELL)
  const pRowHigh = Math.floor((p.y + p.h - 1) / CELL)
  let n = 0

  // ① 敌方子弹：同轴（<19px 带）+ 逼近 + 线上无掩体。
  const bullets = world.bullets
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    if (bulletLaneDist(b.dir, bx, by, pcx, pcy, THREAT_ALIGN_BAND_PX) === BULLET_LANE_MISS) continue
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
    n++
  }

  // ② 敌方坦克：同轴（<19px 带）+ 炮口朝向玩家 + 线上无掩体（「站在别人炮口线上」）。
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    // 共享谓词的调用约定：dir = 炮口朝向，(bcx,bcy) = 被问的点（玩家），(tx,ty) = 炮口所在点。
    if (bulletInFrontDist(t.dir, pcx, pcy, tx, ty, THREAT_ALIGN_BAND_PX) === BULLET_LANE_MISS) {
      continue
    }
    const vertical = t.dir === 'up' || t.dir === 'down'
    const occluded = laneOccluded(
      tileMap,
      Math.floor(tx / CELL),
      Math.floor(ty / CELL),
      vertical,
      vertical ? pRowLow : pColLow,
      vertical ? pRowHigh : pColHigh,
    )
    if (occluded) continue
    n++
  }
  return n
}

/** 是否在敌方弹道/炮口线上（`threatLaneSources > 0`）。 */
export function inThreatLane(world: World): boolean {
  return threatLaneSources(world) > 0
}
