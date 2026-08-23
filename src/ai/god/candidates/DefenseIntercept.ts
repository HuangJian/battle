// candidates/DefenseIntercept.ts — the defenseIntercept candidate body.
// Extracted verbatim from think.ts (plan/refactor.zcode.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BASE_POS, CELL } from '../../../constants'
import { type GodAIInput } from '../../GodAIInput'
import { contractStandingHold, enemyBulletOnRay, ownBulletOnRay } from '../ActionContract'
import { type DecisionContext } from '../DecisionCore'
import {
  enemyInShotCorridorImpl,
  scanAheadImpl,
  shotReachesBaseImpl,
  shouldFireInDirImpl,
} from '../FireControl'
import { enemyApproachingBaseLaneImpl, enemyCanShootBase } from '../SmartThreatModel'

import { manhattan } from '../../../utils/helpers'

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
    if (
      prm.selfFireBaseGuard > 0 &&
      shotReachesBaseImpl(self, pcx, pcy, dir) &&
      (prm.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, dir))
    ) {
      continue
    }
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
      self.branchCounts.defenseIntercept++
      self._lastBranch = 'defenseIntercept'
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
      self.branchCounts.defenseIntercept++
      self._lastBranch = 'defenseIntercept'
      return true
    }
  }
  return false
}
