// god/crossfire.ts — A12: multi-sector crossfire escape.
//
// s14 Steel Web autopsy (40 life losses, 20 games): 75% of the fatal ticks had
// **≥2 enemy bullets in flight**, 80% of the deaths happened while MOVING, and
// the inbound directions were lopsided (left 52 / right 18 / up 10 / down 9).
// The shipped dodge reacts to ONE bullet (`findMostDangerousBullet`) and steps
// perpendicular to it — which is correct for a single shell and fatal inside a
// crossfire: stepping out of one lane keeps you in the other.
//
// This helper answers "which single step reduces the number of lanes that still
// cover me": count the bullets whose travel AXIS matches the step axis (moving
// along a bullet's axis keeps you in its lane, moving across it leaves), pick
// the step with the fewest such bullets. Returns null when the situation is not
// a crossfire (<2 distinct inbound sectors) — the normal dodge then owns it.
import { BULLET, CELL, TANK } from '../../constants'
import type { Direction } from '../../utils/direction'
import { ALL_DIRS, DIR_VECTORS, opposite } from '../../utils/direction'
import type { GodAIInput } from '../GodAIInput'

/** Bullets farther than this never matter for a single-step decision. */
const MAX_THREAT_DIST = 13 * CELL

export function crossfireEscapeDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Direction | null {
  const w = self.world
  const p = self.controlledTank(w)
  if (!p) return null
  const bullets = w.bullets
  const band = TANK / 2 + BULLET / 2
  // Collect the bullets that would actually hit a stationary player, bucketed
  // by their travel axis.
  let vertical = 0
  let horizontal = 0
  let sectors = 0
  let maskV = 0
  let maskH = 0
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const isV = b.dir === 'up' || b.dir === 'down'
    const lateral = isV ? Math.abs(bcx - pcx) : Math.abs(bcy - pcy)
    if (lateral > band) continue
    // Only shells approaching (or already level with) the player matter.
    const ahead = isV
      ? (bcy - pcy) * (b.dir === 'down' ? 1 : -1)
      : (bcx - pcx) * (b.dir === 'right' ? 1 : -1)
    if (ahead > MAX_THREAT_DIST) continue
    if (isV) {
      vertical++
      maskV |= b.dir === 'down' ? 1 : 2
    } else {
      horizontal++
      maskH |= b.dir === 'right' ? 1 : 2
    }
    // Sector = where it comes from (opposite of its travel direction).
    const from = opposite(b.dir)
    sectors |= from === 'up' ? 1 : from === 'down' ? 2 : from === 'left' ? 4 : 8
  }
  // Count distinct sectors by popcount.
  let n = 0
  for (let m = sectors; m !== 0; m &= m - 1) n++
  if (n < 2) return null
  void maskV
  void maskH

  // A12 v3 (2026-10-24): only engage when the situation is actually URGENT —
  // full-suite A/B of v2 was dead neutral (54 up / 54 down over 2100 games):
  // it also hijacked ordinary dodges when both shells were far away, where the
  // normal perpendicular dodge is better. Gate on the soonest shell at the
  // CURRENT cell: if nothing arrives within `crossfireMinTti` ticks, leave the
  // dodge alone.
  let soonestNow = Infinity
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const isV = b.dir === 'up' || b.dir === 'down'
    const lateral = isV ? Math.abs(bcx - pcx) : Math.abs(bcy - pcy)
    if (lateral > band) continue
    const axial = isV
      ? (pcy - bcy) * (b.dir === 'down' ? 1 : -1)
      : (pcx - bcx) * (b.dir === 'right' ? 1 : -1)
    if (axial < 0) continue
    const t = axial / (b.speed && b.speed > 0 ? b.speed : 4)
    if (t < soonestNow) soonestNow = t
  }
  if (soonestNow > self.params.crossfireMinTti) return null

  // A12 v2 (2026-10-23): pick the step that maximises the time until the
  // EARLIEST shell reaches the NEW cell. Counting shells per axis was wrong:
  // one shell 2 cells away and one 15 cells away count the same, so the counter
  // happily walks into the imminent one (see tests/godai-crossfire-escape).
  const main = self.controlledTank(w)!
  const bulletSpeedOf = (b: { speed?: number }): number => (b.speed && b.speed > 0 ? b.speed : 4)
  let best: Direction | null = null
  let bestTti = -1
  for (let i = 0; i < ALL_DIRS.length; i++) {
    const d = ALL_DIRS[i]
    if (!self.canMoveDir(main, d)) continue
    const nc = main.x + DIR_VECTORS[d].dx * CELL + main.w / 2
    const nr = main.y + DIR_VECTORS[d].dy * CELL + main.h / 2
    let soonest = Infinity
    for (let j = 0; j < bullets.length; j++) {
      const b = bullets[j]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const isV = b.dir === 'up' || b.dir === 'down'
      const lateral = isV ? Math.abs(bcx - nc) : Math.abs(bcy - nr)
      if (lateral > band) continue // the new cell is outside this shell's lane
      // Distance from the shell to the NEW cell along its travel direction
      // (positive = still ahead of the shell).
      const axial = isV
        ? (nr - bcy) * (b.dir === 'down' ? 1 : -1)
        : (nc - bcx) * (b.dir === 'right' ? 1 : -1)
      if (axial < 0) continue // already past the new cell
      const tti = axial / bulletSpeedOf(b as { speed?: number })
      if (tti < soonest) soonest = tti
    }
    if (soonest > bestTti) {
      bestTti = soonest
      best = d
    }
  }
  return best
}
