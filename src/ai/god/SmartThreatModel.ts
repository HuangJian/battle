import type { GodAIInput } from '../GodAIInput'
import type { Tank, TankKind } from '../../types'
import { BASE_POS, DIR_VECTORS, GRID } from '../../constants'

// ============================================================
// SmartThreatModel — type/speed/facing/HP-aware base threat
// scoring (plan/God-AI-Next-Round §3, Phase A).
//
// When smartThreatModel is ON:
//   - selectTargetImpl base-threat branch uses defense-priority kind
//     weights (fast > power > armor > basic) and a canShootBaseFrom
//     bonus to prioritize enemies that have a clear shot at the base.
//
// All features are pure World-state reads (AGENTS §2.3 — no RNG, no
// hidden state). OFF = byte-identical to the old behavior.
// ============================================================

/**
 * Speed factor by tank kind, proportional to actual px/tick speed.
 * Used to compute "time to base" = distToBase / speedFactor.
 */
function kindSpeedFactor(kind: TankKind): number {
  switch (kind) {
    case 'fast':
      return 1.0
    case 'power':
      return 0.7
    case 'basic':
      return 0.5
    case 'armor':
      return 0.35
    default:
      return 0
  }
}

/**
 * Compute a threat score for an enemy tank based on how likely it is to
 * destroy the base before the player can stop it.
 *
 * Uses a "time-to-base" metric (distance / speed) as the dominant feature,
 * plus facing and HP. Higher score = more dangerous to the base.
 *
 * Pure function — reads only World/tank state, no RNG (AGENTS §2.3).
 */
export function threatScoreImpl(self: GodAIInput, t: Tank): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const tc = self.tankCell(t)

  const distToBase = Math.abs(tc.col - bc) + Math.abs(tc.row - br)

  // Time-to-base score: combines speed and distance.
  const speedFactor = kindSpeedFactor(t.kind)
  const maxTime = self.params.smartThreatDistRange
  const timeToBase = speedFactor > 0 ? distToBase / speedFactor : maxTime
  const timeScore = Math.max(0, 1 - timeToBase / maxTime)

  // Facing score: dot product of facing dir vs (base - enemy) dir, [0,1].
  const toBaseDx = bc - tc.col
  const toBaseDy = br - tc.row
  const toBaseLen = Math.sqrt(toBaseDx * toBaseDx + toBaseDy * toBaseDy) || 1
  const fv = DIR_VECTORS[t.dir]
  const dot = (fv.dx * toBaseDx + fv.dy * toBaseDy) / toBaseLen
  const facingScore = (dot + 1) / 2

  // HP score: higher HP ratio = harder to kill = higher sustained threat.
  const hpScore = t.hp / (t.maxHp || 1)

  const p = self.params
  return (
    p.smartThreatSpeedWeight * timeScore +
    p.smartThreatFacingWeight * facingScore +
    p.smartThreatHpWeight * hpScore
  )
}

/**
 * Smart isBaseUnderThreat: returns true if any live enemy has a threat
 * score ≥ smartThreatThreshold. (Currently unused — kept for future phases.)
 */
export function smartIsBaseUnderThreatImpl(self: GodAIInput): boolean {
  if (!self.hasBase) return false
  const threshold = self.params.smartThreatThreshold
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks
  for (const t of list) {
    if (!t.alive || t.spawnTimer > 0) continue
    if (threatScoreImpl(self, t) >= threshold) return true
  }
  return false
}

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
