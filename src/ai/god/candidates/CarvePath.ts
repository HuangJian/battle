// candidates/CarvePath.ts — the carvePath candidate body.
// Extracted verbatim from think.ts (plan/refactor.trae.md §3.4):
// the M1 evaluate() closure became this named function; behavior
// is byte-identical (per-tick determinism gate).
import { type GodAIInput } from '../../GodAIInput'
import { type DecisionContext } from '../DecisionCore'
import {
  carvePathInfoCached,
  carvePostImpl,
  carveThreatEnemyImpl,
  findCarveEscapeImpl,
} from '../PathCarve'
import { carveFire } from '../candidates/shared'

import { manhattan } from '../../../utils/helpers'

export function evalCarvePath(self: GodAIInput, ctx: DecisionContext): boolean {
  const { w } = ctx
  const prm = self.params
  if (prm.carvePathMode <= 0 || self.aggressive || !self.hasBase) return false
  const pc = self.playerCell()
  // Lower-half gate (R1: 下半区开路).
  if (pc.row < prm.carveLowerRow) return false
  // Base under threat → the defense candidates / hunt's defense return
  // handle it; the carve is a calm-state reposition.
  if (self.isBaseUnderThreat()) return false

  const post = carvePostImpl(self)
  if (!post) return false
  const distToPost = manhattan(pc.col, pc.row, post.col, post.row)

  // ---- Mode B (R3): at the post, nothing fightable → dig toward the
  // most base-threatening enemy. ----
  if (distToPost <= prm.carveAtPostCells) {
    // Don't steal a close chase from hunt — a nearby enemy is faster
    // dealt with directly.
    const list = self._enemies.length > 0 ? self._enemies : w.tanks
    let closeEnemy = false
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = self.tankCell(t)
      if (manhattan(tc.col, tc.row, pc.col, pc.row) <= prm.carveChaseCells) {
        closeEnemy = true
        break
      }
    }
    if (closeEnemy) return false
    const threat = carveThreatEnemyImpl(self)
    if (!threat) return false
    const info = carvePathInfoCached(self, pc, threat)
    if (!info.path || info.path.length === 0) return false
    const dir = info.path[0]
    self._moveDir = dir
    carveFire(self, ctx, dir)
    self.branchCounts.carvePath++
    self._lastBranch = 'carvePath'
    return true
  }

  // ---- Mode A (R1/R2/R4): no smooth route to the post → carve. ----
  const info = carvePathInfoCached(self, pc, post)
  if (!info.path || info.path.length === 0) {
    // The post is not carve-reachable from here — most often because the
    // spawn sits on the far side of the base ring from the defense post, so
    // every route to it would cross the ring (forbidden by R5/R6). R1/R2
    // still want the player to dig OUT of the sealed pocket: fall back to
    // carving toward the nearest carve-safe escape. This keeps §161 useful
    // on ring-fortified stages without ever breaking ring / base-column
    // bricks (the escape search is itself ring-safe — see findCarveEscapeImpl).
    const escape = findCarveEscapeImpl(self, pc)
    if (!escape) return false
    const einfo = carvePathInfoCached(self, pc, escape)
    // A smooth route to the escape means the player isn't boxed → let
    // navigate handle it (R4: no dig when a corridor exists).
    if (!einfo.path || einfo.path.length === 0 || einfo.corridor) return false
    const dir = einfo.path[0]
    self._moveDir = dir
    carveFire(self, ctx, dir)
    self.branchCounts.carvePath++
    self._lastBranch = 'carvePath'
    return true
  }
  if (info.corridor) return false // R4: 通畅路线 → 不打砖开路
  const dir = info.path[0]
  self._moveDir = dir
  carveFire(self, ctx, dir)
  self.branchCounts.carvePath++
  self._lastBranch = 'carvePath'
  return true
}
