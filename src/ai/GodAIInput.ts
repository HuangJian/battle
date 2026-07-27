import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet } from '../types'
import type { Direction } from '../constants'
import { CELL, TANK, FIELD, DIR_VECTORS, BASE_POS } from '../constants'
import { findPath, pxToCell, type Cell } from '../utils/pathfind'
import { snap, aabb, opposite, ALL_DIRS } from '../utils/helpers'
import type { RNG } from '../utils/RNG'

/**
 * GodAIInput — a "perfect player" simulator that implements `InputLike`.
 *
 * Design (plan/Automated-Level-Design §3.1):
 * - **Global vision**: reads `world.player`, `world.tanks`, `world.bullets`,
 *   `world.tileMap` directly — no perception limits.
 * - **Perfect planning**: A* pathfinding (`pathfind.ts`) to the best target.
 * - **Move-and-shoot**: scans all 4 directions for enemy targets each tick
 *   using global vision (ignores walls). When a target is found, the AI
 *   fires toward it while navigating via A* to close the distance. Bullets
 *   break through walls and hit enemies in the path.
 * - **Base defense**: always prioritizes the enemy closest to the base,
 *   regardless of distance. The AI positions itself to intercept flankers.
 * - **Injected imperfections** (all via `world.rng` for determinism):
 *   - Reaction delay: N ticks before responding to a new bullet threat.
 *   - Aim error: small probability of fumbling a shot.
 *   - Suboptimal path: small chance of taking a non-optimal route step.
 *
 * **Determinism**: all randomness flows through `world.rng` (AGENTS §2.3).
 * Same World state + same RNG state ⇒ identical decisions, always.
 */

/** Configurable imperfection parameters. */
export interface GodAIParams {
  /** Ticks of delay before reacting to a new threat (default: 2). */
  reactionDelay: number
  /** Probability of a fire-control mistake (default: 0.02). */
  aimError: number
  /** Probability of taking a suboptimal route step (default: 0.10). */
  suboptimalPathProb: number
}

/** Default God AI parameters (plan §3.1). */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 2,
  aimError: 0.02,
  suboptimalPathProb: 0.1,
}

/**
 * Skilled Human proxy parameters (plan §3.3C): God AI + double reaction
 * delay + 20% aim error. Represents an experienced but non-perfect human.
 */
export const SKILLED_HUMAN_PARAMS: GodAIParams = {
  reactionDelay: 4,
  aimError: 0.2,
  suboptimalPathProb: 0.2,
}

export class GodAIInput implements InputLike {
  private world: World
  private rng: RNG
  private params: GodAIParams

  /** Cached move direction for this tick. */
  private _moveDir: Direction | null = null
  /** Cached fire decision for this tick. */
  private _fire = false
  /** Whether think() ran this tick (avoids double-computation). */
  private _thought = false

  /** Current A* path being followed. */
  private path: Direction[] = []
  /** Re-plan counter (re-plan every N ticks). */
  private replanTimer = 0

  /** Threat reaction: when a threat is first seen, count down before reacting. */
  private reactionCounter = 0
  /** The last threat bullet id we reacted to (avoids re-triggering). */
  private lastThreatId: number = -1

  constructor(world: World, params: GodAIParams = DEFAULT_GOD_AI_PARAMS) {
    this.world = world
    this.rng = world.rng
    this.params = params
  }

  reset(): void {
    this._moveDir = null
    this._fire = false
    this._thought = false
    this.path = []
    this.replanTimer = 0
    this.reactionCounter = 0
    this.lastThreatId = -1
  }

  getMoveDirection(): Direction | null {
    this.think()
    return this._moveDir
  }

  isFiring(): boolean {
    // think() already ran in getMoveDirection()
    if (!this._thought) this.think()
    return this._fire
  }

  endFrame(): void {
    this._thought = false
  }

  // ================================================================
  // Core decision loop
  // ================================================================

  private think(): void {
    if (this._thought) return
    this._thought = true

    const w = this.world
    const p = w.player
    if (!p || !p.alive || p.spawnTimer > 0) {
      this._moveDir = null
      this._fire = false
      return
    }

    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2

    // ---- 1. Scan for enemy targets (global vision) ----
    const enemyDir = this.findEnemyDirection(pcx, pcy)

    // ---- 2. Threat assessment (dodge incoming bullets) ----
    const threat = this.findMostDangerousBullet(pcx, pcy)

    if (threat) {
      if (threat.id !== this.lastThreatId) {
        this.lastThreatId = threat.id
        this.reactionCounter = this.params.reactionDelay
      }

      if (this.reactionCounter > 0) {
        this.reactionCounter--
        // While reacting, keep moving and firing toward the objective.
        this._fire = this.shouldFireInFacingDir(pcx, pcy)
        this._moveDir = this.followPath()
        return
      }

      // Dodge: move perpendicular to the bullet's direction, but still fire.
      this._fire = this.shouldFireInFacingDir(pcx, pcy)
      this._moveDir = this.dodgeDirection(threat)
      return
    }

    // No threat — reset reaction state.
    this.reactionCounter = 0
    this.lastThreatId = -1

    // ---- 3. Navigate + fire ----
    // Always navigate toward the best target. If an enemy is visible in
    // a cardinal direction, turn to face it so bullets go toward it.
    // Fire proactively — bullets break walls and hit enemies in the path.
    if (enemyDir) {
      // Turn toward the enemy and fire. Don't stop — keep moving to close
      // the distance. The bullet hits the enemy (if clear) or a wall
      // (breaking through over repeated shots).
      this._fire = this.rng.next() >= this.params.aimError
      // Navigate toward the enemy via A* path (which may differ from the
      // raw direction due to walls). The path will turn the tank to face
      // the right way as it follows the route.
      this._moveDir = this.followPath()
    } else {
      // No enemy in any cardinal direction — fire at walls/bullets or
      // proactively while navigating.
      this._fire = this.shouldFireInFacingDir(pcx, pcy)
      this._moveDir = this.followPath()
    }
  }

  // ================================================================
  // Target scanning (stop-and-shoot)
  // ================================================================

  /**
   * Find the direction to the nearest enemy tank, using **global vision**
   * (plan §3.1: "reads world.tanks directly — no perception limits").
   * Unlike `scanAhead`, this does NOT stop at walls — the AI knows where
   * every enemy is, even through terrain. When an enemy is found, the AI
   * turns to face it and fires; the bullet either hits the enemy directly
   * or hits a wall in between (breaking through over repeated shots).
   */
  private findEnemyDirection(pcx: number, pcy: number): Direction | null {
    const w = this.world
    let bestDir: Direction | null = null
    let bestDist = Infinity

    for (const t of w.tanks) {
      if (!t.alive || t.spawnTimer > 0) continue
      const tcx = t.x + t.w / 2
      const tcy = t.y + t.h / 2
      const dx = tcx - pcx
      const dy = tcy - pcy

      // Check if the enemy is roughly aligned in one of the 4 cardinal
      // directions (within tank width).
      const halfT = TANK / 2
      let dir: Direction | null = null
      let dist = Infinity

      if (Math.abs(dx) < halfT) {
        // Same column — up or down
        if (dy < 0) {
          dir = 'up'
          dist = -dy
        } else {
          dir = 'down'
          dist = dy
        }
      } else if (Math.abs(dy) < halfT) {
        // Same row — left or right
        if (dx < 0) {
          dir = 'left'
          dist = -dx
        } else {
          dir = 'right'
          dist = dx
        }
      }

      if (dir && dist < bestDist) {
        bestDist = dist
        bestDir = dir
      }
    }

    return bestDir
  }

  /**
   * Scan ahead in a direction for enemies and walls. Checks **both cells**
   * of the tank's 2-cell width by casting two parallel rays (offset by
   * half a cell on each side of center). A single center ray misses
   * anything in the offset cell — a critical bug that caused the AI to
   * never fire because it couldn't see walls or enemies in its own column.
   */
  private scanAhead(
    pcx: number,
    pcy: number,
    dir: Direction,
  ): { enemy: boolean; wall: boolean; enemyDist: number } {
    const w = this.world
    const v = DIR_VECTORS[dir]
    const vertical = dir === 'up' || dir === 'down'

    // Two parallel rays, offset by half a cell on each side of center.
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

        // Steel and brick block line of sight (and bullets).
        if (terrain === 'steel') break
        if (terrain === 'brick' || terrain === 'base') {
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

    return { enemy, wall, enemyDist }
  }

  // ================================================================
  // Threat assessment
  // ================================================================

  /**
   * Find the most dangerous incoming enemy bullet. "Dangerous" = aligned with
   * the player and approaching. Returns null if no threat.
   */
  private findMostDangerousBullet(pcx: number, pcy: number): Bullet | null {
    const w = this.world
    let best: Bullet | null = null
    let bestDist = Infinity

    for (const b of w.bullets) {
      if (!b.alive || b.isPlayer) continue // only enemy bullets threaten
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const vertical = b.dir === 'up' || b.dir === 'down'
      const aligned = vertical
        ? Math.abs(bcx - pcx) < CELL * 0.75
        : Math.abs(bcy - pcy) < CELL * 0.75
      if (!aligned) continue

      const approaching =
        (b.dir === 'down' && bcy < pcy) ||
        (b.dir === 'up' && bcy > pcy) ||
        (b.dir === 'right' && bcx < pcx) ||
        (b.dir === 'left' && bcx > pcx)
      if (!approaching) continue

      const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
      if (dist < bestDist) {
        bestDist = dist
        best = b
      }
    }
    return best
  }

  /**
   * Choose a dodge direction perpendicular to the incoming bullet.
   * Prefers the direction that makes progress toward the objective.
   */
  private dodgeDirection(bullet: Bullet): Direction | null {
    const w = this.world
    const p = w.player!
    const vertical = bullet.dir === 'up' || bullet.dir === 'down'
    const candidates: Direction[] = vertical ? ['left', 'right'] : ['up', 'down']

    // Try each candidate; prefer the one that's passable.
    const open: Direction[] = []
    for (const d of candidates) {
      if (this.canMoveDir(p, d)) open.push(d)
    }
    if (open.length === 0) {
      // Try any open direction.
      for (const d of ALL_DIRS) {
        if (this.canMoveDir(p, d)) open.push(d)
      }
    }
    if (open.length === 0) return null

    // Pick the first open candidate (deterministic).
    return open[0]
  }

  // ================================================================
  // Fire control (facing direction only — used while navigating)
  // ================================================================

  /**
   * Decide whether to fire in the current facing direction. Fires when:
   * - A brick wall is directly ahead blocking the path
   * - An enemy is in the line of fire
   * - An enemy bullet is approaching (to intercept)
   * - **Proactive fire**: when no specific target is visible, the AI still
   *   fires while navigating. A competent player holds the fire button
   *   while moving — bullets clear walls, create openings, and occasionally
   *   hit enemies that move into the line of fire. The fire cooldown limits
   *   the rate; aim error occasionally fumbles a shot.
   */
  private shouldFireInFacingDir(pcx: number, pcy: number): boolean {
    const w = this.world
    const p = w.player!
    const dir = p.dir

    // Scan for walls and enemies in the facing direction (two-ray scan).
    const result = this.scanAhead(pcx, pcy, dir)
    if (result.enemy || result.wall) {
      return this.rng.next() >= this.params.aimError
    }

    // Also fire if an enemy bullet is approaching (intercept).
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
        return this.rng.next() >= this.params.aimError
      }
    }

    // No specific target in the facing direction. Fire proactively — a
    // competent player holds fire while moving to break walls, create
    // openings, and catch enemies that move into the line of fire.
    // The fire cooldown limits the rate.
    return this.rng.next() >= this.params.aimError
  }

  // ================================================================
  // Navigation
  // ================================================================

  /**
   * Follow the current A* path, re-planning as needed.
   * Returns the next movement direction, or null if no path.
   */
  private followPath(): Direction | null {
    const w = this.world
    const p = w.player!
    const playerCell = pxToCell(p.x, p.y)

    // Re-plan periodically or when the path is exhausted.
    this.replanTimer--
    if (this.replanTimer <= 0 || this.path.length === 0) {
      this.replan(playerCell)
      this.replanTimer = 30 // re-plan every ~0.5s
    }

    // Follow the path.
    if (this.path.length > 0) {
      const nextDir = this.path[0]

      // Suboptimal path: small chance of taking a different direction.
      if (this.rng.next() < this.params.suboptimalPathProb) {
        const altDirs = ALL_DIRS.filter((d) => d !== nextDir && this.canMoveDir(p, d))
        if (altDirs.length > 0) {
          return this.rng.pick(altDirs)
        }
      }

      // Check if we can actually move in the path direction.
      if (this.canMoveDir(p, nextDir)) {
        this.path.shift()
        return nextDir
      }

      // Path blocked (by a tank?) — try alternative directions.
      for (const d of ALL_DIRS) {
        if (d === opposite(nextDir)) continue
        if (this.canMoveDir(p, d)) {
          return d
        }
      }

      // Fully stuck — re-plan next tick.
      this.path = []
      this.replanTimer = 0
    }

    // No path — try to move toward the nearest enemy directly.
    return this.directMove(playerCell)
  }

  /** Re-plan the A* path to the current best target. */
  private replan(playerCell: Cell): void {
    const w = this.world
    const target = this.selectTarget(playerCell)
    if (!target) {
      this.path = []
      return
    }

    // If already at the target, no path needed.
    if (target.col === playerCell.col && target.row === playerCell.row) {
      this.path = []
      return
    }

    const path = findPath(w.tileMap, playerCell, target)
    if (path) {
      this.path = path
    } else {
      // No path found — try a nearby cell.
      this.path = []
    }
  }

  /**
   * Select the best target cell for the player to navigate toward.
   *
   * **Base defense priority**: always targets the enemy closest to the base,
   * regardless of distance. The AI navigates toward the enemy to intercept
   * it before it reaches the base. Combined with stop-and-shoot and
   * proactive fire, this keeps the AI aggressive while defending.
   *
   * If no enemies are alive, moves toward the center of the map to be ready
   * for the next wave.
   */
  private selectTarget(_playerCell: Cell): Cell | null {
    const w = this.world
    const baseX = BASE_POS.col * CELL
    const baseY = BASE_POS.row * CELL

    const enemies = w.tanks.filter((t) => t.alive && t.spawnTimer <= 0)
    if (enemies.length === 0) {
      // No enemies alive — move toward the center of the map (between spawns).
      return { col: 12, row: 6 }
    }

    // Always prioritize the enemy closest to the base.
    let bestEnemy: Tank | null = null
    let minBaseDist = Infinity
    for (const t of enemies) {
      const tx = t.x + t.w / 2
      const ty = t.y + t.h / 2
      const dist = Math.abs(tx - baseX) + Math.abs(ty - baseY)
      if (dist < minBaseDist) {
        minBaseDist = dist
        bestEnemy = t
      }
    }

    if (bestEnemy) {
      return pxToCell(bestEnemy.x, bestEnemy.y)
    }

    return null
  }

  /** Direct movement toward the nearest enemy (fallback when A* fails). */
  private directMove(playerCell: Cell): Direction | null {
    const w = this.world
    const p = w.player!
    const target = this.selectTarget(playerCell)
    if (!target) return null

    const dx = target.col * CELL - p.x
    const dy = target.row * CELL - p.y

    // Try the dominant axis first.
    if (Math.abs(dx) > Math.abs(dy)) {
      const dir = dx > 0 ? 'right' : 'left'
      if (this.canMoveDir(p, dir)) return dir
    }
    if (Math.abs(dy) > 0) {
      const dir = dy > 0 ? 'down' : 'up'
      if (this.canMoveDir(p, dir)) return dir
    }
    // Try the other axis.
    if (Math.abs(dx) > 0) {
      const dir = dx > 0 ? 'right' : 'left'
      if (this.canMoveDir(p, dir)) return dir
    }

    return null
  }

  /** Check if the player tank can move one CELL in the given direction. */
  private canMoveDir(tank: Tank, dir: Direction): boolean {
    const w = this.world
    const v = DIR_VECTORS[dir]
    const gx = snap(tank.x, CELL)
    const gy = snap(tank.y, CELL)
    const nx = gx + v.dx * CELL
    const ny = gy + v.dy * CELL
    if (!w.isInBounds(nx, ny, TANK, TANK)) return false
    if (w.rectHitsTerrain(nx, ny, TANK, TANK)) return false
    // Check tank collisions.
    for (const o of w.allTanks) {
      if (o === tank || !o.alive) continue
      if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
    }
    return true
  }
}
