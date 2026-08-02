import type { GodAIInput } from '../GodAIInput'
import type { Tank } from '../../types'
import type { Cell } from '../../utils/pathfind'
import { findPath } from '../../utils/pathfind'
import { canShootBaseFrom } from './SmartThreatModel'
import { BASE_POS, DIR_VECTORS, GRID } from '../../constants'

// ============================================================
// Chokepoint — 据守咽喉要地 (user request 2026-08-02, §88).
//
// Definitions (from the request):
//   1. 威胁点 (threat point)  = a cell from which an enemy can DIRECTLY
//      shoot the base — exactly the `canShootBaseFrom` predicate
//      (SmartThreatModel). Terrain-derived; recomputed on a throttle.
//   2. 威胁路径 (threat path) = the A* corridor route from an enemy to its
//      NEAREST threat point. Rule 3: if the enemy's turret (炮口朝向) is not
//      along the path's dominant direction, the path is ignored (an enemy
//      facing AWAY from the base is not about to attack it).
//   3. 咽喉要地 (chokepoint)  = the lower-half cell (row >= chokepointMinRow)
//      from which the player can shoot enemies traversing the MOST threat
//      paths. Rule 5: ties break toward the cell with the most steel/brick
//      cover (steel weight >> brick weight).
//
// All functions are pure World-state reads (AGENTS §2.3) — no RNG, no hidden
// state. The plan is recomputed on a throttled cadence (chokepointReplanTicks)
// and stamped with the world frame, exactly like the navigateTowards cache.
// ============================================================

/** A throttled §88 plan: threat points + selected chokepoint cell. */
export interface ChokepointPlan {
  /** world.frame when this plan was computed. */
  tick: number
  /** All threat-point cells (canShootBaseFrom ∧ tank-passable), any terrain. */
  threatPoints: Cell[]
  /** The selected 咽喉要地 cell, or null when no path coverage exists. */
  chokepoint: Cell | null
}

/** Reusable per-tank footprint passability check (brick/steel/water/base block). */
function cellPassable(tm: { grid: string[][] }, col: number, row: number): boolean {
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return false
  const g = tm.grid
  for (let dr = 0; dr <= 1; dr++) {
    const grow = g[row + dr]
    for (let dc = 0; dc <= 1; dc++) {
      const t = grow[col + dc]
      if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') return false
    }
  }
  return true
}

/** Bullets die on brick/steel/base (water lets them pass — TileMap.blocksBullet).
 * Exported for reuse by StrategyPlanner's chokepoint-coverage check (§88). */
export function blocksBullet(t: string): boolean {
  return t === 'brick' || t === 'steel' || t === 'base'
}

/**
 * Compute all threat points: cells where an enemy tank can DIRECTLY shoot the
 * base (canShootBaseFrom) AND can physically stand (2×2 footprint passable).
 * Threat points are terrain-derived — they only change when bricks are
 * destroyed, so they ride the throttled plan cache.
 */
export function computeThreatPointsImpl(self: GodAIInput): Cell[] {
  const tm = self.world.tileMap
  const out: Cell[] = []
  for (let row = 0; row < GRID; row++) {
    for (let col = 0; col < GRID; col++) {
      if (!canShootBaseFrom(self, col, row)) continue
      if (!cellPassable(tm, col, row)) continue
      out.push({ col, row })
    }
  }
  return out
}

/**
 * A* corridor path from the enemy's cell to a threat point, with the rule-3
 * facing gate: the enemy's turret must be along the path's DOMINANT direction
 * toward the threat point (an enemy facing away from the base is not about to
 * attack it). Returns the traversed cells (inclusive of both endpoints), or
 * null when no corridor path exists or the facing gate rejects it.
 */
function enemyThreatPath(self: GodAIInput, e: Tank, tp: Cell): { cells: Cell[] } | null {
  const ec = self.tankCell(e)
  if (ec.col === tp.col && ec.row === tp.row) return null // already on the point — imminence handled elsewhere
  const dc = tp.col - ec.col
  const dr = tp.row - ec.row
  const horizontal = Math.abs(dc) >= Math.abs(dr)

  // Rule 3 facing gate (chokepointFacingGate=1): the turret must point along
  // the dominant axis toward the threat point.
  if (self.params.chokepointFacingGate > 0) {
    const want = horizontal ? (dc > 0 ? 'right' : 'left') : dr > 0 ? 'down' : 'up'
    if (e.dir !== want) return null
  }

  const path = findPath(self.world.tileMap, ec, tp)
  if (!path) return null

  // Reconstruct traversed cells from the Direction[] (perf: reuse a flat walk).
  const cells: Cell[] = [{ col: ec.col, row: ec.row }]
  let col = ec.col
  let row = ec.row
  for (let i = 0; i < path.length; i++) {
    const d = path[i]
    col += DIR_VECTORS[d].dx
    row += DIR_VECTORS[d].dy
    cells.push({ col, row })
  }
  return { cells }
}

/**
 * §88 rule 4: the chokepoint = the lower-half cell (row >= chokepointMinRow)
 * that can shoot the most distinct threat paths. A candidate can shoot a path
 * when any path cell shares its row OR column with clear bullet LOS
 * (no brick/steel/base between). Ties: max cover (steel*SteelWeight +
 * brick*BrickWeight in the ring around the 2×2 footprint), then closest to the
 * base (stability — a max-coverage far corner is not more defensible).
 *
 * Cost is bounded: the plan is throttled (chokepointReplanTicks), paths are
 * limited to the chokepointPathsPerEnemy nearest threat points per enemy, and
 * coverage is built by a per-path stamp walk (no per-candidate × per-cell
 * product in the hot loop).
 */
export function computeChokepointImpl(self: GodAIInput, threatPoints: Cell[]): Cell | null {
  const w = self.world
  const tm = w.tileMap
  const p = self.params
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  if (threatPoints.length === 0 || enemies.length === 0) return null

  // ---- 1. Facing-gated threat paths per enemy (nearest N threat points) ----
  const paths: { cells: Cell[] }[] = []
  const maxDist = p.chokepointMaxThreatDist
  for (let ei = 0; ei < enemies.length; ei++) {
    const e = enemies[ei]
    if (!e.alive || e.spawnTimer > 0) continue
    const ec = self.tankCell(e)
    // Distance cap (perf §88): an enemy farther than maxDist from its NEAREST
    // threat point contributes no path — a failed A* would explore the whole
    // grid for a threat it can't reach soon. Cheap Manhattan pre-filter.
    if (maxDist > 0) {
      let nearest = Infinity
      for (let ti = 0; ti < threatPoints.length; ti++) {
        const d = Math.abs(threatPoints[ti].col - ec.col) + Math.abs(threatPoints[ti].row - ec.row)
        if (d < nearest) nearest = d
      }
      if (nearest > maxDist) continue
    }
    // Nearest-N by Manhattan (selection-sort style, no allocation).
    let best: { tp: Cell; d: number }[] = []
    const N = Math.max(1, p.chokepointPathsPerEnemy)
    for (let ti = 0; ti < threatPoints.length; ti++) {
      const tp = threatPoints[ti]
      const d = Math.abs(tp.col - ec.col) + Math.abs(tp.row - ec.row)
      if (best.length < N) {
        best.push({ tp, d })
        continue
      }
      // Find the worst kept (max d) and replace if closer.
      let wi = -1
      let wd = -1
      for (let bi = 0; bi < best.length; bi++) {
        if (best[bi].d > wd) {
          wd = best[bi].d
          wi = bi
        }
      }
      if (d < wd) best[wi] = { tp, d }
    }
    for (let bi = 0; bi < best.length; bi++) {
      const path = enemyThreatPath(self, e, best[bi].tp)
      if (path) paths.push(path)
    }
  }
  if (paths.length === 0) return null

  // ---- 2. Coverage: mark every cell that can shoot each path's cells ----
  // A candidate cell C can shoot path cell P iff C is in P's row or column
  // with clear LOS. Equivalent (LOS is symmetric): from each path cell P,
  // walk its row/column outward until a bullet-blocker, stamping every cell
  // along the way as covering THIS path. coverage[idx] = # distinct paths.
  const coverage = new Int32Array(GRID * GRID)
  const stamp = new Int32Array(GRID * GRID)
  for (let pi = 0; pi < paths.length; pi++) {
    const sid = pi + 1
    const cells = paths[pi].cells
    for (let ci = 0; ci < cells.length; ci++) {
      const pc = cells[ci]
      // Row walk (left + right).
      for (let dir = -1; dir <= 1; dir += 2) {
        let c = pc.col + dir
        while (c >= 0 && c < GRID) {
          if (blocksBullet(tm.grid[pc.row][c])) break
          const idx = pc.row * GRID + c
          if (stamp[idx] !== sid) {
            stamp[idx] = sid
            coverage[idx]++
          }
          c += dir
        }
      }
      // Column walk (up + down).
      for (let dir = -1; dir <= 1; dir += 2) {
        let r = pc.row + dir
        while (r >= 0 && r < GRID) {
          if (blocksBullet(tm.grid[r][pc.col])) break
          const idx = r * GRID + pc.col
          if (stamp[idx] !== sid) {
            stamp[idx] = sid
            coverage[idx]++
          }
          r += dir
        }
      }
    }
  }

  // ---- 3. Select the chokepoint in the lower half ----
  const minRow = Math.max(0, Math.min(GRID - 2, p.chokepointMinRow))
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let bestCol = -1
  let bestRow = -1
  let bestCov = -1
  let bestCover = -1
  let bestBaseDist = Infinity
  for (let row = minRow; row <= GRID - 2; row++) {
    const grow = tm.grid[row]
    const grow2 = tm.grid[row + 1]
    for (let col = 0; col <= GRID - 2; col++) {
      // Passability (inline 2×2 scan).
      let blocked = false
      for (let dr = 0; dr <= 1 && !blocked; dr++) {
        const g = dr === 0 ? grow : grow2
        for (let dc = 0; dc <= 1; dc++) {
          const t = g[col + dc]
          if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') {
            blocked = true
            break
          }
        }
      }
      if (blocked) continue
      const cov = coverage[row * GRID + col]
      if (cov <= 0) continue
      // Rule 5 cover tie-break: steel/brick in the ring around the footprint.
      let cover = 0
      for (let dr = -1; dr <= 2; dr++) {
        const r = row + dr
        if (r < 0 || r >= GRID) continue
        const g = tm.grid[r]
        for (let dc = -1; dc <= 2; dc++) {
          if (dr >= 0 && dr <= 1 && dc >= 0 && dc <= 1) continue // footprint
          const c = col + dc
          if (c < 0 || c >= GRID) continue
          const t = g[c]
          if (t === 'steel') cover += p.chokepointSteelWeight
          else if (t === 'brick') cover += p.chokepointBrickWeight
        }
      }
      const baseDist = Math.abs(col - bc) + Math.abs(row - br)
      if (
        cov > bestCov ||
        (cov === bestCov && cover > bestCover) ||
        (cov === bestCov && cover === bestCover && baseDist < bestBaseDist)
      ) {
        bestCov = cov
        bestCover = cover
        bestBaseDist = baseDist
        bestCol = col
        bestRow = row
      }
    }
  }

  return bestCol >= 0 ? { col: bestCol, row: bestRow } : null
}

/**
 * Rule 3 facing gate applied to the THREAT-STATE / CHASE arms: is the enemy's
 * turret pointing toward the BASE? An enemy standing within margin of a threat
 * point while facing AWAY from the base is not about to shoot it (the user's
 * rule 3: 炮口朝向不在威胁路径方向上则忽略). This reuses the same dominant-
 * axis rule as `enemyThreatPath`'s gate, but anchored on the base itself — the
 * threat-state question is "will this enemy hit the base soon", and a tank
 * only fires along its turret axis.
 *
 * A/B round 3 (per-seed tick-diff, S26 Brick Maze seed 12): without this
 * gate, an armor at (12,12) facing RIGHT (away from the base at (12,24))
 * tripped the margin check, dragged the player 14 cells to "intercept" a
 * non-threat, and B lost while A won by ignoring it. The gate makes the
 * threat state / chase arms consistent with rule 3.
 *
 * NOTE: this anchors on the BASE, while `enemyThreatPath`'s inline gate
 * anchors on the threat POINT (an enemy heading to a flanking threat point
 * passes the path gate but not necessarily this one). Both are intentional:
 * the state/chase question is "is this enemy about to shoot the BASE", the
 * path question is "is this enemy heading along THIS approach corridor".
 * Do not unify them without A/B evidence.
 */
export function facingTowardBase(e: Tank, ec: Cell): boolean {
  const dc = BASE_POS.col - ec.col
  const dr = BASE_POS.row - ec.row
  const horizontal = Math.abs(dc) >= Math.abs(dr)
  const want = horizontal ? (dc > 0 ? 'right' : 'left') : dr > 0 ? 'down' : 'up'
  return e.dir === want
}

/**
 * §88 rule 1: base-threat state — any fully-spawned enemy within
 * `threatPointMargin` cells (Manhattan) of a threat point. Computed per-tick
 * from the CURRENT enemy cells + the cached threat-point set (threat points
 * only change when bricks are destroyed, so the throttle is safe).
 *
 * Rule 3 facing gate (chokepointFacingGate=1): the enemy must also be facing
 * toward the base — an enemy drifting past a threat point with its turret
 * away from the base is not an imminent attack.
 */
export function isThreatStateImpl(self: GodAIInput, threatPoints: Cell[]): boolean {
  const w = self.world
  const margin = self.params.threatPointMargin
  const facingGate = self.params.chokepointFacingGate > 0
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  for (let ei = 0; ei < enemies.length; ei++) {
    const e = enemies[ei]
    if (!e.alive || e.spawnTimer > 0) continue
    const ec = self.tankCell(e)
    for (let ti = 0; ti < threatPoints.length; ti++) {
      const tp = threatPoints[ti]
      const d = Math.abs(tp.col - ec.col) + Math.abs(tp.row - ec.row)
      if (d > margin) continue
      if (facingGate && !facingTowardBase(e, ec)) continue
      return true
    }
  }
  return false
}

/**
 * §88 rule 2 (chase arm): the enemy nearest a threat point (by Manhattan
 * distance to its closest threat point) — the enemy most likely to attack the
 * base next. Returns its cell, or null when no enemies / no threat points.
 *
 * A/B round 2 (per-seed tick-diff): only enemies whose nearest threat point
 * is within `chokepointChaseMaxDist` count as an imminent threat worth
 * diverting for. Without the gate the chase arm dragged the player across the
 * map after an enemy 10 cells from any threat point (S15 seed 24: player at
 * (7,10) chased (4,16) instead of the nearer (10,6)) — the diversion was pure
 * downside, falling back to the normal nearest-enemy chase is byte-identical
 * to OFF. The gate makes chase fire only when an enemy is genuinely about to
 * reach a threat point.
 */
export function threatChaseTargetImpl(self: GodAIInput, threatPoints: Cell[]): Cell | null {
  const w = self.world
  const maxD = self.params.chokepointChaseMaxDist
  const maxPlayerD = self.params.chokepointChaseMaxPlayerDist
  const facingGate = self.params.chokepointFacingGate > 0
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  let bestTank: Tank | null = null
  let bestD = Infinity
  let bestPlayerD = Infinity
  const pc = self.playerCell()
  for (let ei = 0; ei < enemies.length; ei++) {
    const e = enemies[ei]
    if (!e.alive || e.spawnTimer > 0) continue
    const ec = self.tankCell(e)
    // Rule 3 facing gate: an enemy facing away from the base is not about to
    // reach a threat point with intent to shoot — skip it entirely.
    if (facingGate && !facingTowardBase(e, ec)) continue
    let minD = Infinity
    for (let ti = 0; ti < threatPoints.length; ti++) {
      const tp = threatPoints[ti]
      const d = Math.abs(tp.col - ec.col) + Math.abs(tp.row - ec.row)
      if (d < minD) minD = d
    }
    if (minD > maxD) continue // not an imminent threat — no diversion
    // Player-distance cap (A/B round 3): intercepting only pays off when the
    // player can actually reach the enemy before it hits the threat point. A
    // 27-cell chase is a race the player loses (S32 seed 10: player at (8,3)
    // sent after (0,22) while A's normal hunt won).
    //
    // The cap scales with the enemy's speed (inverse of kindSpeedFactor in
    // SmartThreatModel: fast 1.0 / power 0.7 / basic 0.5 / armor 0.35): a
    // slow armor 25 cells away can still be intercepted before it reaches the
    // base, while a fast tank must be chased from nearby. S32 seed 48: armor
    // at (2,22) chased from 25 cells → the march down the base column won;
    // a flat cap that blocked it reverted B to A's losing hunt.
    const playerD = Math.abs(ec.col - pc.col) + Math.abs(ec.row - pc.row)
    const speedScale =
      e.kind === 'armor' ? 3 : e.kind === 'basic' ? 2 : e.kind === 'power' ? 1.5 : 1
    if (maxPlayerD > 0 && playerD > maxPlayerD * speedScale) continue
    if (minD < bestD || (minD === bestD && playerD < bestPlayerD)) {
      bestD = minD
      bestPlayerD = playerD
      bestTank = e
    }
  }
  if (!bestTank) return null
  const tc = self.tankCell(bestTank)
  return { col: tc.col, row: tc.row }
}

/**
 * Throttled plan computation: recompute when the cached plan is stale
 * (world.frame - plan.tick >= chokepointReplanTicks) or missing. Pure function
 * of World state + frame — deterministic, replay-safe. Callers (GodAIInput
 * wrappers) own the cache; this just computes.
 */
export function computeChokepointPlanImpl(self: GodAIInput): ChokepointPlan {
  const threatPoints = computeThreatPointsImpl(self)
  const chokepoint = computeChokepointImpl(self, threatPoints)
  return { tick: self.world.frame, threatPoints, chokepoint }
}
