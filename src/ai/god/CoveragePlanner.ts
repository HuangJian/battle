import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { World } from '../../game/World'
import { BASE_POS, CELL, GRID, TICK_MS } from '../../constants'
import type { Cell } from '../../utils/pathfind'
import { enemyDeadline, aimDirTo, playerShotsToKill, firePower } from './ThreatBudget'
import { blocksBullet } from './Chokepoint'

/**
 * COORDINATE CONVENTION (§209, §210): every cell in this module is CORNER
 * space — the sub-block CONTAINING the tank's top-left corner
 * (floor(x/CELL)), the same space as tileMap and BASE_POS. A tank with
 * corner cell (c,r) physically occupies a 2×2 footprint (c..c+1, r..r+1).
 * ThreatBudget.tankCell() is CENTER space and must NOT be used here.
 *
 * §210: corner cells use floor(), NOT round(). round(x/CELL) flips at the
 * cell MIDPOINT (x = 16k+8) while the tank's top-left corner is still
 * inside cell k — the footprint read is one cell off, and a ±1px jitter
 * around the midpoint (navigation bounce, collision settle) flips the cell
 * every tick → laneAligned / clearLane / occupancy decisions oscillate.
 * floor() flips exactly at the true cell boundary (x = 16k), where the
 * footprint really changes: in-cell jitter never flips the decision.
 */

/**
 * Phase 3 (plan/God-AI-Hard-Breakthrough-Implementation.md §7): dynamic
 * attack coverage point. When the base is NOT under threat but a major
 * threat (enemy whose base-damage deadline is inside the horizon) exists,
 * holding a coverage point with positive intercept slack beats roaming
 * hunt — this is the S34/S8 fix: the player used to drift back to the
 * anchor ("回基地驻守") and lose field pressure, or roam without covering
 * any base ray.
 *
 * §7.1 candidates (current World only, NO stage IDs — all geometric from
 * BASE_POS / enemy cells): player cell (baseline), the base throat
 * (baseCol±1, baseRow−2/−3), each threat's lane cell just above the ring
 * and the cell between the enemy and the ring on its column, and firing
 * row/col intersections that see 2+ threats. Fixed cap (8) + low-frequency
 * recompute (coverageReplanTicks) + cheap per-tick release checks.
 *
 * §7.2 scoring: coverageValue(I) = Σ prevent(e,I) − travelCost − turnCost
 * − exposureRisk. Move only when the best point beats the baseline value
 * (value at the player cell) by a margin AND ≥1 major threat has positive
 * intercept slack at I. The point is a lease (coverageLeaseTicks), released
 * on target death, a flank threat (deadline tightened ≥ delta), slack ≤ 0,
 * or expiry — then falls back to the normal hunt/engage.
 *
 * §7.3 guardrails (hard blocks): (a) with 3+ enemies, a tighter second
 * threat blocks coverage (finish the current kill first); (b) two
 * independent base rays that the point does not cover both block it;
 * (c) player beyond COVERAGE_MAX_PLAYER_BASE_DIST with return ETA > base
 * slack blocks it.
 *
 * Mode 0 = never read/written (byte-identical). Pure World reads only.
 */

/** Threats with a damage deadline beyond this are handled by the normal hunt.
 * Measured bands (enemyDeadline, hard, stage 0, seed 2): csb ≈300, cbr 1-2
 * bricks ≈360-421, walk ≥ ≈435 (nearest ring cell + 2 cycles + window). The
 * horizon sits in the 421-435 gap — never on a jitter boundary. */
const COVERAGE_THREAT_HORIZON = 425
/** Coverage must beat the baseline (value at the player cell) by this many HP. */
const COVERAGE_VALUE_MARGIN = 10
/** Fixed candidate cap (M3 deliverable — no field-wide scans). */
const COVERAGE_CANDIDATE_CAP = 8
/** Travel cost weight: HP lost per tick of march. */
const COVERAGE_TRAVEL_COST = 0.25
/** Soft exposure term: HP per cell of distance from the base beyond 3. */
const COVERAGE_EXPOSURE_PER_CELL = 1.0
/** Guardrail (c): beyond this player→base distance, coverage needs slack. */
const COVERAGE_MAX_PLAYER_BASE_DIST = 12
/** Flank release: a threat whose deadline tightened ≥ this many ticks. */
const COVERAGE_FLANK_DELTA = 30

interface CoverageThreat {
  e: Tank
  deadline: number
  col: number
  row: number
  cadence: number
  flight: number
  fp: number
}

const msToTicks = (ms: number): number => Math.max(1, Math.round(ms / TICK_MS))

/** Corner-space cell of a tank (top-left sub-block; footprint is 2×2). */
function cornerCell(t: Tank): Cell {
  return { col: Math.floor(t.x / CELL), row: Math.floor(t.y / CELL) }
}

/**
 * Can a bullet fired from a player standing on corner cell (c,r) hit the tank?
 * Bullets spawn at the tank's front-edge center and are BULLET-wide: the
 * player's bullet band is (c..c+1) columns / rows (corner space), the target's
 * footprint is (tc..tc+1, tr..tr+1) — the two bands must overlap. This is the
 * §209 fix: the old check compared the candidate CELL against the tank's
 * CENTER cell (ThreatBudget.tankCell), half a cell off, so aligned lanes were
 * rejected and mis-aligned ones accepted.
 * §210: tc/tr are floor() — the tank's true top-left sub-block. round() flips
 * at the cell midpoint and oscillates under ±1px in-cell jitter.
 */
function laneAligned(c: number, r: number, t: Tank): boolean {
  const tc = Math.floor(t.x / CELL)
  const tr = Math.floor(t.y / CELL)
  return Math.abs(c - tc) <= 1 || Math.abs(r - tr) <= 1
}

function collectThreats(w: World, enemies: Tank[]): CoverageThreat[] {
  const out: CoverageThreat[] = []
  for (let i = 0; i < enemies.length; i++) {
    const e = enemies[i]
    const d = enemyDeadline(w, e)
    if (d.enemyDamageDeadline >= COVERAGE_THREAT_HORIZON) continue
    const ec = cornerCell(e)
    out.push({
      e,
      deadline: d.enemyDamageDeadline,
      col: ec.col,
      row: ec.row,
      cadence: e.nextFireInterval > 0 ? msToTicks(e.nextFireInterval) : 0,
      flight: e.bulletSpeed > 0 ? (CELL * 2) / e.bulletSpeed : 0,
      fp: firePower(w, e.kind),
    })
  }
  out.sort((a, b) => a.deadline - b.deadline)
  return out
}

function walkable(w: World, col: number, row: number): boolean {
  if (col < 0 || col >= GRID || row < 0 || row >= GRID) return false
  const t = w.tileMap.get(col, row)
  return t === 'empty' || t === 'ice' || t === 'forest'
}

/** Does a tank (corner (c,r), 2×2 footprint) block cell (col,row)? */
function tankBlocksCell(w: World, col: number, row: number, skip: Tank | null): boolean {
  for (let i = 0; i < w.tanks.length; i++) {
    const t = w.tanks[i]
    if (!t.alive || t.spawnTimer > 0) continue
    if (skip !== null && t === skip) continue
    const tc = Math.floor(t.x / CELL)
    const tr = Math.floor(t.y / CELL)
    if (col >= tc && col <= tc + 1 && row >= tr && row <= tr + 1) return true
  }
  return false
}

/**
 * §209: can a bullet from corner cell (aCol,aRow) reach (bCol,bRow) without
 * hitting terrain or a tank? Bullets are BULLET-wide bands, so the ray is the
 * overlap of the shooter's 2×2 footprint band and the target's: for a vertical
 * shot the columns are the overlap of [aCol,aCol+1] and [bCol,bCol+1] (must
 * non-empty → |aCol−bCol| ≤ 1); rows scanned strictly between, skipping the
 * target tank itself (skip) — its footprint is the endpoint, not an obstacle.
 */
function clearLane(
  w: World,
  aCol: number,
  aRow: number,
  bCol: number,
  bRow: number,
  skip: Tank | null = null,
): boolean {
  // The endpoint is the target tank's 2×2 footprint, not a single cell: a
  // bullet stops at the nearest footprint edge (and must NOT be "blocked" by
  // the target's own footprint cells or the terrain under it). Snap the scan
  // end to that edge; without a skip tank the endpoint stays the given cell.
  let endCol = bCol
  let endRow = bRow
  if (skip !== null) {
    const tc = Math.floor(skip.x / CELL)
    const tr = Math.floor(skip.y / CELL)
    // Vertical shot (lane bands overlap in columns): snap the scan end to the
    // target's NEAREST footprint row — bullets stop at the tank edge, and the
    // terrain under the tank must not count as an obstacle.
    if (Math.abs(aCol - bCol) <= 1) {
      endRow = aRow < bRow ? tr : tr + 1
    }
    // Horizontal shot: snap to the nearest footprint column.
    if (Math.abs(aRow - bRow) <= 1) {
      endCol = aCol < bCol ? tc : tc + 1
    }
  }
  const cellBlocked = (c: number, r: number): boolean => {
    if (c < 0 || c >= GRID || r < 0 || r >= GRID) return true
    if (blocksBullet(w.tileMap.get(c, r))) return true
    return tankBlocksCell(w, c, r, skip)
  }
  if (Math.abs(aCol - endCol) <= 1) {
    // Vertical shot: columns = intersection of the two 2-col bands.
    const cLo = Math.max(aCol, endCol)
    const cHi = Math.min(aCol + 1, endCol + 1)
    if (cLo > cHi) return false
    const step = aRow < endRow ? 1 : -1
    for (let r = aRow + step; r !== endRow; r += step) {
      for (let c = cLo; c <= cHi; c++) {
        if (cellBlocked(c, r)) return false
      }
    }
    return true
  }
  if (Math.abs(aRow - endRow) <= 1) {
    const rLo = Math.max(aRow, endRow)
    const rHi = Math.min(aRow + 1, endRow + 1)
    if (rLo > rHi) return false
    const step = aCol < endCol ? 1 : -1
    for (let c = aCol + step; c !== endCol; c += step) {
      for (let r = rLo; r <= rHi; r++) {
        if (cellBlocked(c, r)) return false
      }
    }
    return true
  }
  return false
}

/** Reach the candidate cell and align to fire at the threat (ticks). */
function reachAndTurn(w: World, p: Tank, pc: Cell, i: Cell, t: CoverageThreat): number {
  const travel =
    (Math.abs(pc.col - i.col) + Math.abs(pc.row - i.row)) * (p.speed > 0 ? CELL / p.speed : 1e9)
  const aimDir = aimDirTo(t.col, t.row, i.col, i.row)
  const turn =
    aimDir !== null && aimDir !== p.dir ? msToTicks(w.rules?.turnCooldownMs ?? 200) + 1 : 0
  return travel + turn
}

/** Damage the threat would land in the intercept window if untouched (HP).
 * Only counts when the point can actually shoot the threat's lane: aligned
 * (footprint band overlap) with clear bullet LOS — a point that cannot cover
 * the ray prevents nothing (this is what makes the baseline at an unaligned
 * player cell lose to a lane/throat point). */
function prevent(w: World, t: CoverageThreat, slack: number, i: Cell): number {
  if (slack <= 0) return 0
  if (!laneAligned(i.col, i.row, t.e)) return 0
  if (!clearLane(w, i.col, i.row, t.col, t.row, t.e)) return 0
  const cycle = t.cadence + t.flight
  if (cycle <= 0) return 0
  const shots = Math.max(1, Math.floor(slack / cycle))
  return Math.min(w.baseHp, t.fp * shots)
}

/** §7.2: coverage value of a candidate point (turn cost is inside reachAndTurn). */
function coverageValue(w: World, p: Tank, pc: Cell, i: Cell, threats: CoverageThreat[]): number {
  let v = 0
  for (let k = 0; k < threats.length; k++) {
    const t = threats[k]
    const slack = t.deadline - reachAndTurn(w, p, pc, i, t)
    v += prevent(w, t, slack, i)
  }
  // One gun: prevented damage cannot exceed what the base could lose.
  if (v > w.baseHp) v = w.baseHp
  const travel =
    (Math.abs(pc.col - i.col) + Math.abs(pc.row - i.row)) * (p.speed > 0 ? CELL / p.speed : 1e9)
  const exposure = Math.max(0, Math.abs(i.col - BASE_POS.col) + Math.abs(i.row - BASE_POS.row) - 3)
  v -= travel * COVERAGE_TRAVEL_COST
  v -= exposure * COVERAGE_EXPOSURE_PER_CELL
  return v
}

function collectCandidates(w: World, pc: Cell, threats: CoverageThreat[]): Cell[] {
  const out: Cell[] = [{ col: pc.col, row: pc.row }]
  const seen = new Set<number>()
  const key = (c: number, r: number): number => c * GRID + r
  const push = (c: number, r: number): void => {
    if (out.length >= COVERAGE_CANDIDATE_CAP) return
    const k = key(c, r)
    if (seen.has(k)) return
    if (!walkable(w, c, r)) return
    // A tank's 2×2 footprint overlaps the cell — the player cannot stand
    // there (footprint-band overlap, §209 coordinate fix).
    for (let i = 0; i < w.tanks.length; i++) {
      const t = w.tanks[i]
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = Math.floor(t.x / CELL)
      const tr = Math.floor(t.y / CELL)
      if (c >= tc - 1 && c <= tc + 1 && r >= tr - 1 && r <= tr + 1) return
    }
    // Coverage points must stay within the base neighborhood — a point that
    // far is a hunt assignment, not a coverage assignment (S34 forensics:
    // base lost with the player 20+ cells away).
    if (Math.abs(c - BASE_POS.col) + Math.abs(r - BASE_POS.row) > COVERAGE_MAX_PLAYER_BASE_DIST)
      return
    seen.add(k)
    out.push({ col: c, row: r })
  }
  seen.add(key(pc.col, pc.row))
  // Base throat (geometric — no stage IDs).
  const bc = BASE_POS.col
  const br = BASE_POS.row
  push(bc, br - 2)
  push(bc - 1, br - 2)
  push(bc + 1, br - 2)
  push(bc, br - 3)
  // Per-threat lane cells: just above the ring on the threat's column, and
  // one cell BETWEEN the threat and the ring (toward the base — row + 1 when
  // the threat is above the ring). A point above the threat (t.row - 1) would
  // drag the player AWAY from the base, leaving it unguarded (S34 forensics:
  // base lost with the player 20+ cells away).
  for (let k = 0; k < threats.length; k++) {
    const t = threats[k]
    push(t.col, br - 2)
    const toward = t.row < br - 1 ? t.row + 1 : t.row - 1
    push(t.col, toward)
  }
  // Firing row/col intersections seeing 2+ threats.
  for (let i = 0; i < threats.length; i++) {
    for (let j = i + 1; j < threats.length; j++) {
      const a = threats[i]
      const b = threats[j]
      const c1: Cell = { col: b.col, row: a.row }
      if (
        walkable(w, c1.col, c1.row) &&
        clearLane(w, c1.col, c1.row, a.col, a.row) &&
        clearLane(w, c1.col, c1.row, b.col, b.row)
      ) {
        push(c1.col, c1.row)
      }
      const c2: Cell = { col: a.col, row: b.row }
      if (
        walkable(w, c2.col, c2.row) &&
        clearLane(w, c2.col, c2.row, a.col, a.row) &&
        clearLane(w, c2.col, c2.row, b.col, b.row)
      ) {
        push(c2.col, c2.row)
      }
    }
  }
  return out
}

function heldThreatIdsMatch(self: GodAIInput, enemies: Tank[]): boolean {
  const n = self._coverageThreatCount
  let found = 0
  for (let i = 0; i < enemies.length; i++) {
    const id = enemies[i].id
    for (let j = 0; j < n; j++) {
      if (self._coverageThreatIds[j] === id) {
        found++
        break
      }
    }
  }
  return found === n
}

/**
 * The coverage branch (called from selectTargetUncached, after every defense
 * and override branch — a coverage point never delays threat response).
 * Returns the cell to hold, or null to fall through to the normal hunt.
 */
export function coveragePlanImpl(
  self: GodAIInput,
  w: World,
  p: Tank,
  pc: Cell,
  enemies: Tank[],
): Cell | null {
  // §210: re-derive the player cell in THIS module's corner space (floor).
  // The caller passes playerCell() which is round() semantics — the midpoint
  // flip would leave pc one cell off from every floor-based candidate.
  pc = { col: Math.floor(p.x / CELL), row: Math.floor(p.y / CELL) }
  const threats = collectThreats(w, enemies)
  const held = self._coverageCell
  // Cheap per-tick fast path: while the lease is open and the committed
  // threat set is intact, keep holding the point (no deadline re-scans).
  if (held && w.frame < self._coverageUntil) {
    if (heldThreatIdsMatch(self, enemies)) {
      // Flank release throttled to the replan grid: a threat whose deadline
      // tightened ≥ COVERAGE_FLANK_DELTA makes the plan stale. §209 BUG-2:
      // an EMPTY current threat set (all threats walked past the horizon)
      // must also release — the old `else return held` kept the player
      // parked on a point that protects nothing.
      if (w.frame % self.params.coverageReplanTicks === 0) {
        const cur = collectThreats(w, enemies)
        if (
          cur.length === 0 ||
          cur[0].deadline < self._coverageMinDeadline - COVERAGE_FLANK_DELTA
        ) {
          self._coverageCell = null
        } else {
          return held
        }
      } else {
        return held
      }
    } else {
      self._coverageCell = null // committed threat died / unreachable
    }
  }
  if (threats.length === 0) {
    self._coverageCell = null
    return null
  }
  // Low-frequency cache: full recompute at most once per replan grid, unless
  // the alive threat set changed (cheap id signature).
  let sig = 0
  for (let i = 0; i < threats.length; i++) sig += threats[i].e.id
  if (
    self._coveragePlanFrame === w.frame ||
    (self._coveragePlanFrame !== -1 &&
      w.frame - self._coveragePlanFrame < self.params.coverageReplanTicks &&
      sig === self._coverageIdSignature)
  ) {
    return held
  }
  self._coveragePlanFrame = w.frame
  self._coverageIdSignature = sig

  // §7.3 guardrails.
  if (threats.length >= 3) {
    // (a) a tighter second threat blocks coverage — finish the current kill.
    const k = playerShotsToKill(w, threats[0].e)
    const cadence = p.fireCooldown > 0 ? msToTicks(p.fireCooldown) : msToTicks(p.nextFireInterval)
    const flight = p.bulletSpeed > 0 ? (CELL * 2) / p.bulletSpeed : 0
    const killEta = Math.max(1, k - 1) * cadence + flight
    if (threats[1].deadline < killEta) {
      self._coverageCell = null
      return null
    }
  }
  // (c) player too far: coverage is a BASE-neighborhood assignment. When the
  // player is beyond the bound, the defense cascade (race/clearshot) handles
  // imminent damage and the normal hunt handles the rest — re-routing the
  // player to a far point is exactly the S34 collapse pattern (forensics:
  // base lost with the player 20+ cells away from the base).
  const baseDist = Math.abs(pc.col - BASE_POS.col) + Math.abs(pc.row - BASE_POS.row)
  if (baseDist > COVERAGE_MAX_PLAYER_BASE_DIST) {
    self._coverageCell = null
    return null
  }

  // §7.2: score all candidates; require a winner beating the baseline.
  const cands = collectCandidates(w, pc, threats)
  let best: Cell | null = null
  let bestV = -Infinity
  let bestSlackPos = false
  const baseV = coverageValue(w, p, pc, pc, threats)
  for (let i = 1; i < cands.length; i++) {
    const c = cands[i]
    const v = coverageValue(w, p, pc, c, threats)
    let slackPos = false
    for (let k = 0; k < threats.length; k++) {
      if (threats[k].deadline - reachAndTurn(w, p, pc, c, threats[k]) > 0) {
        slackPos = true
        break
      }
    }
    if (v > bestV) {
      bestV = v
      best = c
      bestSlackPos = slackPos
    }
  }
  if (best === null || !bestSlackPos || bestV <= baseV + COVERAGE_VALUE_MARGIN) {
    self._coverageCell = null
    return null
  }
  // (b) two independent base rays must both be coverable from the point.
  // Independent = different lane BANDS (|col diff| > 1 — a 2×2 footprint
  // spans two columns/rows). §209 fix: the old check required a clear lane
  // from the threat to BASE_POS.row, which the intact ring bricks always
  // blocked → (b) NEVER fired while the ring stood (the S34 collapse). The
  // threat set already guarantees each threat can hit the ring/base line.
  if (threats.length >= 2) {
    const t0 = threats[0]
    const t1 = threats[1]
    const independent = Math.abs(t0.col - t1.col) > 1 || Math.abs(t0.row - t1.row) > 1
    if (independent) {
      const covers0 = laneAligned(best.col, best.row, t0.e)
      const covers1 = laneAligned(best.col, best.row, t1.e)
      if (!(covers0 && covers1)) {
        self._coverageCell = null
        return null
      }
    }
  }

  self._coverageCell = best
  self._coverageUntil = w.frame + self.params.coverageLeaseTicks
  self._coverageMinDeadline = threats[0].deadline
  const n = Math.min(threats.length, 4)
  self._coverageThreatCount = n
  for (let i = 0; i < n; i++) self._coverageThreatIds[i] = threats[i].e.id
  return best
}
