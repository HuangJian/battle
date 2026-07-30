import type { GodAIInput } from '../GodAIInput'
import type { Direction } from '../../constants'
import { CELL, TANK, FIELD, DIR_VECTORS, BASE_POS } from '../../constants'
import { aabb } from '../../utils/helpers'
import { AIM_RANGE_CELLS, KIND_THREAT_WEIGHT } from './constants'

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

  for (const t of w.tanks) {
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
    const threatWeight = KIND_THREAT_WEIGHT[t.kind] ?? 1
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

// Hoisted constants — avoids allocating 2-element tuple arrays on every call.
const VERTICAL_OFFSETS: readonly (readonly [number, number])[] = [
  [-CELL / 2, 0],
  [CELL / 2, 0],
]
const HORIZONTAL_OFFSETS: readonly (readonly [number, number])[] = [
  [0, -CELL / 2],
  [0, CELL / 2],
]

/**
 * Scan ahead in a direction for enemies, walls, and base protection.
 * Returns what's in the line of fire, distinguishing steel from brick
 * (T11) and detecting base-protection bricks (T6).
 *
 * Writes into `self._scanResult` (a reusable object) to avoid allocating
 * a result object on every call. Callers use the result immediately and
 * never store the reference, so this is safe.
 */
export function scanAheadImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
): { enemy: boolean; wall: boolean; steel: boolean; baseWall: boolean; enemyDist: number } {
  const w = self.world
  const v = DIR_VECTORS[dir]
  const vertical = dir === 'up' || dir === 'down'
  const offsets = vertical ? VERTICAL_OFFSETS : HORIZONTAL_OFFSETS
  const tanksArr = w.tanks

  const r = self._scanResult
  r.enemy = false
  r.wall = false
  r.steel = false
  r.baseWall = false
  r.enemyDist = Infinity

  // Reusable aligned-tank buffer (perf): pre-filter tanks whose perpendicular
  // position overlaps the scan line. For a vertical scan, the x-condition of
  // the aabb (t.x − 1 < sx < t.x + 33) is constant per offset — if it fails,
  // the tank can NEVER be hit at any cell step. This reduces the per-cell tank
  // loop from O(N) to O(alignedN), where alignedN is typically 0-2. The aabb
  // check at each cell is identical to the original — just fewer iterations.
  const aligned = self._scanAligned

  for (let oi = 0; oi < offsets.length; oi++) {
    const ox = offsets[oi][0]
    const oy = offsets[oi][1]
    const sx = pcx + ox
    const sy = pcy + oy

    // Pre-filter: collect tanks whose perpendicular axis overlaps this offset's
    // scan line (the constant half of the aabb condition).
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

    for (let d = CELL; d <= FIELD; d += CELL) {
      const cx = sx + v.dx * d
      const cy = sy + v.dy * d
      if (cx < 0 || cx > FIELD || cy < 0 || cy > FIELD) break

      const col = Math.floor(cx / CELL)
      const row = Math.floor(cy / CELL)
      const terrain = w.tileMap.get(col, row)

      if (terrain === 'steel') {
        r.steel = true
        r.wall = true
        break
      }
      if (terrain === 'brick') {
        // T6: check if this brick is protecting the base.
        if (self.isBaseProtectionBrick(col, row)) {
          r.baseWall = true
        }
        r.wall = true
        break
      }
      if (terrain === 'base') {
        r.baseWall = true
        r.wall = true
        break
      }

      // Check only pre-filtered aligned tanks (not all tanks). When
      // alignedCount is 0 this loop body is skipped entirely.
      let found = false
      for (let ai = 0; ai < alignedCount; ai++) {
        const t = aligned[ai]
        if (aabb(cx - 1, cy - 1, 2, 2, t.x, t.y, t.w, t.h)) {
          if (d / CELL < r.enemyDist) r.enemyDist = d / CELL
          r.enemy = true
          found = true
          break
        }
      }
      if (found) break
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

  for (const t of w.tanks) {
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

  const result = self.scanAhead(pcx, pcy, dir)

  // T6/T11: Don't fire at base protection bricks or steel (level < 3).
  // These checks MUST come before the enemy check because scanAhead uses
  // two independent offset scan lines — if offset 0 finds steel and offset 1
  // finds an enemy, BOTH result.steel and result.enemy are true. Checking
  // enemy first would cause the AI to fire through steel.
  if (result.baseWall) return false
  if (result.steel && (p.level ?? 0) < 3) return false
  // Steel with level ≥ 3: fall through to enemy check (can pierce).

  // Enemy in line of fire — fire.
  if (result.enemy) {
    return self.rng.next() >= self.params.aimError
  }

  // Steel with level ≥ 3 (no enemy found): fire to pierce.
  if (result.steel) {
    return self.rng.next() >= self.params.aimError
  }

  // Check for enemy bullet to intercept (T5).
  for (const b of w.bullets) {
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
