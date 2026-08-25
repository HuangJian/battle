// candidates/DefenseIntercept.ts — the defenseIntercept candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BASE_POS, CELL } from '../../../constants'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { contractStandingHold, enemyBulletOnRay, ownBulletOnRay } from '../ActionContract'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import {
  scanAheadImpl,
  shouldFireInDirImpl,
} from '../FireControl'
import { enemyApproachingBaseLaneImpl, enemyCanShootBase } from '../SmartThreatModel'

import { manhattan } from '../../../utils/helpers'
import { selfFireBaseGuardBlocks } from '../candidates/shared'

export function evalDefenseIntercept(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown } = ctx
  const prm = self.params
  if (prm.defenseInterceptMode <= 0 || !self.hasBase || self.aggressive) return false
  const pc = self.playerCell()
  const playerDistToBase = manhattan(pc.col, pc.row, BASE_POS.col, BASE_POS.row)
  if (playerDistToBase > prm.defenseInterceptMaxDist) return false

  // Cluster C: reuse the per-tick enemy snapshot (falls back to w.tanks).
  const list = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let li = 0; li < list.length; li++) {
    const t = list[li]
    if (!t.alive || t.spawnTimer > 0) continue
    // §134: enemy is ON the base's firing lanes RIGHT NOW (aligned + clear
    // LOS — it can destroy the base with its next bullet). §135 (predict):
    // OR it is about to enter a lane — shares the base's column/row, FACES
    // the base, within defenseInterceptPredictCells (0 = OFF, byte-identical
    // to §134 SHIPPED). Intercept BEFORE it reaches the ring and fires.
    // §135: predict flag is reused by the §136 dig branch below.
    const approaching =
      prm.defenseInterceptPredictCells > 0 &&
      enemyApproachingBaseLaneImpl(self, t, prm.defenseInterceptPredictCells)
    if (!enemyCanShootBase(self, t) && !approaching) continue
    const tc = self.tankCell(t)
    const dCol = tc.col - pc.col
    const dRow = tc.row - pc.row
    // Player must share the enemy's row or column (interceptable shot).
    if (dCol !== 0 && dRow !== 0) continue
    const distCells = Math.abs(dCol) + Math.abs(dRow)
    if (distCells === 0 || distCells > prm.defenseInterceptRangeCells) continue
    const dir: Direction = dCol !== 0 ? (dCol > 0 ? 'right' : 'left') : dRow > 0 ? 'down' : 'up'
    // Self-fire base guard (same as ENGAGE/aggressive §121): never shoot
    // THROUGH the base at an enemy on the far side.
    if (selfFireBaseGuardBlocks(self, pcx, pcy, dir)) continue
    // Confirm a live enemy is actually on the line within range — the
    // scan may hit a nearer enemy, which is equally worth shooting.
    const scan = scanAheadImpl(self, pcx, pcy, dir)
    if (scan.enemy && scan.enemyDist <= distCells * CELL + CELL) {
      // Phase 2 §6.1 行动有效性契约: 站立（已面向）+ 冷却中的提交 = 无产出
      // 站桩（M0 台账 no_output_commit 60% 失败族）。mode>0 时先过契约 —
      // 敌弹在射线 / 自己子弹在飞 / 站桩击杀 killSlack>0 才允许原地等待，
      // 否则放弃本分支（fall through 到 engage/hunt/navigate 产生输出）。
      // 绝不否决有移动/有开火的提交（§199 弃守教训）。mode=0 短路 → byte-identical。
      if (
        prm.actionContractMode > 0 &&
        p.dir === dir &&
        onCooldown &&
        !contractStandingHold({
          world: w,
          player: p,
          threat: t,
          enemyBulletOnRay: enemyBulletOnRay(w, p, dir),
          ownBulletOnRay: ownBulletOnRay(w, p, dCol === 0),
        }).valid
      ) {
        continue
      }
      self._moveDir = p.dir === dir ? null : dir
      self._fire = !onCooldown && self.rng.next() >= self.params.aimError
      recordBranch(self, 'defenseIntercept')
      return true
    }
    // §136 / 方向 D 破砖版: 预测命中（enemyApproachingBaseLaneImpl）但
    // 弹道被砖挡 → 打砖开路，为即将进车道的敌人建立射界。复用
    // shouldFireInDirImpl（默认 allowWallFire=true）——其内部对
    // baseWall（基地保护环）与钢墙（level<3）一律禁止，子弹只打场景砖。
    // 第一发破砖，敌人走进射界时后续子弹直接命中。天然自终止：砖打光后
    // scan.wall=false → 本分支不再成立（fall through 到正常拦截/走位）。
    if (
      approaching &&
      prm.defenseInterceptDigBricks > 0 &&
      scan.wall &&
      !scan.baseWall &&
      !scan.steel
    ) {
      if (
        prm.actionContractMode > 0 &&
        p.dir === dir &&
        onCooldown &&
        !contractStandingHold({
          world: w,
          player: p,
          threat: t,
          enemyBulletOnRay: enemyBulletOnRay(w, p, dir),
          ownBulletOnRay: ownBulletOnRay(w, p, dCol === 0),
        }).valid
      ) {
        continue
      }
      self._moveDir = p.dir === dir ? null : dir
      self._fire = !onCooldown && shouldFireInDirImpl(self, pcx, pcy, dir)
      recordBranch(self, 'defenseIntercept')
      return true
    }
  }
  return false
}


/**
 * defenseIntercept(550) — §134/方向 D: 防守位停射拦截基地车道敌人。
 *
 * 与 §132（selectTarget 威胁重排，追快车）的本质区别：本候选**不离开防守位**。
 * 玩家在基地附近（distToBase ≤ defenseInterceptMaxDist）时，若某存活敌人
 * 已经与基地对齐且无遮挡（enemyCanShootBase——下一发子弹就能毁基地），同时
 * 该敌人与玩家同排/同列（玩家能从防守位直接命中），则停射拦截它——turn to
 * face + fire，像 T2a 但目标不是 aimDir 选中的最近敌人，而是基地车道上的敌人。
 *
 * 背景（hard 35×120 取证，§131-§133）：Battlement 基地被毁 117/120、凶手 59%
 * fast。三个方向先后证伪——T8 拦子弹（已离膛）、威胁重排（fast 4.5cps 追不上
 * 1★ 玩家 4.19cps）、距离收紧（早回防=把中场让给敌人）。存活下来的思路是
 * 「在车道口把敌人打掉」：敌人与 base 对齐的瞬间（它破砖进入 row 23-25 或 base
 * 列的走廊）正是它最脆弱也最危险的时刻，玩家在防守位（base 列上方）与它同列
 * 的概率最高，一枪命中即解除威胁。
 *
 * 门控（全部默认 OFF → byte-identical）：defenseInterceptMode=0 短路；
 * aggressive（freeze 窗口由 aggro 处理）；无基地关；玩家太远（不出防位追）。
 * 复用 ENGAGE 的 self-fire base guard（shotReachesBaseImpl）——绝不朝基地方向
 * 开火穿过基地打敌人。
 */

export const DEFENSE_INTERCEPT: Candidate = {
  id: 'defenseIntercept',
  weight: ACTION_WEIGHTS.defenseIntercept,
  evaluate: evalDefenseIntercept,
}
