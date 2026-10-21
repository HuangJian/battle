// god/lead-fire.ts — A10: arrival-time hit predicate for a shot fired NOW.
//
// Single source of truth shared by the God AI fire gate (`GodAIInput.isFiring`)
// and the NN observation `leadHitD[4]` (`nn/policy-extra.computePolicyExtra`).
// Moving it here keeps ONE implementation: the two brains must agree on what
// "this shot would land" means, otherwise the executor's mask and the policy's
// feature describe different games.
//
// Semantics (unchanged from the NN feature, B3 / plan §4-B3): fire along world
// direction `dir` from the tank centre (pcx,pcy); the shot lands when
//
//   · the enemy is on the muzzle side, 0 < axial ≤ 13 cells;
//   · its LATERAL position at arrival (t* = axial / bulletSpeed) is inside the
//     hit band, i.e. |lateral + vLat·t*| < TANK/2 + BULLET/2 = 19px — this is
//     the extrapolation the alignment gate does not do;
//   · nothing blocking sits between the muzzle and the enemy cell band.
//
// Enemies turn unpredictably, so the extrapolation uses the LIVE lateral
// velocity (a stopped tank is the degenerate constant-velocity case). It is a
// heuristic, not a guarantee — which is why the God AI ships it behind a gate
// and is measured, not assumed.
//
// Pure read of World, zero allocation.
import { BULLET, CELL, TANK } from '../../constants'
import type { World } from '../../game/World'
import { TileMap } from '../../game/TileMap'
import type { Direction } from '../../utils/direction'

/** TANK/2 + BULLET/2 = 19px — the same band the NN feature uses. Declared
 * locally (rather than imported from `nn/danger-metrics`) because `src/` must
 * not import `src/nn/` at runtime (import-boundary gate); the constant is
 * derived, so the two copies cannot drift. */
const LEAD_HIT_BAND_PX = TANK / 2 + BULLET / 2

/** Any brick/steel/base strictly between the muzzle and the enemy cell band. */
function laneBlocked(
  tm: World['tileMap'],
  srcCol: number,
  srcRow: number,
  vertical: boolean,
  pLow: number,
  pHigh: number,
): boolean {
  if (vertical) {
    if (srcRow < pLow) {
      for (let r = srcRow + 1; r < pLow; r++)
        if (TileMap.blocksBullet(tm.get(srcCol, r))) return true
    } else {
      for (let r = srcRow - 1; r > pHigh; r--)
        if (TileMap.blocksBullet(tm.get(srcCol, r))) return true
    }
    return false
  }
  if (srcCol < pLow) {
    for (let c = srcCol + 1; c < pLow; c++) if (TileMap.blocksBullet(tm.get(c, srcRow))) return true
  } else {
    for (let c = srcCol - 1; c > pHigh; c--)
      if (TileMap.blocksBullet(tm.get(c, srcRow))) return true
  }
  return false
}

export function leadHitForDir(world: World, dir: Direction, pcx: number, pcy: number): number {
  const p = world.player
  if (!p) return 0
  const vertical = dir === 'up' || dir === 'down'
  const sign = dir === 'down' || dir === 'right' ? 1 : -1
  const bulletSpeed = p.bulletSpeed > 0 ? p.bulletSpeed : 4
  const maxAxial = 13 * CELL
  const shooterCol = Math.floor(pcx / CELL)
  const shooterRow = Math.floor(pcy / CELL)
  const tileMap = world.tileMap
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const axial = (vertical ? ty - pcy : tx - pcx) * sign
    if (axial <= 0 || axial > maxAxial) continue
    const lateral = vertical ? tx - pcx : ty - pcy
    const vLat = vertical ? t.vx : t.vy
    const tStar = axial / bulletSpeed
    if (Math.abs(lateral + vLat * tStar) >= LEAD_HIT_BAND_PX) continue
    const colLow = Math.floor(t.x / CELL)
    const colHigh = Math.floor((t.x + t.w - 1) / CELL)
    const rowLow = Math.floor(t.y / CELL)
    const rowHigh = Math.floor((t.y + t.h - 1) / CELL)
    if (
      !laneBlocked(
        tileMap,
        shooterCol,
        shooterRow,
        vertical,
        vertical ? rowLow : colLow,
        vertical ? rowHigh : colHigh,
      )
    ) {
      return 1
    }
  }
  return 0
}
