/**
 * danger-metrics.ts —— 「危险暴露」的共享判定（metrics v8 的 idx41–44）。
 *
 * 为什么存在：41 列指标里没有任何一列能表达「当前有多危险」（audit §6.1：
 * 有 `playerLevel` 却没有 hp，有 `pickupDist` 却没有威胁）——观测侧看得到
 * （obs s19 hp、nearestEnemyDist），奖励侧用不到，于是生存只能事后罚款。
 * 本模块给出三个可落盘的判定，供两个导出器（export-rl-rollout / export-eval-game）
 * **共用同一实现**，避免「training 侧说危险、eval 侧说不危险」的口径分叉。
 *
 * 三个判定：
 *   `playerHpRatio(world)`      —— hp/maxHp（clamp01；与 obs s19 同源）
 *   `inThreatLane(world)`       —— 是否站在敌方弹道/炮口线上（见下）
 *   以及 `DANGER_HP_THRESHOLD`  —— 残血阈值 0.4（与 `src/nn/goal-mask.ts:154` 同源）
 *
 * `inThreatLane` 口径（2026-09-24 冻结，参数与 `src/nn/dodge-l0.ts` 同值 —— 同一反应半径）：
 *   ① 敌方**子弹**：与玩家同轴（中心距 < 0.75 格）、逼近、距离 ≤ 6 格；或
 *   ② 敌方**坦克**：与玩家同轴（同 ±0.75 格带）、`dir` 朝向玩家、距离 ≤ 6 格。
 *   已知简化：**不判墙体遮挡**。c20 阶梯 4 关是「全空场 + 钢边」
 *   （audit §7.3「场地无掩体」），该形态下遮挡不存在；用于其它地形课程前必须重估。
 *   镜（**未**收敛，改动需成对）：`tools/sim/simulation-runner.ts::countIncomingThreats`
 *   （只算弹、无半径）、`src/nn/dodge-l0.ts::laneScore`（只算弹、含半径）、
 *   `tools/diag/decision-trace.ts::countIncomingThreats`。本模块是 metrics 口径的权威；
 *   那三处服务于各自既有行为（L0 覆盖/GUI 诊断），本计划不动它们（改动会动已发布行为）。
 *
 * 纯函数、零分配、不读 rng、不写 World（AGENTS §2.3 / §14.1–14.2）。
 */
import { CELL } from '../constants'
import type { World } from '../game/World'

/** 触发半径（格）：与 `src/nn/dodge-l0.ts::DODGE_RADIUS` 同值（6 格）。 */
export const THREAT_RADIUS_CELLS = 6
/** 对齐带宽：±0.75 格（与 dodge-l0 / simulation-runner 的 countIncomingThreats 同款）。 */
export const THREAT_ALIGN_BAND = CELL * 0.75
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
 * 站在敌方弹道/炮口线上的威胁源数量（0 = 不在弹道上）。
 * 返回计数而非布尔：便于测试与诊断区分「刚进弹道」与「被交叉火力夹住」。
 */
export function threatLaneSources(world: World): number {
  const p = world.player
  if (!p || !p.alive) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const radius = THREAT_RADIUS_CELLS * CELL
  let n = 0

  // ① 敌方子弹：同轴 + 逼近 + 半径内。
  const bullets = world.bullets
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical
      ? Math.abs(bx - pcx) < THREAT_ALIGN_BAND
      : Math.abs(by - pcy) < THREAT_ALIGN_BAND
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && by < pcy) ||
      (b.dir === 'up' && by > pcy) ||
      (b.dir === 'right' && bx < pcx) ||
      (b.dir === 'left' && bx > pcx)
    if (!approaching) continue
    const dist = vertical ? Math.abs(by - pcy) : Math.abs(bx - pcx)
    if (dist <= radius) n++
  }

  // ② 敌方坦克：同轴 + 炮口朝向玩家 + 半径内（「站在别人炮口线上」）。
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const vertical = t.dir === 'up' || t.dir === 'down'
    const aligned = vertical
      ? Math.abs(tx - pcx) < THREAT_ALIGN_BAND
      : Math.abs(ty - pcy) < THREAT_ALIGN_BAND
    if (!aligned) continue
    const facing =
      (t.dir === 'down' && ty < pcy) ||
      (t.dir === 'up' && ty > pcy) ||
      (t.dir === 'right' && tx < pcx) ||
      (t.dir === 'left' && tx > pcx)
    if (!facing) continue
    const dist = vertical ? Math.abs(ty - pcy) : Math.abs(tx - pcx)
    if (dist <= radius) n++
  }
  return n
}

/** 是否在敌方弹道/炮口线上（`threatLaneSources > 0`）。 */
export function inThreatLane(world: World): boolean {
  return threatLaneSources(world) > 0
}
