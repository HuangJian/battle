import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet, TankKind, PowerUpType } from '../types'
import type { Direction } from '../constants'
import { CELL, TANK, FIELD, GRID, DIR_VECTORS, BASE_POS, POWERUP_TIMEOUT_MS } from '../constants'
import { findPath, pxToCell, type Cell } from '../utils/pathfind'
import { snap, aabb, opposite, ALL_DIRS } from '../utils/helpers'
import type { RNG } from '../utils/RNG'
// BASE_SPEED_CPS no longer needed after simplification

/**
 * GodAIInput — a "theoretically optimal player" simulator that implements
 * `InputLike`.
 *
 * Tuning plan: plan/God-AI-Tuning.md
 *
 * Techniques implemented (by ID from the plan):
 *   T2a  — Stop-and-aim: when an enemy is in the same row/col, turn to face
 *          it and fire (instead of firing in the A* movement direction).
 *   T2b  — No fire while moving in wrong direction: fire only when the
 *          movement direction matches the aim direction, or when stopped
 *          with a target in the facing direction.
 *   T1   — Interception geometry: navigate to a point between the enemy and
 *          the base, not to the enemy's current cell (which is a moving target).
 *   T3   — Priority target selection: threat-to-base > power > armor > fast >
 *          basic; bonus enemies get extra weight.
 *   T6   — Don't destroy own base walls: proactive fire checks the ray for
 *          base-protection bricks.
 *   T8   — Base bullet interception: if an enemy bullet's trajectory crosses
 *          the base, move to intercept it (ultimate defense).
 *   T9   — Same-row/col priority sorting: when multiple enemies share a
 *          row/col, fire at the most dangerous (by kind + HP), not nearest.
 *   T11  — Steel wall avoidance: don't fire at steel unless player level ≥ 3.
 *   M3   — Dodge safety: candidate dodge cells checked against other bullets.
 *   M6   — Cooldown-aware firing: don't set _fire=true while on cooldown.
 *   S5a  — Power-up priority: bomb > star > freeze > fence > tank > shield.
 *   S5c  — Bonus enemy priority: tank.bonus === true gets extra target weight.
 *   S7   — Base wall integrity: scan base defense walls; thin walls → urgent
 *          return to defense.
 *   S8   — Freeze window: when freezeTimer > 0, switch to aggressive hunting.
 *   S9   — Shield exploitation: when shieldTimer > 0, skip dodge, go aggro.
 *   S10  — Endgame mode: when ≤ 2 enemies remain, switch from defense to
 *          active hunting to shorten clear time.
 *
 * **Determinism**: all randomness flows through `world.rng` (AGENTS §2.3).
 */

// ============================================================
// Parameters
// ============================================================

/**
 * Configurable parameters (plan/God-AI-Tuning §1).
 *
 * Contains both "imperfection" params (reactionDelay, aimError,
 * suboptimalPathProb) and "strategy/threshold" params (defense positioning,
 * threat ranges, replan intervals, etc.). All tunable values live here per
 * the plan's "implementation discipline" — data over code.
 */
export interface GodAIParams {
  // ---- Imperfection params ----
  /** Ticks of delay before reacting to a new threat. */
  reactionDelay: number
  /** Probability of a fire-control mistake. */
  aimError: number
  /** Probability of taking a suboptimal route step. */
  suboptimalPathProb: number

  // ---- Strategy / threshold params ----
  /** How many rows above the base to position for defense. */
  defenseRowOffset: number
  /** Max columns to shift from base column for defense. */
  defenseColSpread: number
  /** Only consider enemies within this Manhattan distance from base as threats. */
  threatRangeCells: number
  /** Player returns to base if farther than this from the base. */
  maxPlayerDistFromBase: number
  /** T8: max distance (cells) to travel to intercept a base-bound bullet. */
  t8MaxInterceptDistCells: number
  /** S7: cells around the base to scan for wall integrity. */
  baseWallScanRadius: number
  /** Re-plan interval (ticks). */
  replanInterval: number
  /** S5: max distance (cells) to divert for a power-up. */
  powerupMaxDivertDistance: number
  /** S10: enemies remaining threshold for endgame hunting mode. */
  endgameEnemyThreshold: number
}

/** Default God AI parameters — optimized via CMA-ES (2026-07-28).
 * See docs/god-ai-tuning-log.md and .workbuddy/optimization-v2/ for details. */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 0,
  aimError: 0,
  suboptimalPathProb: 0.3,

  defenseRowOffset: 1,
  defenseColSpread: 3,
  threatRangeCells: 8,
  maxPlayerDistFromBase: 4,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 5,
  replanInterval: 50,
  powerupMaxDivertDistance: 3,
  endgameEnemyThreshold: 1,
}

/**
 * Skilled Human proxy parameters (plan §3.3C): God AI + double reaction
 * delay + 20% aim error. Represents an experienced but non-perfect human.
 * MUST remain derived from God params — God gets stronger → human proxy
 * gets stronger automatically. Strategy thresholds are inherited as-is.
 * Minimums ensure the human is always weaker than God (even when God is perfect).
 */
export const SKILLED_HUMAN_PARAMS: GodAIParams = {
  ...DEFAULT_GOD_AI_PARAMS,
  reactionDelay: Math.max(2, DEFAULT_GOD_AI_PARAMS.reactionDelay * 2),
  aimError: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.aimError + 0.15),
  suboptimalPathProb: Math.max(0.15, DEFAULT_GOD_AI_PARAMS.suboptimalPathProb * 1.5),
}

// ============================================================
// Static constants (NOT tunable — game rules, not strategy)
// ============================================================

/** T2a: max distance (in cells) at which to stop-and-aim — entire field. */
const AIM_RANGE_CELLS = 26

/** T8: how far ahead to project a bullet's trajectory (entire field). */
const BULLET_TRAJECTORY_MAX_CELLS = 26

/** T3: kind-based threat weights for target selection. */
const KIND_THREAT_WEIGHT: Record<TankKind, number> = {
  power: 4,
  armor: 3,
  fast: 2,
  basic: 1,
  player: 0,
}

/** S5a: power-up collection priority (lower = higher priority). */
const POWERUP_PRIORITY: Record<PowerUpType, number> = {
  bomb: 0,
  star: 1,
  freeze: 2,
  fence: 3,
  tank: 4,
  shield: 5,
  helmet: 5,
  boat: 6,
}

// ============================================================
// GodAIInput
// ============================================================

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
  /** Re-plan counter. */
  private replanTimer = 0

  /** Threat reaction: when a threat is first seen, count down before reacting. */
  private reactionCounter = 0
  /** The last threat bullet id we reacted to. */
  private lastThreatId: number = -1

  /** Whether the AI is in aggressive mode (freeze/shield — hunt, don't defend). */
  private aggressive = false

  /** Last cell the player was at when consuming a path step (prevents oscillation). */
  private _lastPathCell: Cell | null = null

  /** Debug: branch counters for profiling. */
  branchCounts = { dodge: 0, t8: 0, aggressive: 0, t2a: 0, powerup: 0, navigate: 0, dead: 0 }

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
    this._lastPathCell = null
    this.aggressive = false
  }

  getMoveDirection(): Direction | null {
    this.think()
    return this._moveDir
  }

  isFiring(): boolean {
    if (!this._thought) this.think()
    return this._fire
  }

  endFrame(): void {
    this._thought = false
  }

  // ================================================================
  // Core decision loop (think)
  // ================================================================

  private think(): void {
    if (this._thought) return
    this._thought = true

    const w = this.world
    const p = w.player
    if (!p || !p.alive || p.spawnTimer > 0) {
      this._moveDir = null
      this._fire = false
      this.branchCounts.dead++
      return
    }

    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const now = w.frame * (1000 / 60)

    // ---- M6: Cooldown-aware firing ----
    const onCooldown = now - p.lastFire < p.nextFireInterval

    // ---- S8: Freeze window — aggressive mode ----
    const frozen = w.freezeTimer > 0

    // ---- S9: Shield — skip dodge ----
    const shielded = (p.shieldTimer ?? 0) > 0

    // ---- S8/S9: Set aggressive mode ----
    this.aggressive = frozen || shielded

    // ---- Scan for enemy targets (global vision, T9 priority) ----
    const aimDir = this.findEnemyDirection(pcx, pcy)

    // ---- Threat assessment (dodge incoming bullets) ----
    // Dodge FIRST: survive before defending the base.
    const threat = shielded ? null : this.findMostDangerousBullet(pcx, pcy)

    if (threat) {
      if (threat.id !== this.lastThreatId) {
        this.lastThreatId = threat.id
        this.reactionCounter = this.params.reactionDelay
      }

      if (this.reactionCounter > 0) {
        this.reactionCounter--
        // While reacting, keep navigating but fire only at targets in facing dir.
        this._moveDir = this.followPath()
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
        return
      }

      // Dodge: move perpendicular to the bullet (M3: verify safety).
      this._moveDir = this.dodgeDirection(threat, pcx, pcy)
      this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
      this.branchCounts.dodge++
      return
    }

    // No threat — reset reaction state.
    this.reactionCounter = 0
    this.lastThreatId = -1

    // ---- T8: Base bullet interception (ultimate defense) ----
    // Check AFTER dodge (survive first) but BEFORE aggressive/T2a.
    // Skip in aggressive mode (enemies frozen / player invulnerable).
    if (!this.aggressive) {
      const baseThreat = this.findBulletThreatToBase()
      if (baseThreat) {
        const interceptCell = this.baseBulletInterceptCell(baseThreat)
        if (interceptCell) {
          this._moveDir = this.navigateTowards(interceptCell)
          // Fire to intercept the bullet (T5 extended to base defense).
          this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
          this.branchCounts.t8++
          return
        }
      }
    }

    // ---- S8/S9: Aggressive mode (freeze or shield) ----
    if (this.aggressive) {
      // Skip defense, go straight for the nearest enemy or power-up.
      if (aimDir) {
        // T2a: stop-and-aim — if already facing the enemy, stop; else turn.
        if (p.dir === aimDir) {
          this._moveDir = null
        } else {
          this._moveDir = aimDir
        }
        this._fire = !onCooldown && this.rng.next() >= this.params.aimError
        return
      }
      // No enemy in row/col — check for power-up (S5).
      const puTarget = this.findPowerUpTarget(pcx, pcy)
      if (puTarget) {
        this._moveDir = this.navigateTowards(puTarget)
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
        return
      }
      // Navigate to nearest enemy.
      this._moveDir = this.followPath()
      this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
      this.branchCounts.aggressive++
      return
    }

    // ---- T2a: Stop-and-aim (enemy in same row/col) ----
    // If an enemy is in the same row/col, stop and fire at it. If there's
    // a wall between, fire to break through (T6: never at base protection).
    //
    // CRITICAL FIX: When on cooldown, do NOT stop — fall through to navigation.
    // Stopping while on cooldown wastes ~74 ticks per cycle doing nothing.
    // This was the #1 decision mistake found by CMA-ES trace analysis:
    // the player fired only 7 times in 10116 ticks because it kept stopping
    // in T2a but couldn't fire due to cooldown.
    if (aimDir && !onCooldown) {
      const scan = this.scanAhead(pcx, pcy, aimDir)

      if (scan.enemy || (scan.wall && !scan.baseWall && (!scan.steel || (p.level ?? 0) >= 3))) {
        if (p.dir === aimDir) {
          this._moveDir = null // Already facing — stop and shoot
        } else {
          this._moveDir = aimDir // Turn to face enemy
        }
        this._fire = this.rng.next() >= this.params.aimError
        this.branchCounts.t2a++
        return
      }
      // No clear shot, or wall below defense row — fall through to navigation.
    }

    // ---- T2b: Navigate directly towards target ----
    // Use direct movement instead of A* — the player breaks through walls
    // rather than navigating around them. This is faster and more reactive.
    this._moveDir = this.directMove(this.playerCell())
    this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
    this.branchCounts.navigate++
  }

  // ================================================================
  // Target scanning (T2a, T9)
  // ================================================================

  /**
   * Find the direction to the best enemy tank in the same row/col, using
   * **global vision** (plan §3.1). T9: when multiple enemies share a
   * row/col, select by threat weight (power > armor > fast > basic) + HP,
   * not just proximity.
   *
   * Returns the direction to turn to face the selected enemy, or null.
   */
  private findEnemyDirection(pcx: number, pcy: number): Direction | null {
    const w = this.world
    let bestDir: Direction | null = null
    let bestScore = -Infinity

    const halfT = TANK / 2

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
  private scanAhead(
    pcx: number,
    pcy: number,
    dir: Direction,
  ): { enemy: boolean; wall: boolean; steel: boolean; baseWall: boolean; enemyDist: number } {
    const w = this.world
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
          if (this.isBaseProtectionBrick(col, row)) {
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
   */
  private isBaseProtectionBrick(col: number, row: number): boolean {
    const bc = BASE_POS.col
    const br = BASE_POS.row
    const dc = Math.abs(col - bc)
    const dr = Math.abs(row - br)
    // The base occupies cols 12-13, rows 24-25. Protection bricks are
    // those immediately adjacent (within 2 cells) that form the wall.
    const r = this.params.baseWallScanRadius
    return dc <= r && dr <= r && (dc <= 2 || dr <= 2)
  }

  // ================================================================
  // Threat assessment (dodge, T8)
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
      if (!b.alive || b.isPlayer) continue
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
   * T8: Find an enemy bullet whose trajectory will cross the base area.
   * This is the ultimate defense — intercept bullets heading for the base
   * even if they're not threatening the player.
   */
  private findBulletThreatToBase(): Bullet | null {
    const w = this.world
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    const baseHalf = CELL // base is 2×2 cells = 32px, half = 16px

    let best: Bullet | null = null
    let bestDist = Infinity

    for (const b of w.bullets) {
      if (!b.alive || b.isPlayer) continue
      const bcx = b.x + b.w / 2
      const bcy = b.y + b.h / 2
      const v = DIR_VECTORS[b.dir]

      // Project the bullet's trajectory forward and check if it crosses the base.
      let crossesBase = false
      for (let d = CELL; d <= BULLET_TRAJECTORY_MAX_CELLS * CELL; d += CELL) {
        const fx = bcx + v.dx * d
        const fy = bcy + v.dy * d

        // Check if the trajectory point is within the base area.
        // BULLET_TRAJECTORY_MAX_CELLS is static (full field).
        if (Math.abs(fx - baseCx) < baseHalf * 2 && Math.abs(fy - baseCy) < baseHalf * 2) {
          crossesBase = true
          break
        }

        // If the trajectory goes off-field, stop.
        if (fx < 0 || fx > FIELD || fy < 0 || fy > FIELD) break

        // If the trajectory hits a wall (brick/steel/base), stop — the
        // bullet would be blocked before reaching the base.
        const col = Math.floor(fx / CELL)
        const row = Math.floor(fy / CELL)
        const terrain = w.tileMap.get(col, row)
        if (terrain === 'brick' || terrain === 'steel') break
        // If it hits the base itself, that's a direct hit.
        if (terrain === 'base') {
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
  private baseBulletInterceptCell(bullet: Bullet): Cell | null {
    const w = this.world
    const p = w.player!
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
    if (bestDist > this.params.t8MaxInterceptDistCells * CELL) return null

    return bestCell
  }

  /**
   * Choose a dodge direction perpendicular to the incoming bullet.
   * M3: verify the candidate direction is safe (not into another bullet's path).
   */
  private dodgeDirection(bullet: Bullet, pcx: number, pcy: number): Direction | null {
    const w = this.world
    const p = w.player!
    const vertical = bullet.dir === 'up' || bullet.dir === 'down'
    const candidates: Direction[] = vertical ? ['left', 'right'] : ['up', 'down']

    // Try each candidate; prefer the one that's passable AND safe (M3).
    const open: Direction[] = []
    for (const d of candidates) {
      if (this.canMoveDir(p, d) && this.isSafeDir(pcx, pcy, d, bullet.id)) {
        open.push(d)
      }
    }

    // If no safe candidate, try passable but unsafe.
    if (open.length === 0) {
      for (const d of candidates) {
        if (this.canMoveDir(p, d)) open.push(d)
      }
    }

    // If still nothing, try any open direction.
    if (open.length === 0) {
      for (const d of ALL_DIRS) {
        if (this.canMoveDir(p, d)) open.push(d)
      }
    }
    if (open.length === 0) return null

    // Prefer the direction that keeps the player closer to the base.
    // This prevents dodge from sending the player on a wild chase away
    // from the defense position.
    const baseCx = BASE_POS.col * CELL + CELL
    const baseCy = BASE_POS.row * CELL + CELL
    open.sort((a, b) => {
      const va = DIR_VECTORS[a]
      const vb = DIR_VECTORS[b]
      const distA = Math.abs(pcx + va.dx * CELL - baseCx) + Math.abs(pcy + va.dy * CELL - baseCy)
      const distB = Math.abs(pcx + vb.dx * CELL - baseCx) + Math.abs(pcy + vb.dy * CELL - baseCy)
      return distA - distB
    })

    return open[0]
  }

  /**
   * M3: Check if moving in direction `d` would put the player into another
   * bullet's trajectory (excluding the one we're already dodging).
   */
  private isSafeDir(pcx: number, pcy: number, dir: Direction, excludeBulletId: number): boolean {
    const w = this.world
    const v = DIR_VECTORS[dir]
    // Check the cell we'd move into.
    const newCx = pcx + v.dx * CELL
    const newCy = pcy + v.dy * CELL

    for (const b of w.bullets) {
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

  // ================================================================
  // Fire control (T2b, T6, T11, M6)
  // ================================================================

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
  private shouldFireInDir(pcx: number, pcy: number, dir: Direction): boolean {
    const w = this.world
    const p = w.player!

    const result = this.scanAhead(pcx, pcy, dir)

    // Enemy in line of fire — always fire.
    if (result.enemy) {
      return this.rng.next() >= this.params.aimError
    }

    // T6/T11: Don't fire at base protection bricks or steel (level < 3).
    // These are checked but always return false — we don't waste bullets
    // on walls during navigation.
    if (result.baseWall) return false
    if (result.steel && (p.level ?? 0) < 3) return false
    // Steel with level ≥ 3: fire to pierce (enemy might be behind it).
    if (result.steel) {
      return this.rng.next() >= this.params.aimError
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
        return this.rng.next() >= this.params.aimError
      }
    }

    // Fire at brick walls to clear paths and create firing lanes.
    // This is critical for mobility — without it, the player navigates
    // around walls, which takes too long and lets enemies reach the base.
    // Never fire at base protection bricks (T6) or steel (T11).
    if (result.wall && !result.baseWall && !result.steel) {
      return this.rng.next() >= this.params.aimError
    }

    return false
  }

  // ================================================================
  // S5: Power-up economy
  // ================================================================

  /**
   * S5a/S5c/NEW-Requirement-3: Find a power-up worth collecting.
   * Returns the target cell if a power-up is available and worth the risk,
   * null otherwise.
   *
   * NEW: Dynamic priority based on:
   *   - Power-up effect (bomb > star > freeze > fence > tank > shield/helmet > boat)
   *   - Travel distance (cost in time/opportunity)
   *   - Route danger (how many enemies are between player and power-up)
   */
  private findPowerUpTarget(pcx: number, pcy: number): Cell | null {
    const w = this.world
    if (w.powerUps.length === 0) return null

    let bestPu: { cell: Cell; score: number } | null = null

    for (const pu of w.powerUps) {
      if (!pu.alive) continue
      const cx = pu.x + pu.w / 2
      const cy = pu.y + pu.h / 2
      const dist = Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL)

      // S5d: if about to expire and too far, skip.
      const lifeRemaining = POWERUP_TIMEOUT_MS - pu.lifeTimer
      if (lifeRemaining < 3000 && dist > 5) continue

      // S5a: base priority by type.
      const priority = POWERUP_PRIORITY[pu.type] ?? 5

      // NEW Requirement 3: Calculate route danger
      const dangerLevel = this.calculateRouteDanger(pcx, pcy, cx, cy)

      // NEW Requirement 3: Dynamic scoring
      // Score = (base_priority * 1000) - (distance * 10) - (danger * 500)
      // High-value power-ups (bomb/star) get bonus to offset higher risk
      let score = priority * 1000 - dist * 10 - dangerLevel * 500

      // Extra bonus for bomb (it clears the whole screen, worth high risk)
      if (pu.type === 'bomb') {
        score += 2000
      }

      // Extra bonus for star (permanent upgrade)
      if (pu.type === 'star') {
        score += 1000
      }

      // Penalty for boat (only situationally useful)
      if (pu.type === 'boat') {
        score -= 500
      }

      // S5b: high-value power-ups are worth longer diversions
      const maxDist = priority <= 1 ? this.params.powerupMaxDivertDistance : 8
      if (dist > maxDist) continue

      // NEW: Don't collect if route is too dangerous unless it's a bomb/star
      if (dangerLevel > 3 && priority > 2) continue // Too dangerous for low-value power-ups
      if (dangerLevel > 5 && pu.type !== 'bomb') continue // Bomb is worth almost any risk

      if (!bestPu || score > bestPu.score) {
        bestPu = { cell: pxToCell(pu.x, pu.y), score }
      }
    }

    return bestPu?.cell ?? null
  }

  /**
   * NEW Requirement 3: Calculate how dangerous a route is.
   * Returns a danger level from 0 (safe) to N (many enemies on the path).
   */
  private calculateRouteDanger(fromX: number, fromY: number, toX: number, toY: number): number {
    const w = this.world
    let danger = 0

    // Simple heuristic: count enemies that are closer to the target than we are
    const targetCell = pxToCell(toX, toY)
    const playerCell = pxToCell(fromX, fromY)
    const playerDistToTarget =
      Math.abs(targetCell.col - playerCell.col) + Math.abs(targetCell.row - playerCell.row)

    for (const t of w.tanks) {
      if (!t.alive || t.spawnTimer > 0) continue

      const enemyCell = pxToCell(t.x, t.y)
      const enemyDistToTarget =
        Math.abs(targetCell.col - enemyCell.col) + Math.abs(targetCell.row - enemyCell.row)

      // If enemy is closer to target than player, and on the path, add danger
      if (enemyDistToTarget < playerDistToTarget) {
        // Check if enemy is roughly between player and target
        const dx = enemyCell.col - playerCell.col
        const dy = enemyCell.row - playerCell.row
        const tx = targetCell.col - playerCell.col
        const ty = targetCell.row - playerCell.row

        // Simple projection check
        if (Math.sign(dx) === Math.sign(tx) && Math.sign(dy) === Math.sign(ty)) {
          danger += 1
          // Extra danger for power/armor tanks
          if (t.kind === 'power' || t.kind === 'armor') {
            danger += 1
          }
        }
      }
    }

    return danger
  }

  // ================================================================
  // Navigation (T1, S7, S10)
  // ================================================================

  /** Get the player's grid-aligned cell (matches canMoveDir's snap). */
  private playerCell(): Cell {
    const p = this.world.player!
    return { col: Math.round(p.x / CELL), row: Math.round(p.y / CELL) }
  }

  /** Get a tank's grid-aligned cell (consistent with playerCell). */
  private tankCell(t: Tank): Cell {
    return { col: Math.round(t.x / CELL), row: Math.round(t.y / CELL) }
  }

  /**
   * Navigate towards a specific cell using A* pathfinding.
   * Returns the next movement direction, or null if no path.
   */
  private navigateTowards(target: Cell): Direction | null {
    const w = this.world
    const p = w.player!
    const playerCell = this.playerCell()

    if (target.col === playerCell.col && target.row === playerCell.row) {
      return null
    }

    const path = findPath(w.tileMap, playerCell, target)
    if (!path || path.length === 0) return null

    // Suboptimal path: small chance of taking a different direction.
    if (this.rng.next() < this.params.suboptimalPathProb) {
      const altDirs = ALL_DIRS.filter((d) => d !== path[0] && this.canMoveDir(p, d))
      if (altDirs.length > 0) {
        return this.rng.pick(altDirs)
      }
    }

    const nextDir = path[0]
    if (this.canMoveDir(p, nextDir)) {
      return nextDir
    }

    // Path blocked — try alternative directions.
    for (const d of ALL_DIRS) {
      if (d === opposite(nextDir)) continue
      if (this.canMoveDir(p, d)) return d
    }

    return null
  }

  /**
   * Follow the current A* path, re-planning as needed.
   * Returns the next movement direction, or null if no path.
   */
  private followPath(): Direction | null {
    const w = this.world
    const p = w.player!
    const playerCell = this.playerCell()

    // Only consume path steps when the player enters a new grid cell.
    // The player moves at ~0.7 px/tick, so it takes ~23 ticks per cell.
    // Shifting every tick would exhaust the path before arrival.
    if (
      !this._lastPathCell ||
      this._lastPathCell.col !== playerCell.col ||
      this._lastPathCell.row !== playerCell.row
    ) {
      if (this.path.length > 0) this.path.shift()
      this._lastPathCell = { col: playerCell.col, row: playerCell.row }
    }

    // Re-plan periodically or when the path is exhausted.
    this.replanTimer--
    if (this.replanTimer <= 0 || this.path.length === 0) {
      this.replan(playerCell)
      this.replanTimer = this.params.replanInterval
      this._lastPathCell = { col: playerCell.col, row: playerCell.row }
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

  /**
   * Default defense position: centered above the base at the defense row.
   * This is the fallback when no enemies are present.
   */
  private getDefaultDefensePosition(): Cell {
    return { col: BASE_POS.col, row: BASE_POS.row - this.params.defenseRowOffset }
  }

  /** Re-plan the A* path to the current best target. */
  private replan(playerCell: Cell): void {
    const w = this.world
    const target = this.selectTarget(playerCell)
    if (!target) {
      this.path = []
      return
    }

    if (target.col === playerCell.col && target.row === playerCell.row) {
      this.path = []
      return
    }

    const path = findPath(w.tileMap, playerCell, target)
    if (path) {
      this.path = path
    } else {
      this.path = []
    }
  }

  /**
   * Select the best target cell for the player to navigate toward.
   *
   * Core strategy: ALWAYS intercept the enemy closest to the base by
   * navigating to that enemy's column at the defense row. This puts
   * the player in position to shoot the enemy via T2a when it crosses
   * the defense row. No gating on "isEnemyThreateningBase" — every
   * enemy is a potential threat, and the player should proactively
   * position itself, not wait passively.
   *
   * Priority:
   *   1. Enemy closest to base → intercept at defense row
   *   2. Aggressive mode (freeze) → chase nearest enemy directly
   *   3. Endgame (≤2 enemies remaining) → chase last enemy
   *   4. No enemies → default defense position
   */
  private selectTarget(playerCell: Cell): Cell | null {
    const w = this.world
    const p = w.player
    if (!p) return null

    const baseCol = BASE_POS.col
    const baseRow = BASE_POS.row
    const defenseRow = baseRow - this.params.defenseRowOffset

    // If the player is too far from the base, return to defense position.
    const playerDistToBase = Math.abs(playerCell.col - baseCol) + Math.abs(playerCell.row - baseRow)
    if (playerDistToBase > this.params.maxPlayerDistFromBase) {
      return this.getDefaultDefensePosition()
    }

    const enemies = w.tanks.filter((t) => t.alive && t.spawnTimer <= 0)
    if (enemies.length === 0) return this.getDefaultDefensePosition()

    // Aggressive mode (freeze): enemies can't move — chase nearest directly.
    if (this.aggressive) {
      let best = enemies[0]
      let bestDist = Infinity
      for (const t of enemies) {
        const tc = this.tankCell(t)
        const d = Math.abs(tc.col - playerCell.col) + Math.abs(tc.row - playerCell.row)
        if (d < bestDist) {
          bestDist = d
          best = t
        }
      }
      return this.tankCell(best)
    }

    // Endgame: ≤2 enemies remaining and ≤1 on field → hunt directly.
    if (w.enemiesRemaining <= this.params.endgameEnemyThreshold && enemies.length <= 1) {
      return this.tankCell(enemies[0])
    }

    // Find the enemy closest to the base that's within threat range.
    let bestEnemy: Tank | null = null
    let bestScore = -Infinity
    for (const t of enemies) {
      const tc = this.tankCell(t)
      const distToBase = Math.abs(tc.col - baseCol) + Math.abs(tc.row - baseRow)
      if (distToBase > this.params.threatRangeCells) continue

      const threatWeight = KIND_THREAT_WEIGHT[t.kind] ?? 1
      const bonusWeight = t.bonus ? 3 : 0
      // HUGE bonus for enemies at or below the defense row — critical threat
      const urgencyBonus = tc.row >= defenseRow ? (baseRow - tc.row) * 100 : 0
      // Small bonus for enemies in the base row region (rows 20-23)
      const proximityBonus = tc.row >= 20 ? 50 : 0
      const score =
        -distToBase * 10 + (threatWeight + bonusWeight) * 30 + urgencyBonus + proximityBonus
      if (score > bestScore) {
        bestScore = score
        bestEnemy = t
      }
    }

    if (!bestEnemy) return this.getDefaultDefensePosition()

    return this.interceptCell(bestEnemy, defenseRow, baseCol, baseRow)
  }

  /**
   * T1: Calculate the intercept cell — the position where the player should
   * be to shoot the enemy as it approaches the base.
   *
   * Strategy: position at the enemy's column at the defense row. This gives
   * the player a vertical firing line to shoot the enemy when it crosses.
   * If the enemy is already below the defense row (close to base), intercept
   * at the enemy's row minus 1 — get above it and shoot down.
   */
  private interceptCell(enemy: Tank, defenseRow: number, baseCol: number, _baseRow: number): Cell {
    const ec = this.tankCell(enemy)

    let targetCol: number
    let targetRow: number

    // If enemy is at or below the defense row, go directly above it — urgent!
    // Don't clamp distance — we need to be there NOW.
    if (ec.row >= defenseRow) {
      targetCol = ec.col
      targetRow = Math.max(0, ec.row - 1)
    } else {
      // Intercept at the defense row. Clamp the column to stay close to the base.
      const spread = this.params.defenseColSpread
      targetCol = Math.max(baseCol - spread, Math.min(baseCol + spread, ec.col))
      targetRow = defenseRow
    }

    targetCol = Math.max(0, Math.min(GRID - 2, targetCol))
    targetRow = Math.max(0, Math.min(GRID - 2, targetRow))

    return { col: targetCol, row: targetRow }
  }

  /** Direct movement toward the target, breaking through walls. */
  private directMove(playerCell: Cell): Direction | null {
    const w = this.world
    const p = w.player!
    const target = this.selectTarget(playerCell)
    if (!target) return null
    if (target.col === playerCell.col && target.row === playerCell.row) return null

    const dx = target.col * CELL - p.x
    const dy = target.row * CELL - p.y

    // Build direction preference: primary axis first, then secondary.
    const dirs: Direction[] = []
    if (Math.abs(dx) >= Math.abs(dy)) {
      if (dx > 0) dirs.push('right')
      else if (dx < 0) dirs.push('left')
      if (dy > 0) dirs.push('down')
      else if (dy < 0) dirs.push('up')
    } else {
      if (dy > 0) dirs.push('down')
      else if (dy < 0) dirs.push('up')
      if (dx > 0) dirs.push('right')
      else if (dx < 0) dirs.push('left')
    }

    // Return the first preferred direction that we can either move through
    // or break through (brick wall). This enables wall-breaking: the tank
    // faces the wall, shouldFireInDir fires at it, and the wall breaks.
    for (const dir of dirs) {
      if (this.canMoveOrBreak(p, dir)) return dir
    }

    // All preferred directions blocked by unbreakable terrain or tanks —
    // try any passable direction (excluding reverse of primary).
    for (const d of ALL_DIRS) {
      if (dirs.length > 0 && d === opposite(dirs[0])) continue
      if (this.canMoveDir(p, d)) return d
    }

    return null
  }

  /**
   * Check if the tank can move in a direction OR break through a brick wall.
   * Returns true if the direction is passable, or if it's blocked only by
   * non-base-protection brick walls (which can be destroyed by firing).
   * Returns false if blocked by steel, water, base, or another tank.
   */
  private canMoveOrBreak(tank: Tank, dir: Direction): boolean {
    if (this.canMoveDir(tank, dir)) return true

    const w = this.world
    const v = DIR_VECTORS[dir]
    const gx = snap(tank.x, CELL)
    const gy = snap(tank.y, CELL)
    const nx = gx + v.dx * CELL
    const ny = gy + v.dy * CELL

    // Out of bounds — can't break through
    if (!w.isInBounds(nx, ny, TANK, TANK)) return false

    // Blocked by a tank? — can't break through, need to go around
    for (const o of w.allTanks) {
      if (o === tank || !o.alive) continue
      if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
    }

    // Check terrain at new position — if any cell is unbreakable, can't break through
    const c0 = Math.floor(nx / CELL)
    const r0 = Math.floor(ny / CELL)
    const c1 = Math.floor((nx + TANK - 1) / CELL)
    const r1 = Math.floor((ny + TANK - 1) / CELL)

    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        if (c < 0 || c >= GRID || r < 0 || r >= GRID) continue
        const terrain = w.tileMap.get(c, r)
        if (terrain === 'steel' || terrain === 'water' || terrain === 'base') return false
        if (terrain === 'brick' && this.isBaseProtectionBrick(c, r)) return false
      }
    }

    // Only breakable brick walls blocking — can break through by firing
    return true
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
    for (const o of w.allTanks) {
      if (o === tank || !o.alive) continue
      if (aabb(nx, ny, TANK, TANK, o.x, o.y, o.w, o.h)) return false
    }
    return true
  }
}
