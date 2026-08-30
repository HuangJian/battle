/**
 * rl-reward-toy.ts — 玩具场课程奖励（plan/goal-nn-action.md §3.4 / 卡 A2）。
 *
 * R = w_kill·击杀 − w_dmg·被命中 + w_alive·存活（每 tick 小常数）
 * 终局：全歼 +w_clear；阵亡终局（lives_exhausted）−w_death
 *
 * 实现口径（记录于 docs/goal-nn.progress.md §A2）：
 *   * 势塑形（Ng et al.）：Φ = w_kill·kills − w_dmg·playerHits + w_alive·t，
 *     窗口势差即稠密奖励；终局奖励并入最后窗口。Σr = Φ_end − Φ_0 + 终局项。
 *   * §3.4 的 w_hit（命中）与 w_death（阵亡）合并为一个 w_dmg 项，记在
 *     `player_hit` 事件上——事件流没有"敌方被命中未死"信号（§7.2 预注册代理），
 *     而 hard 下玩家被命中 ≈ 阵亡（player_hit 在死亡与星盾消耗时触发），
 *     两个分开的惩罚项在此口径下不可分且会双重计费。
 *   * **不污染** `tools/sim/rl-reward.ts` 的守家 Φ 常量（其在无基地场无意义）；
 *     S4 奖励（玩具项 + baseIntegrity/baseSafety Φ）在卡 A7 出口预注册后另加。
 *
 * 本模块放在 `src/nn/` 下：导出器在 dist 节点上执行，奖励定义必须进 codeHash
 * 覆盖集（`src/nn/**`），否则权重臂改了节点不知情。
 */

import type { ArenaLevel } from './arena-ladder'

export interface ToyRewardArm {
  name: string
  /** 击杀（tank_destroyed by=player）。 */
  wKill: number
  /** 被命中（player_hit 事件；hard 口径含阵亡与星盾消耗）。 */
  wDmg: number
  /** 存活：每 tick 小常数（1200 tick 局满额 0.6–2.4，与一杀同量级偏小）。 */
  wAlive: number
  /** 终局全歼（stage_clear）。 */
  wClear: number
  /** 终局阵亡（lives_exhausted）。 */
  wDeath: number
}

/**
 * 预注册三臂（§3.4：只扫一次，禁止第四组）。量纲锚：一次击杀 = 1.0；
 * 终局全歼 ≈ 2 次击杀（S1 只 1 个敌人 ⇒ 终局项是主要回报，防"杀完就死"）。
 */
export const TOY_REWARD_ARMS: Record<string, ToyRewardArm> = {
  kill: { name: 'kill', wKill: 1.0, wDmg: 0.15, wAlive: 0.0005, wClear: 2.0, wDeath: 0.5 },
  balanced: { name: 'balanced', wKill: 1.0, wDmg: 0.35, wAlive: 0.001, wClear: 2.0, wDeath: 1.0 },
  survival: { name: 'survival', wKill: 0.5, wDmg: 0.5, wAlive: 0.002, wClear: 2.0, wDeath: 1.5 },
}

/** 各级默认臂。A2 扫描按"门指标最高者"选定后改写此表（代码即预注册记录）。 */
export const TOY_REWARD_DEFAULT_ARM: Record<ArenaLevel, string> = {
  S1: 'kill',
  S2: 'kill',
  S3: 'kill',
  S3H: 'kill',
  S4a: 'kill',
}

/** 势函数输入计数器（事件流 + World 计数，零埋点，§7.2）。 */
export interface ToyCounters {
  kills: number
  playerHits: number
}

/** 势 Φ：窗口势差即稠密奖励。 */
export function toyPotential(c: ToyCounters, ticks: number, arm: ToyRewardArm): number {
  return arm.wKill * c.kills - arm.wDmg * c.playerHits + arm.wAlive * ticks
}

/** 终局奖励（并入最后一个窗口的 reward）。timeout = 0。 */
export function toyTerminal(outcome: string, arm: ToyRewardArm): number {
  if (outcome === 'stage_clear') return arm.wClear
  if (outcome === 'lives_exhausted') return -arm.wDeath
  return 0
}
