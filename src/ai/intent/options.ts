/**
 * intent/options.ts — P′-4a option 槽位契约（3 option × pathMode 联合槽）。
 *
 * 预注册（plan/hier-options-prereg.v1.json `p4` / plan/nn-hierarchical-options.plan.md
 * §4.1）：选择层动作 = `HUNT / INTERCEPT_BASE / HOLD`，pathMode 为嵌套修饰维度；
 * 第一版只保留 `pathMode ∈ {direct, break}`（P′-2a2 实测 break 独活，
 * targetKind/target/param 回到执行器规则）。联合槽 = 6：
 *
 *   0 HUNT_direct          1 HUNT_break
 *   2 INTERCEPT_BASE_direct 3 INTERCEPT_BASE_break
 *   4 HOLD_direct          5 HOLD_break（mask=0：L0 禁止为拆墙走向基地锚点）
 *
 * 8 意图标签 → 槽（预注册 p4 映射 + pathMode）：CLEAR 是唯一 break 标签源
 * （清障 = 拆砖），其余按 option3 映射走 direct。HOLD_break 恒无标签。
 *
 * target 规则（P′-4a 不学 target 头，计划 §9.7「target 头删除/辅助」）：
 *   HUNT            最近敌（NNOption 逐 tick 解析）→ 无敌则最近道具 → 都无则纯委托
 *   INTERCEPT_BASE  基地受威胁且有敌 → 最近敌；否则基地锚点（环外最近可站格）
 *   HOLD            基地锚点；无锚点则纯委托
 * pathMode=break 只对 **enemy** 目标生效（与 P′-2a2 L3 同规则）；anchor/powerup 目标
 * 一律 direct，HOLD_break 因此天然退化为 direct（仍整槽掩码，防策略浪费探索）。
 *
 * 本文件是槽位/映射/掩码的唯一实现：executor、rollout 导出、m1-eval 与测试都从这里取，
 * 禁止第二份表（与 vocab.ts 同一纪律）。
 */
import { BASE_POS, CELL } from '../../constants'
import type { World } from '../../game/World'
import type { NNOptionCommand, NNOptionTarget } from '../god/NNOption'
import { INTENT_IDS, type IntentId } from './vocab'

export const OPTION_IDS = ['HUNT', 'INTERCEPT_BASE', 'HOLD'] as const
export type OptionId = (typeof OPTION_IDS)[number]

export type OptionPathMode = 'direct' | 'break'

export interface OptionSlot {
  id: string
  option: OptionId
  pathMode: OptionPathMode
}

/** 联合槽表（顺序 = 训练/推理/日志的动作 id，禁止重排）。 */
export const OPTION_SLOTS: readonly OptionSlot[] = [
  { id: 'HUNT_direct', option: 'HUNT', pathMode: 'direct' },
  { id: 'HUNT_break', option: 'HUNT', pathMode: 'break' },
  { id: 'INTERCEPT_BASE_direct', option: 'INTERCEPT_BASE', pathMode: 'direct' },
  { id: 'INTERCEPT_BASE_break', option: 'INTERCEPT_BASE', pathMode: 'break' },
  { id: 'HOLD_direct', option: 'HOLD', pathMode: 'direct' },
  { id: 'HOLD_break', option: 'HOLD', pathMode: 'break' },
]

export const OPTION_DIM = OPTION_SLOTS.length // 6
/** 注入维 = 上一槽 one-hot(6) + 槽龄(1)。 */
export const OPTION_INJECT_DIM = OPTION_DIM + 1 // 7
export const OPTION_SLOT_IDS = OPTION_SLOTS.map((s) => s.id)
/** 采样/训练掩码（1 = 合法；HOLD_break 整槽禁用）。 */
export const OPTION_SLOT_MASK: readonly number[] = [1, 1, 1, 1, 1, 0]

export function isOptionSlotValid(slot: number): boolean {
  return slot >= 0 && slot < OPTION_DIM && OPTION_SLOT_MASK[slot] === 1
}

/** 槽的 option 展开到 God-AI 意图子链（纯委托 / 目标不可达时的回退链）。 */
export const OPTION_EXEC_INTENT: Record<OptionId, IntentId> = {
  HUNT: 'HUNT',
  INTERCEPT_BASE: 'INTERCEPT',
  HOLD: 'HOLD_LANE',
}

/**
 * 8 意图 → 联合槽（顺序 = vocab.INTENT_IDS：INTERCEPT / RETURN_DEFENSE / HUNT /
 * HOLD_LANE / CLEAR / PICKUP / CRUISE / ESCAPE）。与预注册 option3 映射一致：
 *   HUNT 0；CLEAR 1（break）；INTERCEPT/RETURN_DEFENSE 2；HOLD_LANE/ESCAPE 4。
 */
export const INTENT_TO_OPTION_SLOT: Record<IntentId, number> = {
  INTERCEPT: 2,
  RETURN_DEFENSE: 2,
  HUNT: 0,
  HOLD_LANE: 4,
  CLEAR: 1,
  PICKUP: 0,
  CRUISE: 0,
  ESCAPE: 4,
}

export function intentToOptionSlot(intent: IntentId): number {
  return INTENT_TO_OPTION_SLOT[intent]
}

/**
 * potential shaping 的槽级权重（镜像 intent-rl-reward 的 INTENT_SHAPING_MULT：
 * 防守类放大，反制 HUNT 单点坍缩）。槽 1（HUNT_break，源自 CLEAR）与 HUNT 同为 1.0。
 */
export const OPTION_SLOT_SHAPING_MULT: readonly number[] = [1.0, 1.0, 1.2, 1.2, 1.6, 1.0]

export function optionSlotShapingMult(slot: number): number {
  return OPTION_SLOT_SHAPING_MULT[slot] ?? 1.0
}

/** 基地 2×2 外侧的 6 个候选锚点（环外一格；上/左/右顺序 = tie-break）。 */
export function baseAnchorCells(): NNOptionTarget[] {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  return [
    { col: bc, row: br - 2 },
    { col: bc + 1, row: br - 2 },
    { col: bc - 2, row: br },
    { col: bc - 2, row: br + 1 },
    { col: bc + 3, row: br },
    { col: bc + 3, row: br + 1 },
  ]
}

/** 坦克可站立格（与 God-AI StrategyPlanner 同口径：brick/steel/water/base 不可站）。 */
function standable(world: World, col: number, row: number): boolean {
  const t = world.tileMap.get(col, row)
  return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
}

/** 玩家中心格（与 NNOption.resolveTarget 的目标格口径一致）。 */
export function playerCellOf(world: World): NNOptionTarget {
  const p = world.player
  if (!p) return { col: BASE_POS.col, row: BASE_POS.row - 2 }
  return {
    col: Math.floor((p.x + p.w / 2) / CELL),
    row: Math.floor((p.y + p.h / 2) / CELL),
  }
}

/** 最近的基地锚点（环外一格、可站立）；全被占/不可站 ⇒ null。 */
export function nearestBaseAnchor(world: World): NNOptionTarget | null {
  const pc = playerCellOf(world)
  let best: NNOptionTarget | null = null
  let bestD = Infinity
  for (const c of baseAnchorCells()) {
    if (!standable(world, c.col, c.row)) continue
    const d = Math.abs(c.col - pc.col) + Math.abs(c.row - pc.row)
    if (d < bestD) {
      bestD = d
      best = c
    }
  }
  return best
}

export function hasLiveEnemy(world: World): boolean {
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && !t.isPlayer && t.spawnTimer <= 0) return true
  }
  return false
}

export function hasLivePowerUp(world: World): boolean {
  const pus = world.powerUps
  for (let i = 0; i < pus.length; i++) if (pus[i].alive) return true
  return false
}

/** `buildOptionCommand` 的纯输入（测试可脱离 World 构造）。 */
export interface OptionCommandContext {
  hasEnemy: boolean
  hasPowerUp: boolean
  threatened: boolean
  anchor: NNOptionTarget | null
}

/**
 * 槽 → E2′ `nn-option` 注入命令（纯函数，决策时调用一次，非热路径）。
 * 未知/掩码槽回退到槽 0（HUNT_direct）：调用方的 mask 已经保证不会发生，
 * 这里只保证行为有定义、不抛错打断 rollout。
 */
export function buildOptionCommandFromContext(
  slot: number,
  ctx: OptionCommandContext,
): NNOptionCommand {
  const def = OPTION_SLOTS[slot] ?? OPTION_SLOTS[0]
  const base: NNOptionCommand = {
    option: OPTION_EXEC_INTENT[def.option],
    targetKind: 'none',
    target: null,
    pathMode: 'direct',
    param: 0,
  }
  let targetKind: NNOptionCommand['targetKind'] = 'none'
  let target: NNOptionTarget | null = null
  if (def.option === 'HUNT') {
    if (ctx.hasEnemy) targetKind = 'enemy'
    else if (ctx.hasPowerUp) targetKind = 'powerup'
  } else if (def.option === 'INTERCEPT_BASE') {
    if (ctx.threatened && ctx.hasEnemy) targetKind = 'enemy'
    else if (ctx.anchor) {
      targetKind = 'anchor'
      target = ctx.anchor
    }
  } else if (ctx.anchor) {
    // HOLD：守基地锚点（break 在掩码里，且非 enemy 目标强制 direct）。
    targetKind = 'anchor'
    target = ctx.anchor
  }
  // break 只对 enemy 目标生效（L0：不为 anchor/powerup 拆墙）。
  const pathMode = def.pathMode === 'break' && targetKind === 'enemy' ? 'break' : 'direct'
  return { ...base, targetKind, target, pathMode }
}

/** World + 威胁标志 → 注入命令（executor 每 replan 调用一次）。 */
export function buildOptionCommand(
  slot: number,
  world: World,
  threatened: boolean,
): NNOptionCommand {
  return buildOptionCommandFromContext(slot, {
    hasEnemy: hasLiveEnemy(world),
    hasPowerUp: hasLivePowerUp(world),
    threatened,
    anchor: nearestBaseAnchor(world),
  })
}

/** 调试/日志用：槽 id（越界返回 '?'）。 */
export function optionSlotId(slot: number): string {
  return OPTION_SLOT_IDS[slot] ?? '?'
}

/** 8 意图下标 → 槽（导出器/测试用；越界返回 0）。 */
export function intentIndexToOptionSlot(idx: number): number {
  const id = INTENT_IDS[idx]
  return id ? INTENT_TO_OPTION_SLOT[id] : 0
}
