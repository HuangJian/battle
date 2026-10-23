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
import { ALL_DIRS, opposite } from '../../utils/direction'
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

  // Pick the axis with fewer shells covering it; step along that axis (away is
  // safer than toward, so prefer the side opposite the nearest shell).
  let best: Direction | null = null
  let bestCover = Infinity
  for (let i = 0; i < ALL_DIRS.length; i++) {
    const d = ALL_DIRS[i]
    const stepV = d === 'up' || d === 'down'
    if (!self.canMoveDir(p, d)) continue
    const cover = stepV ? vertical : horizontal
    if (cover < bestCover) {
      bestCover = cover
      best = d
    }
  }
  return best
}
