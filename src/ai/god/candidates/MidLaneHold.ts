// candidates/MidLaneHold.ts — the midLaneHold candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'

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
    self.branchCounts.midLaneHold++
    self._lastBranch = 'midLaneHold'
    return true
  }
  if (!busy) return false
  // 前往对消格（findParryHoldCellImpl 已保证走廊可达 — 顶部广场不打砖）。
  self._moveDir = self.navigateTowards(hold)
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir ?? p.dir)
  self.branchCounts.midLaneHold++
  self._lastBranch = 'midLaneHold'
  return true
}
