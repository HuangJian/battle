// candidates/Survive.ts — the survive candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type Direction, BASE_POS, DIR_VECTORS, GRID } from '../../../constants'
import { ALL_DIRS } from '../../../utils/direction'
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import { survivalPressure } from '../EnemyModel'

import { manhattan } from '../../../utils/helpers'

export function evalSurvive(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w, p, pcx, pcy, onCooldown, aimDir } = ctx
  if (self.aggressive) return false
  // Only when there is no immediate bullet threat (dodge already declined)
  // AND survival pressure is active (P1-3: preserve the last lives).
  if (self.params.surviveMinEnemies <= 0) return false
  if (survivalPressure(self) <= 0) return false
  // When an enemy is ALREADY aligned in the line of fire (aimDir set), the
  // T2a counter-fire / stop-and-aim tactic is the right call — survive is
  // for MULTI-DIRECTION crossfire (no single shootable enemy, plan §3.2
  // "无在飞子弹但处于交叉火力/包围位置"), where standing to fire at one
  // of several threats is death. An aligned target stays engage's job.
  if (aimDir) return false
  // The current cell must be a positional dead-end (≤ 2 passable exits).
  const pc = self.playerCell()
  let exits = 0
  for (let di = 0; di < ALL_DIRS.length; di++) {
    if (self.canMoveDir(p, ALL_DIRS[di])) exits++
  }
  if (exits > 2) return false
  // Enemies must be surrounding the dead-end.
  const radius = self.params.surviveEnemyRadiusCells
  const need = self.params.surviveMinEnemies
  let nearby = 0
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let i = 0; i < enemies.length; i++) {
    const t = enemies[i]
    if (!t.alive || t.spawnTimer > 0) continue
    const ec = self.tankCell(t)
    const d = manhattan(ec.col, ec.row, pc.col, pc.row)
    if (d <= radius) {
      if (++nearby >= need) break
    }
  }
  if (nearby < need) return false
  // Pick the open direction whose next cell has the most passable exits,
  // strictly more than the current cell, tie-broken toward the base.
  const baseCol = BASE_POS.col + 1
  const baseRow = BASE_POS.row + 1
  let bestDir: Direction | null = null
  let bestExits = exits
  let bestBaseDist = Infinity
  for (let di = 0; di < ALL_DIRS.length; di++) {
    const d = ALL_DIRS[di]
    if (!self.canMoveDir(p, d)) continue
    const dv = DIR_VECTORS[d]
    const cx = pc.col + dv.dx
    const cy = pc.row + dv.dy
    if (cx < 0 || cx >= GRID || cy < 0 || cy >= GRID) continue
    let dExits = 0
    for (let dj = 0; dj < ALL_DIRS.length; dj++) {
      const v2 = DIR_VECTORS[ALL_DIRS[dj]]
      const c2 = cx + v2.dx
      const r2 = cy + v2.dy
      if (c2 < 0 || c2 >= GRID || r2 < 0 || r2 >= GRID) continue
      if (!w.isCellBlocked(c2, r2)) dExits++
    }
    const baseDist = manhattan(cx, cy, baseCol, baseRow)
    if (dExits > bestExits || (dExits === bestExits && baseDist < bestBaseDist)) {
      bestDir = d
      bestExits = dExits
      bestBaseDist = baseDist
    }
  }
  if (bestDir === null) return false
  // Strictly-more-open guarantee: never trade a dead-end for a dead-end.
  if (bestExits <= exits) return false
  self._moveDir = bestDir
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, bestDir)
  self.branchCounts.survive++
  self._lastBranch = 'survive'
  // Update the last-aim so the engine sees a coherent turn (same as T2b).
  if (aimDir) void aimDir
  return true
}
