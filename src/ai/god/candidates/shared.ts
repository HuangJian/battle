// candidates/shared.ts — module-level helpers shared by the candidate files
// (plan/refactor.zcode.md §3.4). Moved verbatim from think.ts during the
// candidate extraction; pure relocation, zero semantic change. This module
// breaks the import cycle: candidates import helpers HERE, never from
// think.ts.
import type { GodAIInput } from '../../GodAIInput'
import type { World } from '../../../game/World'
import type { Cell } from '../../../utils/grid-search'
import type { Direction } from '../../../constants'
import { BASE_POS, GRID } from '../../../constants'
import { scanAheadImpl } from '../FireControl'
import { isFieldRetreatConditionImpl } from '../StrategyPlanner'
import type { DecisionContext } from '../DecisionCore'

import { manhattan } from '../../../utils/helpers'

/**
 * §178 (autopsy hard-s34-base-l3-t25-seed2): dual central-breach P1 central-
 * hold gate. When true, this tank is P1 in a dual central-breach stage and
 * should behave as a PURE defender — hold the central anchor (§178), snipe the
 * col-12 spawn lane, and fire at enemies, but NEVER divert to grab power-ups
 * (P2 is the free tank that handles items: fence/bomb/star/shield). This is
 * the "sticky hold" that keeps P1 from wandering to top-row power-ups and
 * abandoning the base while it is dug out from below. Gated by spectateDual &&
 * centralBreachRisk && !isPlayer2 && dualCentralBreachP1HoldSticky>0 — single-
 * player and P2 stay byte-identical.
 */
export function isDualCentralBreachHoldP1(self: GodAIInput): boolean {
  return (
    self.world.spectateDual &&
    self._centralBreachRisk &&
    !self.isPlayer2() &&
    self.params.dualCentralBreachP1HoldSticky > 0
  )
}
/** §152-W2: map-center escape target for the aggressive movement-stuck guard. */
export const MAP_CENTER: Cell = { col: 12, row: 12 }
/**
 * §146 C: fieldRetreatPickupGate — HIGH-tier pickup gate. When the M13
 * field-pressure retreat condition holds (far from base + full enemy field,
 * base NOT under threat), the HIGH-tier urgent pickup (bomb/freeze/fence)
 * must NOT hijack the retreat: the pickup branch (800) evaluates before hunt
 * (200), so M13's return to the defense position never runs while an item
 * sits within divert range (S8: the player stayed in the dead-end pocket 43%
 * of loss-ending time while the base fell). Same predicate as
 * selectTargetUncached's M13 block (isFieldRetreatConditionImpl — single
 * source of truth). The item may still be picked on the way back (S5) or
 * after the retreat.
 *
 * SCOPE (A/B-measured 2026-08-05, §147): HIGH tier ONLY. Extending the gate
 * to MID (star/tank/shield) + LOW (S5 opportunistic) was measured NET NEGATIVE
 * — chaos aggregate −1.3pp (S12 −9pp / S4 −9pp / S15 −7pp / S34 −7pp) AND
 * hard per-stage dips to −11pp (S10 −11pp / S7 −7pp / S11 −6pp / S14 −6pp,
 * hard aggregate ~flat only because gains offset losses). MID items are the
 * permanent-DPS economy core and LOW pickups out-value the retreat under
 * pressure; suppressing them loses more than the retreat gains. HIGH tier
 * (bomb/freeze/fence) is the S8-measured hijack class and stays gated.
 * 0 = OFF (byte-identical).
 */
export function retreatGateBlocksPickup(self: GodAIInput): boolean {
  if (self.params.fieldRetreatPickupGate <= 0) return false
  const pcC = self.playerCell()
  const distC = manhattan(pcC.col, pcC.row, BASE_POS.col, BASE_POS.row)
  return isFieldRetreatConditionImpl(self, self.isBaseUnderThreat(), distC, self._enemies.length)
}
/** §X 原语: 8 个基地保护环格是否已有被击穿的洞（任一非砖/钢）。
 * 几何与 SimulationCombat.isBaseProtectionCell 一致（row 23 × cols 11-14，
 *  cols 11/14 × rows 24-25）。钢环（classic 某些关）= 永不击穿 → 哨兵自关。 */
export function baseRingBreachedImpl(w: World): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const g = w.tileMap.grid
  const hole = (c: number, r: number) => g[r][c] !== 'brick' && g[r][c] !== 'steel'
  return (
    hole(bc - 1, br - 1) ||
    hole(bc, br - 1) ||
    hole(bc + 1, br - 1) ||
    hole(bc + 2, br - 1) ||
    hole(bc - 1, br) ||
    hole(bc - 1, br + 1) ||
    hole(bc + 2, br) ||
    hole(bc + 2, br + 1)
  )
}
/** laneCorridorBlocked 的越界哨兵（端点出界 → 走廊不可用）。
 * 取 999 = 永远大于任何合法的格距（GRID=26），调用方只判 >0。 */
export const LANE_OUT_OF_BOUNDS = 999
/** §X 原语: 格对齐走廊检查 — (c,r)→(tc,tr) 必须同排或同列，两格之间逐格扫描。
 * 返回 0 = 走廊全通；>0 = 距 (c,r) 第 n 格（1 起）被非空地形挡住
 * （'base' 亦计 — 永不穿基地射击）。非对齐返回 -1。 */
export function laneCorridorBlocked(
  w: World,
  c: number,
  r: number,
  tc: number,
  tr: number,
): number {
  const g = w.tileMap.grid
  if (c === tc) {
    if (r === tr) return 0
    if (r < 0 || r >= GRID || tr < 0 || tr >= GRID || c < 0 || c >= GRID) return LANE_OUT_OF_BOUNDS
    const step = r < tr ? 1 : -1
    for (let rr = r + step; rr !== tr; rr += step) {
      if (g[rr][c] !== 'empty') return rr < r ? r - rr : rr - r
    }
    return 0
  }
  if (r === tr) {
    if (c === tc) return 0
    if (r < 0 || r >= GRID || c < 0 || c >= GRID || tc < 0 || tc >= GRID) return LANE_OUT_OF_BOUNDS
    const step = c < tc ? 1 : -1
    for (let cc = c + step; cc !== tc; cc += step) {
      if (g[r][cc] !== 'empty') return cc < c ? c - cc : cc - c
    }
    return 0
  }
  return -1
}
/**
 * §139 / 方向 A（进攻侧）: 火力死区解除 (firing-lane re-engage).
 *
 * Battlement 击杀效率分析（2026-08-05）: 命中率 23.7% 正常，瓶颈是射击量——
 * 玩家 51% 时间静止、34% 全 tick 钉在 (11,24) 火力死区（四方向无敌人 LOS），
 * 射击量 24.9 发/局只有 S32 的 37%（67.7），击杀 5.9/20 局基地即失守。
 *
 * 本候选：玩家处于死区（四方向 scan 全无敌人）且所有敌人较远（>=
 * firingLaneMinEnemyDist，无法直接追到）时，不再原地待机——在半径
 * firingLaneRadius 内找可站、能看到 ≥1 个敌人（同排/列 + 无遮挡）的瞭望格，
 * 导航过去重新接战（到了之后由 engage/aggressive 接管开火）。与 §137/§138
 * （去守位格「站着防守」）本质区别：这是「解卡 + 保持移动找射界」，不驻守。
 *
 * 门控：firingLaneMode=0 短路（byte-identical）；freeze/aggressive 跳过；
 * 已有敌人 LOS 跳过（engage/aggressive 接管）；敌人近在咫尺跳过（hunt
 * 直接追更快）。瞭望格搜索带 tick 节流（firingLaneReplanTicks）。纯函数：
 * 无 RNG、不改 World；分支计数仅观察。
 */
export function findFiringLaneCellImpl(self: GodAIInput, pc: Cell): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const prm = self.params
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  // Enemy cells + base distance (bounded allocation — throttled rare path,
  // same discipline as the §88 chokepoint replan, not per-tick).
  const ecols: number[] = []
  const erows: number[] = []
  const ebd: number[] = []
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    ecols.push(tc.col)
    erows.push(tc.row)
    ebd.push(manhattan(tc.col, tc.row, BASE_POS.col, BASE_POS.row))
  }
  const r = prm.firingLaneRadius
  let best: Cell | null = null
  let bestScore = -Infinity
  for (let rr = pc.row - r; rr <= pc.row + r; rr++) {
    for (let cc = pc.col - r; cc <= pc.col + r; cc++) {
      if (cc < 0 || cc >= GRID || rr < 0 || rr >= GRID) continue
      const t = tm.get(cc, rr)
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') continue
      // Count enemies visible from (cc,rr): same row/col with clear bullet line.
      let vis = 0
      let score = 0
      for (let ei = 0; ei < ecols.length; ei++) {
        if (cc === ecols[ei]) {
          const step = erows[ei] < rr ? -1 : 1
          let clear = true
          for (let y = rr + step; y !== erows[ei]; y += step) {
            const ty = tm.get(cc, y)
            if (ty === 'brick' || ty === 'steel' || ty === 'base') {
              clear = false
              break
            }
          }
          if (clear) {
            vis++
            // Base-adjacent enemies are urgent (intercept bias under pressure).
            score += ebd[ei] <= prm.threatRangeCells ? 2 : 1
          }
        } else if (rr === erows[ei]) {
          let clear = true
          for (let x = Math.min(cc, ecols[ei]) + 1; x < Math.max(cc, ecols[ei]); x++) {
            const tx = tm.get(x, rr)
            if (tx === 'brick' || tx === 'steel' || tx === 'base') {
              clear = false
              break
            }
          }
          if (clear) {
            vis++
            score += ebd[ei] <= prm.threatRangeCells ? 2 : 1
          }
        }
      }
      if (vis === 0) continue
      const dist = manhattan(cc, rr, pc.col, pc.row)
      const s = score * 10 - dist
      if (s > bestScore) {
        bestScore = s
        best = { col: cc, row: rr }
      }
    }
  }
  return best
}
/**
 * §161 carve fire: when the next path step is blocked by a plain brick (the
 * dig path's current frontier), fire to break it — NEVER at steel (R5, even
 * when the player could pierce) and never at ring bricks (scanAhead's
 * baseWall flag, which under the hard default baseWallExactRing=1 is the
 * exact ring). Moving freely → fire at enemies in the facing direction
 * only (suppression, allowWallFire=false — side walls are not carved).
 */
export function carveFire(self: GodAIInput, ctx: DecisionContext, dir: Direction | null): void {
  const { p, pcx, pcy, onCooldown } = ctx
  if (dir && !self.canMoveDir(p, dir)) {
    const bs = scanAheadImpl(self, pcx, pcy, dir)
    if (bs.wall && !bs.baseWall && !bs.baseSteel && !bs.steel) {
      self._fire = !onCooldown
      return
    }
  }
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir ?? p.dir, false)
}
