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
