import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet } from '../types'
import type { Direction } from '../constants'
import type { Cell } from '../utils/pathfind'
import type { RNG } from '../utils/RNG'
import { BASE_POS } from '../constants'
import { ALL_DIRS } from '../utils/helpers'
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
import { threatScoreImpl, smartIsBaseUnderThreatImpl } from './god/SmartThreatModel'

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
 * **Determinism**: all randomness flows through `this.rng` (AGENTS §2.3).
 * When an independent RNG is provided (headless sim recording), `this.rng`
 * is decoupled from `world.rng` so God AI decisions don't consume the
 * world RNG stream — enabling faithful replay playback (DECISIONS #47).
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
  /**
   * P4: race-to-base check range. An enemy within this Manhattan distance
   * of the base triggers emergency defense IF the player would lose the
   * race back (player farther from base than the enemy, plus margin).
   * Catches flanking runners that the static ±3-col threat box misses
   * (S6 Iron Curtain: enemies sweep the bottom edge from the far corner
   * while the player roams the steel maze 20+ cells away).
   */
  baseRaceRangeCells: number
  /**
   * P4: safety margin (cells) for the race check. The player must be at
   * least this many cells CLOSER to the base than the enemy to keep
   * hunting; otherwise it returns to defense.
   */
  baseRaceMarginCells: number
  /**
   * P4.2: outnumbered retreat — if at least this many live enemies are
   * within `outnumberedRadiusCells` of the player, fall back to the
   * defense position instead of pressing the attack. Fixes the S18
   * (Frozen Field) failure family: the AI over-extends into the corridor
   * band below the enemy spawn rows and dies in 3-way crossfire.
   * Set to 5+ to effectively disable (max 4 enemies alive on field).
   */
  outnumberedEnemyCount: number
  /** P4.2: radius (cells) for the outnumbered check. */
  outnumberedRadiusCells: number
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

  // ---- P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round) ----
  /**
   * P0.1: max ticks the player may camp (stop-and-aim) at the same cell
   * with no kills before falling through to navigate. Eliminates the T2a
   * deadlock where the player stops and fires at a wall/enemy forever.
   */
  campTimeoutTicks: number
  /**
   * P0.1: ticks to suppress T2a camping after an anti-camp escape. Ensures
   * the player gets enough consecutive navigate ticks to actually move
   * away from the stuck position (otherwise the next tick re-enters T2a
   * and the cycle repeats with no movement).
   */
  antiCampSuppressTicks: number
  /**
   * P0.3: max ticks the player may stay at the same cell in the navigate
   * branch without a kill before forcing a roam to the map center. Breaks
   * pursuit loops where the player chases a faster enemy indefinitely.
   */
  navStuckTicks: number

  // ---- D1: Guard band mode (plan/god-ai-progress Round 4) ----
  /**
   * D1: 0=off, 1=on. When on, the player stays in a horizontal band above
   * the base, engaging enemies that enter the band rather than chasing
   * across the map. Designed for armor-heavy stages with an open bottom
   * (S32 Diamond): armor tanks are slow and must approach the player,
   * giving close-range fire rate (kills 4-HP armor faster); fast tanks
   * are intercepted before reaching the base.
   */
  guardBandMode: number
  /** D1: the guard row (player patrols at this row when idle). */
  guardBandRow: number
  /** D1: half-width of the patrol zone (cols from base col). */
  guardBandHalfWidth: number
  /**
   * D2: score bonus for targeting damaged armor tanks. When > 0, the AI
   * prefers finishing an armor tank that has already taken damage, avoiding
   * the "spread damage across 4 full-HP armor" trap where each gets 1-2
   * hits but none dies. Damage is persistent (HP doesn't regen), so
   * finishing a damaged tank is strictly better than starting a new one.
   */
  damagedArmorBonus: number
  /**
   * Close-combat T2a range: max distance (in cells) at which the player
   * stops to aim-and-fire at an enemy in the same row/col. Default 15
   * (AIM_RANGE_CELLS — unchanged behavior). When set lower (e.g. 5), the
   * player only stops at POINT-BLANK range, maximizing fire rate against
   * high-HP armor tanks (bullet travel time ≈ 0 → kills 4-HP armor in
   * ~0.5s instead of ~4s at long range). Beyond this range, the player
   * keeps moving to close the distance.
   */
  t2aMaxRange: number

  // ---- Smart threat model (Phase A, plan/God-AI-Next-Round §3) ----
  /**
   * 0=OFF (default), non-zero=ON. When ON, selectTargetImpl base-threat
   * branch uses type/speed/facing/HP-aware threat scoring to prioritize
   * fast rushers over slow armor. isBaseUnderThreat() and skipT2aForDefense
   * are NOT changed (the old box + race check remains for threat detection).
   * OFF = byte-identical to pre-smart-model behavior.
   */
  smartThreatModel: number
  /**
   * Minimum threat score [0..1] for an enemy to be considered a base
   * threat. Default 0.55 — armor at 3 cells scores ~0.5 (not a threat),
   * fast at 6 cells scores ~0.7 (threat), armor at 1 cell scores ~0.83
   * (threat).
   */
  smartThreatThreshold: number
  /**
   * Weight for the time-to-base feature (distToBase / speedFactor) in
   * threat scoring (default 0.6 — dominant). This combines speed and
   * distance into "how soon can this enemy reach the base?"
   */
  smartThreatSpeedWeight: number
  /** Weight for enemy facing direction in threat scoring (default 0.2). */
  smartThreatFacingWeight: number
  /** Weight for enemy HP in threat scoring (default 0.2). */
  smartThreatHpWeight: number
  /**
   * Time-to-base normalization range (default 12). Enemies whose
   * timeToBase (= distToBase / speedFactor) ≥ this value get timeScore 0.
   * Effectively: a fast tank at 12+ cells or an armor at 4+ cells won't
   * trigger the threat threshold via the time feature alone.
   */
  smartThreatDistRange: number
  /**
   * Phase A: extra race-check range (cells) for fast/power tanks when
   * smartThreatModel is ON. Extends the base race range
   * (baseRaceRangeCells) by this amount for fast/power tanks in the
   * lower half of the map (row ≥ 10). Default 4 → effective range 15
   * for fast tanks vs 11 for armor/basic. Detects fast rushers earlier,
   * giving the player more time to intercept.
   */
  smartRushDetectBonus: number
}

/** Default God AI parameters — optimized via CMA-ES P4 round 7 (2026-07-29).
 * See .workbuddy/optimization-p4-r7/ for details.
 *
 * P4 R7 CMA-ES used the floor-aware v5.0 fitness over ALL 35 classic
 * stages × 20 seeds (18000 ticks), warm-started from R6, with the
 * per-stage override table (godai-stage-overrides.ts) active in the
 * inner loop — the optimizer pushes the global mean while the override
 * table guards the per-stage floor.
 *
 * P4 R7 truth-scale results (35 stages × 60 seeds, classic, 18000 ticks,
 * override table active):
 *   Mean win rate: 81.9%  (target > 80% — PASS)
 *   Below 60% floor: 1/35 — S32 Diamond 52% (known structural hard case;
 *   verified not param-tunable at 60 seeds: manual probes on R6+R7 bases
 *   and a dedicated single-stage CMA-ES all scored at or below base).
 *
 * Key changes from P3:
 *   - defenseColSpread 9→5, threatRangeCells 20→10 (defense triggers only
 *     on real threats; the race-to-base check covers flankers)
 *   - maxPlayerDistFromBase 19→26 (roam freely; race check guards base)
 *   - baseRaceRangeCells 12→11, margin 2→0 (leaner race-to-base trigger)
 *   - t8MaxInterceptDistCells 2→8, baseWallScanRadius 1→3 (protect base
 *     bricks more actively)
 *   - powerupMaxDivertDistance 3→16 (power-ups are worth a detour)
 *   - endgameEnemyThreshold 4→6, huntAllyCount 6→1 (hunt earlier, alone)
 *   - aimError 0→0.03 (counter-intuitive: tiny aim noise breaks mutual-
 *     block standoffs; per-stage overrides set it back to 0 where armor
 *     density punishes wasted shots)
 */
export const DEFAULT_GOD_AI_PARAMS: GodAIParams = {
  reactionDelay: 0,
  aimError: 0.03030591179971963,
  suboptimalPathProb: 0,

  defenseRowOffset: 1,
  defenseColSpread: 5,
  threatRangeCells: 10,
  maxPlayerDistFromBase: 26,
  // P4: race-to-base emergency defense (see interface docs). Range 11 keeps
  // the check regional; margin 0 = defend only when the enemy would win
  // the race outright.
  baseRaceRangeCells: 11,
  baseRaceMarginCells: 0,
  // P4.2: retreat when 3+ enemies converge within 9 cells — the player
  // trades 1-for-1 at best in open crossfire; falling back to the defense
  // row funnels enemies into single-file corridors instead.
  outnumberedEnemyCount: 3,
  outnumberedRadiusCells: 9,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 3,
  replanInterval: 50,
  powerupMaxDivertDistance: 16,
  endgameEnemyThreshold: 6,
  huntAllyCount: 1,

  // P0: Anti-camp / T2a deadlock fix (plan/God-AI-Next-Round).
  // campTimeoutTicks=90 (1.5s) — if the player hasn't gotten a kill in 1.5s
  // of camping, something is wrong (enemy dodging, wall in the way, etc.).
  // antiCampSuppressTicks=60 (1s) — enough to move ~2 cells at player speed,
  // changing the tactical situation before T2a can re-trigger.
  campTimeoutTicks: 90,
  antiCampSuppressTicks: 60,
  // P0.3: navStuckTicks=180 (3s) — if the player hasn't progressed (stayed
  // at the same cell) for 3 seconds of navigating, force a roam to the map
  // center. This breaks pursuit loops with faster enemies.
  navStuckTicks: 180,

  // D1/D2: Guard band mode + damaged armor priority. Default OFF (0) so
  // all 34 other stages are byte-identical. Only the S32 override enables
  // these — regression-safe by construction.
  guardBandMode: 0,
  guardBandRow: 20,
  guardBandHalfWidth: 7,
  damagedArmorBonus: 0,
  // Close-combat: default 15 (= AIM_RANGE_CELLS, unchanged behavior).
  t2aMaxRange: 15,

  // Smart threat model (Phase A): default OFF (0). All 35 stages are
  // byte-identical when OFF. Only stages with smartThreatModel > 0 in
  // the override table activate the smart scoring.
  smartThreatModel: 0,
  smartThreatThreshold: 0.55,
  smartThreatSpeedWeight: 0.6,
  smartThreatFacingWeight: 0.2,
  smartThreatHpWeight: 0.2,
  smartThreatDistRange: 12,
  smartRushDetectBonus: 4,
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

  /** P0.1: the cell where the player is currently camping (T2a stop-and-aim). */
  _campCell: Cell | null = null
  /** P0.1: consecutive ticks spent at _campCell in T2a. */
  _campTicks = 0
  /** P0.1: world.killCount when camping started (to detect "no kills during camp"). */
  _campKillsAtStart = 0
  /** P0.1: countdown to suppress T2a after an anti-camp escape. */
  _antiCampSuppress = 0

  /** P0.3: the cell where the player is currently stuck in navigate. */
  _navStuckCell: Cell | null = null
  /** P0.3: consecutive ticks spent at _navStuckCell in navigate. */
  _navStuckTicks = 0

  /** Debug: branch counters for profiling. */
  branchCounts = { dodge: 0, t8: 0, aggressive: 0, t2a: 0, powerup: 0, navigate: 0, dead: 0 }

  /** Reusable scan result for scanAheadImpl — avoids allocating a result object on every call. */
  _scanResult: {
    enemy: boolean
    wall: boolean
    steel: boolean
    baseWall: boolean
    enemyDist: number
  } = {
    enemy: false,
    wall: false,
    steel: false,
    baseWall: false,
    enemyDist: Infinity,
  }

  /** Reusable buffer for scanAheadImpl's per-offset aligned-tank pre-filter.
   * Reset (via alignedCount=0) at the start of each offset — no allocation. */
  _scanAligned: Tank[] = []

  /**
   * Cluster C (perf): per-tick snapshot of live, fully-spawned enemy tanks —
   * identical to `w.tanks.filter(t => t.alive && t.spawnTimer <= 0)` in both
   * membership and iteration order. Reused by `isBaseUnderThreat`,
   * `selectTarget`, and `calculateRouteDanger` instead of re-filtering
   * `w.tanks` on every call. Same filter + order ⇒ byte-identical decisions
   * (incl. `enemies[0]` tie-breaks), so calibration stays valid.
   */
  _enemies: Tank[] = []

  /**
   * Cluster C (perf): per-tick snapshot of all live tanks (player included) —
   * identical to `w.allTanks.filter(o => o.alive)` in membership/order.
   * Reused by `canMoveOrBreak`/`canMoveDir` for collision checks (the loop
   * still does `if (o === tank ...) continue`, so every caller — only the
   * player in practice — gets the exact same obstacle set as before).
   */
  _otherTanks: Tank[] = []

  /**
   * Per-tick lazy caches (perf): these methods are pure functions of World
   * state and are called multiple times per tick from different branches of
   * think(). The player doesn't move during think() (movement is applied
   * later in Simulation.updateMovement), so caching within a tick is
   * byte-identical. Invalidated in endFrame() alongside _thought.
   */
  _baseUnderThreatCache: boolean | null = null
  _fastThreatCache: boolean | null = null
  _playerCellCache: Cell = { col: 0, row: 0 }
  _playerCellValid = false
  /** Reusable buffer for tankCellImpl — avoids allocating a {col,row} per call
   * (tankCell is called ~15× per think, many in enemy loops). Callers must
   * consume the result before calling tankCell again. */
  _tankCellBuf: Cell = { col: 0, row: 0 }
  /** Per-tick canMoveDir cache for the player (perf): bitmask. Bit i
   * (0=up,1=down,2=left,3=right) set in _canMoveComputed when computed,
   * in _canMoveResult when passable. Invalidated in endFrame(). */
  _canMoveComputed = 0
  _canMoveResult = 0

  constructor(world: World, params: GodAIParams = DEFAULT_GOD_AI_PARAMS, rng?: RNG) {
    this.world = world
    this.rng = rng ?? world.rng
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
    this._campCell = null
    this._campTicks = 0
    this._campKillsAtStart = 0
    this._antiCampSuppress = 0
    this._navStuckCell = null
    this._navStuckTicks = 0
    this.aggressive = false
    this._enemies = []
    this._otherTanks = []
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
    // Invalidate per-tick lazy caches.
    this._baseUnderThreatCache = null
    this._fastThreatCache = null
    this._playerCellValid = false
    this._canMoveComputed = 0
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

    // ---- Cluster C: per-tick snapshots (built once, reused across modules) ----
    // These mirror the exact filters the god/* sub-modules used to run on every
    // call, in the same iteration order, so no decision (incl. enemies[0]
    // tie-breaks) changes. Pure recomputation elimination, not a behavior change.
    const tanks = w.tanks
    this._enemies.length = 0
    for (let i = 0; i < tanks.length; i++) {
      const t = tanks[i]
      if (t.alive && t.spawnTimer <= 0) this._enemies.push(t)
    }
    const all = w.allTanks
    this._otherTanks.length = 0
    for (let i = 0; i < all.length; i++) {
      const o = all[i]
      if (o.alive) this._otherTanks.push(o)
    }

    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const now = w.frame * (1000 / 60)

    // P0.1: Decrement anti-camp suppression every tick the player is alive.
    if (this._antiCampSuppress > 0) this._antiCampSuppress--

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
        if (b.alive && b.ownerId === p.id) {
          if (++inFlight >= cap) break // early exit — cap reached
        }
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
        // T2a: stop-and-aim — check if enemy is visible (no steel blocking).
        // Without this check, the AI fires through steel walls at enemies
        // it can see via global vision but cannot actually hit.
        const aggScan = this.scanAhead(pcx, pcy, aimDir)
        if (aggScan.enemy) {
          if (p.dir === aimDir) {
            this._moveDir = null
          } else {
            this._moveDir = aimDir
          }
          this._fire = !onCooldown && this.rng.next() >= this.params.aimError
          return
        }
        // Enemy behind obstacle — fall through to navigate toward it.
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
    // P0.2: Only camp when there's a REAL enemy in the line of fire
    // (scan.enemy == true). The old code also camped when there was just a
    // wall (scan.wall && !scan.baseWall), which caused the T2a deadlock:
    // the player would stop and fire at a wall endlessly, never advancing.
    // Now the player only stops to aim when there's an actual enemy to shoot.
    // When the enemy is behind a wall, the player falls through to navigate,
    // which moves toward the enemy and breaks walls via directMove/canMoveOrBreak.
    //
    // P0.1: Anti-camp escape — track how long the player has been at the
    // same cell in T2a. If camping exceeds campTimeoutTicks with no kills,
    // fall through to navigate and hunt the enemy directly. A suppression
    // timer (antiCampSuppressTicks) ensures the player gets enough
    // consecutive navigate ticks to actually move away from the stuck cell.
    //
    // P1: Skip T2a when the base is under threat and the player is too far
    // from the base. Camping far from the base while enemies approach it
    // was the #1 cause of base_destroyed gameovers.
    //
    // D1 (guard band mode): when enabled, skip T2a on fast/power tank
    // threats near the base ONLY (not armor — armor is slow and can wait).
    // This is the targeted version of the guard band: the player camps at
    // armor for efficient point-blank kills, but the instant a fast tank
    // approaches the base, it disengages to intercept. The previous
    // untargeted version (any base threat) was too aggressive and caused
    // the player to disengage from armor too often, increasing deaths.
    const fastThreat = this.params.guardBandMode > 0 && this.hasFastThreatNearBase()

    const skipT2aForDefense =
      this.hasBase &&
      (fastThreat ||
        (this.isBaseUnderThreat() &&
          Math.abs(this.playerCell().col - BASE_POS.col) +
            Math.abs(this.playerCell().row - BASE_POS.row) >
            this.params.maxPlayerDistFromBase))

    if (aimDir && this._antiCampSuppress <= 0 && !skipT2aForDefense) {
      const scan = this.scanAhead(pcx, pcy, aimDir)

      if (scan.enemy && scan.enemyDist <= this.params.t2aMaxRange) {
        // Track camping duration in a ZONE (±1 cell), not exact cell.
        // P2.1fix: the old exact-cell check was defeated by sub-cell
        // oscillation — the player bounces between two adjacent cells
        // (e.g., x=32→40→32) at the TANK/CELL boundary, resetting the
        // camp cell each time the boundary is crossed. This prevented
        // the anti-camp escape from EVER firing, causing the Stage 3/4
        // deadlocks (player stuck at one spot for 17000+ ticks). The
        // zone fix accumulates camp time across nearby cells, so the
        // escape triggers even if the player wiggles between two cells.
        const pc = this.playerCell()
        if (
          this._campCell &&
          Math.abs(this._campCell.col - pc.col) <= 1 &&
          Math.abs(this._campCell.row - pc.row) <= 1
        ) {
          this._campTicks++
          // If a kill happened since camping started, reset the camp timer.
          // The player is being productive — let it continue camping.
          if (w.killCount !== this._campKillsAtStart) {
            this._campTicks = 1
            this._campKillsAtStart = w.killCount
          }
        } else {
          // Moved outside the camp zone — start fresh camp tracking.
          this._campCell = { col: pc.col, row: pc.row }
          this._campTicks = 1
          this._campKillsAtStart = w.killCount
        }

        // Anti-camp: if too long at this cell with no kills, break out.
        const campedTooLong =
          this._campTicks > this.params.campTimeoutTicks && w.killCount === this._campKillsAtStart

        if (!campedTooLong) {
          if (p.dir === aimDir) {
            this._moveDir = null // Already facing — stop and shoot
          } else {
            this._moveDir = aimDir // Turn to face enemy
          }
          this._fire = !onCooldown && this.rng.next() >= this.params.aimError
          this.branchCounts.t2a++
          return
        }

        // Camped too long with no kills — suppress T2a and fall through
        // to navigate, which will move the player toward the enemy.
        this._campCell = null
        this._campTicks = 0
        this._antiCampSuppress = this.params.antiCampSuppressTicks
      }
      // No real enemy in line of fire (wall-only or clear) — fall through.
    } else if (this._campCell) {
      // Not in T2a (suppressed or no aimDir) — reset camp tracking.
      this._campCell = null
      this._campTicks = 0
    }

    // ---- S5: Power-up economy (normal mode) ----
    // Check for power-ups when no enemy is in line of fire. Previously this
    // only ran in aggressive mode (freeze/shield), wasting bomb/star pickups.
    // Now the AI opportunistically grabs power-ups when it's safe to divert.
    // P1: Skip power-ups when the base is under threat — defense first.
    // P3.2: Also skip when there are enemies within 5 cells of the player —
    // chasing power-ups while enemies are nearby was a major cause of
    // defense-collapse gameovers on S6/S26/S32 (player diverted to a power-up
    // at the top of the map while enemies destroyed the base).
    if ((!aimDir || onCooldown) && !(this.hasBase && this.isBaseUnderThreat())) {
      // P3.2: Don't divert to power-ups when enemies are close.
      const pc2 = this.playerCell()
      let nearbyEnemy = false
      // Cluster C: reuse the per-tick enemy snapshot.
      const nearbyScan = this._enemies.length > 0 ? this._enemies : w.tanks
      for (const t of nearbyScan) {
        if (!t.alive || t.spawnTimer > 0) continue
        const tc = this.tankCell(t)
        if (Math.abs(tc.col - pc2.col) + Math.abs(tc.row - pc2.row) <= 5) {
          nearbyEnemy = true
          break
        }
      }
      if (!nearbyEnemy) {
        const puTarget = this.findPowerUpTarget(pcx, pcy)
        if (puTarget) {
          this._moveDir = this.navigateTowards(puTarget)
          this._fire = !onCooldown && this.shouldFireInDir(pcx, pcy, this._moveDir ?? p.dir)
          this.branchCounts.powerup++
          return
        }
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
    // P0.3: Navigate stuck escape — if the player has been at the same cell
    // in the navigate branch for too long (pursuit loop with a faster enemy),
    // override the target to the map center. This breaks the loop by moving
    // the player to a crossroads position where enemies are more likely to
    // cross its row/col, creating new T2a opportunities.
    //
    // Fire control in classic bulletCap mode (1 bullet in flight):
    // - If blocked by a wall in the path direction → fire to break through.
    // - If moving freely → fire only at enemies in the line of fire.
    const pc = this.playerCell()
    if (
      this._navStuckCell &&
      this._navStuckCell.col === pc.col &&
      this._navStuckCell.row === pc.row
    ) {
      this._navStuckTicks++
    } else {
      this._navStuckCell = { col: pc.col, row: pc.row }
      this._navStuckTicks = 1
    }

    // Reset stuck timer when a kill happens (player is making progress).
    if (this._navStuckTicks > 1 && w.killCount !== this._campKillsAtStart) {
      this._navStuckTicks = 1
      this._campKillsAtStart = w.killCount
    }

    const navStuck = this._navStuckTicks > this.params.navStuckTicks

    let navTarget: Cell | null
    // P3.1: When nav-stuck triggers, only go to center if the player is
    // NOT already at/near center. Going to center when already there causes
    // a deadlock (target == current cell → no movement → stuck forever,
    // which was the S9 root cause: player stuck at (12,12) for 2600+ ticks).
    // When the player IS at/near center, chase the nearest enemy directly
    // (directMove breaks through walls) — this gets the player moving.
    const distToCenter = Math.abs(pc.col - 12) + Math.abs(pc.row - 12)
    const stuckAtCenter = distToCenter <= 2
    if (navStuck && !stuckAtCenter) {
      navTarget = { col: 12, row: 12 }
    } else {
      navTarget = this.selectTarget(pc)
    }

    const navDist = navTarget
      ? Math.abs(navTarget.col - pc.col) + Math.abs(navTarget.row - pc.row)
      : Infinity

    if (navStuck && !stuckAtCenter) {
      // P2.2: Stuck too long — break the loop. Try A* to center first, then
      // fall back to any passable direction (not directMove, which would
      // re-select the enemy target and re-enter the stuck loop). Trying any
      // open direction ensures the player physically moves away from the
      // stuck cell, which is the whole point of the escape.
      this._moveDir = this.navigateTowards(navTarget!)
      if (!this._moveDir) {
        // A* failed (walled off) — try directions toward center first,
        // then any passable direction.
        const dx = navTarget!.col - pc.col
        const dy = navTarget!.row - pc.row
        const pref: Direction[] = []
        if (Math.abs(dy) > Math.abs(dx)) {
          pref.push(dy > 0 ? 'down' : 'up')
          pref.push(dx > 0 ? 'right' : 'left')
        } else {
          pref.push(dx > 0 ? 'right' : 'left')
          pref.push(dy > 0 ? 'down' : 'up')
        }
        let moved = false
        for (const d of pref) {
          if (this.canMoveDir(p, d)) {
            this._moveDir = d
            moved = true
            break
          }
        }
        if (!moved) {
          // All preferred directions blocked — try any open direction.
          for (const d of ALL_DIRS) {
            if (this.canMoveDir(p, d)) {
              this._moveDir = d
              break
            }
          }
        }
      }
    } else if (navStuck && stuckAtCenter) {
      // P3.1: Stuck at/near center — chase nearest enemy directly instead
      // of re-targeting center. directMove breaks through brick walls,
      // which (combined with the A* dig-through-brick fix) gets the player
      // moving toward enemies instead of deadlocking at center.
      this._moveDir = this.directMove(pc)
      if (!this._moveDir) {
        // directMove also failed — try any passable direction to get moving.
        for (const d of ALL_DIRS) {
          if (this.canMoveDir(p, d)) {
            this._moveDir = d
            break
          }
        }
      }
    } else if (navDist <= 5) {
      // Close range — directMove (responsive, tracks moving enemies).
      this._moveDir = this.directMove(pc)
    } else {
      // Long range — A* pathfinding (finds corridors in mazes).
      this._moveDir = this.followPath()
      if (!this._moveDir) {
        // A* failed or path exhausted — fall back to direct movement.
        this._moveDir = this.directMove(pc)
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

  /**
   * P1/P2.3: Check if any enemy is threatening the base. An enemy is a
   * threat if:
   *   - Within 3 cols of base AND row >= 18 (close lateral threat), OR
   * Used to skip power-ups/T2a and prioritize defense.
   */
  isBaseUnderThreat(): boolean {
    if (!this.hasBase) return false
    // Per-tick cache: called up to 3× per tick (think skipT2a, think powerup
    // gate, selectTarget). Pure function of World state — byte-identical.
    if (this._baseUnderThreatCache !== null) return this._baseUnderThreatCache
    const bc = BASE_POS.col
    const br = BASE_POS.row
    // P4: race-to-base check — player's distance to the base. If the player
    // is dead/respawning, treat any near-base enemy as a threat.
    const p = this.world.player
    const pc = p ? this.playerCell() : null
    const playerDistToBase = pc ? Math.abs(pc.col - bc) + Math.abs(pc.row - br) : Infinity
    // Cluster C: reuse the per-tick snapshot (falls back to a fresh scan only
    // if think() hasn't populated it yet — should never happen in normal flow).
    const list = this._enemies.length > 0 ? this._enemies : this.world.tanks
    let result = false
    for (const t of list) {
      if (!t.alive || t.spawnTimer > 0) continue
      const tc = this.tankCell(t)
      // Static box: close lateral threat (original P1/P2.3 rule).
      if (Math.abs(tc.col - bc) <= 3 && tc.row >= 18) {
        result = true
        break
      }
      // P4: race check — enemy is in the base region AND would beat the
      // player back to the base (with safety margin). Catches flanking
      // runners along the map edges that the static box misses (S6 root
      // cause: base died with the player 20+ cells away behind steel).
      const enemyDistToBase = Math.abs(tc.col - bc) + Math.abs(tc.row - br)
      if (
        enemyDistToBase <= this.params.baseRaceRangeCells &&
        playerDistToBase + this.params.baseRaceMarginCells >= enemyDistToBase
      ) {
        result = true
        break
      }
    }
    this._baseUnderThreatCache = result
    return result
  }

  /**
   * D1: Check if any fast/power tank is near the base (within the existing
   * threat detection zone). Used by the T2a skip to decide whether the
   * player should disengage from armor camping. Only checks fast/power
   * kinds — armor tanks are slow and don't require immediate disengagement.
   */
  hasFastThreatNearBase(): boolean {
    if (!this.hasBase) return false
    // Per-tick cache: called once per tick from think() (only when
    // guardBandMode > 0). Pure function of World state — byte-identical.
    if (this._fastThreatCache !== null) return this._fastThreatCache
    const bc = BASE_POS.col
    const br = BASE_POS.row
    const list = this._enemies.length > 0 ? this._enemies : this.world.tanks
    let result = false
    for (const t of list) {
      if (!t.alive || t.spawnTimer > 0) continue
      if (t.kind !== 'fast' && t.kind !== 'power') continue
      const tc = this.tankCell(t)
      // Static box: within 3 cols of base AND row >= 18
      if (Math.abs(tc.col - bc) <= 3 && tc.row >= 18) {
        result = true
        break
      }
      // Race check: within baseRaceRangeCells of base
      const enemyDist = Math.abs(tc.col - bc) + Math.abs(tc.row - br)
      if (enemyDist <= this.params.baseRaceRangeCells) {
        result = true
        break
      }
    }
    this._fastThreatCache = result
    return result
  }

  // --- SmartThreatModel (Phase A, plan/God-AI-Next-Round §3) ---
  /** Threat score for an enemy [0..1]. Higher = more dangerous to base. */
  threatScore(t: Tank): number {
    return threatScoreImpl(this, t)
  }
  /** Smart isBaseUnderThreat: any enemy with threatScore ≥ threshold. */
  smartIsBaseUnderThreat(): boolean {
    return smartIsBaseUnderThreatImpl(this)
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
