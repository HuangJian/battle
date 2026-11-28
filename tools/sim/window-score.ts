/**
 * window-score.ts — T6 反事实窗口分的**单一实现**（P′-2a-B oracle-search 复用）。
 *
 * 公式与权重原样来自 `export-counterfactual-goals.ts` §11.3（目标空间重建立项时的
 * 工程初值，不是拟合值）：kills / deaths / 基地环墙损失 / 基地压力变化 / 存活敌数变化。
 * 两个消费方（T6 语料导出、P′-2a-B 意图搜索）必须逐字节同式——分开写第二份，
 * 训练标签与搜索裁决就会悄悄分叉。
 */
import type { World } from '../../src/game/World'

export const W_KILL = 4.0
export const W_DEATH = -5.0
export const W_WALL = -3.0
export const W_PRESSURE = 10.0
export const W_ENEMIES = 2.0

/**
 * 窗口分：正 = 对玩家有利。
 * `pressureGain = phi0 − phi1`（`basePressure` 下降为正）；`enemyDrop = enemies0 − enemies1`。
 * 表达式顺序与 T6 原实现一致（浮点结果逐字节不变）。
 */
export function counterfactualWindowScore(
  kills: number,
  deaths: number,
  wallLoss: number,
  pressureGain: number,
  enemyDrop: number,
): number {
  return (
    W_KILL * kills +
    W_DEATH * deaths +
    W_WALL * wallLoss +
    W_PRESSURE * pressureGain +
    W_ENEMIES * enemyDrop
  )
}

/** 存活、已出场（spawnTimer≤0）的敌人（extra 之外）计数。 */
export function countEnemies(world: World): number {
  let n = 0
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && !t.isPlayer && t.spawnTimer <= 0) n++
  }
  return n
}
