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
import { BULLET, CELL, GRID, TANK } from '../constants'
import type { Bullet } from '../types'
import type { World } from '../game/World'
import { TileMap } from '../game/TileMap'
import { BULLET_LANE_MISS, bulletInFrontDist, bulletLaneDist } from '../utils/helpers'
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
 * 「被包围」共享扫描（不分配对象；一个循环两个读数）：
 *   · `wantWeight = false` ⇒ 返回对齐敌数（`alignedEnemyCount` 语义，逐位不变）；
 *   · `wantWeight = true`  ⇒ 返回 Σ max(0,K_ENCL−d)（n ≥ 2 才非零）。
 * 谓词（19px 带 + 轴向无遮挡 + 已激活 + 存活）本体只此一份。
 */
function alignedEnemyScan(world: World, wantWeight: boolean): number {
  const p = world.player
  if (!p || !p.alive) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const tileMap = world.tileMap
  // 玩家身体的像素精确格跨度（遮挡判据；与 threatLaneSources 同规）。
  const pColLow = Math.floor(p.x / CELL)
  const pColHigh = Math.floor((p.x + p.w - 1) / CELL)
  const pRowLow = Math.floor(p.y / CELL)
  const pRowHigh = Math.floor((p.y + p.h - 1) / CELL)
  let n = 0
  let w = 0
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const sameCol = Math.abs(tx - pcx) < THREAT_ALIGN_BAND_PX
    const sameRow = Math.abs(ty - pcy) < THREAT_ALIGN_BAND_PX
    if (!sameCol && !sameRow) continue
    const occ = sameCol
      ? laneOccluded(tileMap, Math.floor(tx / CELL), Math.floor(ty / CELL), true, pRowLow, pRowHigh)
      : laneOccluded(
          tileMap,
          Math.floor(tx / CELL),
          Math.floor(ty / CELL),
          false,
          pColLow,
          pColHigh,
        )
    if (occ) continue
    n++
    if (wantWeight) {
      const d = Math.max(
        Math.abs(Math.floor(tx / CELL) - Math.floor(pcx / CELL)),
        Math.abs(Math.floor(ty / CELL) - Math.floor(pcy / CELL)),
      )
      if (d < K_ENCL) w += K_ENCL - d
    }
  }
  return wantWeight ? (n >= 2 ? w : 0) : n
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
  const col = Math.floor((p.x + p.w / 2) / CELL)
  const row = Math.floor((p.y + p.h / 2) / CELL)
  const a = CORNER_ANCHOR_MARGIN
  const b = GRID - 4
  let d = Math.max(Math.abs(col - a), Math.abs(row - a))
  let dd = Math.max(Math.abs(col - b), Math.abs(row - a))
  if (dd < d) d = dd
  dd = Math.max(Math.abs(col - b), Math.abs(row - b))
  if (dd < d) d = dd
  dd = Math.max(Math.abs(col - a), Math.abs(row - b))
  if (dd < d) d = dd
  return d < K_CORNER ? K_CORNER - d : 0
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
