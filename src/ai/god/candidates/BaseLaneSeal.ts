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

  // Seal cell: the standable cell closest to the base on that column, strictly
  // below the shooter (between it and the eagle) AND inside the base band
  // (rows `row-5 .. row-2`). The band matters: scanning all the way up to the
  // shooter sends the player to a cell 10+ rows above the eagle to "seal" a
  // lane at its far end — blocking works from anywhere on the line, but
  // leaving the base area for a cell deep in the maze measured −10pp on
  // Brickworks / −8pp on Twin Towers. Sealing must be a base-side job.
  const sealRowFor = (col: number, row: number): number => {
    const low = Math.max(BASE_POS.row - 5, row + 1)
    for (let r = BASE_POS.row - 2; r >= low && r >= 0; r--) {
      if (standable(self, col, r)) return r
    }
    return -1
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
  if (pc.col === bestCol && pc.row === bestSealRow) {
    self._moveDir = p.dir === dir ? null : dir
    self._fire = !onCooldown && self.shouldFireInDir(pcx, pcy, dir)
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
