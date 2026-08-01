import type { GodAIInput } from '../GodAIInput'
import type { Direction } from '../../constants'
import type { Tank, TankKind } from '../../types'
import { CELL, TANK, FIELD, GRID, BASE_POS } from '../../constants'
import { AIM_RANGE_CELLS, kindThreatWeight } from './constants'

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
    const threatWeight = kindThreatWeight(t.kind)
    const bonusWeight = t.bonus ? 2 : 0 // S5c: bonus enemies are higher priority
    // D2: when damagedArmorBonus > 0, add a bonus for finishing damaged
    // armor tanks. The base hpFactor (hp/maxHp) preferentially weights
    // full-HP targets — which is backwards for armor-heavy stages where
    // spreading damage across 4 fresh armor tanks kills none. The bonus
    // inverts the priority: a 1/4-HP armor gets damagedArmorBonus×1000
    // extra, making the AI commit to the kill.
    const hpFactor = t.hp / (t.maxHp || 1)
    const damagedBonus =
      self.params.damagedArmorBonus > 0 && t.maxHp > 1
        ? (1 - hpFactor) * self.params.damagedArmorBonus * 1000
        : 0
    const score = (threatWeight + bonusWeight) * 10000 - dist + hpFactor * 100 + damagedBonus

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

/**
 * Scan ahead in a direction for enemies, walls, and base protection.
 * Returns what's in the line of fire, distinguishing steel from brick
 * (T11) and detecting base-protection bricks (T6).
 *
 * Writes into `self._scanResult` (a reusable object) to avoid allocating
 * a result object on every call. Callers use the result immediately and
 * never store the reference, so this is safe.
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
): {
  enemy: boolean
  wall: boolean
  steel: boolean
  baseWall: boolean
  baseSteel: boolean
  steelCol: number
  steelRow: number
  enemyDist: number
  enemyKind: TankKind
  enemyHp: number
  enemyMaxHp: number
} {
  const w = self.world
  // Fast dir → index (avoid DIR_VECTORS string-keyed dict lookup).
  const dirIdx = dir === 'up' ? 0 : dir === 'down' ? 1 : dir === 'left' ? 2 : 3
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

  const r = self._scanResult
  r.enemy = false
  r.wall = false
  r.steel = false
  r.baseWall = false
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
        if (hasBase) {
          const dc = col - baseCol
          const dr = row - baseRow
          const ad = dc < 0 ? -dc : dc
          const ar = dr < 0 ? -dr : dr
          if (ad <= wallScanR && ar <= wallScanR && (ad <= 2 || ar <= 2)) {
            r.baseWall = true
          }
        }
        r.wall = true
        break
      }
      if (terrain === 'base') {
        r.baseWall = true
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
  const p = w.player!
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
  const p = w.player!

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
