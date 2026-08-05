import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import { BASE_POS, GRID } from '../../constants'

// ============================================================
// SmartThreatModel — canShootBaseFrom (base clear-shot predicate).
//
// M0.5 退役（2026-08-03, DECISIONS §96）: threatScoreImpl /
// smartIsBaseUnderThreatImpl（Phase A 否决）已移入 experimental.ts 归档。
// 本文件保留 canShootBaseFrom / enemyCanShootBase——它们是 §59
// defenseClearShotBonus（SHIPPED）与 §88 chokepoint 威胁点（SHIPPED）的
// 依赖。
// ============================================================

/**
 * canShootBaseFrom (plan §3.5): check if a tank at the given cell has a
 * CLEAR shot at the base — aligned (same row or col) AND no brick/steel
 * in between. An enemy with a clear shot can destroy the base with its
 * next bullet, making it the highest-priority target.
 *
 * This is a static terrain check — it doesn't predict enemy movement or
 * wall destruction. It only checks the CURRENT terrain state.
 */
export function canShootBaseFrom(self: GodAIInput, col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tm = self.world.tileMap

  if (col === bc) {
    // Same column — check vertical line from (col, row) to (col, br).
    const step = row < br ? 1 : -1
    for (let r = row + step; r !== br; r += step) {
      if (r < 0 || r >= GRID) return false
      const t = tm.get(col, r)
      if (t === 'brick' || t === 'steel') return false
    }
    // Also check the base-adjacent cell (the cell just before the base).
    const adjRow = br - step
    if (adjRow >= 0 && adjRow < GRID) {
      const t = tm.get(col, adjRow)
      if (t === 'brick' || t === 'steel') return false
    }
    return true
  }

  if (row === br || row === br + 1) {
    // Same row (or base row + 1 for the 2-cell-tall base) — check horizontal.
    for (let c = Math.min(col, bc) + 1; c < Math.max(col, bc); c++) {
      if (c < 0 || c >= GRID) return false
      const t = tm.get(c, row)
      if (t === 'brick' || t === 'steel') return false
    }
    return true
  }

  return false
}

/**
 * Check if an enemy tank currently has a clear shot at the base.
 * Uses canShootBaseFrom on the tank's current cell.
 */
export function enemyCanShootBase(self: GodAIInput, t: Tank): boolean {
  const tc = self.tankCell(t)
  return canShootBaseFrom(self, tc.col, tc.row)
}

/**
 * §135 / 方向 D 预测版: is the enemy ABOUT to enter the base's firing lane?
 *
 * §134 (SHIPPED) intercepts enemies already ON a lane (enemyCanShootBase —
 * aligned with the base AND clear LOS). That misses the final approach: on
 * Battlement the fast reaches the base ring and fires before the static
 * predicate ever becomes true from the defense position. This predicate
 * extends the trigger to the last `predictCells` of the approach: the enemy
 * shares the base's column (or the base's row rows 24-25), is FACING the
 * base (t.dir points at it), and is within `predictCells` of the lane.
 *
 * Deliberately does NOT check clear LOS (unlike canShootBaseFrom) — the
 * approach lane is often blocked by the base-protection brick ring the
 * enemy is about to shoot through; the §134 intercept already handles the
 * LOS-confirmed lane state, and the DEFENSE_INTERCEPT candidate re-verifies
 * with scanAheadImpl before firing (a brick between player and enemy makes
 * scan.enemy false and the candidate declines — same as §134).
 *
 * Pure function of World state (tank cell + dir + params) — no RNG.
 */
export function enemyApproachingBaseLaneImpl(
  self: GodAIInput,
  t: Tank,
  predictCells: number,
): boolean {
  if (predictCells <= 0) return false
  const tc = self.tankCell(t)
  const bc = BASE_POS.col
  const br = BASE_POS.row
  // Same column as the base — the vertical lane. Approaching from above
  // (typical: enemy spawns at the top and drives down) or below.
  if (tc.col === bc) {
    if (tc.row < br && t.dir === 'down' && br - tc.row <= predictCells) return true
    if (tc.row > br + 1 && t.dir === 'up' && tc.row - (br + 1) <= predictCells) return true
    return false
  }
  // Same row as the base (rows 24-25 — the 2-cell-tall base) — the
  // horizontal lane. Approaching from the left or right.
  if (tc.row === br || tc.row === br + 1) {
    if (tc.col < bc && t.dir === 'right' && bc - tc.col <= predictCells) return true
    if (tc.col > bc && t.dir === 'left' && tc.col - bc <= predictCells) return true
    return false
  }
  return false
}
