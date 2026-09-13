/**
 * stuck-detect.ts —— obs v3 sN4 / reward stuckTicks 的**共享判定**（dsf A4 同源）。
 *
 * 语义（与导出器 tools/sim/export-rl-rollout.ts 的历史实现逐一对齐）：
 *   停滞 tick = 玩家存活 且 玩家中心 cell 不变 且 本 tick 未命中敌车。
 *   - 中心 cell 锚 = (x + 16) / CELL（TANK/2 = 16，格锚点 plan §1.1）。
 *   - 「命中敌车」= SimulationCombat push 的 `enemy_hit` 事件（含致死那一枪——
 *     击杀枪同时推 enemy_hit + tank_destroyed，命中即有意义非重复记账）。
 *   - prev=null（首 tick / 快照恢复后）⇒ 非停滞（导出器初值 {-1,-1} 同效）。
 *
 * 为什么共享：reward 的 stuckTicks 曾由导出器自算、obs sN4 由 World 字段供给——
 * 两处各写各的判定就会「obs 说没卡、reward 说卡」。唯一实现 + 序列 golden
 * （tests/stuck-detect.test.ts）锁死同源性；tickHash **不含**本字段（它是已哈希
 * 状态 player 位置 + enemy_hit 事件的派生量，入哈希只有 golden 重铸成本、零检出
 * 增益——对 ms F3 字面的有意偏离，理由见 roadmap §3-N1）。
 *
 * 纯函数，不读 rng，不写 World（§2.1/§2.3）。
 */
import { CELL } from '../constants'
import type { Tank } from '../types'

export interface StuckCell {
  col: number
  row: number
}

/** 玩家中心 cell（32×32 车身 → 16px 格锚点）。玩家不存在返回 null。 */
export function playerCenterCell(t: Tank | null): StuckCell | null {
  if (!t) return null
  return { col: Math.floor((t.x + 16) / CELL), row: Math.floor((t.y + 16) / CELL) }
}

/** 停滞判定核心：alive 且中心 cell 不变且本 tick 未命中敌车。 */
export function isStuckTick(
  alive: boolean,
  cur: StuckCell | null,
  prev: StuckCell | null,
  hitEnemyThisTick: boolean,
): boolean {
  if (!alive || !cur || !prev) return false
  return cur.col === prev.col && cur.row === prev.row && !hitEnemyThisTick
}

/**
 * 有状态步进：给 World 侧维护用（Simulation.updatePlaying 末尾固定调用点）。
 * 返回新的 stuckTicks；调用方负责把 prevStuckCell 更新为 cur、清命中标记。
 */
export function nextStuckTicks(
  stuckTicks: number,
  alive: boolean,
  cur: StuckCell | null,
  prev: StuckCell | null,
  hitEnemyThisTick: boolean,
): number {
  return isStuckTick(alive, cur, prev, hitEnemyThisTick) ? stuckTicks + 1 : 0
}
