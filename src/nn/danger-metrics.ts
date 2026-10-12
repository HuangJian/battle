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
 *   `alignedEnemyCount(world)`  —— 「被包围」计数：同行/同列（19px 带 + 无遮挡）的敌车数
 *                                  （metrics v10 的 `encl*Ticks` 族用它，见 plan/metrics-v10-gap-columns）
 *   `nearestEnemyDistPx(world)` —— 最近存活敌车距离（px；哨兵 `ENEMY_DIST_SENTINEL`）
 *   `damageClusterStats(...)`   —— 伤害成簇：连击数 / 120t 窗内最大承伤
 *   `exposureExempt(world, shield)` —— 本批豁免机制 A 谓词（冻 ∨ **盾道具窗**；与宽口径 `threatLaneExempt` 不是同一件事）
 *   `enemyBulletLaneWeightTick(...)` —— 敌弹火线承伤逐摄权重 Σ(6−x)（本批 `hurtWeight`）
 *   `enclWeightTick(world)`      —— n≥2 时 Σ max(0,K_ENCL−d)（本批 `enclWeightTicks`；与计数共用扫描）
 *   `cornerWeightTick(world)`    —— 四角锚点 Σ max(0,K_CORNER−d)（本批 `cornerWeightTicks`）
 *   —— metrics v11（plan/metrics-v11-hotlane.plan.md §2；**签入 2 列 idx74–75**，第三列被
 *   §4bis.4 杀线拦下）：
 *   `nearSqWeightTick(world)`    —— 全敌 Σ(K_NEAR_SQ−d)²（本批 `nearSqSum`）
 *   `hotLaneAxisOf/Open/Tick`    —— 热线停留窗（本批 `postHitLaneTicks`；**逐局实例**，非模块级状态）
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
import { BASE_POS, BULLET, CELL, DIR_VECTORS, GRID, TANK, type Direction } from '../constants'
import type { Bullet, Tank } from '../types'
import type { World } from '../game/World'
import { TileMap } from '../game/TileMap'
import { BULLET_LANE_MISS, bulletInFrontDist, bulletLaneDist, snap } from '../utils/helpers'
import type { ShieldWindow } from './shield-window'

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
/**
 * 近敌带宽（px）：`nearEnemy4Ticks`（metrics v10）累计「最近敌距 ≤ 本值」的 tick。
 * 4 格 = 64px 是**观测阈值**（pin 值，可调；调则剂量连带重算），语义 = 「敌方已进入近身威胁带」。
 */
export const NEAR_ENEMY_BAND_PX = 4 * CELL
/**
 * 「连击」窗（tick）：`damageBursts` 统计相邻两次扣血间隔 ≤ 本值的次数（120 tick = 12 决策步）。
 * `maxDamage120` 的滑窗宽度同源（metrics v10；与 `docs/nn/experiments.md` §71 面板同值）。
 */
export const DMG_BURST_TICKS = 120
/**
 * `enemyDist`（metrics v10）哨兵：无存活敌车（或玩家不在场/已死）时填此值。
 * 真实欧氏距离恒 ≥0（同格 ≠ 0 但 ≥0）⇒ -1 不可能与真实值混淆；公式侧 `where` 归零。
 * 与 `PICKUP_DIST_SENTINEL`（export-rl-rollout.ts）同构，不共用常量只为免跨模块依赖。
 */
export const ENEMY_DIST_SENTINEL = -1

/**
 * 统一权重律 `w(d) = max(0, K − d)`（aim-dodge-levers plan §3.3）的窗口常量：
 * K = 窗口上界 + 1。**三列 w 不可横比**（窗口不同）。
 *   · `K_HURT`   = 6：敌弹火线权重，窗口 x ≤ 5（6 格 ≈ 96px ≈ 0.38–0.45s 反应余量）；
 *   · `K_ENCL`   = 5：被包围距离加权，窗口 d ≤ 4；
 *   · `K_CORNER` = 4：角落距离加权，窗口 d ≤ 3。
 */
export const K_HURT = 6
export const K_ENCL = 5
export const K_CORNER = 4
/**
 * 角锚点内边距（格）：四角 = `(M,M) / (GRID−4,M) / (GRID−4,GRID−4) / (M,GRID−4)`，M = 本值。
 * `GRID−4` = 钢边框 2 格 + 坦克占格 2 格（**禁用** `GRID−1−MARGIN` 的写法 —— 差 1 格）。
 */
export const CORNER_ANCHOR_MARGIN = 2

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
 * 本批豁免机制 A 谓词（**窄**，与上面 `threatLaneExempt` 的宽口径**不是同一件事**）：
 * `freezeTimer > 0` ∨ **盾道具窗**（`src/nn/shield-window.ts` 粘性 tracker：
 * 只有 `+= POWERUP_DURATION_MS` 量级的拾取算；出生/复活盾与星盾 grace 不计入）。
 * 逐列适用性见 plan §3.6 豁免矩阵。调用约定：每 tick 先 `shield.update(world)`，
 * 再问本函数（tracker 需要增量序列，无法从单帧重建）。
 */
export function exposureExempt(world: World, shield: ShieldWindow): boolean {
  return world.freezeTimer > 0 || shield.powerupActive()
}

/**
 * 敌弹「火线承伤」逐拍权重（本批 `hurtWeight` 的逐拍项，plan §3.2）：
 *   x = 玩家中心格到弹中心格的**切比雪夫距**（格）；谓词 = 19px 带 ∧ 玩家在弹前方
 *   ∧ `x ≤ K_HURT − 1`；权重 = `max(0, K_HURT − x)`（= 6−x）。
 * **非豁免拍才返回非零**（A 拌入：冻期不新增；盾道具窗内弹会被零事件吞掉、权重永不结算）。
 * 复用 `bulletLaneDist`（helpers 的唯一 19px 带实现）——**禁第三套 19px**（plan §3.2）。
 * 纯函数、零分配、不读 rng、不写 World。
 */
export function enemyBulletLaneWeightTick(
  world: World,
  bullet: Bullet,
  shield: ShieldWindow,
): number {
  const p = world.player
  if (!p || !p.alive || !bullet.alive) return 0
  if (exposureExempt(world, shield)) return 0
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  if (bulletLaneDist(bullet.dir, bcx, bcy, pcx, pcy, THREAT_ALIGN_BAND_PX) === BULLET_LANE_MISS) {
    return 0
  }
  const x = Math.max(
    Math.abs(Math.floor(bcx / CELL) - Math.floor(pcx / CELL)),
    Math.abs(Math.floor(bcy / CELL) - Math.floor(pcy / CELL)),
  )
  return x < K_HURT ? K_HURT - x : 0
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
 *
 * 2026-10-05 导出（plan/policy-spatial-head.plan.md §3.2：POLICY_EXTRA 的「能打中」
 * 射线与威胁计数必须复用同族口径；评审给的两个选项「导出或搬迁」取导出，零行为改动）。
 */
export function laneOccluded(
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

/**
 * 「被包围」计数（metrics v10，plan/metrics-v10-gap-columns.plan.md §1）：与玩家
 * **同行/同列**、且中间无阻弹地形的**敌车**个数。三条件：
 *   ① `alive ∧ allegiance === 'enemy' ∧ spawnTimer <= 0`（未激活车不算暴露）；
 *   ② 中心距在垂直方向上 `< THREAT_ALIGN_BAND_PX`（19px = 坦克半宽 16 + 子弹半高 3）；
 *   ③ 沿源自己的轴无阻弹地形（`laneOccluded`，与 `threatLaneSources` 同一实现）。
 *
 * ⚠ 三条都是**物理/几何真值**，不含朝向、不含半径上限（lane 是 lane）——与 §71 ②b 的口径
 * 逐字一致。**不要**把 ② 写成 naive「中心格同行列」或旧 12px 带（§71 ②b 实测：12px 读数
 * 腰斩且翻符号，naive 直接归零）——本函数是唯一实现，两个导出器都 import 它。
 * 本批的 `enclWeightTick` 与它共用同一扫描循环（`alignedEnemyScan`：计数/加权两读数，
 * 谓词只此一份；加权的 d 取中心格切比雪夫距，K_ENCL = 5）。
 *
 * 纯函数、零分配、不读 rng、不写 World（AGENTS §2.3 / §14.1–14.2）。
 */
export function alignedEnemyCount(world: World): number {
  return alignedEnemyScan(world, false)
}

/**
 * 本批被包围距离加权（`enclWeightTicks` 的逐 tick 项，plan §3.3）：
 * `alignedEnemyCount ≥ 2` 时返回 `Σ max(0, K_ENCL − d)`（d = 敌方与玩家中心格的
 * 切比雪夫距；d ≥ K_ENCL 的敌贡献 0）。**与计数共用同一扫描循环** ⇒ 谓词不会漂移。
 * 豁免（A 拌入）由**调用方**用 `exposureExempt` gate —— 本函数只做几何。
 */
export function enclWeightTick(world: World): number {
  return alignedEnemyScan(world, true)
}

/**
 * 对齐扫描（19px 带 + 轴向无遮挡）的**参数化核心**（obs-v5，plan §2.5）。
 *
 * 参数语义（唯一读法，别再造第二套）：
 *   · `cx,cy` = 被瞄目标的**像素中心** —— 对齐判据是固定的 `THREAT_ALIGN_BAND_PX` 中心带；
 *   · `colLow..rowHigh` = 遮挡 raycast 的**目标体跨格**（玩家＝自身身体格；基地＝2×2 足印）；
 *   · `tanks` = `world.allTanks` 的**单次取值**（AGENTS §14.6：每 encode 取一次传引用）；
 *   · `out`（长度 6，调用方复用缓冲；**零分配**）：
 *       `out[0]` = 对齐且无遮挡的敌车计数、`out[1]` = 距离加权（wantWeight 时）、
 *       `out[2..5]` = 世界方位 up/down/left/right 的计数（竖直对齐归 up/down，
 *       横向对齐归 left/right；**Σout[2..5] ≡ out[0]**）。
 *
 * 谓词（19px 带 + 轴向无遮挡 + 已激活 + 存活）本体只此一份：玩家族
 * （`alignedEnemyCount`/`enclWeightTick`）与基地族（`baseThreat*`）、
 * `laneE*` 全部走这里。
 */
export function alignedScan(
  world: World,
  cx: number,
  cy: number,
  colLow: number,
  colHigh: number,
  rowLow: number,
  rowHigh: number,
  wantWeight: boolean,
  out: Float64Array,
  tanks: readonly Tank[] = world.allTanks,
): number {
  const tileMap = world.tileMap
  out[0] = 0
  out[1] = 0
  out[2] = 0
  out[3] = 0
  out[4] = 0
  out[5] = 0
  let n = 0
  let w = 0
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const sameCol = Math.abs(tx - cx) < THREAT_ALIGN_BAND_PX
    const sameRow = Math.abs(ty - cy) < THREAT_ALIGN_BAND_PX
    if (!sameCol && !sameRow) continue
    const srcCol = Math.floor(tx / CELL)
    const srcRow = Math.floor(ty / CELL)
    const occ = sameCol
      ? laneOccluded(tileMap, srcCol, srcRow, true, rowLow, rowHigh)
      : laneOccluded(tileMap, srcCol, srcRow, false, colLow, colHigh)
    if (occ) continue
    n++
    if (sameCol) {
      if (ty < cy) out[2]++
      else out[3]++
    } else if (tx < cx) {
      out[4]++
    } else {
      out[5]++
    }
    if (wantWeight) {
      const d = Math.max(
        Math.abs(srcCol - Math.floor(cx / CELL)),
        Math.abs(srcRow - Math.floor(cy / CELL)),
      )
      if (d < K_ENCL) w += K_ENCL - d
    }
  }
  out[0] = n
  out[1] = wantWeight ? (n >= 2 ? w : 0) : 0
  return wantWeight ? out[1] : n
}

/** 玩家族复用的对齐扫描缓冲（§14.1：热路径不分配）。只做返回值的传手，内容不外传。 */
const _ALIGNED_SCRATCH = new Float64Array(6)

/**
 * 「被包围」共享扫描（不分配对象；一个循环两个读数）：
 *   · `wantWeight = false` ⇒ 返回对齐敌数（`alignedEnemyCount` 语义，逐位不变）；
 *   · `wantWeight = true`  ⇒ 返回 Σ max(0,K_ENCL−d)（n ≥ 2 才非零）。
 * obs-v5 起它是 `alignedScan` 的薄封装（玩家自身为锚、自身身体为遮挡终点）。
 */
function alignedEnemyScan(world: World, wantWeight: boolean): number {
  const p = world.player
  if (!p || !p.alive) return 0
  // 玩家身体的像素精确格跨度（遮挡判据；与 threatLaneSources 同规）。
  return alignedScan(
    world,
    p.x + p.w / 2,
    p.y + p.h / 2,
    Math.floor(p.x / CELL),
    Math.floor((p.x + p.w - 1) / CELL),
    Math.floor(p.y / CELL),
    Math.floor((p.y + p.h - 1) / CELL),
    wantWeight,
    _ALIGNED_SCRATCH,
  )
}

/**
 * 四角锚点（格坐标；顺序 左上/右上/右下/左下）——**只从 `GRID` 派生**：
 * `(M,M)/(GRID−4,M)/(GRID−4,GRID−4)/(M,GRID−4)`，M = `CORNER_ANCHOR_MARGIN`。
 * 供测试做「锚点 === 关卡四角 spawn」一致性断言（plan §3.3 / sb P1-H）。
 * 离热路径（导出器只用 `cornerWeightTick`），可以分配。
 */
export function cornerAnchors(): Array<{ col: number; row: number }> {
  const a = CORNER_ANCHOR_MARGIN
  const b = GRID - 4
  return [
    { col: a, row: a },
    { col: b, row: a },
    { col: b, row: b },
    { col: a, row: b },
  ]
}

/**
 * 角落暴露权重（本批 `cornerWeightTicks` 的逐 tick 项，plan §3.3）：
 * 玩家**中心格**到四角锚点（`cornerAnchors` 同一派生）最小切比雪夫距 d ⇒ `max(0, K_CORNER − d)`（d ≤ 3）。
 * `enemy_spawns` 不是运行时输入（降为一致性断言）；拌入由调用方 gate（§3.6）。
 */
export function cornerWeightTick(world: World): number {
  const p = world.player
  if (!p || !p.alive) return 0
  const d = cornerAnchorDist(Math.floor((p.x + p.w / 2) / CELL), Math.floor((p.y + p.h / 2) / CELL))
  return d < K_CORNER ? K_CORNER - d : 0
}

/**
 * 中心格到四角锚点的**最小切比雪夫距**（格；`cornerAnchors()` 同式派生）。
 * 抽出来是给 obs-v5 的 `cornerDist` 标量用——同一几何，不给观测侧另建一份公式
 * （plan §2.5 P3）。零分配（`cornerAnchors()` 会分配 4 个对象，热路径不用它）。
 */
export function cornerAnchorDist(col: number, row: number): number {
  const a = CORNER_ANCHOR_MARGIN
  const b = GRID - 4
  let d = Math.max(Math.abs(col - a), Math.abs(row - a))
  let dd = Math.max(Math.abs(col - b), Math.abs(row - a))
  if (dd < d) d = dd
  dd = Math.max(Math.abs(col - b), Math.abs(row - b))
  if (dd < d) d = dd
  dd = Math.max(Math.abs(col - a), Math.abs(row - b))
  if (dd < d) d = dd
  return d
}

// ───────────────────────── metrics v11（idx74–75）─────────────────────────
// plan/metrics-v11-hotlane.plan.md §2；核与 K 常量与本族并列（R13）。

/**
 * 近敌平方核的 K（`nearSqSum`）：核 = (K−d)²，`d ≤ K`。
 * ⚠ `d = K` 处核值恒 0 ⇒ **有效半径 = K−1 = 2 格**；不许再写成「3 格带/3 格缓冲」（R13）。
 */
export const K_NEAR_SQ = 3
/**
 * 热线窗并发硬护栏（语义上限 = 存活敌数：**一源一窗**）；溢出即丢弃并 `dropped++`，
 * 调用方读到增量就打 warn（「warn 即非静默」，plan §2.1）。
 */
export const MAX_HOT_LANES = 64

/** 中心格编号（像素中心 floor；与 `nearestPickupDist`/`playerCenterCell` 同式）。 */
function cellOf(x: number, y: number, w: number, h: number, vertical: boolean): number {
  return vertical ? Math.floor((x + w / 2) / CELL) : Math.floor((y + h / 2) / CELL)
}

/**
 * `nearSqSum` 的逐拍项（metrics v11 idx74）：对每个**存活已激活**敌车（`spawnTimer <= 0`，
 * 与 `nearestEnemyDistPx`/`alignedEnemyCount` 同规）取**切比雪夫**格距 d，`d ≤ K_NEAR_SQ`
 * 时累加 (K_NEAR_SQ−d)²。与 `nearEnemy4Ticks`（4 格**像素**带、只认最近一个）不是等比量。
 *
 * 纯函数、零分配、不读 rng、不写 World。
 */
export function nearSqWeightTick(world: World): number {
  const p = world.player
  if (!p || !p.alive) return 0
  const pcol = Math.floor((p.x + p.w / 2) / CELL)
  const prow = Math.floor((p.y + p.h / 2) / CELL)
  let sum = 0
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const d = Math.max(
      Math.abs(Math.floor((t.x + t.w / 2) / CELL) - pcol),
      Math.abs(Math.floor((t.y + t.h / 2) / CELL) - prow),
    )
    if (d <= K_NEAR_SQ) {
      const w = K_NEAR_SQ - d
      sum += w * w
    }
  }
  return sum
}

/**
 * 热线轴（`postHitLaneTicks` 的窗键）：开窗瞬间记录的源火线，**整数格轴**。
 * ⚠ 与 `threatLaneSources` 家族的 19px **像素连续带**是两套谓词（R3）：带边缘抖动会让
 * 「玩家还在轴上」高频翻转 ⇒ 关窗抖动，故本窗用整数轴；本窗也**不判遮挡**。
 */
export interface HotLane {
  /** true = 沿**列**（源朝 up/down；轴上序号 = 源中心格 col）；false = 沿行。 */
  vertical: boolean
  /** 记录瞬间的轴序号（vertical ⇒ col，否则 row）。 */
  axis: number
}

/** 取坦克当前的火线轴（显式返回对象：只用于开窗/关窗判定，不在逐拍热路径里分配）。 */
export function hotLaneAxisOf(t: Tank): HotLane {
  const vertical = t.dir === 'up' || t.dir === 'down'
  return { vertical, axis: cellOf(t.x, t.y, t.w, t.h, vertical) }
}

/** 玩家中心格是否落在给定轴上（整数格比较）。 */
function playerOnLane(world: World, lane: HotLane): boolean {
  const p = world.player
  if (!p || !p.alive) return false
  return cellOf(p.x, p.y, p.w, p.h, lane.vertical) === lane.axis
}

/** 源是否仍沿同一条轴持有火线（存活 ∧ 朝向轴不变 ∧ 自己的轴序号不变 ⇒ 未转向换道）。 */
function sourceHeldLane(world: World, sourceId: number, lane: HotLane): boolean {
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.id !== sourceId) continue
    if (!t.alive) return false
    return (
      hotLaneAxisOf(t).vertical === lane.vertical &&
      cellOf(t.x, t.y, t.w, t.h, lane.vertical) === lane.axis
    )
  }
  return false
}

interface HotLaneSlot {
  sourceId: number
  lane: HotLane
}

/** 逐局热线窗状态（**每局一个实例**，见 `createHotLaneWindow`）。 */
export interface HotLaneWindow {
  slots: HotLaneSlot[]
  /** 溢出丢弃次数（只增；调用方读增量打 warn）。 */
  dropped: number
}

/**
 * 逐局新建窗状态。
 * ⚠ **严禁模块级单例**（AGENTS §2.2 隐藏状态）：eval worker / 测试在同一进程里连跑多局，
 * 模块级容器会让第二局继承第一局的窗（R10）。
 */
export function createHotLaneWindow(): HotLaneWindow {
  return { slots: [], dropped: 0 }
}

/**
 * 开/重开窗（只由伤害事件驱动，§2.1）：同源重开 = 用新一击的轴替换旧窗（**一源一窗**
 * ⇒ 并发上限天然 = 存活敌数）；超 `MAX_HOT_LANES` 丢弃并 `dropped++`。
 * 豁免**不影响开窗**（窗可以开在豁免拍上，只是那些拍不计数）。
 */
export function hotLaneOpen(win: HotLaneWindow, sourceId: number, lane: HotLane): void {
  const slots = win.slots
  for (let i = 0; i < slots.length; i++) {
    if (slots[i].sourceId === sourceId) {
      slots[i].lane = lane
      return
    }
  }
  if (slots.length >= MAX_HOT_LANES) {
    win.dropped++
    return
  }
  slots.push({ sourceId, lane })
}

/**
 * 按源 id 开窗（伤害事件驱动，两个导出器共用本实现）：源不存在 / 已死 ⇒ 不开
 * （state-init 交棒的死源是合法漏窗，不 warn）；溢出丢弃记在 `win.dropped`，
 * 由调用方在局末统一 warn（「warn 即非静默」，plan §2.1）。
 */
export function hotLaneOpenFromTank(win: HotLaneWindow, world: World, sourceId: number): void {
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.id !== sourceId) continue
    if (t.alive) hotLaneOpen(win, sourceId, hotLaneAxisOf(t))
    return
  }
}

/**
 * 逐拍推进：返回**本拍应计的 tick 数**（0 或 1×持有窗数）。
 * 关窗只看「源死亡 / 源转向换道 / 本局结束」（与豁免无关）；`exempt` 只决定**本拍是否计数**
 * （决定 A：豁免拍照常持有窗但不计入）——两件事必须分开，否则冻住时站在轴上会被关窗。
 * 零分配（原地 splice/截断），不读 rng。
 */
export function hotLaneTick(win: HotLaneWindow, world: World, exempt: boolean): number {
  if (!world.player?.alive) {
    if (win.slots.length > 0) win.slots.length = 0 // 局终/阵亡：全关
    return 0
  }
  let n = 0
  const slots = win.slots
  for (let i = slots.length - 1; i >= 0; i--) {
    const s = slots[i]
    // 关窗：源已不持有该轴（转向/换道/死亡）**或玩家已脱离该轴**。
    // ⚠ 「玩家脱离即关」是 §2.1 的硬口径（“位移回归不自动重开”）：脱离后再走回来**不算**
    // 续窗，要重新被击中才开新窗——否则站回轴上可无限续长度，税基被行为刷出来。
    if (!playerOnLane(world, s.lane) || !sourceHeldLane(world, s.sourceId, s.lane)) {
      slots.splice(i, 1)
      continue
    }
    if (!exempt) n++
  }
  return n
}

/**
 * 玩家中心到**最近存活敌车**中心的欧氏距离（px）；无存活敌车/玩家不在场 ⇒ `ENEMY_DIST_SENTINEL`。
 * metrics v10 的 `enemyDist` 列（每行采样，与 `pickupDist` 同族）。
 *
 * 「存活」含未激活车吗？**不含**（`spawnTimer > 0` 不算）：与 `threatLaneSources` /
 * `alignedEnemyCount` 同规——出生保护期的车还不是暴露源；否则 `nearEnemy4Ticks` 会把
 * 「刚出生在玩家旁边」算成近敌 tick。
 *
 * 纯函数、零分配、不读 rng、不写 World。
 */
export function nearestEnemyDistPx(world: World): number {
  const p = world.player
  if (!p || !p.alive) return ENEMY_DIST_SENTINEL
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  let best = ENEMY_DIST_SENTINEL
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const dx = t.x + t.w / 2 - pcx
    const dy = t.y + t.h / 2 - pcy
    const d = Math.sqrt(dx * dx + dy * dy)
    if (best < 0 || d < best) best = d
  }
  return best
}

/**
 * 「伤害成簇」两个读数（metrics v10 的 `damageBursts` / `maxDamage120`），
 * 由**扣血事件序列**（tick 升序 + 对应伤害量）一次算出：
 *   · `bursts` = 相邻两笔间隔 ≤ `DMG_BURST_TICKS` 的次数（首笔不计）；
 *   · `max120` = 任意以某笔为起点的 `DMG_BURST_TICKS` 滑窗内最大累积承伤（单笔自成窗）。
 *
 * 两个都是**单调不减**函数 ⇒ Φ 逐行差分非负（不像 tick 计数器那样「每 tick 罚款」）。
 * 零分配（不建子数组）、纯函数；两个导出器共用同一实现。
 */
export function damageClusterStats(
  ticks: readonly number[],
  amounts: readonly number[],
): { bursts: number; max120: number } {
  let bursts = 0
  let max120 = 0
  for (let i = 0; i < ticks.length; i++) {
    if (i > 0 && ticks[i] - ticks[i - 1] <= DMG_BURST_TICKS) bursts++
    let sum = 0
    for (let j = i; j < ticks.length && ticks[j] - ticks[i] <= DMG_BURST_TICKS; j++)
      sum += amounts[j]
    if (sum > max120) max120 = sum
  }
  return { bursts, max120 }
}

// ════════════════════════════════════════════════════════════════
// obs-v5：跨层公式的**唯一归家处**（plan/obs-v5-vector-gap.plan.md §2.5 / P3）
//
// 下面两类符号以前散在 `tools/sim/*`（每个导出器一份拷贝）：观测侧在 `src/nn` 里
// 要么 import 不到（`tools/` 是导入方的下游），要么只能复制第二份 = 几何事实源分叉。
// 所以：**公式搬进来，工具侧改成 re-export**（既有 import 面一行不改）。
// ════════════════════════════════════════════════════════════════

/**
 * 基地威胁半径（格）：`basePressure = max(0, 1 − minDist/本值)`。
 * 数值与 `tools/sim/rl-reward.ts` 旧值同（12）；telemetry 侧照旧 re-export。
 */
export const BASE_PRESSURE_RADIUS = 12

/**
 * pickupDist 哨兵：无存活拾取（或玩家不在场）时填此值 —— 与 firstKillTick/clearTick
 * 的 -1 哨兵同构。真实曼哈顿距离恒 ≥0（同格 = 0），-1 不可能与真实值混淆。
 * 势能侧 Φ 不直接消费哨兵：公式统一 `where(pickupDist < 0, 0, pickupDist)` 归零。
 */
export const PICKUP_DIST_SENTINEL = -1

/**
 * 基地威胁压力（`tools/sim/*` 三处拷贝的**唯一实现**）。
 *
 * `enemiesOnly=false`（默认）＝ telemetry 口径，**逐字节不变**：对 `world.tanks`（含玩家）
 * 取「距 BASE_POS 左上格曼哈顿格距最近」者的 `1 − dist/半径`（截断在 [0,1]）。
 * `enemiesOnly=true` ＝ 观测口径（s38）：只认 `allegiance==='enemy'` 的车——否则玩家自己
 * 贴家会把读数顶满，把「基地被逼」读成「我在守家」（plan §2.5）。
 * 纯函数、零分配。
 */
export function sampleBasePressure(world: World, enemiesOnly = false): number {
  if (!world.tileMap.hasBase()) return 0
  let worst = 0
  for (const t of world.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue
    if (enemiesOnly && t.allegiance !== 'enemy') continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const p = 1 - dist / BASE_PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

/**
 * 当步玩家到最近**存活**拾取的曼哈顿距离（格；metrics v7）。
 * - 距离口径 = 中心格曼哈顿（`floor((x+w/2)/CELL)`），与 census/telemetry 同构；
 * - 玩家不在场（`?alive` 为假/无 player）⇒ 哨兵 `PICKUP_DIST_SENTINEL`；
 * - 无存活拾取 ⇒ 哨兵（公式侧 `where` 归零）。
 * 热路径（AGENTS §14.1）：零分配 —— 只读迭代 `world.powerUps`，无 filter/map/Set。
 * 防御性 `pu.alive` 检查：dead 拾取可能在 _cleanup 前残留于数组（与 census 同口径）。
 */
export function nearestPickupDist(world: World): number {
  return nearestPickupCenter(world, _PU_CENTER)
}

/** `nearestPickupCenter` 的复用缓冲（§14.1）。 */
const _PU_CENTER = new Float64Array(2)

/**
 * 最近存活拾取的**中心格曼哈顿距**（格）＋其**像素中心**写进 `out`（长度 2）。
 *
 * 抽出来的原因（obs-v5 P3）：观测侧要的不只是距离，还要方向（s33/s34 的单位向量），
 * 而「哪个拾取算最近」的规则只能有一份——否则距离列与向量列会在换目标的那一拍分叉。
 * 玩家不在场/已死 ⇒ 哨兵 `PICKUP_DIST_SENTINEL` 且 `out` 写 0（调用方仍须判哨兵）。
 * 零分配、不读 rng、不写 World。
 */
export function nearestPickupCenter(world: World, out: Float64Array): number {
  const p = world.player
  out[0] = 0
  out[1] = 0
  if (!p || !p.alive) return PICKUP_DIST_SENTINEL
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const pcol = Math.floor(pcx / CELL)
  const prow = Math.floor(pcy / CELL)
  let best = PICKUP_DIST_SENTINEL // -1 兼作「无存活拾取」记号
  const pus = world.powerUps
  for (let i = 0; i < pus.length; i++) {
    const pu = pus[i]
    if (!pu.alive) continue
    const cx = pu.x + pu.w / 2
    const cy = pu.y + pu.h / 2
    const col = Math.floor(cx / CELL)
    const row = Math.floor(cy / CELL)
    const d = Math.abs(col - pcol) + Math.abs(row - prow)
    if (best < 0 || d < best) {
      best = d
      out[0] = cx
      out[1] = cy
    }
  }
  return best
}

/** 世界方位码（与 `alignedScan` 的 `out[2..5]` 同序：up/down/left/right）。 */
export const SIDE_UP = 0
export const SIDE_DOWN = 1
export const SIDE_LEFT = 2
export const SIDE_RIGHT = 3

/**
 * 基地侧判定（世界方位码，序同 `alignedScan` 的 `out[2..5]`：0=up 1=down 2=left 3=right）。
 * 规则（plan §2.5 冻结）：先看行（基地上方 ⇒ U；足印下方 ⇒ D），再看列（左 ⇒ L；右 ⇒ R）；
 * **落在 2×2 足印内 ⇒ -1**（坦克不可能与基地同格，属不可达域，观测侧不计入任何侧）。
 */
export function baseSideCode(col: number, row: number): number {
  if (row < BASE_POS.row) return SIDE_UP
  if (row > BASE_POS.row + 1) return SIDE_DOWN
  if (col < BASE_POS.col) return SIDE_LEFT
  if (col > BASE_POS.col + 1) return SIDE_RIGHT
  return -1
}

/** 基地 2×2 足印的像素中心（供 `alignedScan` 做基地族对齐；零分配、无 World 依赖）。 */
export const BASE_CENTER_X = (BASE_POS.col + 1) * CELL
export const BASE_CENTER_Y = (BASE_POS.row + 1) * CELL

/**
 * 沿 `dir` 的**连续可走格数**（上限 `cap`；`corr` 维的几何，plan §2.5）。
 *
 * 起点 = `canStep` 同规 `snap`（槽格对齐）；逐格步进 `CELL`，查**目的地** `TANK` 矩形是否
 * 出界/撞地形；首命中即停。只问**地形**（坦克不算，与 `canStep(…, false)` 同族）——
 * 一辆挡路敌车在下一拍就会走开，不是走位的结构性约束。
 *
 * ⚠ 与 `canStep` 的**扫掠并集**不同（后者为防「贴墙侧向」幽灵重叠而取当前∪目的）：
 * 坦克不可能合法重叠障碍，故格对齐后两者在 k=1 上一致（A2 的「地形版 canStep 对照」钉它）。
 * 纯函数、零分配、不读 rng、不写 World。
 */
export function corridorCells(world: World, tank: Tank, dir: Direction, cap: number): number {
  const v = DIR_VECTORS[dir]
  const gx = snap(tank.x, CELL)
  const gy = snap(tank.y, CELL)
  let k = 0
  for (let i = 1; i <= cap; i++) {
    const nx = gx + v.dx * CELL * i
    const ny = gy + v.dy * CELL * i
    if (!world.isInBounds(nx, ny, TANK, TANK)) break
    if (world.rectHitsTerrain(nx, ny, TANK, TANK)) break
    k = i
  }
  return k
}

/**
 * 玩家中心格沿 `dir` 的首个**阻弹格**距离（px）；一路无阻或出界 ⇒ -1（调用方转 1.5 哨兵）。
 *
 * 阻弹 = `TileMap.blocksBullet`（brick/steel/base 挡；水/草/冰过）——与 `laneOccluded`
 * 用**同一个**谓词。距离按「格序号 × CELL」（第 1 格 = 相邻格 = CELL px）。坦克一律忽略
 * （子弹穿车不穿墙）。纯函数、零分配。
 */
export function firstBulletBlockerPx(world: World, px: number, py: number, dir: Direction): number {
  const tileMap = world.tileMap
  const v = DIR_VECTORS[dir]
  const col0 = Math.floor(px / CELL)
  const row0 = Math.floor(py / CELL)
  for (let i = 1; i <= GRID; i++) {
    const c = col0 + v.dx * i
    const r = row0 + v.dy * i
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return -1
    if (TileMap.blocksBullet(tileMap.get(c, r))) return i * CELL
  }
  return -1
}
