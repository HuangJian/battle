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
  /** 击杀凸性指数（p>1 时 Convex 递增，默认 1=线性，向后兼容）。 */
  p?: number
  /** 每个 powerup_collected 的步内分（默认 0）。 */
  wLoot?: number
  /** 按命损稠密扣分：每 HP × wDmg2（默认 0）；pool 血池下让非致命扣血有梯度。 */
  wDmg2?: number
  /** 每次命中敌方 +wHit（含致死命中；对齐弹道稠密信号）。 */
  wHit?: number
  /** 每次打偏 −wMiss（开火未命中；省 cooldown、防乱开火）。 */
  wMiss?: number
  /** 停滞惩罚：连续「原地 + 未命中」超阈值后每 tick -wStuck。 */
  wStuck?: number
}

/**
 * 预注册三臂（§3.4：只扫一次，禁止第四组）。量纲锚：一次击杀 = 1.0；
 * 终局全歼 ≈ 2 次击杀（S1 只 1 个敌人 ⇒ 终局项是主要回报，防"杀完就死"）。
 */
export const TOY_REWARD_ARMS: Record<string, ToyRewardArm> = {
  kill: { name: 'kill', wKill: 1.0, wDmg: 0.15, wAlive: 0.0005, wClear: 2.0, wDeath: 0.5 },
  // kill2（2026-08-30，卡 S1 追加预算）：wAlive→0。诊断依据（docs/goal-nn.progress.md
  // §10）：A4 贪心评估败局非"冻死"（cellsVisited 9.8、22.7 发/局）而是"上推+扫射
  // 从不追踪敌人"的固定套路——wAlive 让"原地存活骚扰"每局稳拿 0.6，锚死了
  // "转向追杀"的高方差路径（argmax 永不转向 ⇒ 贪心 26.7% << 采样 50%）。存活
  // 压力由 wDmg（被命中惩罚）承担。
  kill2: { name: 'kill2', wKill: 1.0, wDmg: 0.15, wAlive: 0, wClear: 2.0, wDeath: 0.5 },
  balanced: { name: 'balanced', wKill: 1.0, wDmg: 0.35, wAlive: 0.001, wClear: 2.0, wDeath: 1.0 },
  survival: { name: 'survival', wKill: 0.5, wDmg: 0.5, wAlive: 0.002, wClear: 2.0, wDeath: 1.5 },
  // dodge-mix（plan/dodge-item-curriculum.md §2）：递增击杀 + 按命损扣分 + 低拾取分。
  // 一命三命下均可，但在一命时 wDmg2 是让非致命扣血有梯度的关键杠杆。
  'dodge-mix': {
    name: 'dodge-mix',
    wKill: 1.0,
    p: 1.15,
    wHit: 0.2,
    wMiss: 0.063, // 标定：hitRate0=34.07%, c=23.85%, wMiss/wHit=0.313
    wDmg: 1.0,
    wDmg2: 0.01,
    wAlive: 0,
    wClear: 2.0,
    wDeath: 1.5,
    wLoot: 0.4,
    wStuck: 0.002,
  },
}

/** 各级默认臂。A2 扫描按"门指标最高者"选定后改写此表（代码即预注册记录）。 */
export const TOY_REWARD_DEFAULT_ARM: Record<ArenaLevel, string> = {
  S1: 'kill2',
  S2: 'kill',
  S3: 'kill',
  S3H: 'kill',
  S4a: 'kill',
  'S-Dodge': 'dodge-mix',
}

/** 势函数输入计数器（事件流 + World 计数，零埋点，§7.2）。 */
export interface ToyCounters {
  kills: number
  playerHits: number
  /** 非致命扣血累计（player_damage 事件 damage 累计，§2.4）。 */
  playerDamageTaken?: number
  /** 拾取道具数（powerup_collected 事件累计）。 */
  powerUpsCollected?: number
  /** 命中敌方累计（enemy_hit 事件数，含致死命中）。 */
  hits?: number
  /** 开火累计（bullet_fired by player）。 */
  shots?: number
  /** 连续「原地 + 未命中」tick 数。 */
  stuckTicks?: number
}

/** 停滞容忍：连续「原地 + 未命中」超过该 tick 数才开始扣势（3s @60fps）。 */
export const STUCK_THRESHOLD = 180

/** 势 Φ：窗口势差即稠密奖励。 */
export function toyPotential(c: ToyCounters, ticks: number, arm: ToyRewardArm): number {
  let v = arm.wKill * (c.kills + 1) ** (arm.p ?? 1) - arm.wDmg * c.playerHits + arm.wAlive * ticks
  if (arm.wDmg2) v -= arm.wDmg2 * (c.playerDamageTaken ?? 0)
  if (arm.wLoot) v += arm.wLoot * (c.powerUpsCollected ?? 0)
  // 命中率激励（线性化）：命中奖励 + 打偏惩罚 = (wHit+wMiss)·hits − wMiss·shots
  if (arm.wHit || arm.wMiss)
    v += (arm.wHit ?? 0) * (c.hits ?? 0) - (arm.wMiss ?? 0) * ((c.shots ?? 0) - (c.hits ?? 0))
  if (arm.wStuck) v -= arm.wStuck * Math.max(0, (c.stuckTicks ?? 0) - STUCK_THRESHOLD)
  return v
}

/** 终局奖励（并入最后一个窗口的 reward）。timeout = 0。 */
export function toyTerminal(outcome: string, arm: ToyRewardArm): number {
  if (outcome === 'stage_clear') return arm.wClear
  if (outcome === 'lives_exhausted') return -arm.wDeath
  return 0
}
