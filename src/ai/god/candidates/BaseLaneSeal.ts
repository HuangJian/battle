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

/**
 * A TANK occupies a **2×2 block** (`TANK = CELL * 2`), so "this cell is
 * standable" is not enough — the whole footprint must be free.
 *
 * This was a real bug in the seal cell search (modes 0-2): on Riverbed the cell
 * resolved to (12,22), whose footprint (rows 22-23) runs into the base-ring
 * brick at (12,23). The tank could never occupy it, so it pressed down against
 * the wall forever: measured on-station **2%** of the candidate's own ticks.
 * Mode 3 fixes that — and measures WORSE, see the A/B note in `sealRowFor`.
 */
const tankFits = (self: GodAIInput, c: number, r: number): boolean =>
  standable(self, c, r) &&
  standable(self, c + 1, r) &&
  standable(self, c, r + 1) &&
  standable(self, c + 1, r + 1)

export function evalBaseLaneSeal(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  if (prm.baseLaneSealMode <= 0 || !self.hasBase || self.aggressive) return false
  // mode 2 (2026-10-27) is a different mechanism, not a tweak: see
  // `evalPreSeal`. Mode 1 keeps this body byte-for-byte.
  //
  // STAGE GATE (user's rule): the pre-seal is only meaningful on stages whose
  // two base columns carry NO steel — steel is permanent for enemies, so on
  // every other stage a shell can never reach the eagle down the column.
  // ⚠ It must FALL THROUGH to this body (not return false): mode 2 is an
  // addition to the shipped seal, not a replacement. Returning false here
  // disables the shipped seal on 31 stages (measured: Brickworks −4 /
  // Twin Spires −6 / Brick Maze −6 purely from losing the mode-1 seal).
  if (prm.baseLaneSealMode >= 2 && !baseColumnsHaveSteel(self)) return evalPreSeal(self, ctx)
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

/**
 * mode 2 (2026-10-27): PRE-SEAL an already-open base column.
 *
 * Autopsy (Battlement / Riverbed, hard 60 seeds, base-loss forensics):
 *   · every failure is `base_destroyed` (0 lives-out);
 *   · the first enemy breach is on the TOP side of the ring in 17/30 (hard)
 *     and 29/38 (chaos) of Battlement losses, 11/17 Riverbed;
 *   · the killer shell is fired from **row 2-5** (11/17 Riverbed, 8/19
 *     Battlement) straight down the base column — 19-22 cells of flight ≈
 *     80 ticks of warning;
 *   · `enemyCanShootBase` is true on **9.9%** of all ticks, yet
 *     `baseLaneSeal` claims 1.5% and `baseLaneSentry` 0.0-0.2% — both leash on
 *     the distance to the SHOOTER (6 / 14 cells), so the far sniper is
 *     outside every defense envelope.
 *
 * The leash is wrong: time-to-impact is set by the shooter's distance, but the
 * player's job is to reach the LANE. mode 2 therefore leashes on the distance
 * to the seal CELL, and also treats "enemy standing on a clear-to-top base
 * column" as a target (it becomes a sniper the moment it stops).
 */
function evalPreSeal(self: GodAIInput, ctx: DecisionContext): boolean {
  const prm = self.params
  const { p, pcx, pcy, onCooldown } = ctx
  const pc = self.playerCell()
  const list = self._enemies.length > 0 ? self._enemies : self.world.tanks

  // Seal cell = the cell on that column we can REACH fastest (user's rule,
  // 2026-10-27). ANY standable cell strictly between the shooter and the eagle
  // blocks its shell — the body is 16px wide, the shell 6px — so the closest-
  // to-the-base rule was never necessary, and it was actively harmful: on
  // Battlement the route to the base-side cell detours around the plaza walls
  // (traced: 90 ticks from (20,13) to (21,17), never arrived, base died).
  //
  // Constraints kept: never stand in the cell right in front of the muzzle
  // (`row + 2`), and only standable terrain. Ties resolve toward the base
  // (larger row = safer, more cover behind us).
  const sealRowFor = (col: number, row: number): number => {
    const low = Math.max(row + 2, 0) // never stand in the muzzle cell
    const hi = BASE_POS.row - 2
    if (prm.baseLaneSealCellMode !== 1) {
      // SHIPPED (modes 0/2): the standable cell nearest the EAGLE. Measured
      // better than the "any cell on the line" alternatives (see the mode table
      // below) even though the geometry allows any of them.
      // mode 3 uses the 2×2 footprint instead (the tank can actually stand
      // there) — A/B on hard 35×60: overall 0.00pp at 2100 games is the wrong
      // read; per-stage this HURT (Checkers 82%→75%) ⇒ the tank arrives
      // (on-station 2% → 30%) and it does NOT pay.
      const fits = prm.baseLaneSealCellMode >= 3 ? tankFits : standable
      for (let r = hi; r >= low; r--) if (fits(self, col, r)) return r
      return -1
    }
    // mode 1 (A/B, NEGATIVE): the cell we can reach fastest. Any cell on the
    // line blocks the shell, so this arrives sooner — but it measured −10pp on
    // Riverbed / −7pp on Battlement (hard 60) and −0.29pp over all 35 stages,
    // because the seal then claims 3× more ticks mostly WALKING (2.6%→8.0% of
    // the run, on-station still ~1%) and the base takes MORE damage (54→63).
    let best = -1
    let bestD = Infinity
    for (let r = hi; r >= low; r--) {
      if (!standable(self, col, r)) continue
      const d = manhattan(pc.col, pc.row, col, r)
      if (d < bestD) {
        bestD = d
        best = r
      }
    }
    return best
  }

  let bestCol = -1
  let bestRow = -1
  let bestSealRow = -1
  let bestKey = Infinity
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0) continue
    const tc = self.tankCell(t)
    const col = tc.col
    const row = tc.row
    if (col !== BASE_POS.col && col !== BASE_POS.col + 1) continue
    if (row >= BASE_POS.row - 1) continue
    const sealRow = sealRowFor(col, row)
    if (sealRow < 0) continue
    const csb = enemyCanShootBase(self, t)
    // mode 2 arms the ring-breach class itself: a breacher on the column is
    // the thing that OPENS the lane in the first place.
    const cbr =
      !csb && (prm.baseLaneSealMode >= 2 || prm.baseLaneSealCbr > 0) && enemyCanBreachRing(self, t)
    const openLane = !csb && !cbr && columnClearToTop(self.world, col)
    if (!csb && !cbr && !openLane) continue
    // THE LEASH (the only real knob here): measured on the distance to the seal
    // CELL, not to the shooter. Time-to-impact is set by the shooter's distance
    // (a 20-cell sniper gives ~80 ticks), but the player's job is only to reach
    // the lane — and the median loss takes 3 shell hits, so the seal's value is
    // denying the REPEAT fire, not the first shell.
    const dSeal = manhattan(pc.col, pc.row, col, sealRow)
    if (dSeal > prm.baseLaneSealCellRange) continue
    // Live base shot > imminent ring breach > "standing on an open column".
    const key = (csb ? 0 : cbr ? 1000 : 1500) + dSeal
    if (key < bestKey) {
      bestKey = key
      bestCol = col
      bestRow = row
      bestSealRow = sealRow
    }
  }
  if (bestCol < 0) return false

  const dir: Direction = bestRow < bestSealRow ? 'up' : 'down'
  // On station? mode 2 (A/B, NEGATIVE) treats ANY cell of the shooter's column
  // between it and the eagle as on-station — the geometry allows it (body 16px
  // vs shell 6px) — and it measured −4pp Riverbed / −5pp Battlement: holding
  // high up the lane is exposed, and the base-side cell also doubles as "walk
  // home". Modes 0/1 require the exact cell.
  const onStation =
    prm.baseLaneSealCellMode === 2
      ? pc.col === bestCol && pc.row > bestRow && pc.row <= BASE_POS.row - 1
      : pc.col === bestCol && pc.row === bestSealRow
  if (onStation) {
    self._moveDir = p.dir === dir ? null : dir
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir)
    recordBranch(self, 'baseLaneSeal')
    return true
  }
  // mode 3: same column ⇒ go STRAIGHT down/up. The router re-tie-breaks every
  // tick (traced: left/right alternating two rows above the cell), and each
  // flip costs a 12-tick turn, so the tank paced sideways instead of closing.
  if (prm.baseLaneSealCellMode >= 3 && pc.col === bestCol && pc.row !== bestSealRow) {
    self._moveDir = bestSealRow > pc.row ? 'down' : 'up'
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, self._moveDir)
    recordBranch(self, 'baseLaneSeal')
    return true
  }
  const move = self.navigateTowards({ col: bestCol, row: bestSealRow })
  if (move === null) return false
  self._moveDir = move
  self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, move)
  recordBranch(self, 'baseLaneSeal')
  return true
}

/**
 * Stage-level gate for the pre-seal: BOTH base columns free of steel.
 *
 * Only s8 Riverbed / s14 Steel Web / s21 Checkers / s34 Battlement qualify.
 * Per-column gating is not enough: a stage that shields only one column still
 * leaks through the other.
 */
function baseColumnsHaveSteel(self: GodAIInput): boolean {
  return columnHasSteel(self.world, BASE_POS.col) || columnHasSteel(self.world, BASE_POS.col + 1)
}

/**
 * Any steel on `col` between the top edge and the base's top row?
 *
 * Steel is permanent for enemies (only a ★3 player pierces it), so a column
 * with steel is NOT a sniper lane in the mode-2 sense — the shell dies on the
 * metal. This is the structural gate the user asked for: "only use the
 * pre-seal on stages whose mid lane has no steel". Measured steel counts in
 * the base columns across the 35 stages: only **s8 Riverbed / s14 Steel Web /
 * s21 Checkers / s34 Battlement** are steel-free.
 */
function columnHasSteel(world: GodAIInput['world'], col: number): boolean {
  for (let r = 0; r <= BASE_POS.row - 1; r++) {
    if (world.tileMap.get(col, r) === 'steel') return true
  }
  return false
}

/** Is `col` clear from the top edge down to the base's top row? (a sniper lane) */
function columnClearToTop(world: GodAIInput['world'], col: number): boolean {
  for (let r = 0; r <= BASE_POS.row - 1; r++) {
    const t = world.tileMap.get(col, r)
    if (t === 'brick' || t === 'steel' || t === 'base') return false
  }
  return true
}

/** baseLaneSeal(542) — above closePickup(540), below midLaneDefense(545). */
export const BASE_LANE_SEAL: Candidate = {
  id: 'baseLaneSeal',
  weight: ACTION_WEIGHTS.baseLaneSeal,
  evaluate: evalBaseLaneSeal,
}
