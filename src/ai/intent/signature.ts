/**
 * intent/signature.ts — M2 人像行为签名标签器（plan/Intent-Policy-NN-Plan.md §5.2）。
 *
 * 从运动/火力/与基地敌人的位置关系反推意图（纯函数）。判据 = 执行器语义的镜像，
 * 两者同源（vocab.ts 同一词表）。**宁缺勿错**：每个判据组为 AND/OR 明确，模糊帧
 * 返回 null（不喂错误意图标签——意图级错标比 move 级更毒）。
 *
 * 输出逐帧意图 → 分段规则复用 vocab.segmentIntents（同一实现，四件套同步）。
 * ESCAPE 不允许签名（对应 <200 窗口 reflex-only 裁决；人像无可靠脱险判据）。
 */
import type { Direction } from '../../constants'
import { BASE_POS } from '../../constants'

export type { IntentId } from './vocab'

/** 签名器输入的语义结构（由重放层从 world + 帧数据组装）。 */
export interface SigContext {
  /** 玩家格坐标。 */
  playerCell: { col: number; row: number }
  /** 本帧移动方向（null = 静止/转向中）。 */
  moveDir: Direction | null
  /** 玩家朝向（开火基准，与 moveDir 可不同）。 */
  facingDir: Direction
  /** 本帧是否开火。 */
  firing: boolean
  /** 最近敌格与曼哈顿距离；无存活敌 = null。 */
  nearestEnemy: { col: number; row: number; dist: number } | null
  /** 任一敌与玩家同行/列（对齐 = 存在射击线候选，地形细节不建模）。 */
  enemyAligned: boolean
  /** 朝 facingDir 的一步是砖/钢墙（CLEAR 判据）。 */
  wallAhead: boolean
  /** 敌距基地 ≤12 格（INTERCEPT 威胁语义，与 divergence-probe 同半径）。 */
  baseThreat: boolean
  /** 玩家距基地曼哈顿距离。 */
  baseDist: number
  /** 存在存活道具且距玩家 ≤4 格（PICKUP 判据）。 */
  pickupNear: boolean
}

const INTERCEPT_BASE_DIST = 12 // 玩家距基地 ≤12 才判"驻防拦截"

/**
 * 逐帧签名判定。判定顺序（避免同帧多意图歧义）：
 *   INTERCEPT（驻防拦截）> RETURN_DEFENSE（回防赶路）> HOLD_LANE（列上对消驻守）
 *   > HUNT（朝敌追击）> CLEAR（破墙）> PICKUP（顺路拾取）> CRUISE（巡航漫游）
 *   > null（模糊：静止无聚焦等——宁缺勿错）。
 */
export function signatureIntent(c: SigContext): string | null {
  // INTERCEPT：驻防态向威胁开火——敌对齐 ∧ 距基地近 ∧ 在开火。
  if (c.firing && c.enemyAligned && c.baseThreat && c.baseDist <= INTERCEPT_BASE_DIST) {
    return 'INTERCEPT'
  }
  // RETURN_DEFENSE：位移主向指向基地 + 不在驻防圈内（赶路回防）。
  if (c.baseDist > INTERCEPT_BASE_DIST && dirTowardBase(c) && !c.firing) {
    return 'RETURN_DEFENSE'
  }
  // HOLD_LANE：站桩/小幅移动 + 朝敌来路开火（对消驻守）。
  if (c.firing && c.enemyAligned && (c.moveDir === null || littleMovement(c))) {
    return 'HOLD_LANE'
  }
  // HUNT：朝最近敌移动 + 开火（追击）。
  if (c.firing && c.nearestEnemy && dirTowardEnemy(c)) {
    return 'HUNT'
  }
  // CLEAR：开火且面前是墙（清障破墙）。
  if (c.firing && c.wallAhead) {
    return 'CLEAR'
  }
  // PICKUP：不交战、往道具走（**纯拾取**）。
  // 人类"边走边打、顺路捡"的帧因 firing∧朝敌 已被 HUNT/INTERCEPT 捕获——混战顺路
  // 拾取不标 PICKUP（宁缺勿错：意图级错标比少标更毒）。故本判据 = 灵敏度下限：
  // 只捕获"道具远离敌人、专注拾取"的干净决策。
  if (!c.firing && c.pickupNear && c.moveDir !== null) {
    return 'PICKUP'
  }
  // CRUISE：移动中且无聚焦（漫游/回中场）。
  if (c.moveDir !== null) {
    return 'CRUISE'
  }
  // 静止且无以上任何判据 → 模糊帧（宁缺勿错）。
  return null
}

/** moveDir 是否指向基地方向（主轴向基地偏移）。 */
function dirTowardBase(c: SigContext): boolean {
  const pc = c.playerCell
  const dcol = BASE_POS.col - pc.col
  const drow = BASE_POS.row - pc.row
  if (Math.abs(dcol) >= Math.abs(drow)) {
    if (dcol > 0) return c.moveDir === 'right'
    if (dcol < 0) return c.moveDir === 'left'
  } else {
    if (drow > 0) return c.moveDir === 'down'
    if (drow < 0) return c.moveDir === 'up'
  }
  // 已在基地同格——不构成"回防赶路"。
  return false
}

/** moveDir 是否朝着最近敌。 */
function dirTowardEnemy(c: SigContext): boolean {
  const e = c.nearestEnemy
  if (!e) return false
  const dcol = e.col - c.playerCell.col
  const drow = e.row - c.playerCell.row
  if (Math.abs(dcol) >= Math.abs(drow)) {
    if (dcol > 0) return c.moveDir === 'right'
    if (dcol < 0) return c.moveDir === 'left'
  } else {
    if (drow > 0) return c.moveDir === 'down'
    if (drow < 0) return c.moveDir === 'up'
  }
  return false
}

/** 小幅移动：移动但连续两帧方向一致（站桩摇摆判据的代理——极简版：null 由调用方给）。 */
function littleMovement(c: SigContext): boolean {
  // 签名器为逐帧纯函数；"小幅"由重放层在相邻帧间判定后以 `moveDir` 为 null
  // 表达（站桩）。此分支仅保留语义占位。
  void c
  return false
}
