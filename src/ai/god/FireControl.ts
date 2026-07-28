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
    const hpFactor = t.hp / (t.maxHp || 1) // prefer enemies we can finish
    const score = (threatWeight + bonusWeight) * 10000 - dist + hpFactor * 100

    if (score > bestScore) {
      bestScore = score
      bestDir = dir
    }
  }

  return bestDir
}

/**
 * Scan ahead in a direction for enemies, walls, and base protection.
 * Returns what's in the line of fire, distinguishing steel from brick
 * (T11) and detecting base-protection bricks (T6).
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

  const offsets: ReadonlyArray<readonly [number, number]> = vertical
    ? [
        [-CELL / 2, 0],
        [CELL / 2, 0],
      ]
    : [
        [0, -CELL / 2],
        [0, CELL / 2],
      ]

  let enemy = false
  let wall = false
  let steel = false
  let baseWall = false
  let enemyDist = Infinity

  for (const [ox, oy] of offsets) {
    const sx = pcx + ox
    const sy = pcy + oy

    for (let d = CELL; d <= FIELD; d += CELL) {
      const cx = sx + v.dx * d
      const cy = sy + v.dy * d
      if (cx < 0 || cx > FIELD || cy < 0 || cy > FIELD) break

      const col = Math.floor(cx / CELL)
      const row = Math.floor(cy / CELL)
      const terrain = w.tileMap.get(col, row)

      if (terrain === 'steel') {
        steel = true
        wall = true
        break
      }
      if (terrain === 'brick') {
        // T6: check if this brick is protecting the base.
        if (self.isBaseProtectionBrick(col, row)) {
          baseWall = true
        }
        wall = true
        break
      }
      if (terrain === 'base') {
        baseWall = true
        wall = true
        break
      }

      // Check for enemy tank at this position.
      let found = false
      for (const t of w.tanks) {
        if (!t.alive || t.spawnTimer > 0) continue
        if (aabb(cx - 1, cy - 1, 2, 2, t.x, t.y, t.w, t.h)) {
          if (d / CELL < enemyDist) enemyDist = d / CELL
          enemy = true
          found = true
          break
        }
      }
      if (found) break
    }
  }

  return { enemy, wall, steel, baseWall, enemyDist }
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
 * Decide whether to fire in the current facing direction. Fires when:
 * - An enemy is in the line of fire (scanAhead found enemy)
 * - An enemy bullet is approaching (to intercept, T5)
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

  // Enemy in line of fire — always fire.
  if (result.enemy) {
    return self.rng.next() >= self.params.aimError
  }

  // T6/T11: Don't fire at base protection bricks or steel (level < 3).
  // These are checked but always return false — we don't waste bullets
  // on walls during navigation.
  if (result.baseWall) return false
  if (result.steel && (p.level ?? 0) < 3) return false
  // Steel with level ≥ 3: fire to pierce (enemy might be behind it).
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
