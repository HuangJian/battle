// candidates/UnifiedCandidates.ts — the unifiedCandidates candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { BASE_POS } from '../../../constants'
import { type Tank } from '../../../types'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { clearLaneFireDir, evaluateUnifiedCandidates, fireRayBlocked } from '../ActionCandidates'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { shouldFireInDirImpl } from '../FireControl'

import { manhattan } from '../../../utils/helpers'

export function evalUnifiedCandidates(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.candidateMode <= 0 || !self.hasBase || self.aggressive) return false
  const { w, p, pcx, pcy, onCooldown } = ctx
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  // kill-current target: the last selectTarget pick while still alive,
  // else the nearest enemy (the mode-0 hunt rule proxy). No selectTarget
  // call from here — it would write intent state as a side effect.
  let hunt: Tank | null = null
  let nearest: Tank | null = null
  let nearestD = Infinity
  const pcxCell = self.playerCell()
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    if (t.id === self._lastSelectTargetId) hunt = t
    const tc = self.tankCell(t)
    const d = manhattan(tc.col, tc.row, pcxCell.col, pcxCell.row)
    if (d < nearestD) {
      nearestD = d
      nearest = t
    }
  }
  const anchorCol = BASE_POS.col
  const anchorRow = Math.max(2, BASE_POS.row - 1 - prm.defenseRowOffset)
  const v = evaluateUnifiedCandidates(
    w,
    p,
    list,
    hunt ?? nearest,
    anchorCol,
    anchorRow,
    self._candVerdict,
  )
  if (!v.kind) return false
  const roll = (): boolean => self.rng.next() >= prm.aimError
  if (v.kind === 'returnDefense') {
    self._moveDir = self.navigateTowards({ col: anchorCol, row: anchorRow })
    self._fire = false
    recordBranch(self, 'unifiedCandidates', 'candidateReturn')
    return true
  }
  // All fight candidates address one threat tank (verdict.threatId).
  let target: Tank | null = null
  for (let i = 0; i < list.length; i++) {
    if (list[i].id === v.threatId) {
      target = list[i]
      break
    }
  }
  if (!target || !target.alive) return false
  const tc = self.tankCell(target)
  const aligned = tc.col === pcxCell.col || tc.row === pcxCell.row
  const facing =
    p.dir ===
    (aligned
      ? tc.col === pcxCell.col
        ? tc.row > pcxCell.row
          ? 'down'
          : 'up'
        : tc.col > pcxCell.col
          ? 'right'
          : 'left'
      : p.dir)
  if (v.kind === 'clearLane') {
    const dir = clearLaneFireDir(w, p, target)
    if (!dir) return false
    self._moveDir = p.dir === dir ? null : dir
    self._fire = p.dir === dir && !onCooldown && shouldFireInDirImpl(self, pcx, pcy, dir)
    recordBranch(self, 'unifiedCandidates', 'candidateClear')
    return true
  }
  // killCurrent / interceptBase: standing hold only when the VERDICT came
  // from the standing assessment (standingShot — the M1 standing shot that
  // wins the deadline). Never re-derive standing from firstOutputTick ===
  // 0: an aligned approach has zero arrival cost yet may carry a blocked
  // ray. Approach commits always move; fire en route only when the CURRENT
  // ray is ring/base-clear (S30s27 — the commit-time verdict predates the
  // player's alignment).
  if (v.standingShot) {
    self._moveDir = null
    self._fire = v.fireClear && !onCooldown && roll()
  } else {
    self._moveDir = self.navigateTowards(tc)
    self._fire = aligned && facing && !fireRayBlocked(w, p, target) && !onCooldown && roll()
  }
  recordBranch(
    self,
    'unifiedCandidates',
    v.kind === 'killCurrent' ? 'candidateKill' : 'candidateIntercept',
  )
  return true
}

// ================================================================
// §X Base lane sentry — 基地车道哨兵
// ================================================================

/**
 * §X baseLaneSentry(850) — 基地车道哨兵: 基地危局下「走到车道、持位击杀」。
 *
 * 来源（Battlement hard seed 14 弹道级还原）：拆环 fast 在 (16,25)↔(15,25)
 * 口袋横走、hp 24（一枪线），玩家在 (16,21) 与其同列 40 ticks — 但唯一一枪
 * 从 (16,23) 砖格内发射被墙吃掉（双偏线像素扫描看见敌人边缘、真实子弹中线
 * 打墙），800ms 冷却后才恢复开火而敌人已转身逃离；随即 midLaneDefense(545)
 * 把玩家拖去中路横向火力送死（43hp 死于 t2255，重生空隙基地连受 3 发）。
 * 46/60 败局（base_destroyed 100%）同一 archetype 复现。
 *
 * 与 §134 defenseIntercept(550) 的本质区别：
 *   1. 拦截不动位 — 本候选不对齐时**主动导航到对齐站位**（evalBaseLaneSentry
 *      内的导航段）。
 *   2. 双偏线像点扫描的“可见”幻觉 — 本候选以**格对齐走廊**（laneCorridorBlocked，
 *      真实子弹中线）判定射界；单层砖挡则打砖开路（diggable，下一轮窗口生效）。
 *   3. 850 权重压掉 pickupHigh(800)/aggro(700)/midLane(545)/closePickup(540)/
 *      engage(500) — 威胁成立时整体锁定直到车道敌人被处理（dodge 1000 /
 *      interceptBase 900 仍在上方：生存与子弹拦截优先）。
 *
 * 门控：baseLaneSentryMode=0 短路（byte-identical）；无基地关；aggro 期让路。
 * 泛化：环格几何、csb/cbr 谓词、车道走廊全部只依赖 BASE_POS —— 任何带基地
 * 的地图通用；钢环/无拆环风险时哨兵永不激活（自关）。
 */
/**
 * M4 / 统一行动候选 (open-test protocol §7, 2026-08-16)。
 *
 * 基地受直接威胁(csb/cbr)时,不再让旧防守级联在"窗口已关"状态下提交,
 * 而是按 §7.1 度量比较四个固定候选(kill-current / intercept-base /
 * clear-lane / return-defense),§7.2 门控全过才提交:
 *   a. 安全 deadline slack > 0(站桩提交用 standing 击杀 slack — M3 S28s26:
 *      全行程 killAssessment 对"就差一枪"的场景系统性悲观);
 *   b. 有真实产出,站桩仅在 standing shot 赢 deadline 时合法;
 *   c. 第二威胁不可在完成前进入不可逆窗口;
 *   d. 射线不得穿过自家完好环砖(M3 S30s27 反例: 朝威胁开火打掉自家环
 *      反而引弹上身),clear-lane 永不打环砖。
 * 门控全不过 → return false,旧级联照旧(M3 主导机理就是窗口早已关闭,
 * 此时本层让路)。RNG 纪律: 仅提交时消耗 aimError roll,与其它候选一致。
 */

export const UNIFIED_CANDIDATES: Candidate = {
  id: 'unifiedCandidates',
  weight: ACTION_WEIGHTS.unifiedCandidates,
  evaluate: evalUnifiedCandidates,
}
