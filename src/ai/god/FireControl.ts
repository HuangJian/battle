import type { GodAIInput } from '../GodAIInput'
import type { Direction } from '../../constants'
import type { Tank, TankKind } from '../../types'
import { CELL, TANK, FIELD, GRID, BASE_POS } from '../../constants'
import { snap } from '../../utils/helpers'
import { AIM_RANGE_CELLS, kindThreatWeight } from './constants'
import { estimatedEnemyLevel } from './EnemyModel'

// ============================================================
// FireControl — target scanning + fire decisions (T2a, T9, T2b, T6, T11, M6)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state
// (world/params/rng/fields) and call sibling methods via the public
// wrappers on GodAIInput. This is a pure relocation — behavior is identical.
// ============================================================

/**
 * Find the direction to the best enemy tank in the same row/col, using
 * **global vision** (plan §3.1). T9: when multiple enemies share a
 * row/col, select by threat weight (power > armor > fast > basic) + HP,
 * not just proximity.
 *
 * Returns the direction to turn to face the selected enemy, or null.
 */
export function findEnemyDirectionImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Direction | null {
  const w = self.world
  let bestDir: Direction | null = null
  let bestScore = -Infinity

  // Widen alignment threshold from TANK/2 (16px) to TANK (32px).
  // This makes T2a trigger when enemies are within 2 cells of alignment,
  // not just exactly aligned. Critical for getting kills — the tight
  // threshold meant the AI almost never aligned with moving enemies.
  const halfT = TANK

  const tanks = w.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (!t.alive || t.spawnTimer > 0) continue
    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const dx = tcx - pcx
    const dy = tcy - pcy

    let dir: Direction | null = null
    let dist = Infinity

    if (Math.abs(dx) < halfT) {
      if (dy < 0) {
        dir = 'up'
        dist = -dy
      } else {
        dir = 'down'
        dist = dy
      }
    } else if (Math.abs(dy) < halfT) {
      if (dx < 0) {
        dir = 'left'
        dist = -dx
      } else {
        dir = 'right'
        dist = dx
      }
    }

    if (!dir || dist > AIM_RANGE_CELLS * CELL) continue // static — always full field

    // T9: score = threat weight × 1000 - distance (prefer high-threat,
    // then nearest among equal threat).
    // M0.5 退役（2026-08-03）: D2 damagedArmorBonus 加权已移除（S32 -8.4pp 否决，
    // 移入 experimental.ts 归档）——hpFactor 保留（原评分组成部分）。
    let threatWeight = kindThreatWeight(t.kind)
    const bonusWeight = t.bonus ? 2 : 0 // S5c: bonus enemies are higher priority
    const hpFactor = t.hp / (t.maxHp || 1)
    // M3 (plan/God-AI-Redesign-v2 §4.2b, tierWeightScale): when ON, scale the
    // threat weight by the EnemyModel's estimated level — the AI commits its
    // fire priority to enemies it has SEEN play well (empirically accurate /
    // coordinated / disciplined), rather than a static kind weight alone.
    // 0 at default ⇒ byte-identical to pre-M3.
    if (self.params.tierWeightScale > 0) {
      const lvl = estimatedEnemyLevel(self)
      if (lvl > 0) threatWeight *= 1 + self.params.tierWeightScale * lvl
    }
    const score = (threatWeight + bonusWeight) * 10000 - dist + hpFactor * 100

    if (score > bestScore) {
      bestScore = score
      bestDir = dir
    }
  }

  return bestDir
}

// Hoisted constants — flat arrays (perf §63): tuple destructuring
// `const [ox, oy] = offsets[oi]` allocates an iterator per scan-offset loop;
// a single flat array lets the loop read `PERP_OFFSETS[oi]` directly.
//
// DIR_DX/DIR_DY replace DIR_VECTORS[dir] (a Record<string,{dx,dy}> lookup
// that forces a string-keyed dict probe). Index by `dirIdx` instead —
// string→index is a 4-way ternary, no dict hash.
const DIR_DX: readonly number[] = [0, 0, -1, 1] // up, down, left, right
const DIR_DY: readonly number[] = [-1, 1, 0, 0]
// Perpendicular pixel offsets: for a vertical scan (up/down) the offset is on
// the X axis (±CELL/2), for horizontal scans on Y. Same magnitude both axes.
const PERP_OFFSETS: readonly number[] = [-CELL / 2, CELL / 2]

/** The shape `scanAheadImpl` fills in. Callers read it synchronously. */
export interface ScanResult {
  enemy: boolean
  wall: boolean
  steel: boolean
  baseWall: boolean
  baseWallDist: number
  baseSteel: boolean
  steelCol: number
  steelRow: number
  enemyDist: number
  enemyKind: TankKind
  enemyHp: number
  enemyMaxHp: number
}

/** Factory for the reusable per-direction scan buffers held on GodAIInput. */
export function makeScanResult(): ScanResult {
  return {
    enemy: false,
    wall: false,
    steel: false,
    baseWall: false,
    baseWallDist: Infinity,
    baseSteel: false,
    steelCol: -1,
    steelRow: -1,
    enemyDist: Infinity,
    enemyKind: 'basic',
    enemyHp: 1,
    enemyMaxHp: 1,
  }
}

/**
 * Scan ahead in a direction for enemies, walls, and base protection.
 * Returns what's in the line of fire, distinguishing steel from brick
 * (T11) and detecting base-protection bricks (T6).
 *
 * Writes into `self._scanResults[dirIdx]` (a reusable object per direction)
 * to avoid allocating a result object on every call. Callers use the result
 * immediately and never store the reference, so this is safe.
 *
 * (perf §123) Those four buffers double as a **per-tick memo**. `think()` runs
 * exactly once per tick (guarded by `_thought`) and nothing mutates the World
 * during it — movement/combat are applied later in Simulation — so for a fixed
 * scan origin `(pcx, pcy)` this function is pure in `dir`. Direct callers
 * (tests / tools/diag) must keep the same key unchanged between calls or call
 * `endFrame()`/`reset()` to invalidate. `_scanCacheMask`
 * records which direction slots are already filled for the origin stored in
 * `_scanCacheX/_scanCacheY`; a different origin clears the mask, and
 * `endFrame()` clears it every tick. Repeat calls (shouldFireInDir, the
 * aggro/engage/hunt candidates and ThreatAssessor all scan the same aim/move
 * direction from the player centre) return the identical object — byte-identical.
 *
 * (perf §63): DIR_VECTORS lookup, tileMap.get, isBaseProtectionBrick, aabb
 * are all inlined. The aligned-tank pre-filter guarantees the perpendicular
 * axis of the aabb is satisfied, so the inner scan-axis check needs only 2
 * comparisons instead of 4. Cell indices `col/row` are tracked incrementally
 * (col += vdx) to skip per-step Math.floor; pixel positions `cx/cy` are kept
 * to preserve the original `cx > FIELD` boundary semantics exactly.
 */
export function scanAheadImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): ScanResult {
  const w = self.world
  // Fast dir → index (avoid DIR_VECTORS string-keyed dict lookup).
  const dirIdx = dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3

  // §123 per-tick memo — see the doc comment above. NaN sentinels on the first
  // call always miss (NaN !== NaN).
  const bit = 1 << dirIdx
  if (self._scanCacheX === pcx && self._scanCacheY === pcy) {
    if ((self._scanCacheMask & bit) !== 0) return self._scanResults[dirIdx]
  } else {
    self._scanCacheX = pcx
    self._scanCacheY = pcy
    self._scanCacheMask = 0
  }
  self._scanCacheMask |= bit

  const vdx = DIR_DX[dirIdx]
  const vdy = DIR_DY[dirIdx]
  const vertical = vdx === 0 // up or down
  const offsets = PERP_OFFSETS
  const tanksArr = w.tanks
  // Direct grid access — inlines tileMap.get (with OOB→'steel' fallback below).
  const grid = w.tileMap.grid
  // Hoist hasBase + base position params — inlines isBaseProtectionBrick.
  const hasBase = self.hasBase
  const baseCol = BASE_POS.col // 12
  const baseRow = BASE_POS.row // 24
  const wallScanR = self.params.baseWallScanRadius
  // §D4: exact-ring base-wall flag — see the param doc (Battlement pocket fix).
  const exactRing = self.params.baseWallExactRing > 0

  const r = self._scanResults[dirIdx]
  r.enemy = false
  r.wall = false
  r.steel = false
  r.baseWall = false
  r.baseWallDist = Infinity
  r.baseSteel = false
  r.steelCol = -1
  r.steelRow = -1
  r.enemyDist = Infinity
  r.enemyKind = 'basic'
  r.enemyHp = 1
  r.enemyMaxHp = 1

  // Reusable aligned-tank buffer (perf): pre-filter tanks whose perpendicular
  // position overlaps the scan line. For a vertical scan, the x-condition of
  // the aabb (t.x − 1 < sx < t.x + 33) is constant per offset — if it fails,
  // the tank can NEVER be hit at any cell step. This reduces the per-cell tank
  // loop from O(N) to O(alignedN), where alignedN is typically 0-2. The aabb
  // check at each cell is identical to the original — just fewer iterations.
  const aligned = self._scanAligned

  for (let oi = 0; oi < 2; oi++) {
    const off = offsets[oi]
    const sx = vertical ? pcx + off : pcx
    const sy = vertical ? pcy : pcy + off

    // Pre-filter: collect tanks whose perpendicular axis overlaps this offset's
    // scan line (the constant half of the aabb condition). 33 = TANK + 1.
    let alignedCount = 0
    for (let ti = 0; ti < tanksArr.length; ti++) {
      const t = tanksArr[ti]
      if (!t.alive || t.spawnTimer > 0) continue
      if (vertical) {
        if (sx > t.x - 1 && sx < t.x + 33) aligned[alignedCount++] = t
      } else {
        if (sy > t.y - 1 && sy < t.y + 33) aligned[alignedCount++] = t
      }
    }

    // First step (d = CELL): pixel position + cell index.
    let cx = sx + vdx * CELL
    let cy = sy + vdy * CELL
    let col = Math.floor(cx / CELL)
    let row = Math.floor(cy / CELL)
    // stepCount = d / CELL, tracked incrementally (no per-step division).
    let stepCount = 1

    // Loop matches original boundary check exactly: `cx > FIELD` (strict
    // greater-than) lets cx == FIELD enter, which makes col = GRID (= 26)
    // hit the OOB→'steel' branch — preserving the original tileMap.get
    // fallback behavior at the field edge for cell-aligned scan starts.
    while (cx >= 0 && cx <= FIELD && cy >= 0 && cy <= FIELD) {
      // Inline tileMap.get(col, row): bounds check + direct grid access.
      // OOB returns 'steel' (matches TileMap.get fallback for off-grid cells).
      let terrain: string
      if (col < 0 || col >= GRID || row < 0 || row >= GRID) {
        terrain = 'steel'
      } else {
        terrain = grid[row][col]
      }

      // (perf §124, REJECTED) Wrapping these compares in a `terrain !== 'empty'`
      // guard to short-circuit open cells measured SLOWER — see the note in
      // World.rectHitsTerrain. Leave the flat compare chain alone.
      if (terrain === 'steel') {
        // Store steel cell coords for post-loop baseSteel check (§70).
        // Only two assignments — keeps the hot loop untouched (V8 JIT stable).
        r.steelCol = col
        r.steelRow = row
        r.steel = true
        r.wall = true
        break
      }
      if (terrain === 'brick') {
        // Inline isBaseProtectionBrick: only when stage has a base, and
        // the brick is within the configured radius AND the cross-shaped
        // band (within 2 on at least one axis) of the base.
        // §D4: baseWallExactRing=1 uses the EXACT ring-cell predicate
        // (identical to SimulationCombat.isBaseProtectionCell) instead of the
        // loose rectangle — the rectangle flags ordinary bricks up to
        // baseWallScanRadius cells from the base (Battlement (10,19):
        // dc=2/dr=5, not a ring cell), and on a dual-offset scan that far
        // flag suppresses break-through fire at the REAL ordinary brick in
        // front → spawn-pocket lock (player never digs, zero fire).
        if (hasBase) {
          if (exactRing) {
            // Ring cells: row 23 across cols 11-14; cols 11/14 at rows 24-25.
            if (
              (row === baseRow - 1 && col >= baseCol - 1 && col <= baseCol + 2) ||
              (col === baseCol - 1 && (row === baseRow || row === baseRow + 1)) ||
              (col === baseCol + 2 && (row === baseRow || row === baseRow + 1))
            ) {
              r.baseWall = true
              r.baseWallDist = stepCount
            }
          } else {
            const dc = col - baseCol
            const dr = row - baseRow
            const ad = dc < 0 ? -dc : dc
            const ar = dr < 0 ? -dr : dr
            if (ad <= wallScanR && ar <= wallScanR && (ad <= 2 || ar <= 2)) {
              r.baseWall = true
              r.baseWallDist = stepCount
            }
          }
        }
        r.wall = true
        break
      }
      if (terrain === 'base') {
        r.baseWall = true
        r.baseWallDist = stepCount
        r.wall = true
        break
      }

      // Aligned tank check — inlined aabb. The perpendicular axis was
      // pre-filtered (sx > t.x-1 && sx < t.x+33 ⟺ perp half of aabb), so
      // only the scan-axis half (2 comparisons) is needed here. TANK=32.
      let found = false
      if (vertical) {
        // cx = sx (constant per offset); cy varies. Check cy vs t.y.
        for (let ai = 0; ai < alignedCount; ai++) {
          const t = aligned[ai]
          // aabb(cx-1, cy-1, 2, 2, t.x, t.y, TANK, TANK) ⟺
          //   cx-1 < t.x+TANK && t.x < cx+1 (perp, pre-filtered)  AND
          //   cy-1 < t.y+TANK && t.y < cy+1 (scan axis — checked here)
          if (cy - 1 < t.y + 32 && t.y < cy + 1) {
            if (stepCount < r.enemyDist) {
              r.enemyDist = stepCount
              r.enemyKind = t.kind
              r.enemyHp = t.hp
              r.enemyMaxHp = t.maxHp
            }
            r.enemy = true
            found = true
            break
          }
        }
      } else {
        // cy = sy (constant per offset); cx varies. Check cx vs t.x.
        for (let ai = 0; ai < alignedCount; ai++) {
          const t = aligned[ai]
          if (cx - 1 < t.x + 32 && t.x < cx + 1) {
            if (stepCount < r.enemyDist) {
              r.enemyDist = stepCount
              r.enemyKind = t.kind
              r.enemyHp = t.hp
              r.enemyMaxHp = t.maxHp
            }
            r.enemy = true
            found = true
            break
          }
        }
      }
      if (found) break

      // Advance to next cell (pixel + cell index in lockstep).
      cx += vdx * CELL
      cy += vdy * CELL
      col += vdx
      row += vdy
      stepCount++
    }
  }

  // §70: Post-loop baseSteel detection. Compute OUTSIDE the hot scan loop
  // to avoid changing V8's JIT optimization of the per-cell iteration.
  // OOB cells (steelCol=-1 or out of grid) are field edges, not base
  // protection — skip them.
  if (
    r.steel &&
    hasBase &&
    r.steelCol >= 0 &&
    r.steelCol < GRID &&
    r.steelRow >= 0 &&
    r.steelRow < GRID
  ) {
    const dc = r.steelCol - baseCol
    const dr = r.steelRow - baseRow
    const ad = dc < 0 ? -dc : dc
    const ar = dr < 0 ? -dr : dr
    if (ad <= wallScanR && ar <= wallScanR && (ad <= 2 || ar <= 2)) {
      r.baseSteel = true
    }
  }

  return r
}

/**
 * T6: Check if a brick cell is part of the base's defensive wall.
 * The base is at rows 24-25, cols 12-13. Bricks within a small radius
 * of the base are considered "protection" bricks.
 * Gap B: returns false when the stage has no base.
 */
export function isBaseProtectionBrickImpl(self: GodAIInput, col: number, row: number): boolean {
  if (!self.hasBase) return false
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const dc = Math.abs(col - bc)
  const dr = Math.abs(row - br)
  // The base occupies cols 12-13, rows 24-25. Protection bricks are
  // those immediately adjacent (within 2 cells) that form the wall.
  const r = self.params.baseWallScanRadius
  return dc <= r && dr <= r && (dc <= 2 || dr <= 2)
}

/**
 * P2.4: Predictive firing (lead the target). When an enemy is moving
 * perpendicular to the player's facing direction and will cross the
 * line of fire at the same time the bullet arrives, fire preemptively.
 * This is critical for hitting fast enemies (2 px/tick) that dodge
 * bullets by moving perpendicular — the player can't catch them in a
 * chase (1 px/tick), but can intercept them with a well-timed shot.
 *
 * Algorithm: for each enemy moving perpendicular to the player's aim
 * direction, calculate:
 *   - enemyTimeToCross = perpendicular distance / enemy speed
 *   - bulletTimeToReach = parallel distance / bullet speed
 * If |enemyTimeToCross - bulletTimeToReach| ≤ tolerance, fire.
 * Tolerance = half the tank-crossing time + small slack, so the bullet
 * arrives while the enemy's body is still straddling the line of fire.
 */
function predictEnemyCrossingImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): boolean {
  const w = self.world
  // §79: controlled tank, not `w.player` — bullet speed is per-tank.
  const p = self.controlledTank(w)!
  const bulletSpeed = p.bulletSpeed
  if (bulletSpeed <= 0) return false

  const vertical = dir === 'up' || dir === 'down'

  const tanks = w.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (!t.alive || t.spawnTimer > 0) continue
    if (!t.moving) continue

    // Enemy must be moving perpendicular to the player's facing dir.
    const enemyVertical = t.dir === 'up' || t.dir === 'down'
    if (vertical === enemyVertical) continue

    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const dx = tcx - pcx
    const dy = tcy - pcy

    // Enemy must be in front of the player (in the facing direction).
    const inFront = vertical
      ? (dir === 'up' && dy < 0) || (dir === 'down' && dy > 0)
      : (dir === 'left' && dx < 0) || (dir === 'right' && dx > 0)
    if (!inFront) continue

    // Enemy must be moving toward the player's line (not away).
    const movingToward = vertical
      ? (t.dir === 'left' && dx > 0) || (t.dir === 'right' && dx < 0)
      : (t.dir === 'up' && dy > 0) || (t.dir === 'down' && dy < 0)
    if (!movingToward) continue

    // Perpendicular distance: how far the enemy is from the player's line.
    const perpDist = vertical ? Math.abs(dx) : Math.abs(dy)
    // Parallel distance: how far the enemy is along the facing direction.
    const parallelDist = vertical ? Math.abs(dy) : Math.abs(dx)
    if (parallelDist > AIM_RANGE_CELLS * CELL) continue // too far for bullet

    const enemySpeed = t.speed
    if (enemySpeed <= 0) continue

    const enemyTimeToCross = perpDist / enemySpeed
    const bulletTimeToReach = parallelDist / bulletSpeed

    // Tolerance: the time it takes for half the tank body to cross the
    // line, plus a few ticks of slack for sub-pixel jitter.
    const tolerance = TANK / (2 * enemySpeed) + 6
    if (Math.abs(enemyTimeToCross - bulletTimeToReach) <= tolerance) {
      return true // pure check — caller handles RNG
    }
  }
  return false
}

/**
 * §80: Turn-snap aim guard — will this aim still see the enemy AFTER the
 * turn physically moves the tank?
 *
 * `Simulation.updateMovement` axis-locks movement to one axis per tick and
 * **snaps the perpendicular coordinate to the grid** on every direction
 * change (`axis === 'x' ? tank.y = snap(tank.y, CELL) : tank.x = snap(...)`).
 * A tank sitting at a non-grid-aligned sub-cell offset therefore *teleports*
 * up to CELL/2 px sideways the instant it turns — which can drag the target
 * out of `scanAhead`'s ±CELL/2 offset lines.
 *
 * That produces a period-2 deadlock in the aggressive (freeze) branch, which
 * — unlike T2a (`_campTicks`) and navigate (`_navStuckTicks`) — has **no
 * anti-stall guard**:
 *
 *   tick A: off-grid, scan sees enemy → turn to aim → snap pushes tank off
 *           the firing line
 *   tick B: on-grid, scan sees nothing → navigate perpendicular → snap
 *           pushes tank back to the tick-A position
 *   → repeat forever, zero net displacement, for the whole freeze window.
 *
 * Observed in `classic-s11-clear-l1-t51-seed…123.replay` (P2 pinned at
 * cell (1,4) for the entire 20s freeze) and reproduced across the sweep:
 * 4.3% of all co-op freeze ticks were burned this way, worst case 1166 of
 * 1200 freeze ticks (97%) on s27.
 *
 * The guard re-runs the scan from the **post-snap** position. If the enemy
 * survives the turn, the aim is real — commit. If not, the aim is an
 * illusion; return false so the caller falls through to navigate (which has
 * its own stall detection and will actually reposition the tank).
 *
 * @param gateOn via `self.params.aimTurnSnapGuard` — 0 = OFF, byte-identical
 *   to pre-§80 behavior (returns true without scanning).
 */
export function aimSurvivesTurnImpl(self: GodAIInput, p: Tank, aimDir: Direction): boolean {
  if (self.params.aimTurnSnapGuard <= 0) return true
  // Already facing that way — no turn, therefore no snap.
  if (p.dir === aimDir) return true
  // Mirror Simulation.updateMovement: horizontal move snaps y, vertical snaps x.
  const horizontal = aimDir === 'left' || aimDir === 'right'
  const nx = horizontal ? p.x : snap(p.x, CELL)
  const ny = horizontal ? snap(p.y, CELL) : p.y
  // Already grid-aligned on the perpendicular axis — the snap is a no-op.
  if (nx === p.x && ny === p.y) return true
  // NOTE (perf §123): scanAheadImpl writes into the shared per-direction
  // buffers `self._scanResults[dirIdx]` (memoized per origin+dir within a
  // tick). Callers must invoke this guard BEFORE computing their own scan
  // result, never after, or their result gets clobbered.
  return scanAheadImpl(self, nx + TANK / 2, ny + TANK / 2, aimDir).enemy
}

/**
 * Decide whether to fire in the current facing direction. Fires when:
 * - An enemy is in the line of fire (scanAhead found enemy)
 * - An enemy bullet is approaching (to intercept, T5)
 * - P2.4: An enemy is moving perpendicular and will cross the line of fire
 *   at the right time (predictive firing / lead the target)
 *
 * T2b: does NOT fire at walls during navigation. A* routes around walls,
 * so firing at them wastes bullets and cooldown time. Wall-breaking is
 * handled exclusively by the T2a stop-and-aim logic (which fires at walls
 * between the player and a visible enemy in the same row/col).
 *
 * T6: base protection bricks are never fired at.
 * T11: steel is only fired at if player level ≥ 3 (can pierce steel).
 */
export function shouldFireInDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  allowWallFire = true,
): boolean {
  const w = self.world
  // §79: controlled tank, not `w.player` — the T11 steel-pierce gate reads
  // `p.level`, which is per-tank (P1 and P2 upgrade independently).
  const p = self.controlledTank(w)!

  // Inline scanAheadImpl (perf §66): the thin self.scanAhead wrapper adds
  // ~14ms (2.8%) of function-call overhead. shouldFireInDir is called up to
  // ~3× per think; each call goes through the wrapper. Calling scanAheadImpl
  // directly (same module) skips one V8 call frame.
  const result = scanAheadImpl(self, pcx, pcy, dir)

  // T6/T11: Don't fire at base protection bricks or steel (level < 3).
  // These checks MUST come before the enemy check because scanAhead uses
  // two independent offset scan lines — if offset 0 finds steel and offset 1
  // finds an enemy, BOTH result.steel and result.enemy are true. Checking
  // enemy first would cause the AI to fire through steel.
  if (result.baseWall) return false
  if (result.baseSteel && (p.level ?? 0) >= 3) return false
  // Non-ring steel (level < 3): can't pierce, block. Non-ring steel at
  // level ≥ 3 falls through to the enemy check (can pierce).
  if (result.steel && !result.baseSteel && (p.level ?? 0) < 3) return false

  // §121: self-fire base guard — never fire a bullet whose CENTER line
  // (the actual 6px path, NOT the scan's ±8px offset lines) can reach the
  // base. The scan can be screened by an enemy off the bullet's path (the
  // §120 enemy-screen self-kill: scan.enemy=true, baseWall=false because the
  // enemy body covers both offset lines, but the 6px bullet passes beside
  // it into the base). Mode 2 (lenient) keeps the shot when an enemy body
  // truly overlaps the corridor (the bullet hits the enemy first).
  if (self.params.selfFireBaseGuard > 0) {
    if (shotReachesBaseImpl(self, pcx, pcy, dir)) {
      if (self.params.selfFireBaseGuard < 2 || !enemyInShotCorridorImpl(self, pcx, pcy, dir)) {
        self._selfFireGuardBlocks++
        return false
      }
    }
  }

  // Enemy in line of fire — fire.
  if (result.enemy) {
    return self.rng.next() >= self.params.aimError
  }

  // Steel with level ≥ 3 (no enemy found): fire to pierce.
  if (result.steel) {
    return self.rng.next() >= self.params.aimError
  }

  // Check for enemy bullet to intercept (T5).
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const aligned =
      dir === 'up' || dir === 'down'
        ? Math.abs(bcx - pcx) < CELL * 0.75
        : Math.abs(bcy - pcy) < CELL * 0.75
    if (!aligned) continue
    const inFront =
      (dir === 'up' && bcy < pcy) ||
      (dir === 'down' && bcy > pcy) ||
      (dir === 'left' && bcx < pcx) ||
      (dir === 'right' && bcx > pcx)
    if (!inFront) continue
    const d = Math.abs(dir === 'up' || dir === 'down' ? bcy - pcy : bcx - pcx)
    if (d < TANK * 4) {
      return self.rng.next() >= self.params.aimError
    }
  }

  // P2.4: Predictive firing (lead the target). If no wall blocks the
  // line of fire, check if an enemy moving perpendicular will cross
  // the bullet's path at the right time. This lets the player hit
  // fast enemies that dodge bullets by moving sideways.
  if (!result.wall) {
    if (predictEnemyCrossingImpl(self, pcx, pcy, dir)) {
      return self.rng.next() >= self.params.aimError
    }
  }

  // Fire at brick walls to clear paths and create firing lanes.
  // This is critical for mobility — without it, the player navigates
  // around walls, which takes too long and lets enemies reach the base.
  // Never fire at base protection bricks (T6) or steel (T11).
  // When allowWallFire is false (moving freely during navigation in
  // classic bulletCap mode), skip wall-firing to reserve the bullet cap
  // for enemies and enemy bullets.
  if (allowWallFire && result.wall && !result.baseWall && !result.steel) {
    return self.rng.next() >= self.params.aimError
  }

  return false
}

/**
 * §74: Steel-fire gate — should the given scan result block firing?
 *
 * Mirrors the T11 steel check in shouldFireInDirImpl exactly (which inlines
 * the same `result.steel && !result.baseSteel && level < 3` check):
 * `result.steel && !result.baseSteel && level < STEEL_PIERCE_PLAYER_LEVEL`.
 * Used by shouldFireBreakThroughImpl (the navigate break-through sites in
 * think()), so the steel gate is uniform everywhere the AI might fire at a
 * wall — the navigate sites previously bypassed shouldFireInDirImpl and
 * never applied T11.
 *
 * Base-ring steel (`baseSteel`) is deliberately NOT blocked here: base-ring
 * steel is already handled by the §70 guard at each call site, and at
 * level < 3 the player cannot pierce base steel either, so T6's
 * "never destroy own base" concern is moot below the pierce level.
 *
 * @param gateOn the steelFireGate param value (0 = OFF = byte-identical
 *   to pre-§74 behavior).
 */
export function steelFireBlockedImpl(
  result: { steel: boolean; baseSteel: boolean },
  level: number | undefined,
  gateOn: number,
): boolean {
  return gateOn > 0 && result.steel && !result.baseSteel && (level ?? 0) < 3
}

/**
 * §74: Break-through fire decision (T2b navigate + aggressive navigate).
 *
 * The navigate branches fire at a wall in the movement direction to break
 * through it (dig path). That fire is FUTILE when the wall is steel and the
 * player cannot pierce steel (level < 3) — the bullet does nothing, the AI
 * wastes the bullet cap, and then camps at the wall for the full camp
 * timeout (the reported "shoot steel → can't break → stuck in place"
 * behavior). This helper applies the steel-fire gate to the break-through
 * condition.
 *
 * NOTE (per-seed A/B finding, 2026-08-01): this gate is applied ONLY to the
 * break-through sites. It is deliberately NOT applied to the T2a/aggressive
 * stop-and-aim fire (which fires when scan.enemy is true) — the dual-offset
 * case there (steel on one scan line, enemy on the other) means the enemy is
 * genuinely reachable by the center-line bullet, and suppressing that fire
 * costs kills (arena A/B: 20 kills → 7 kills, gameover @1634 vs clear @4592).
 */
export function shouldFireBreakThroughImpl(
  bs: {
    enemy: boolean
    baseWall: boolean
    baseSteel: boolean
    steel: boolean
  },
  level: number | undefined,
  gateOn: number,
): boolean {
  if (steelFireBlockedImpl(bs, level, gateOn)) return false
  // §70/§74 base-ring guard: never fire through base brick/steel. NO `bs.enemy ||
  // ...` short-circuit — the old `bs.enemy || (!bs.baseWall && ...)` fired through
  // the base wall on dual-offset scans (see DECISIONS §75 / commit 54600f9),
  // causing 4 player-suicide base destructions in S32. Break-through is for
  // breaking walls, so only fire when the wall ahead is breakable (not a base
  // wall / base-ring steel). Enemy-as-obstacle still fires (baseWall=false).
  return !bs.baseWall && !(bs.baseSteel && (level ?? 0) >= 3)
}

/**
 * §121: Is (col,row) one of the 8 permanent base protection-ring cells that
 * STOP a bullet before it can damage the base? The ring is a real barrier in
 * bulletHitsTerrain (unlike ordinary brick which bullets plow through). The
 * bottom edge of the ring is outside the field (rows 26), so only the top /
 * left / right edges are valid: row 23 across cols 11-14, and cols 11 / 14
 * at rows 24-25.
 *
 * MUST stay byte-identical to SimulationCombat.isBaseProtectionCell (verified
 * 2026-08-04) — a drift here is a false NEGATIVE: the guard would think a
 * brick stops the bullet when the simulation plows it into the base.
 */
function isBaseRingCell(col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  if (row === br - 1 && col >= bc - 1 && col <= bc + 2) return true
  if (col === bc - 1 && (row === br || row === br + 1)) return true
  return col === bc + 2 && (row === br || row === br + 1)
}

/**
 * §121: Would a bullet fired from (pcx,pcy) along `dir` REACH the base eagle?
 *
 * Walks the bullet's actual CENTER line (its real 6px path) — unlike
 * scanAheadImpl's two ±8px offset lines, which can be screened by an enemy
 * up to ~25px off the center line. That screening is the §120 self-kill
 * mechanism: the scan sees `scan.enemy` (closer than the eagle) so the §74
 * guard allows fire, but the 6px bullet misses the off-line enemy and
 * continues into the base.
 *
 * The walk mirrors bulletHitsTerrain's terrain semantics exactly:
 *   - ring brick/steel (isBaseRingCell) STOPS the bullet → safe (no base hit)
 *   - non-ring steel stops unless the player has steel-pierce (level ≥ 3,
 *     bullet power 2 — same gate as steelFireBlockedImpl)
 *   - non-ring brick is PLOWED through (destroyed), not a stop
 *   - 'base' terrain (or the 2×2 base area) reached → the bullet WOULD
 *     damage the base → true
 *
 * Tanks are deliberately NOT blockers: an enemy on the line can dodge away
 * before the bullet arrives (the exact §120 mechanism). Terrain is the only
 * reliable stop.
 *
 * Gate: 0 = OFF (byte-identical); the caller decides from
 * `self.params.selfFireBaseGuard`. Pure World read — no RNG, no mutation.
 */
export function shotReachesBaseImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): boolean {
  if (!self.hasBase) return false
  const w = self.world
  const p = self.controlledTank(w)
  const pierce = (p?.level ?? 0) >= 3
  const dirIdx = dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3
  const vdx = DIR_DX[dirIdx]
  const vdy = DIR_DY[dirIdx]
  const grid = w.tileMap.grid
  // Base 2×2 rect + the bullet's 6px hitbox (±BULLET/2 = ±3) — a bullet whose
  // center line runs up to 3px OUTSIDE the eagle span still grazes it (hard
  // S16 s82: center x=224, box [221,227] overlaps the eagle [192,224) by 3px
  // while the center column 14 never walks a 'base' cell).
  const baseL = BASE_POS.col * CELL - 3
  const baseR = (BASE_POS.col + 2) * CELL + 3
  const baseT = BASE_POS.row * CELL - 3
  const baseB = (BASE_POS.row + 2) * CELL + 3

  // Hot-path quick reject (§14): the 6px box overlaps the base rect ONLY if
  // the center lies inside the expanded rect. A shot whose perpendicular
  // coordinate is outside that band can never reach the eagle — bail before
  // walking the 26-cell line. DEFAULT=2 runs this on every fire-direction
  // check in pool games; most shots aren't aimed at the base columns.
  const vertical = dir === 'up' || dir === 'down'
  if (vertical ? pcx <= baseL || pcx >= baseR : pcy <= baseT || pcy >= baseB) return false

  for (let d = CELL; d <= FIELD; d += CELL) {
    const fx = pcx + vdx * d
    const fy = pcy + vdy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break
    const col = Math.floor(fx / CELL)
    const row = Math.floor(fy / CELL)
    if (col < 0 || col >= GRID || row < 0 || row >= GRID) break
    const terrain = grid[row][col]
    // Ring brick/steel stops the bullet before the eagle → safe to fire. A
    // destroyed (empty) ring cell is a gap — fall through to the rect check
    // (the bullet at this position can already be grazing the eagle edge).
    if (isBaseRingCell(col, row) && (terrain === 'brick' || terrain === 'steel')) {
      return false
    }
    // Bullet-box overlap with the base rect (catches the 3px edge-graze that
    // the center-cell walk misses: hard S16 s82 — center x=224, box [221,227]
    // overlaps the eagle [192,224) by 3px while column 14 never has 'base').
    if (fx > baseL && fx < baseR && fy > baseT && fy < baseB) return true
    if (terrain === 'base') return true
    if (terrain === 'steel') {
      if (pierce) continue // level ≥ 3 pierces non-ring steel
      return false
    }
    if (terrain === 'brick') continue // plowed through (destroyed)
    // empty / water / forest / ice — bullets pass (bulletHitsTerrain skips)
  }
  return false
}

/**
 * §152-W1: would a bullet fired from (pcx,pcy) along `dir` hit NON-RING steel
 * before travelling `maxDist` px?
 *
 * Walks the bullet's ACTUAL 6px path (BULLET=6 → ±3px half-width) in CELL
 * steps, checking every grid cell the box overlaps — the SAME cell set
 * `SimulationCombat.bulletHitsTerrain` tests per tick, so a true result means
 * the sim would provably stop the bullet before it reaches the enemy (no
 * false positives — the §74 lesson). Unlike `scanAheadImpl`'s two ±8px offset
 * lines (which can see steel that the 6px bullet never touches), this is the
 * exact center-line predicate.
 *
 * The W1 mechanism (hard S12 Lattice seed 934391936, 0:59-1:01): the player
 * stopped at (17,18) with center x=288 exactly on the col-17/18 boundary and
 * stop-and-aimed up at a fast enemy at (17,3). The scan saw the enemy on the
 * left offset line, but the bullet box [285,291] clips the steel column 18
 * [288,304) at rows 8-9 — the bullet died at row 9, never reaching the enemy.
 * The T2a/aggressive stop-and-aim gates only checked baseWall/baseSteel, so
 * the fire was wasted (and the player camped).
 *
 * Semantics (mirrors bulletHitsTerrain exactly): non-ring steel stops the
 * bullet unless the player can pierce (level ≥ 3 → bullet power 2); ring
 * steel/brick is skipped (the existing baseWall/baseSteel gates handle the
 * base ring); non-ring brick is plowed through; OOB cells are 'steel'
 * (TileMap.get fallback — the bullet dies at the field edge).
 *
 * Gate: caller checks `self.params.t2aSteelPathBlock` (0 = OFF,
 * byte-identical). Pure World read — no RNG, no mutation.
 */
export function bulletPathSteelBlockedImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  maxDist: number,
): boolean {
  const w = self.world
  const p = self.controlledTank(w)
  const pierce = (p?.level ?? 0) >= 3
  const dirIdx = dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3
  const vdx = DIR_DX[dirIdx]
  const vdy = DIR_DY[dirIdx]
  const grid = w.tileMap.grid
  const half = 3 // BULLET / 2
  for (let d = CELL; d <= maxDist; d += CELL) {
    const fx = pcx + vdx * d
    const fy = pcy + vdy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) return true // dies at the edge
    const c0 = Math.floor((fx - half) / CELL)
    const c1 = Math.floor((fx + half) / CELL)
    const r0 = Math.floor((fy - half) / CELL)
    const r1 = Math.floor((fy + half) / CELL)
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        if (c < 0 || c >= GRID || r < 0 || r >= GRID) return true // OOB → 'steel'
        const terrain = grid[r][c]
        if (terrain !== 'steel') continue
        if (isBaseRingCell(c, r)) continue // base ring: handled by baseWall/baseSteel gates
        if (pierce) continue // level ≥ 3 pierces non-ring steel
        return true // the bullet would stop here — before the enemy
      }
    }
  }
  return false
}

/**
 * §121 (mode 2, lenient): does any alive enemy tank body overlap the bullet's
 * 6px corridor BETWEEN the player and the base? When one does, the bullet
 * provably hits the enemy before the base (point-blank overlap kill) — mode 2
 * keeps that shot instead of suppressing it. Enemies are matched by the
 * corridor band (±(BULLET/2 + TANK/2) = ±19px) on the fire axis, in front of
 * the player, and no farther than the base's far edge. Pure World read.
 */
export function enemyInShotCorridorImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): boolean {
  const w = self.world
  const baseCx = BASE_POS.col * CELL + CELL
  const baseCy = BASE_POS.row * CELL + CELL
  const vertical = dir === 'up' || dir === 'down'
  const band = 19 // BULLET/2 (3) + TANK/2 (16) — body overlaps the 6px corridor
  const tanks = w.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue
    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    if (vertical) {
      if (Math.abs(tcx - pcx) >= band) continue
      // In front of the player, before the base's far edge (in the dir of travel)
      if (dir === 'down') {
        if (!(tcy > pcy && tcy < baseCy + CELL)) continue
      } else if (!(tcy < pcy && tcy > baseCy - CELL)) continue
    } else {
      if (Math.abs(tcy - pcy) >= band) continue
      if (dir === 'right') {
        if (!(tcx > pcx && tcx < baseCx + CELL)) continue
      } else if (!(tcx < pcx && tcx > baseCx - CELL)) continue
    }
    // The distance band IS the exact body-vs-6px-column overlap condition
    // (|tankCenter - pcx| < 3 + 16 ⇔ 32px body intersects the 6px column).
    return true
  }
  return false
}

/**
 * §49: Check if an enemy is facing toward the player (炮口相向) and is in
 * the player's line of fire. Returns the enemy + distance, or null.
 *
 * "Facing toward player" means:
 *   - Player faces up   → enemy faces down  (and is above the player)
 *   - Player faces down → enemy faces up    (and is below the player)
 *   - Player faces left → enemy faces right (and is left of the player)
 *   - Player faces right→ enemy faces left  (and is right of the player)
 *
 * The enemy must also be roughly aligned (within TANK px) so that both
 * tanks are in the same row/col.
 */
export function findEnemyFacingPlayerImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  aimDir: Direction,
): { enemy: Tank; dist: number } | null {
  const w = self.world
  const vertical = aimDir === 'up' || aimDir === 'down'
  const expectedEnemyDir: Direction =
    aimDir === 'up' ? 'down' : aimDir === 'down' ? 'up' : aimDir === 'left' ? 'right' : 'left'

  let best: { enemy: Tank; dist: number } | null = null
  const tanksArr = w.tanks

  for (let i = 0; i < tanksArr.length; i++) {
    const t = tanksArr[i]
    if (!t.alive || t.spawnTimer > 0) continue
    if (t.dir !== expectedEnemyDir) continue

    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2

    let inLine = false
    let dist = Infinity

    if (vertical) {
      if (Math.abs(tcx - pcx) < TANK) {
        const dy = tcy - pcy
        if ((aimDir === 'up' && dy < 0) || (aimDir === 'down' && dy > 0)) {
          inLine = true
          dist = Math.abs(dy)
        }
      }
    } else {
      if (Math.abs(tcy - pcy) < TANK) {
        const dx = tcx - pcx
        if ((aimDir === 'left' && dx < 0) || (aimDir === 'right' && dx > 0)) {
          inLine = true
          dist = Math.abs(dx)
        }
      }
    }

    if (inLine && dist <= AIM_RANGE_CELLS * CELL) {
      if (!best || dist < best.dist) {
        best = { enemy: t, dist }
      }
    }
  }

  return best
}
