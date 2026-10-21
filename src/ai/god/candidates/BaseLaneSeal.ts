// candidates/BaseLaneSeal.ts — A9: remote base-shot line sealing (提前判线 + 封线).
//
// The 700-game audit (DECISIONS §2026-10-19-godai-audit-hard-700) found the base
// dies to shots fired from FAR away along an open line: at the moment the base
// fell the player was a median 9 cells away, and only 0.23 enemies on average
// stood within 6 cells of it. The fatal bullet arrived `down` 40% / `left` 32% /
// `right` 28% of the time — i.e. an enemy on the base COLUMN with a clear lane.
//
// `baseLaneSentry` (§198) already reacts to `canShootBase` / `canBreachRing`
// shooters, but only from the base band: it either shoots an ALIGNED target or
// walks to a station within `baseLaneSentryRange + 1`. A shooter 9-20 cells up
// the column is outside that envelope, so the player never contests the lane
// and the base eats the shell.
//
// This candidate seals the lane EARLY:
//   1. find the most dangerous REMOTE shooter (line to the base live, distance
//      ≥ `baseLaneSealMinDist`, inside `baseLaneSealRange` of the player);
//   2. walk to the closest-to-base standable cell on that shooter's column —
//      standing there both blocks the shell (body) and opens a firing line UP
//      the column at the shooter;
//   3. once on station, hold, face the shooter and fire.
//
// Vertical lanes only: the base column is the dominant fatal line (down 40%),
// and a horizontal seal would require standing inside the base band, which
// `baseLaneSentry` / `defenseIntercept` already own.
//
// MEASURED (2026-10-21, hard 35×20, paired): adding the horizontal half
// (`left`+`right` = 60% of fatal shells on paper) turns the vertical-only
// **+0.71pp into −0.29pp** — i.e. ≈ −1.0pp. Reason: a horizontal lane can only
// be sealed from a cell just outside the ring ON THE BASE ROW, which parks the
// player in the open a few pixels from the eagle, dueling at point-blank with
// whatever is coming; and horizontal base shots are usually fired from inside
// the base band, where `baseLaneSentry` already reacts. Do not re-add without
// a new measurement that beats vertical-only.
import type { Direction } from '../../../constants'
import { BASE_POS, GRID } from '../../../constants'
import { type GodAIInput, recordBranch } from '../../GodAIInput'
import { type Candidate, type DecisionContext, ACTION_WEIGHTS } from '../DecisionCore'
import { enemyCanBreachRing, enemyCanShootBase } from '../SmartThreatModel'
import { manhattan } from '../../../utils/helpers'

const standable = (self: GodAIInput, c: number, r: number): boolean => {
  if (c < 0 || c >= GRID || r < 0 || r >= GRID) return false
  const t = self.world.tileMap.get(c, r)
  return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
}

export function evalBaseLaneSeal(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.baseLaneSealMode <= 0 || !self.hasBase || self.aggressive) return false
  const { p, pcx, pcy, onCooldown } = ctx
  const pc = self.playerCell()
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks

  let bestRow = -1
  let bestCol = -1
  let bestKey = Infinity
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0) continue
    // `tankCell` writes a shared buffer — snapshot before the next call.
    const tc = self.tankCell(t)
    const col = tc.col
    const row = tc.row
    // Vertical lane only (see header): the shooter must sit on a base column.
    if (col !== BASE_POS.col && col !== BASE_POS.col + 1) continue
    // Remote shooters only — the near band belongs to baseLaneSentry.
    if (manhattan(col, row, BASE_POS.col, BASE_POS.row) < prm.baseLaneSealMinDist) continue
    const dPlayer = manhattan(pc.col, pc.row, col, row)
    if (dPlayer > prm.baseLaneSealRange) continue
    const csb = enemyCanShootBase(self, t)
    const cbr = prm.baseLaneSealCbr > 0 && !csb && enemyCanBreachRing(self, t)
    if (!csb && !cbr) continue
    // Direct base shots outrank ring breaches; ties break on the closer shooter
    // (shorter flight time ⇒ less time to arrive).
    const key = (csb ? 0 : 1000) + dPlayer
    if (key < bestKey) {
      bestKey = key
      bestRow = row
      bestCol = col
    }
  }
  if (bestRow < 0) return false

  // Seal cell: the standable cell closest to the base on the shooter's column.
  let sealRow = -1
  for (let r = BASE_POS.row - 2; r > bestRow && r >= 0; r--) {
    if (standable(self, bestCol, r)) {
      sealRow = r
      break
    }
  }
  if (sealRow < 0) return false

  const dir: Direction = bestRow < sealRow ? 'up' : 'down'
  if (pc.col === bestCol && pc.row === sealRow) {
    // On station: hold the lane, face the shooter, fire up the column.
    self._moveDir = p.dir === dir ? null : dir
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir)
    recordBranch(self, 'baseLaneSeal')
    return true
  }
  const move = self.navigateTowards({ col: bestCol, row: sealRow })
  if (move === null) return false
  self._moveDir = move
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, move)
  recordBranch(self, 'baseLaneSeal')
  return true
}

/** baseLaneSeal(542) — above closePickup(540), below midLaneDefense(545). */
export const BASE_LANE_SEAL: Candidate = {
  id: 'baseLaneSeal',
  weight: ACTION_WEIGHTS.baseLaneSeal,
  evaluate: evalBaseLaneSeal,
}
