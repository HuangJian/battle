import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet } from '../types'
import type { Direction } from '../constants'
import type { Cell } from '../utils/pathfind'
import type { RNG } from '../utils/RNG'
import {
  findEnemyDirectionImpl,
  scanAheadImpl,
  shouldFireInDirImpl,
  isBaseProtectionBrickImpl,
} from './god/FireControl'
import {
  findMostDangerousBulletImpl,
  findBulletThreatToBaseImpl,
  baseBulletInterceptCellImpl,
  dodgeDirectionImpl,
  isSafeDirImpl,
} from './god/ThreatAssessor'
import {
  findPowerUpTargetImpl,
  calculateRouteDangerImpl,
  getDefaultDefensePositionImpl,
  selectTargetImpl,
} from './god/StrategyPlanner'
import {
  playerCellImpl,
  tankCellImpl,
  navigateTowardsImpl,
  followPathImpl,
  replanImpl,
  directMoveImpl,
  canMoveOrBreakImpl,
  canMoveDirImpl,
} from './god/Navigator'

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
 *
 * **§0.5 split (plan/God-AI-Curriculum)**: the decision logic was extracted
 * into `./god/*` sub-modules (FireControl, ThreatAssessor, StrategyPlanner,
 * Navigator). Each moved method body lives in a `<name>Impl(self, ...)` free
 * function; this class keeps the shared state, `think()` (verbatim), and a
 * thin `public` wrapper per method so call sites and external consumers are
 * unchanged. This is a pure relocation — runtime behavior is identical.
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
  /**
   * S6/S10: enemies remaining in the spawn queue at which the AI switches
   * from defense to aggressive hunting (plan/God-AI-Curriculum §5.3).
   * Previously hardcoded to 3 (too conservative — 0% win rate root cause).
   * Raising it lets the AI start clearing earlier instead of turtling.
   */
  endgameEnemyThreshold: number
  /**
   * S6: max enemies *alive on field* at which the AI may enter hunt mode
   * (plan/God-AI-Curriculum §5.3). Replaces the hardcoded `2` in `canHunt`.
   * Set to 4 (= MAX_ENEMIES_ALIVE) so the binding constraint is
   * `endgameEnemyThreshold` (queue count), not field count.
   */
  huntAllyCount: number
}

/** Default God AI parameters — optimized via CMA-ES v4.1 (2026-07-28).
 * See .workbuddy/optimization-v4_1/ for details.
 *
 * CMA-ES v4.1 used a clear-speed-centric fitness function with a
 * gameover-loophole fix:
 *   win*5000 + kills*60 + base*200 + speed*800
 *   - remaining*25 - gameover*500 - lowKill*400
 *
 * The fitness penalizes ALL non-wins for remaining enemies (not just
 * timeouts), adds an extra gameover penalty (base lost = always worse
 * than timeout), and was evaluated across 40 seeds (up from 8).
 *
 * v4.1 results (40 seeds, 18000 ticks, classic stage 0):
 *   Default (v3): 20% win / 85% base / 10.8 kills / 6 gameovers
 *   Optimized:    20% win / 97.5% base / 12.0 kills / 1 gameover
 *
 * Key strategy changes from v3 (all 11 optimizer-tuned fields; replanInterval=3
 * and powerupMaxDivertDistance=9 were set in the earlier v3 work and unchanged here):
 *   - reactionDelay 0→1 (slightly slower reactions → less jittery firing)
 *   - aimError 0.0024→0 (perfect aim)
 *   - suboptimalPathProb 0.062→0.093 (more path noise / willingness to take imperfect routes)
 *   - Wider defense (spread 5→9, offset 1→2) with larger wall scan (1→2)
 *   - threatRangeCells 26→20 (tighter enemy threat sensing window)
 *   - Longer bullet interception range (3→7) for better base protection
 *   - Earlier hunting (endgameEnemyThreshold 1→3) with more enemies allowed (huntAllyCount 4→6)
 *   - Further roaming distance (10→14) for aggressive kill pursuit
 */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 1,
  aimError: 0,
  suboptimalPathProb: 0.09313768317029014,

  defenseRowOffset: 2,
  defenseColSpread: 9,
  threatRangeCells: 20,
  maxPlayerDistFromBase: 14,
  t8MaxInterceptDistCells: 7,
  baseWallScanRadius: 2,
  replanInterval: 3,
  powerupMaxDivertDistance: 9,
  endgameEnemyThreshold: 3,
  huntAllyCount: 6,
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
// GodAIInput (orchestrator)
// ============================================================

export class GodAIInput implements InputLike {
  // Shared state — public so the extracted ./god/* sub-modules can read/write
  // it via the `self: GodAIInput` they receive. This is the §0.5 split;
  // behavior is unchanged from before the split.
  world: World
  rng: RNG
  params: GodAIParams

  /** Cached move direction for this tick. */
  _moveDir: Direction | null = null
  /** Cached fire decision for this tick. */
  _fire = false
  /** Whether think() ran this tick (avoids double-computation). */
  _thought = false

  /** Current A* path being followed. */
  path: Direction[] = []
  /** Re-plan counter. */
  replanTimer = 0

  /** Threat reaction: when a threat is first seen, count down before reacting. */
  reactionCounter = 0
  /** The last threat bullet id we reacted to. */
  lastThreatId: number = -1

  /** Whether the AI is in aggressive mode (freeze/shield — hunt, don't defend). */
  aggressive = false

  /**
   * Whether the current stage has a base (plan/God-AI-Curriculum §3 Gap B).
   * Cached in reset() from `world.tileMap.hasBase()`. When false, ALL base-
   * defense logic is skipped: no T8 bullet interception, no defense positioning,
   * no `baseUnderThreat` check, no `playerDistToBase` constraint. The AI
   * degrades to pure hunting (chase nearest enemy).
   */
  hasBase = true

  /** Last cell the player was at when consuming a path step (prevents oscillation). */
  _lastPathCell: Cell | null = null

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
    // Gap B (plan §3): cache whether this stage has a base. All BASE_POS-
    // dependent logic checks this flag instead of assuming a base exists.
    this.hasBase = this.world.tileMap.hasBase()
  }

  getMoveDirection(): Direction | null {
    this.think()
    return this._moveDir
  }

  isFiring(): boolean {
    if (!this._thought) this.think()
    return this._fire
  }

  wasItemPressed(_kind: 'guard' | 'frenzy'): boolean {
    return false
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
    // In 'bulletCap' mode (classic FC), the engine gates fire by on-screen
    // bullet count, NOT by a time cooldown. The AI must mirror this:
    // "on cooldown" means the player's bullet is still in flight (cap
    // reached), not that a timer hasn't elapsed. Using the time check here
    // would suppress fire for ~1.3s after each shot even though the engine
    // allows refire the instant the previous bullet resolves — this was the
    // #1 root cause of the AI's abysmal kill count (1-3 kills/game) in classic.
    let onCooldown: boolean
    if (w.rules.fireModel === 'bulletCap') {
      const cap =
        (w.rules.maxBullets['player'] ?? 1) +
        ((p.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
      let inFlight = 0
      for (const b of w.bullets) {
        if (b.alive && b.ownerId === p.id) inFlight++
      }
      onCooldown = inFlight >= cap
    } else {
      onCooldown = now - p.lastFire < p.nextFireInterval
    }

    // ---- S8: Freeze window — aggressive hunt mode ----
    // When enemies are frozen, the player can hunt freely — enemies can't
    // fight back or approach the base. This is a free-clear window.
    const frozen = w.freezeTimer > 0

    // ---- S9: Shield — skip dodge but DON'T abandon defense ----
    // The 3-second respawn shield makes the player invulnerable, so dodge
    // is unnecessary. But the player must STILL defend the base — chasing
    // enemies across the map during the shield window leaves the base
    // undefended (the #1 cause of 330-tick base losses in classic).
    const shielded = (p.shieldTimer ?? 0) > 0

    // ---- S8: Set aggressive mode (freeze only, NOT shield) ----
    this.aggressive = frozen

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
        if (!this._moveDir) this._moveDir = this.directMove(this.playerCell())
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
    // Skip only when enemies are frozen (aggressive hunt — no bullets to
    // intercept). When shielded, the player can still intercept bullets
    // headed for the base — the shield protects the player, not the base.
    // Gap B (plan §3): skip entirely when the stage has no base.
    if (!this.aggressive && this.hasBase) {
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
      if (!this._moveDir) this._moveDir = this.directMove(this.playerCell())
      // Proactive fire — but ALWAYS check shouldFireInDir to avoid shooting
      // the player's own base (T6). In classic instant combat the base has
      // 1 HP, so a single self-inflicted bullet destroys it.
      if (this._moveDir && !this.canMoveDir(p, this._moveDir)) {
        this._fire = !onCooldown
      } else {
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
      }
      this.branchCounts.aggressive++
      return
    }

    // ---- T2a: Stop-and-aim (enemy in same row/col) ----
    // If an enemy is in the same row/col, stop and fire at it. If there's
    // a wall between, fire to break through (T6: never at base protection).
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

    // ---- T2a-hold: Aligned with enemy but on cooldown ----
    // When the player's bullet is in flight AND an enemy is in the same
    // row/col, hold position and wait for the bullet to resolve. This
    // prevents the player from navigating away from an aligned enemy
    // during the bullet's flight, which was the #1 cause of 0-kill games.
    if (aimDir && onCooldown) {
      this._moveDir = p.dir === aimDir ? null : aimDir
      this._fire = false
      this.branchCounts.t2a++
      return
    }

    // ---- S5: Power-up economy (normal mode) ----
    // Check for power-ups when no enemy is in line of fire. Previously this
    // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
    // Now the AI opportunistically grabs power-ups when it's safe to divert.
    if (!aimDir || onCooldown) {
      const puTarget = this.findPowerUpTarget(pcx, pcy)
      if (puTarget) {
        this._moveDir = this.navigateTowards(puTarget)
        this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
        this.branchCounts.powerup++
        return
      }
    }

    // ---- T2b: Navigate towards target (distance-adaptive) ----
    // Far from target (>5 cells): A* pathfinding routes around walls via
    // corridors — essential for maze stages. A* finds the corridor, not the
    // direct path through walls.
    //
    // Close to target (≤5 cells): directMove chases the moving enemy
    // directly, adjusting every tick. A* paths go stale before the player
    // arrives (the enemy moves away), causing the player to chase the
    // enemy's old position — directMove tracks the enemy's current position.
    //
    // When A* can't find a path (target walled off), directMove breaks
    // through brick walls by firing at them.
    //
    // Fire control in classic bulletCap mode (1 bullet in flight):
    // - If blocked by a wall in the path direction → fire to break through.
    // - If moving freely → fire only at enemies in the line of fire.
    const navTarget = this.selectTarget(this.playerCell())
    const navDist = navTarget
      ? Math.abs(navTarget.col - this.playerCell().col) +
        Math.abs(navTarget.row - this.playerCell().row)
      : Infinity

    if (navDist <= 5) {
      // Close range — directMove (responsive, tracks moving enemies).
      this._moveDir = this.directMove(this.playerCell())
    } else {
      // Long range — A* pathfinding (finds corridors in mazes).
      this._moveDir = this.followPath()
      if (!this._moveDir) {
        // A* failed or path exhausted — fall back to direct movement.
        this._moveDir = this.directMove(this.playerCell())
      }
    }
    // Fire control: when blocked by a breakable wall (verified by
    // canMoveOrBreak in directMove), fire immediately to break through.
    // Don't check shouldFireInDir here — it might fire at enemy bullets
    // (T5) instead of the wall, leaving the player stuck. When moving
    // freely, fire only at enemies (not walls) to save the bullet cap.
    if (this._moveDir && !this.canMoveDir(p, this._moveDir)) {
      // Blocked by a breakable wall or enemy tank — fire to break through.
      // canMoveOrBreak already verified the wall is non-base-protection.
      this._fire = !onCooldown
    } else {
      this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir, false)
    }
    this.branchCounts.navigate++
  }

  // ================================================================
  // Delegating wrappers — the decision logic lives in ./god/* (§0.5 split).
  // Each wrapper is a thin pass-through to the matching <name>Impl(self, ...).
  // `think()` and all external callers keep using the same method names.
  // ================================================================

  // --- FireControl ---
  findEnemyDirection(pcx: number, pcy: number): Direction | null {
    return findEnemyDirectionImpl(this, pcx, pcy)
  }
  scanAhead(
    pcx: number,
    pcy: number,
    dir: Direction,
  ): { enemy: boolean; wall: boolean; steel: boolean; baseWall: boolean; enemyDist: number } {
    return scanAheadImpl(this, pcx, pcy, dir)
  }
  isBaseProtectionBrick(col: number, row: number): boolean {
    return isBaseProtectionBrickImpl(this, col, row)
  }
  shouldFireInDir(pcx: number, pcy: number, dir: Direction, allowWallFire = true): boolean {
    return shouldFireInDirImpl(this, pcx, pcy, dir, allowWallFire)
  }

  // --- ThreatAssessor ---
  findMostDangerousBullet(pcx: number, pcy: number): Bullet | null {
    return findMostDangerousBulletImpl(this, pcx, pcy)
  }
  findBulletThreatToBase(): Bullet | null {
    return findBulletThreatToBaseImpl(this)
  }
  baseBulletInterceptCell(bullet: Bullet): Cell | null {
    return baseBulletInterceptCellImpl(this, bullet)
  }
  dodgeDirection(bullet: Bullet, pcx: number, pcy: number): Direction | null {
    return dodgeDirectionImpl(this, bullet, pcx, pcy)
  }
  isSafeDir(pcx: number, pcy: number, dir: Direction, excludeBulletId: number): boolean {
    return isSafeDirImpl(this, pcx, pcy, dir, excludeBulletId)
  }

  // --- StrategyPlanner ---
  findPowerUpTarget(pcx: number, pcy: number): Cell | null {
    return findPowerUpTargetImpl(this, pcx, pcy)
  }
  calculateRouteDanger(fromX: number, fromY: number, toX: number, toY: number): number {
    return calculateRouteDangerImpl(this, fromX, fromY, toX, toY)
  }
  getDefaultDefensePosition(): Cell {
    return getDefaultDefensePositionImpl(this)
  }
  selectTarget(playerCell: Cell): Cell | null {
    return selectTargetImpl(this, playerCell)
  }

  // --- Navigator ---
  playerCell(): Cell {
    return playerCellImpl(this)
  }
  tankCell(t: Tank): Cell {
    return tankCellImpl(this, t)
  }
  navigateTowards(target: Cell): Direction | null {
    return navigateTowardsImpl(this, target)
  }
  followPath(): Direction | null {
    return followPathImpl(this)
  }
  replan(playerCell: Cell): void {
    replanImpl(this, playerCell)
  }
  directMove(playerCell: Cell): Direction | null {
    return directMoveImpl(this, playerCell)
  }
  canMoveOrBreak(tank: Tank, dir: Direction): boolean {
    return canMoveOrBreakImpl(this, tank, dir)
  }
  canMoveDir(tank: Tank, dir: Direction): boolean {
    return canMoveDirImpl(this, tank, dir)
  }
}
