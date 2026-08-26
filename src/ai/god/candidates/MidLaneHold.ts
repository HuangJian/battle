// candidates/MidLaneHold.ts — the midLaneHold candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction } from '../../../constants'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'

import { manhattan } from '../../../utils/helpers'
import {
  enemyNearLaneImpl,
  findParryHoldCellImpl,
  laneColumnOpenToBaseImpl,
  laneShellInColumnImpl,
} from '../PathCarve'

export function evalMidLaneHold(self: GodAIInput, ctx: DecisionContext): boolean {
  const { p, pcx, pcy, onCooldown } = ctx
  const prm = self.params
  if (prm.midLaneHold <= 0 || !self.hasBase || self.aggressive) return false
  const pc = self.playerCell()
  // 上半区才锚定 — 永不在底部迷宫把玩家拽去中路（§162 出袋/§163 归档教训）。
  if (pc.row > prm.midLaneHoldMaxRow) return false
  // 列内有钢/水 → 敌弹到不了基地，对消无意义（S13 式钢防关直接跳过）。
  if (!laneColumnOpenToBaseImpl(self)) return false
  const hold = findParryHoldCellImpl(self, pc)
  if (!hold) return false
  const dist = manhattan(hold.col, hold.row, pc.col, pc.row)
  // 中路繁忙：列内有向下敌弹（§163 laneShellInColumnImpl — 真实凿穿信号），
  // 或敌人临近基地列（凿墙者即将到达）。两者皆无 = 中路无威胁 → 放行。
  const busy = laneShellInColumnImpl(self) || enemyNearLaneImpl(self, prm.midLaneHoldEnemyDist)
  if (dist === 0) {
    // 已在对消格上：无威胁则放行 hunt（击杀边路），有威胁则驻守向上对消。
    // 注意只允许 dist===0 — dist≤range(1) 时玩家还在邻格，强制面朝上会
    // 冻结在 (11,6) 永远进不了 (12,6)（§164 探针实测 stageclear→gameover）。
    if (!busy) return false
    const dir: Direction = 'up'
    self._moveDir = p.dir === dir ? null : dir
    self._fire = !onCooldown && (laneShellInColumnImpl(self) || self.shouldFireInDir(pcx, pcy, dir))
    recordBranch(self, 'midLaneHold')
    return true
  }
  if (!busy) return false
  // 前往对消格（findParryHoldCellImpl 已保证走廊可达 — 顶部广场不打砖）。
  self._moveDir = self.navigateTowards(hold)
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
  recordBranch(self, 'midLaneHold')
  return true
}

/**
 * §164 中路列旁主动驻守 (proactive mid-lane flank hold) — 用户需求
 * 2026-08-06：§162 出袋后玩家优先走中路走廊（而非左侧），在列旁持枪对消。
 * 基地列无钢防时，顶部广场是基地凿穿弹的必经之路：本候选在玩家处于地图
 * 上半区（row ≤ midLaneHoldMaxRow）且中路繁忙（列内有敌弹，或敌人临近基地
 * 列）时，导航到/驻守基地列旁的对消格（pcx 在列 x 范围 ±BULLET 内、可站、
 * 走廊可达——findParryHoldCellImpl），面朝上开火对消。中路无威胁时
 * return false 放行 hunt/engage（击杀边路游荡敌人）；已在对消格且无威胁时
 * 同样放行（不钉子户）。
 *
 * 权重 220：carvePath(250) 之下、hunt(200) 之上 — 覆盖 hunt 的盲走，低于
 * 一切战斗/道具/瞭望格/开路候选；DODGE/ENGAGE/DEFENSE_INTERCEPT 全部高于
 * 它，危险时正常接战。默认 midLaneHold=0 OFF（byte-identical）。
 */

export const MID_LANE_HOLD: Candidate = {
  id: 'midLaneHold',
  weight: ACTION_WEIGHTS.midLaneHold,
  evaluate: evalMidLaneHold,
}
