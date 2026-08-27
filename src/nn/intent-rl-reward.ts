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
import { INTENT_IDS, type IntentId } from '../ai/intent/vocab'

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

/**
 * potential shaping 的意图加权（2026-08-27 M8 坍缩修复，plan §6 P2-7 消融定稿）。
 *
 * 背景：M8 it22→it28 实测策略熵 0.38 → 0.006，意图分布坍缩为纯 HUNT（99.9%）。
 * 根因 = 密集分量（击杀 4.0）对**全部意图**同等生效 → 选择意图没有语义收益差 →
 * argmax 自然退化为"行为上限最宽、最容易吃击杀的类"（HUNT 白名单覆盖 God-AI 默认
 * 行为的大部分）。P2-7 预注册的"量纲平衡"在纯 shaping（×1.0）下不足以拦下坍缩。
 *
 * 修复：potential shaping（守家语义的即时梯度通道）按意图加权——基地高压时选
 * RETURN_DEFENSE/HOLD_LANE 的压力减免收益放大（非饱和、无 sparse 死区；shaping 每 tick
 * 都在跑，加权只是幅度差），PPO 因此学到"何时守、何时攻"而非"永远攻"。
 *
 * telescoping 一致性（P1-8k3）保持：同一意图窗口内 shaping 仍精确 = mult × (Φ_末 − Φ_初)；
 * 加权不引入 farming（势差对 γ=1 的零和性质不变）。跨窗口切换时各窗口乘各自 mult，
 * 只改变"防守类窗口的相对价值"，不产生凭空正分。
 */
export const INTENT_SHAPING_MULT: Record<IntentId, number> = {
  INTERCEPT: 1.2, // 拦截 = 主动守家，略放大
  RETURN_DEFENSE: 1.8, // 回防/据守:主被动守家，放大最强（HUNT 坍缩的主要缺口）
  HUNT: 1.0, // 进攻基线
  HOLD_LANE: 1.6, // 走廊据守（近基地防线），放大
  CLEAR: 1.0, // 清障与守家语义无关
  PICKUP: 1.0, // 拾取
  CRUISE: 1.3, // 巡航含收尾守势，温和放大
  ESCAPE: 1.0, // reflex-only 掩码，不进训练（占位值）
}

/** 按意图索引取 shaping 权重（vocab 顺序 = INTENT_IDS，与掩码/标签同源）。 */
export function shapingMult(idx: number): number {
  const id = INTENT_IDS[idx]
  return id ? INTENT_SHAPING_MULT[id] : 1.0
}

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
