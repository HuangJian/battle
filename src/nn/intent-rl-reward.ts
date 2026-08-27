/**
 * intent-rl-reward.ts — M8 意图窗口奖励表（plan/Intent-Policy-NN-Plan.md §6 + P2-5 + P1-8k3）。
 *
 * semi-MDP 奖励结构（I13）：
 *   - 决策只发生在 replan tick（replanEvery，默认 30）；动作 = 采样的意图，窗口内冻结。
 *   - 奖励按**意图窗口**累计：窗口内逐 tick 密集分量（击杀/清砖/拾取/阵亡/基地墙损）
 *     + potential shaping 求和后结算为该 intent-step 的 reward；GAE 在意图步上算
 *     （γ_step = γ_tick^Δt，ppo_intent.py）。
 *   - 切换成本：**无产出切换** → 负分（治意图摇摆；配合注入的"意图时长"特征形成可学承诺）。
 *
 * 8 类"每类一张表"的落地（§6 P2-7 量纲平衡）：密集分量对全部意图生效，但每类意图的
 * 收入自然与语义对齐（HUNT/INTERCEPT 吃击杀、CLEAR 吃清砖、PICKUP 吃拾取、
 * RETURN_DEFENSE/HOLD_LANE/CRUISE 吃 potential shaping 的守家梯度）——HUNT 击杀密集
 * vs RETURN_DEFENSE 防守稀疏的量纲失衡由 shaping 的**非饱和即时梯度**补齐（v7 教训：
 * baseSafety 饱和零梯度）。不做逐意图乘法门控，避免给每类意图制造新的稀疏/零梯度区。
 *
 * potential shaping（P2-5）：Φ(s) = -P(s)，P = 最近敌距基地的 base pressure（0..1，
 * 按格分档，与 telemetry 口径一致）。**逐 tick 势差 F_t = Φ(s_{t+1}) − Φ(s_t)**（γ=1）：
 * 敌人逼近基地即负势差（非饱和即时惩罚），敌人被清/撤退即正势差。
 *
 * 为何 γ=1（P1-8k3 一致性）：
 *   - 半 MDP 的逐 tick 奖励在窗口内**不带折扣求和**，GAE 折扣发生在意图步（γ_step）。
 *     若逐 tick 塑形再乘 γ_tick，会引入 (γ−1)·ΣΦ 的跨窗口累积残余（实测高压长局可
 *     累积 ~+60 伪正奖励——高压持续反而得正分，与"守家"意图相反）。
 *   - γ=1 势差在窗口内精确 telescoping：窗口塑形和 ≡ Φ(窗末) − Φ(窗初)，整局塑形和
 *     ≡ Φ_T − Φ_0 ∈ [−1,1]，有界、无 farming（闭合状态循环累积恒 0，P1-8k3 断言）。
 */
import { CELL, BASE_POS } from '../constants'
import type { World } from '../game/World'

/** 逐 tick 密集分量（窗口内累加）。 */
export const INTENT_REWARD = {
  KILL: 4.0, // 击杀（HUNT/INTERCEPT 稠密收入；对全部意图生效）
  BRICK_CLEAR: 0.5, // 玩家清砖格（CLEAR 稠密收入；每格生命周期有界，天然防刷）
  PICKUP: 2.0, // 自动道具拾取（PICKUP 稠密收入）
  LIFE_LOSS: -5.0, // 玩家阵亡
  BASE_WALL_LOSS: -3.0, // 基地保护圈墙格被拆
  CLEAR_STAGE: 50.0, // 通关（终局）
  BASE_DESTROYED: -50.0, // 基地失守（终局；F3 降级口径：负终局不入 reward 缩放——意图 RL 无 gatedScore 对账）
  LIVES_EXHAUSTED: -30.0, // 命尽（终局）
  TIMEOUT: -1.0, // 超时（轻罚防挂机）
} as const

/** 切换成本（每无产出切换，预注册 #5 初值 0.05）。"无产出" = 前窗口无击杀/清砖/拾取。 */
export const SWITCH_COST = 0.05

/** 基地威胁半径（格，与 telemetry BASE_PRESSURE_RADIUS 同口径）。 */
export const PRESSURE_RADIUS = 12

/** per-tick 折扣（与 ppo.py GAMMA=0.995 一致；γ_step = γ_tick^Δt）。 */
export const GAMMA_TICK = 0.995

/**
 * base pressure P(s) ∈ [0,1]：最近敌距基地（Manhattan 格）分档衰减。
 * P = max(0, 1 - dist/RADIUS)。与 export-rl-rollout 的 sampleBasePressure 同公式
 * （取 worst——即最近敌），非饱和（距离连续）。
 */
export function basePressure(world: World): number {
  if (!world.tileMap.hasBase()) return 1
  let worst = 0
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - BASE_POS.col) + Math.abs(row - BASE_POS.row)
    const p = 1 - dist / PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

/** 势函数 Φ(s) = -P(s)。 */
export function potential(world: World): number {
  return -basePressure(world)
}

/** 单 tick potential shaping：F = Φ(s') − Φ(s)（γ=1；见模块 docstring P1-8k3）。 */
export function shapingStep(phiBefore: number, phiAfter: number): number {
  return phiAfter - phiBefore
}

/**
 * 窗口结算：把逐 tick 累加量（dense + shaping）结为意图步 reward，并按"无产出切换"
 * 附加切换成本。
 *
 * @param windowReward 窗口内已累加的 dense+shaping 求和。
 * @param switched 本窗口末意图是否与上一窗口不同。
 * @param hadOutput 窗口内是否发生过击杀/清砖/拾取（任一即"有产出"）。
 */
export function settleWindow(windowReward: number, switched: boolean, hadOutput: boolean): number {
  let r = windowReward
  if (switched && !hadOutput) r -= SWITCH_COST
  return r
}
