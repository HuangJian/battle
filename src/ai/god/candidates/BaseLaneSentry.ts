// candidates/BaseLaneSentry.ts — the baseLaneSentry candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BASE_POS, GRID } from '../../../constants'
import { type Tank } from '../../../types'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { contractStandingHold, enemyBulletOnRay, ownBulletOnRay } from '../ActionContract'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { shouldFireInDirImpl } from '../FireControl'
import { enemyCanBreachRing, enemyCanShootBase } from '../SmartThreatModel'
import { selfFireBaseGuardBlocks,
  baseRingBreachedImpl, laneCorridorBlocked } from '../candidates/shared'
import { manhattan } from '../../../utils/helpers'

export function evalBaseLaneSentry(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown } = ctx
  const prm = self.params
  if (prm.baseLaneSentryMode <= 0 || !self.hasBase || self.aggressive) return false
  // 危局态触发: 环砖已被拆（通道洞开，口袋司机变为致命威胁）。
  const ringBreached = baseRingBreachedImpl(w)
  // 选目标: csb（下一发毁基地，地图任何位置都是第一优先）> cbr（下一发拆环，
  // 谓词自身即近基地带）> 口袋司机（环已破且敌人在 rows 23-25 / cols base±6 带）。
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  let best: Tank | null = null
  let bestScore = Infinity
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const toc = self.tankCell(t)
    const dc = Math.abs(toc.col - BASE_POS.col)
    const dr = Math.abs(toc.row - BASE_POS.row)
    const csb = enemyCanShootBase(self, t)
    const cbr = !csb && enemyCanBreachRing(self, t)
    const inBand = toc.row >= BASE_POS.row - 1 && dc <= 6
    if (!csb && !cbr && !(ringBreached && inBand)) continue
    const score = csb ? dc + dr : cbr ? 100 + dc + dr : 200 + dc + dr
    if (score < bestScore) {
      bestScore = score
      best = t
    }
  }
  if (!best) return false
  const tc = self.tankCell(best)
  const pc = self.playerCell()
  const ccol = pc.col
  const crow = pc.row
  const aligned = ccol === tc.col || crow === tc.row
  const blocked = laneCorridorBlocked(w, ccol, crow, tc.col, tc.row)
  const sentryDist = manhattan(ccol, crow, tc.col, tc.row)
  const bestCsb = enemyCanShootBase(self, best)
  if (aligned && (blocked === 0 || (blocked === 1 && !bestCsb))) {
    // 对齐且中线畅通（或仅一层砖挡且敌人不是即刻杀手 — 原位打砖开路，
    // 下一轮车道窗口击杀；csb 敌人则绝不浪费弹药，交给导航换位）。
    const dir: Direction =
      ccol === tc.col ? (tc.row > crow ? 'down' : 'up') : tc.col > ccol ? 'right' : 'left'
    // 自射基地守卫（与 §134/ENGAGE 同源 — 绝不穿基地开火）。
    if (selfFireBaseGuardBlocks(self, pcx, pcy, dir)) return false
    if (blocked === 0) {
      // 中线畅通 — 持位射击: 立定向目标翻转 + 开火。仅在**能发射的 tick**
      // 接管（onCooldown 不 claim — 冷却期交给 midLane/navigate 正常流动，
      // 站位由流动保持或改善；冷却一过若仍对齐则哨兵再次接管开火）。
      if (sentryDist > prm.baseLaneSentryRange) return false
      if (onCooldown) return false
      self._moveDir = p.dir === dir ? null : dir
      self._fire = !onCooldown && self.rng.next() >= self.params.aimError
      recordBranch(self, 'baseLaneSentry')
      return true
    }
    // 单层砖挡（相邻格）且非即刻杀手 — 原位打砖开路。本 tick 打砖不可行
    // （shouldFireInDir 拒绝 — 如钢墙/自射守卫）则立刻让位，绝不空持死锁：
    // 正常流动（midLane/navigate）会移动玩家，几何改善后哨兵再接管。
    if (blocked === 1 && !bestCsb && shouldFireInDirImpl(self, pcx, pcy, dir)) {
      if (sentryDist > prm.baseLaneSentryRange) return false
      // Phase 2 §6.1 行动有效性契约: 站立 + 冷却中的无产出站桩先过契约
      // （敌弹在射线 / 自己子弹在飞 / 站桩击杀 killSlack>0），否则让位。
      // mode=0 短路 → byte-identical。
      if (
        prm.actionContractMode > 0 &&
        p.dir === dir &&
        onCooldown &&
        !contractStandingHold({
          world: w,
          player: p,
          threat: best,
          enemyBulletOnRay: enemyBulletOnRay(w, p, dir),
          ownBulletOnRay: ownBulletOnRay(w, p, ccol === tc.col),
        }).valid
      ) {
        return false
      }
      self._moveDir = p.dir === dir ? null : dir
      self._fire = !onCooldown
      recordBranch(self, 'baseLaneSentry')
      return true
    }
    return false
  }
  // §192 v6: 卫位导航（station-approach）— 非对齐或中线被挡时（含 dig 不可行、
  // csb 敌人不给挖等让位情形），走向相邻「清晰射击列」站位：站台 = 敌人列
  // ±1 列、玩家当前行、竖直到敌人行无砖、站台格可站。到达后由上方对齐开火
  // 接管（口袋 fast 横穿站台列时击杀）。仅限基地带内近距（manhattan ≤
  // range+1，敌列距 base ≤ 6）——绝不跨图劫持；威胁消失/敌人离开即让位。
  if (prm.baseLaneSentryStation > 0 && !bestCsb && sentryDist <= prm.baseLaneSentryRange + 1) {
    const tdc = Math.abs(tc.col - BASE_POS.col)
    // 仅拦截「带外」敌人（row 20-22，即将进带）：敌人已入带（row ≥ 23）
    // 时基地受威胁，须下行堵口而非横向挪位（seed 51 实证：fast 已到
    // (7,23) 时站台步把下行回防劫持成左移，基地被打爆）。
    if (tdc <= 6 && tc.row >= BASE_POS.row - 4 && tc.row < BASE_POS.row - 1) {
      // §198 门槛 (5)（chaos S34 seed 7 铁证 t2052）：带内（row ≥ 23）
      // 已有敌人时站台让位 — 玩家应留在带内处理防线威胁（selectTarget
      // 正在击杀 (6,24) 低血 fast），横向挪去带外目标会放弃基地防线。
      // 玩家带内击杀进行中不得被站台导航劫持。
      // 注意：tankCellImpl 写入共享 _tankCellBuf — 循环内每次调用都会
      // 覆盖外层 tc 的引用内容，必须先快照（Navigator.ts 契约）。
      const bestCol = tc.col
      const bestRow = tc.row
      let inBandThreat = false
      for (let li = 0; li < list.length; li++) {
        const t = list[li]
        if (!t.alive || t.spawnTimer > 0 || t === best) continue
        const toc = self.tankCell(t)
        if (toc.row >= BASE_POS.row - 1 && Math.abs(toc.col - BASE_POS.col) <= 6) {
          inBandThreat = true
          break
        }
      }
      if (inBandThreat) return false
      // §198 门槛 (6)（chaos S34 seed 7 铁证 t2136）：玩家在目标下方
      // （crow > tc.row）且面向上行、row 差 ≤ 3 → 站台步多余 — 玩家
      // 两格即达目标行，横向挪位反而拖慢（该局殊途同归却因 14-tick
      // 延迟 RNG 蝴蝶翻局 stageclear→gameover）。
      if (crow > bestRow && crow - bestRow <= 3 && p.dir === 'up') return false
      // 玩家须已在带内（row ≥ 21）：带外下行回防中的玩家不得被拽横移
      // （seed 32 实证：玩家 (11,18) 下行被拽去 col 12，回防延迟致败）。
      if (crow < BASE_POS.row - 3) return false
      // 玩家列与目标列差 ≤ 1 → 玩家已在拦截列/目标即将入列，站台步
      // 纯属多余（seed 25 实证：idle 防守位被拖走导致败局）。
      const colGap = bestCol > ccol ? bestCol - ccol : ccol - bestCol
      if (colGap <= 1) return false
      // 目标横向移动（left/right）且与玩家同行 → 目标将横穿玩家所在行，
      // 守株待兔即可（seed 53 实证：fast 横穿玩家行时换列迎击错过窗口）。
      const targetDir = best ? best.dir : ''
      const rowGap = bestRow > crow ? bestRow - crow : crow - bestRow
      if ((targetDir === 'left' || targetDir === 'right') && rowGap <= 1) return false
      // 站台列 = 目标列 ±1（就近优先，距离限 2）：差 2 时玩家恰好能
      // 跨一列拦截（seed 17 实证：玩家 (13,21) 对目标 (11,20) 下行走
      // 近，换到 col 12 拦截即击杀）；差 ≥ 4 时距离限自然放弃（seed
      // 53 实证：fast 横移中远距换列纯属赶路）。不追踪敌列 ± 差≤1
      // 跳过共同防横向振荡（seed 32 实证）。
      // §233 (perf): the 2-element cands array was allocated per evaluate
      // call (baseLaneSentry runs every tick). Two locals, same order
      // (near-first), byte-identical selection (AGENTS §14.1).
      const d1 = bestCol - 1
      const d2 = bestCol + 1
      const dd1 = d1 > ccol ? d1 - ccol : ccol - d1
      const dd2 = d2 > ccol ? d2 - ccol : ccol - d2
      const sc1 = dd1 <= dd2 ? d1 : d2
      const sc2 = dd1 <= dd2 ? d2 : d1
      if (sc1 >= 0 && sc1 < GRID && sc1 !== ccol) {
        const gap1 = sc1 > ccol ? sc1 - ccol : ccol - sc1
        if (
          gap1 <= 2 &&
          w.tileMap.get(sc1, crow) === 'empty' &&
          laneCorridorBlocked(w, sc1, crow, sc1, bestRow) === 0
        ) {
          self._moveDir = sc1 > ccol ? 'right' : 'left'
          self._fire = false
          recordBranch(self, 'baseLaneSentry')
          return true
        }
      }
      if (sc2 >= 0 && sc2 < GRID && sc2 !== ccol) {
        const gap2 = sc2 > ccol ? sc2 - ccol : ccol - sc2
        if (
          gap2 <= 2 &&
          w.tileMap.get(sc2, crow) === 'empty' &&
          laneCorridorBlocked(w, sc2, crow, sc2, bestRow) === 0
        ) {
          self._moveDir = sc2 > ccol ? 'right' : 'left'
          self._fire = false
          recordBranch(self, 'baseLaneSentry')
          return true
        }
      }
    }
  }
  // §225-A: 带内应急进 lane — ring 已破 + 敌人已在基地带内（row ≥ 23）+
  // 玩家与敌人不同列 → 横移到敌人列（colGap ≤ 3，保持当前行）堵口。站台
  // 导航只服务带外敌人（row 20-22），此处补带内空白（§225 实证：62.5%
  // 败局窗口内 sentry 0 tick — 玩家 lane 外时哨兵无路径, navigate 盲跑）。
  // 到位后由上方对齐开火段接管（同列 + 中线畅通 + manhattan ≤ range）。
  // 带内击杀进行中（aligned 已 return true）不会到此；§198 门控精神保持。
  if (prm.baseLaneSentryInBandNav > 0 && tc.row >= BASE_POS.row - 1 && crow >= BASE_POS.row - 3) {
    const colGap = tc.col > ccol ? tc.col - ccol : ccol - tc.col
    // colGap=1 跳过（§198 seed25 先例：玩家已在拦截列附近，横移纯属多余）。
    if (colGap >= 2 && colGap <= 3) {
      const sc = tc.col
      if (w.tileMap.get(sc, crow) !== 'empty') return false
      if (laneCorridorBlocked(w, sc, crow, sc, tc.row) !== 0) return false
      self._moveDir = sc > ccol ? 'right' : 'left'
      self._fire = false
      recordBranch(self, 'baseLaneSentry')
      return true
    }
  }
  return false
}



export const BASE_LANE_SENTRY: Candidate = {
  id: 'baseLaneSentry',
  weight: ACTION_WEIGHTS.baseLaneSentry,
  evaluate: evalBaseLaneSentry,
}
