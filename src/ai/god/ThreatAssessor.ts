import type { GodAIInput } from '../GodAIInput'
import type { Bullet } from '../../types'
import type { Direction } from '../../constants'
import { CELL, TANK, DIR_VECTORS, BASE_POS, FIELD, GRID } from '../../constants'
import { type Cell } from '../../utils/pathfind'
import { ALL_DIRS, opposite } from '../../utils/helpers'
import { BULLET_TRAJECTORY_MAX_CELLS } from './constants'
import { scanAheadImpl } from './FireControl'

// ============================================================
// ThreatAssessor — bullet-threat assessment + dodging (T8, M3)
// Moved verbatim from GodAIInput.ts during the §0.5 split.
// Each `impl` takes `self: GodAIInput` so it can read shared state and
// call sibling methods via the public wrappers on GodAIInput.
// ============================================================

/**
 * Find the most dangerous incoming enemy bullet. "Dangerous" = aligned with
 * the player and approaching. Returns null if no threat.
 */
export function findMostDangerousBulletImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
): Bullet | null {
  const w = self.world
  let best: Bullet | null = null
  let bestDist = Infinity

  // §48-revisit: steel-only occlusion. When ON, skip bullets whose path to
  // the player is blocked by steel (permanent for enemy bullets —
  // STEEL_PIERCE_PLAYER_LEVEL is player-only). Brick is NOT checked: dodging
  // brick-blocked bullets is load-bearing anticipatory dodging (DECISIONS
  // §48). Computed once (loop-invariant) so V8 guards the scan block when OFF.
  const steelOcclusion = self.params.evasionSteelOcclusion > 0
  // Distance gate (px): 0 = suppress all steel-blocked bullets; >0 suppresses
  // only blocked bullets at dist >= range. Near blocked bullets keep their
  // dodge (load-bearing repositioning — per-seed tick-diff, S32 seed 11).
  const steelOcclusionRangePx =
    self.params.evasionSteelOcclusionRange > 0 ? self.params.evasionSteelOcclusionRange * CELL : 0

  // Pinned-position gate (user finding, §48-revisit): the S32 seed-11
  // regression is NOT about dodging vs not dodging — it's that suppressing
  // the dodge left the player PINNED in a corner (tick 738: player at (0,1)
  // stayed + fired while the baseline dodged down and escaped). When the
  // player is geometrically constrained (≤ 2 open directions), the dodge IS
  // the escape — never suppress it, even for a steel-blocked bullet. Only in
  // open space (3-4 open directions) is a steel-blocked dodge genuinely
  // wasteful. Computed only when occlusion is active (OFF stays byte-identical).
  let playerConstrained = false
  if (steelOcclusion) {
    const pTank = self.controlledTank(self.world)
    if (pTank) {
      let openDirs = 0
      for (let di = 0; di < ALL_DIRS.length; di++) {
        if (self.canMoveDir(pTank, ALL_DIRS[di])) openDirs++
      }
      playerConstrained = openDirs <= 2
    }
  }

  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bcx - pcx) < TANK : Math.abs(bcy - pcy) < TANK
    if (!aligned) continue

    const approaching =
      (b.dir === 'down' && bcy < pcy) ||
      (b.dir === 'up' && bcy > pcy) ||
      (b.dir === 'right' && bcx < pcx) ||
      (b.dir === 'left' && bcx > pcx)
    if (!approaching) continue

    const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)

    // §48-revisit: scan the bullet→player path for steel. If any steel cell
    // blocks the path, this bullet (and all future bullets from this enemy
    // in this direction) can never reach the player — skip the threat.
    // OOB-safe: break on off-field, never use TileMap.get's OOB→'steel'
    // default (the §70 false-positive bug). Brick does NOT cause a skip —
    // keep scanning past brick for steel behind it.
    // Distance gate: only suppress when dist >= the range threshold, so
    // NEAR blocked bullets keep their load-bearing repositioning dodge.
    if (steelOcclusion && dist >= steelOcclusionRangePx) {
      const v = DIR_VECTORS[b.dir]
      const grid = w.tileMap.grid
      let steelBlocked = false
      for (let d = CELL; d < dist; d += CELL) {
        const fx = bcx + v.dx * d
        const fy = bcy + v.dy * d
        const col = Math.floor(fx / CELL)
        const row = Math.floor(fy / CELL)
        if (col < 0 || col >= GRID || row < 0 || row >= GRID) break
        if (grid[row][col] === 'steel') {
          steelBlocked = true
          break
        }
      }
      // Pinned gate: only suppress when the player is NOT geometrically
      // constrained. When pinned (≤2 open directions), the dodge is the
      // escape from the corner — keep it (S32 seed-11 regression mechanism).
      if (steelBlocked && !playerConstrained) continue
    }

    if (dist < bestDist) {
      bestDist = dist
      best = b
    }
  }
  return best
}

/**
 * T8: Find an enemy bullet whose trajectory will cross the base area.
 * This is the ultimate defense — intercept bullets heading for the base
 * even if they're not threatening the player.
 * Gap B: returns null when the stage has no base.
 */
export function findBulletThreatToBaseImpl(self: GodAIInput): Bullet | null {
  if (!self.hasBase) return null
  const w = self.world
  const baseCx = BASE_POS.col * CELL + CELL
  const baseCy = BASE_POS.row * CELL + CELL
  const baseHalf = CELL // base is 2×2 cells = 32px, half = 16px

  let best: Bullet | null = null
  let bestDist = Infinity

  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Directional pre-filter (perf): the base sits at rows 24-25 (bottom of
    // the field). Skip bullets that physically cannot reach it, avoiding the
    // per-cell trajectory scan for the majority of in-flight bullets.
    // - 'up' bullets move away from the base → never a threat.
    // - 'left'/'right' bullets stay at their row → only a threat if already
    //   in the base's row band (≥ row 22, generous — base is rows 24-25).
    // - 'down' bullets move toward the base → always potentially a threat.
    // Strict superset: any filtered bullet provably cannot cross the base
    // area or hit base terrain, so no threat is missed.
    if (b.dir === 'up') continue
    if ((b.dir === 'left' || b.dir === 'right') && bcy < 22 * CELL) continue

    const v = DIR_VECTORS[b.dir]

    // Project the bullet's trajectory forward and check if it crosses the base.
    // Fix Bug 4: terrain check must come BEFORE base-area check — otherwise
    // walls protecting the base are ignored, causing false-positive threats
    // and wasted interception actions.
    let crossesBase = false
    for (let d = CELL; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
      const fx = bcx + v.dx * d
      const fy = bcy + v.dy * d

      // If the trajectory goes off-field, stop.
      if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

      // Check terrain FIRST — if a wall blocks the bullet, it never
      // reaches the base, so there's no threat.
      const col = Math.floor(fx / CELL)
      const row = Math.floor(fy / CELL)
      const terrain = w.tileMap.get(col, row)
      if (terrain === 'brick' || terrain === 'steel') break

      // If it hits the base itself, that's a direct hit.
      if (terrain === 'base') {
        crossesBase = true
        break
      }

      // Check if the trajectory point is within the base area.
      // Use baseHalf (16px = 1 cell) instead of baseHalf * 2 — the base
      // is 2×2 cells centered at (baseCx, baseCy), so half-width = CELL.
      if (Math.abs(fx - baseCx) < baseHalf && Math.abs(fy - baseCy) < baseHalf) {
        crossesBase = true
        break
      }
    }

    if (crossesBase) {
      const dist = Math.abs(bcx - baseCx) + Math.abs(bcy - baseCy)
      if (dist < bestDist) {
        bestDist = dist
        best = b
      }
    }
  }

  return best
}

/**
 * T8: Calculate the cell where the player should move to intercept
 * a bullet heading toward the base. This is the cell on the bullet's
 * trajectory that is closest to the player.
 */
export function baseBulletInterceptCellImpl(self: GodAIInput, bullet: Bullet): Cell | null {
  const w = self.world
  const p = self.controlledTank(self.world)!
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const bcx = bullet.x + bullet.w / 2
  const bcy = bullet.y + bullet.h / 2
  const v = DIR_VECTORS[bullet.dir]

  // Walk along the bullet's trajectory and find the closest point to the player
  // that is BETWEEN the bullet and the base (in front of the bullet).
  let bestCell: Cell | null = null
  let bestDist = Infinity

  for (let d = 0; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
    const fx = bcx + v.dx * d
    const fy = bcy + v.dy * d
    if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

    const col = Math.floor(fx / CELL)
    const row = Math.floor(fy / CELL)

    // Stop if the trajectory hits a wall.
    const terrain = w.tileMap.get(col, row)
    if (terrain === 'brick' || terrain === 'steel') break

    // Check if the player can reach this cell.
    const cellCx = col * CELL + CELL / 2
    const cellCy = row * CELL + CELL / 2
    const dist = Math.abs(cellCx - pcx) + Math.abs(cellCy - pcy)
    if (dist < bestDist) {
      bestDist = dist
      bestCell = { col, row }
    }

    // If we've passed the base, stop searching.
    if (terrain === 'base') break
  }

  // Only intercept if the player can reach the intercept point in time.
  // If the closest point is too far, the player can't get there before
  // the bullet — intercepting would just send the player on a wild goose
  // chase, leaving the base undefended.
  if (bestDist > self.params.t8MaxInterceptDistCells * CELL) return null

  return bestCell
}

/**
 * Choose a dodge direction perpendicular to the incoming bullet.
 * M3: verify the candidate direction is safe (not into another bullet's path).
 */
export function dodgeDirectionImpl(
  self: GodAIInput,
  bullet: Bullet,
  pcx: number,
  pcy: number,
): Direction | null {
  const p = self.controlledTank(self.world)!
  const vertical = bullet.dir === 'up' || bullet.dir === 'down'
  // Use module-level constants instead of allocating arrays on every dodge.
  const candA: Direction = vertical ? 'left' : 'up'
  const candB: Direction = vertical ? 'right' : 'down'

  // Try each candidate; prefer the one that's passable AND safe (M3).
  // Use local booleans instead of allocating an `open` array.
  let safeA = false
  let safeB = false
  if (self.canMoveDir(p, candA) && self.isSafeDir(pcx, pcy, candA, bullet.id)) safeA = true
  if (self.canMoveDir(p, candB) && self.isSafeDir(pcx, pcy, candB, bullet.id)) safeB = true

  // If no safe candidate, try passable but unsafe.
  if (!safeA && !safeB) {
    if (self.canMoveDir(p, candA)) safeA = true
    if (self.canMoveDir(p, candB)) safeB = true
  }

  // If still nothing [no perpendicular dodge passable], the player is pinned
  // in a corridor aligned with the bullet. §83: NEVER flee in the bullet's own
  // travel direction — the bullet is faster, so fleeing down the corridor in
  // its wake is futile death (reproduced in classic-s02 seed 1785636440494
  // @00:27: player fled 'down' for 39 ticks, bullet overtook it). Instead,
  // prefer turning TOWARD the bullet (the opposite of its travel): the player
  // faces the incoming bullet and the T5 fire logic cancels it (对枪抵消). This
  // dominates fleeing in BOTH cases: when not on cooldown the player cancels
  // and survives; when on cooldown the player at least FACES the bullet so the
  // instant its shot resolves it fires to cancel (fleeing faces AWAY → the
  // cooldown-end shot goes the wrong way → certain death). Only fall back to
  // the flee direction as an absolute last resort (when toward is blocked too).
  if (!safeA && !safeB) {
    const fleeDir: Direction = bullet.dir
    const towardDir: Direction = opposite(bullet.dir)
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    // First pass: open directions EXCLUDING the futile flee direction. Prefer
    // towardDir, then (for hasBase) the one closest to the base.
    let bestDist = Infinity
    for (let di = 0; di < ALL_DIRS.length; di++) {
      const d = ALL_DIRS[di]
      if (d === fleeDir) continue
      if (!self.canMoveDir(p, d)) continue
      // Toward the bullet wins outright (it enables counter-fire).
      if (d === towardDir) {
        bestDist = -1
        break
      }
      if (self.hasBase) {
        const vd = DIR_VECTORS[d]
        const dist = Math.abs(pcx + vd.dx * CELL - baseCx) + Math.abs(pcy + vd.dy * CELL - baseCy)
        if (dist < bestDist) bestDist = dist
      } else {
        bestDist = 0 // no base — first non-flee open direction
      }
    }
    if (bestDist < Infinity) {
      if (bestDist === -1) return towardDir
      for (let di = 0; di < ALL_DIRS.length; di++) {
        const d = ALL_DIRS[di]
        if (d === fleeDir) continue
        if (!self.canMoveDir(p, d)) continue
        if (self.hasBase) {
          const vd = DIR_VECTORS[d]
          const dist = Math.abs(pcx + vd.dx * CELL - baseCx) + Math.abs(pcy + vd.dy * CELL - baseCy)
          if (dist === bestDist) return d
        } else {
          return d
        }
      }
    }
    // Last resort: any open direction (including the flee direction — any
    // movement beats standing still when the player truly cannot turn toward).
    for (let di = 0; di < ALL_DIRS.length; di++) {
      if (self.canMoveDir(p, ALL_DIRS[di])) return ALL_DIRS[di]
    }
    return null
  }

  // We have at least one perpendicular candidate (safeA or safeB).
  // Prefer the direction that keeps the player closer to the base.
  if (self.hasBase) {
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    const va = DIR_VECTORS[candA]
    const vb = DIR_VECTORS[candB]
    const distA = Math.abs(pcx + va.dx * CELL - baseCx) + Math.abs(pcy + va.dy * CELL - baseCy)
    const distB = Math.abs(pcx + vb.dx * CELL - baseCx) + Math.abs(pcy + vb.dy * CELL - baseCy)
    if (safeA && safeB) return distA <= distB ? candA : candB
    return safeA ? candA : candB
  }
  // No base — first safe candidate.
  return safeA ? candA : candB
}

/**
 * M3: Check if moving in direction `d` would put the player into another
 * bullet's trajectory (excluding the one we're already dodging).
 */
export function isSafeDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  dir: Direction,
  excludeBulletId: number,
): boolean {
  const w = self.world
  const v = DIR_VECTORS[dir]
  // Check the cell we'd move into.
  const newCx = pcx + v.dx * CELL
  const newCy = pcy + v.dy * CELL

  // Indexed loop (AGENTS §14.1): isSafeDir is called up to 2× per dodge
  // (candA + candB), and dodgeDirection runs whenever a threat is detected.
  // `for (const b of w.bullets)` allocates an iterator per call.
  const bullets = w.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer || b.id === excludeBulletId) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical
      ? Math.abs(bcx - newCx) < CELL * 0.75
      : Math.abs(bcy - newCy) < CELL * 0.75
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && bcy < newCy) ||
      (b.dir === 'up' && bcy > newCy) ||
      (b.dir === 'right' && bcx < newCx) ||
      (b.dir === 'left' && bcx > newCx)
    if (approaching) return false
  }
  return true
}

/**
 * §68-v2: Time-aware path threat projection.
 *
 * Scans the player's movement path from cell 1 to LOOKAHEAD cells ahead in
 * moveDir. For each cell, checks ALL enemy bullets (from target or non-target
 * enemies) using time-of-arrival estimation:
 *
 *   - Player arrives at cell i at tick:  i * CELL / playerSpeed
 *   - Player departs (clears TANK hitbox) at tick:  arrival + TANK / playerSpeed
 *   - Bullet arrives at cell i at tick:  dist / bullet.speed
 *   - Threat if bullet arrives before player departs the cell
 *
 * This replaces the old fixed-proximity approach (which used TANK or TANK*2
 * as a distance threshold). The time-aware check naturally adapts to bullet
 * speed: a fast bullet (4.2 px/tick) is flagged at a greater distance than
 * a slow one (3.6 px/tick), giving the player appropriate warning time.
 *
 * The current position (i=0) is NOT checked here — findMostDangerousBullet
 * in the dodge section of think() already handles it. This function only
 * catches threats to FUTURE positions: bullets the player would move INTO
 * by following moveDir.
 *
 * Returns the bullet with the earliest arrival time, or null if the path
 * is safe.
 */
const PATH_THREAT_LOOKAHEAD = 3

export function findPathThreatImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  moveDir: Direction,
  playerSpeed: number,
): Bullet | null {
  const w = self.world
  const v = DIR_VECTORS[moveDir]
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0

  let bestBullet: Bullet | null = null
  let bestThreatTick = Infinity

  const bullets = w.bullets
  for (let i = 1; i <= PATH_THREAT_LOOKAHEAD; i++) {
    const ccx = pcx + v.dx * i * CELL
    const ccy = pcy + v.dy * i * CELL

    const playerArrivalTick = (i * CELL) / ps
    // Collision window: both player and bullet hitboxes must overlap the cell
    // at the same time. Player hitbox (TANK=32px) + bullet hitbox (BULLET=6px)
    // → centers must be within (TANK+BULLET)/2 = 19px at the same tick.
    // At bullet speed ~4px/tick, that's ~5 ticks. At player speed ~1px/tick,
    // that's ~19 ticks. We use ±10 ticks as a balance: catches genuine
    // same-time collisions without flagging bullets that arrive much earlier
    // (already passed) or much later (player has moved on).
    // This is MUCH tighter than ±TANK/ps (±30 ticks), which caused
    // false positives on maze stages (S6/S12/S14/S22/S26 regressions).
    const threatWindow = 10
    const playerDepartureTick = playerArrivalTick + threatWindow
    const playerEnterTick = playerArrivalTick - threatWindow

    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue

      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'

      const aligned = vertical ? Math.abs(bcx - ccx) < TANK : Math.abs(bcy - ccy) < TANK
      if (!aligned) continue

      const approaching =
        (b.dir === 'down' && bcy < ccy) ||
        (b.dir === 'up' && bcy > ccy) ||
        (b.dir === 'right' && bcx < ccx) ||
        (b.dir === 'left' && bcx > ccx)
      if (!approaching) continue

      const dist = vertical ? Math.abs(bcy - ccy) : Math.abs(bcx - ccx)
      const bulletArrivalTick = dist / b.speed

      if (bulletArrivalTick >= playerEnterTick && bulletArrivalTick <= playerDepartureTick) {
        if (bulletArrivalTick < bestThreatTick) {
          bestThreatTick = bulletArrivalTick
          bestBullet = b
        }
      }
    }
  }

  return bestBullet
}

/**
 * §68-v2: Find a safe alternative movement direction.
 *
 * Called when findPathThreat detected a threat in the current movement
 * direction. Checks perpendicular and backward directions for immediate
 * safety (cell 1 only — not the full 3-cell path). This is less conservative
 * than checking the full path: a direction is accepted if the immediate next
 * cell is safe, even if farther cells have threats. The full path check will
 * run again next tick for the new direction.
 *
 * Returns the first safe direction, or null if no direction is safe.
 * Caller keeps the original direction when null is returned — never stops.
 */
export function findSafeMoveDirImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  threatenedDir: Direction,
  playerSpeed: number,
): Direction | null {
  const p = self.controlledTank(self.world)!
  const w = self.world
  const ps = playerSpeed > 0.1 ? playerSpeed : 1.0

  // Time window for cell 1 (immediate next cell) — same tight ±10 as findPathThreat
  const arrivalTick = CELL / ps
  const threatWin = 10
  const departTick = arrivalTick + threatWin
  const enterTick = arrivalTick - threatWin

  // Check if cell 1 in direction `dir` is safe from bullets
  function isCell1Safe(dir: Direction): boolean {
    const v = DIR_VECTORS[dir]
    const ccx = pcx + v.dx * CELL
    const ccy = pcy + v.dy * CELL
    const bullets = w.bullets
    for (let bi = 0; bi < bullets.length; bi++) {
      const b = bullets[bi]
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'
      const aligned = vertical ? Math.abs(bcx - ccx) < TANK : Math.abs(bcy - ccy) < TANK
      if (!aligned) continue
      const approaching =
        (b.dir === 'down' && bcy < ccy) ||
        (b.dir === 'up' && bcy > ccy) ||
        (b.dir === 'right' && bcx < ccx) ||
        (b.dir === 'left' && bcx > ccx)
      if (!approaching) continue
      const dist = vertical ? Math.abs(bcy - ccy) : Math.abs(bcx - ccx)
      const bat = dist / b.speed
      if (bat >= enterTick && bat <= departTick) return false
    }
    return true
  }

  const threatenedVertical = threatenedDir === 'up' || threatenedDir === 'down'
  const perpA: Direction = threatenedVertical ? 'left' : 'up'
  const perpB: Direction = threatenedVertical ? 'right' : 'down'
  const backward: Direction =
    threatenedDir === 'up'
      ? 'down'
      : threatenedDir === 'down'
        ? 'up'
        : threatenedDir === 'left'
          ? 'right'
          : 'left'

  if (self.canMoveDir(p, perpA) && isCell1Safe(perpA)) return perpA
  if (self.canMoveDir(p, perpB) && isCell1Safe(perpB)) return perpB
  if (self.canMoveDir(p, backward) && isCell1Safe(backward)) return backward

  return null
}

/**
 * §49: Check if there's an enemy bullet traveling toward the player in the
 * given direction's line of fire. Used by the armor "对枪" (trade-shots)
 * logic to decide whether to fire for bullet cancellation.
 *
 * Returns true if an enemy bullet is in the line, approaching the player,
 * within a reasonable distance.
 */
export function hasEnemyBulletInLineImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  aimDir: Direction,
): boolean {
  const w = self.world
  const vertical = aimDir === 'up' || aimDir === 'down'
  const bullets = w.bullets

  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.isPlayer) continue

    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2

    // Bullet must be roughly aligned with the player in the aimDir line
    const aligned = vertical ? Math.abs(bcx - pcx) < TANK : Math.abs(bcy - pcy) < TANK
    if (!aligned) continue

    // Bullet must be approaching the player (in front, heading toward player)
    const approaching =
      (aimDir === 'up' && b.dir === 'down' && bcy < pcy) ||
      (aimDir === 'down' && b.dir === 'up' && bcy > pcy) ||
      (aimDir === 'left' && b.dir === 'right' && bcx < pcx) ||
      (aimDir === 'right' && b.dir === 'left' && bcx > pcx)
    if (!approaching) continue

    // Within a reasonable distance (8 cells)
    const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
    if (dist < TANK * 8) return true
  }
  return false
}

/**
 * §85: Close-range enemy exposure check — is the player about to turn its
 * back on a close enemy that could fire and kill it before it can dodge?
 *
 * The navigate branch only checks for BULLET threats (findPathThreat). But
 * an enemy tank that is aligned with the player (same row/col), close
 * (within `range` cells), and has no wall between them can fire at any
 * moment. If the player's moveDir moves it ALONG the enemy's line of fire
 * (not perpendicular — a perpendicular move would be a dodge), the player
 * is exposed: the enemy fires, the bullet is faster, and the player gets
 * hit in the back.
 *
 * This function returns the direction the player should face to engage the
 * threatening enemy (stop-and-fire), or null if no close-range exposure
 * is detected.
 *
 * Condition for "exposed":
 *   1. Enemy within `range` cells (Manhattan distance in the scan axis)
 *   2. Enemy aligned with the player (same row or col, within TANK px)
 *   3. No wall/steel between player and enemy (scanAhead finds enemy, not wall)
 *   4. The player's moveDir is NOT toward the enemy (turning away)
 *
 * When exposed, the player should stop and fire at the enemy instead of
 * moving away. If the enemy is in a different direction than the player's
 * current facing, the player should turn to face the enemy.
 */
export function closeCombatExposureImpl(
  self: GodAIInput,
  pcx: number,
  pcy: number,
  moveDir: Direction | null,
  range: number,
): Direction | null {
  if (!moveDir) return null
  const w = self.world
  const tanksArr = w.tanks
  const rangePx = range * CELL

  for (let ti = 0; ti < tanksArr.length; ti++) {
    const t = tanksArr[ti]
    if (!t.alive || t.spawnTimer > 0 || t.isPlayer) continue

    const tcx = t.x + t.w / 2
    const tcy = t.y + t.h / 2
    const dx = tcx - pcx
    const dy = tcy - pcy

    // Check alignment: same row or col (within TANK px)
    let enemyDir: Direction | null = null
    let scanDist = 0
    if (Math.abs(dx) < TANK) {
      if (dy < 0) {
        enemyDir = 'up'
        scanDist = -dy
      } else {
        enemyDir = 'down'
        scanDist = dy
      }
    } else if (Math.abs(dy) < TANK) {
      if (dx < 0) {
        enemyDir = 'left'
        scanDist = -dx
      } else {
        enemyDir = 'right'
        scanDist = dx
      }
    }
    if (!enemyDir) continue
    if (scanDist > rangePx) continue

    // Check no wall between player and enemy (scanAhead finds enemy)
    const scan = scanAheadImpl(self, pcx, pcy, enemyDir)
    if (!scan.enemy) continue

    // Check if moveDir is NOT toward the enemy — the player is turning away.
    // "Toward the enemy" = same direction as enemyDir (closing distance — safe).
    // Perpendicular moves are dodges — also safe (the player clears the
    // enemy's line of fire before a bullet can arrive).
    // Only FLEEING (moving in the opposite direction = exposing the back)
    // is the dangerous case the check is designed to prevent.
    if (moveDir === enemyDir) continue // moving toward enemy — safe
    if (moveDir !== opposite(enemyDir)) continue // perpendicular — dodge, safe

    // The player is fleeing from a close enemy with a clear shot.
    // Return the direction to face the enemy and fire instead.
    return enemyDir
  }
  return null
}
