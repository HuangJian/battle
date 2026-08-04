import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet, TankKind } from '../types'
import type { Direction } from '../constants'
import type { Cell } from '../utils/pathfind'
import type { RNG } from '../utils/RNG'
import { BASE_POS } from '../constants'
import {
  findEnemyDirectionImpl,
  findEnemyFacingPlayerImpl,
  scanAheadImpl,
  shouldFireInDirImpl,
  isBaseProtectionBrickImpl,
} from './god/FireControl'
import { thinkImpl, CANDIDATES } from './god/think'
import { orderedCandidates, type DecisionContext, type Candidate } from './god/DecisionCore'
import {
  DEFAULT_GOD_AI_PARAMS,
  computeStageAdaptedParams,
  CLASSIC_MODEL_PARAMS,
} from './god/params'
import type { GodAIParams } from './god/params'
export {
  DEFAULT_GOD_AI_PARAMS,
  SKILLED_HUMAN_PARAMS,
  computeStageAdaptedParams,
} from './god/params'
export type { GodAIParams } from './god/params'
import { initEnemyModel, type EnemyModelState } from './god/EnemyModel'
import {
  findMostDangerousBulletImpl,
  findBulletThreatToBaseImpl,
  baseBulletInterceptCellImpl,
  dodgeDirectionImpl,
  isSafeDirImpl,
  hasEnemyBulletInLineImpl,
  findPathThreatImpl,
  findSafeMoveDirImpl,
  closeCombatExposureImpl,
  isTerrainPinnedImpl,
  hasCrossFireBulletImpl,
} from './god/ThreatAssessor'
import {
  findPowerUpTargetImpl,
  findUrgentPowerUpTargetImpl,
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
import {
  computeChokepointPlanImpl,
  isThreatStateImpl,
  threatChaseTargetImpl,
} from './god/Chokepoint'
import type { ChokepointPlan } from './god/Chokepoint'

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
// GodAIInput (orchestrator)
// ============================================================

export class GodAIInput implements InputLike {
  // Shared state — public so the extracted ./god/* sub-modules can read/write
  // it via the `self: GodAIInput` they receive. This is the §0.5 split;
  // behavior is unchanged from before the split.
  world: World
  rng: RNG
  params: GodAIParams
  /**
   * §58: the original params passed to the constructor, before stage-level
   * adaptation is applied in reset(). reset() computes `this.params` from
   * this base each time, so the adaptation never compounds across resets.
   */
  _baseParams: GodAIParams

  /**
   * Lie-Back-Win-Mode §3.8 P1: returns the tank this AI controls.
   * Default: `w => w.player` (single-player / parity-safe).
   * Co-op mode: pass `w => w.player2` to make God AI control P2.
   */
  controlledTank: (w: World) => Tank | null = (w) => w.player

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

  /** §84: the cell where the player is camping in the aggressive branch. */
  _aggCampCell: Cell | null = null
  /** §84: consecutive ticks spent at _aggCampCell in aggressive stop-and-aim. */
  _aggCampTicks = 0
  /** §84: world.killCount when aggressive camping started. */
  _aggCampKillsAtStart = 0
  /** §84: countdown to suppress aggressive stop-and-aim after a stall escape. */
  _aggCampSuppress = 0

  /**
   * §86: Dodge direction persistence — the last dodge direction used for a
   * given threat bullet id. When the same threat persists across ticks and
   * the last dodge direction is still safe, `dodgeDirectionImpl` returns it
   * immediately instead of recomputing. This prevents the 1px oscillation
   * where the player alternates between two positions every tick (e.g.,
   * y=55↔56), making the player effectively stationary while the bullet
   * approaches and eventually hits.
   */
  _lastDodgeDir: Direction | null = null
  _lastDodgeThreatId: number = -1
  /**
   * §86: Counter for consecutive dodge direction flips (same threat).
   * When this reaches 3, the player is oscillating (up→down→up→down) and
   * `dodgeDirectionImpl` switches to counter-fire: face the bullet (turn
   * toward it) so the think() fire logic cancels it with the player's own
   * bullet. This is more targeted than persistence (which overrides ALL
   * direction switches) — it only activates during actual oscillation.
   */
  _dodgeFlipCount: number = 0

  /** P0.3: the cell where the player is currently stuck in navigate. */
  _navStuckCell: Cell | null = null
  /** P0.3: consecutive ticks spent at _navStuckCell in navigate. */
  _navStuckTicks = 0

  /**
   * M3 diag: total counter-fire ticks (think.ts dodge branch, DECISIONS §101).
   * Pure observation — no RNG, no gameplay effect; reset per stage.
   */
  _counterFireTicks = 0

  /** Debug: branch counters for profiling. */
  branchCounts = {
    dodge: 0,
    t8: 0,
    aggressive: 0,
    t2a: 0,
    powerup: 0,
    navigate: 0,
    dead: 0,
    chokepoint: 0,
    // M3: survive 候选（主动换位）提交计数（纯观察）。
    survive: 0,
  }

  /**
   * Death attribution (M0, plan/God-AI-Redesign-v2 §6): the think() branch
   * taken this tick, set at every return point in thinkImpl. Pure observation
   * — read by tools/diag/death-attribution.ts via runSimulation telemetry.
   * No gameplay effect, no RNG, no serialization.
   */
  _lastBranch: string = 'navigate'

  /** Reusable scan result for scanAheadImpl — avoids allocating a result object on every call. */
  _scanResult: {
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
  } = {
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

  /** Reusable buffer for scanAheadImpl's per-offset aligned-tank pre-filter.
   * Reset (via alignedCount=0) at the start of each offset — no allocation. */
  _scanAligned: Tank[] = []

  /**
   * M1: reusable per-tick decision context (plan/God-AI-Redesign-v2 §3, §14.2
   * hot-path rule — no per-tick object allocation in thinkImpl). Built lazily
   * on the first think of the first tick (world/player already exist then);
   * the shell overwrites the fields each tick. Candidates read it
   * synchronously and never retain it, so reuse is safe.
   */
  _decisionCtx: DecisionContext | null = null

  /**
   * M2: the candidate chain in effective-weight order for the CURRENT
   * params (built once per reset — never sorted per tick, AGENTS §14.3).
   * Default params ⇒ exactly the M1 chain order (parity by construction);
   * `actionWeights` overrides reorder it data-driven.
   */
  _orderedCandidates: Candidate[] = []

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

  /**
   * (perf §68 Round 9) Cross-tick navigateTowards cache.
   *
   * `navigateTowardsImpl` is called every tick from think()'s navigate branch,
   * but its inputs (playerCell + targetCell) only change when the player
   * crosses a cell boundary (~every 23 ticks at player speed) or the target
   * cell changes (also second-scale, since enemies move at most ~1 cell/sec).
   * The terrain that A* reads also only changes when a brick is destroyed,
   * which always coincides with the player entering a new cell or a target
   * shift. Re-running A* while `(playerCell, target)` is unchanged is pure
   * waste — the result is byte-identical.
   *
   * The cache holds the last (playerCell, target, result) triple and is
   * reused while both inputs are stable. A safety timer forces a replan
   * every `_navReplanMax` ticks (default 60 = 1 second) to bound staleness
   * in edge cases (e.g. an enemy destroys a brick between cells).
   *
   * NOT invalidated in endFrame() — this is intentionally a cross-tick cache
   * (the inputs are the same across ticks; that's the whole point). It is
   * reset on resetForRun() / loadStage().
   */
  _navCacheValid = false
  _navPlayerCol = 0
  _navPlayerRow = 0
  _navTargetCol = 0
  _navTargetRow = 0
  _navCache: Direction | null = null
  _navReplanTimer = 0
  _navReplanMax = 60

  /**
   * §88: throttled chokepoint plan (threat points + selected 咽喉要地 cell).
   * Recomputed every chokepointReplanTicks (default 30) or when missing — the
   * same cross-tick cache discipline as _navCacheValid (threat points only
   * change when bricks are destroyed). Pure function of World state + frame:
   * deterministic, replay-safe. Reset in reset() per stage.
   */
  _chokepointPlan: ChokepointPlan | null = null

  /**
   * M3 (plan/God-AI-Redesign-v2 §4.2b): 敌情感知模型状态。Per-tick EMA of
   * observable enemy behavior (fire accuracy / base approach / alignment /
   * turn discipline) → `estimatedLevel` [0,1] + survival pressure inputs.
   * Pure World observation, no RNG, no difficultyKey reads. Same snapshot
   * semantics as `_campTicks` — not serialized, re-converges after a rewind.
   * Reset per stage. Active only when enemyModelMode > 0 && window > 0.
   */
  _enemyModel: EnemyModelState = initEnemyModel(false)
  /** M3: previous-tick player HP — hit detection for the accuracy feature. */
  _enemyModelLastHp = 0

  constructor(
    world: World,
    params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
    rng?: RNG,
    controlledTank?: (w: World) => Tank | null,
  ) {
    this.world = world
    this.rng = rng ?? world.rng
    // Clone the params: per-instance mutations (e.g. tests doing
    // `input.params.x = y` for A/B) must never leak into the shared
    // DEFAULT_GOD_AI_PARAMS / SKILLED_HUMAN_PARAMS singletons. Cross-file
    // module state IS shared inside `bun test` (proven 2026-08-03: a test
    // setting dodgeClearanceScore=1 on the singleton flipped the hard/chaos
    // gate's S25 result from 1/20 to 0/20 — a silent global corruption of
    // every later simulation in the process). DECISIONS §98.
    this._baseParams = { ...params }
    this.params = this._baseParams
    this._orderedCandidates = orderedCandidates(CANDIDATES, this._baseParams.actionWeights)
    // M3: initialize the EnemyModel (active flag resolved in reset() from the
    // stage-adapted params — the base params may not have the mode set).
    this._enemyModel = initEnemyModel(false)
    this._enemyModelLastHp = 0
    if (controlledTank) this.controlledTank = controlledTank
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
    this._aggCampCell = null
    this._aggCampTicks = 0
    this._aggCampKillsAtStart = 0
    this._aggCampSuppress = 0
    // §86: reset dodge direction persistence.
    this._lastDodgeDir = null
    this._lastDodgeThreatId = -1
    this._dodgeFlipCount = 0
    this._navStuckCell = null
    this._navStuckTicks = 0
    this.aggressive = false
    this._enemies = []
    this._otherTanks = []
    // M3 diag: reset the counter-fire trigger counter per stage.
    this._counterFireTicks = 0
    // §88: invalidate the throttled chokepoint plan on stage reset.
    this._chokepointPlan = null
    // M3: reset the EnemyModel per stage (same cross-tick-cache discipline as
    // _navCache / _campTicks — the model must not carry knowledge across
    // stages, and the per-tank trackers reference dead tank ids otherwise).
    const modelActive = this.params.enemyModelMode > 0 && this.params.enemyModelWindowTicks > 0
    this._enemyModel = initEnemyModel(modelActive)
    this._enemyModelLastHp = this.world.player ? this.world.player.hp : 0
    // (perf §68 Round 9) Invalidate cross-tick navigateTowards cache on
    // stage reset — the next tick must recompute A* from scratch.
    this._navCacheValid = false
    this._navReplanTimer = 0
    // Gap B (plan §3): cache whether this stage has a base. All BASE_POS-
    // dependent logic checks this flag instead of assuming a base exists.
    this.hasBase = this.world.tileMap.hasBase()
    // §58: compute stage-level adaptive params from the base params — the
    // unified data-driven adaptation based on stage characteristics (armor
    // ratio, brick/steel/forest/water density). No per-stage special-casing.
    // §115 (M4 round-2): the M4 defaults are POOL-model tuning (hard/chaos,
    // HP-buffer combat). classic ('instant', flat damage) measured -2.4pp if
    // they leak in (91.0% → 88.6%), so restore the pre-M4 values here. The
    // restore is applied BEFORE computeStageAdaptedParams so stage adaptations
    // still override on top, exactly as they did pre-M4.
    let baseForAdapt = this._baseParams
    if (this.world.rules.combatModel === 'instant') {
      // Copy before restoring — never mutate the caller's params object.
      baseForAdapt = { ...this._baseParams }
      // Restore only params still at their M4 DEFAULT value — a caller that
      // EXPLICITLY overrode an M4 param (A/B script, custom difficulty) keeps
      // its override; the default flow (gate, browser) restores classic values.
      // SENTINEL LIMITATION: an override that sets a param TO its M4 value on
      // classic (e.g. replanInterval=1) is indistinguishable from "at default"
      // and gets restored — classic A/Bs should override to NON-M4 values.
      // (DECISIONS §115)
      for (const key in CLASSIC_MODEL_PARAMS) {
        const k = key as keyof GodAIParams
        if (baseForAdapt[k] === DEFAULT_GOD_AI_PARAMS[k]) {
          ;(baseForAdapt as unknown as Record<string, number>)[key] = (
            CLASSIC_MODEL_PARAMS as unknown as Record<string, number>
          )[key]
        }
      }
    }
    this.params = computeStageAdaptedParams(baseForAdapt, this.world)
    // M2: rebuild the candidate chain in effective-weight order from the
    // (possibly stage-adapted) params. Default = M1 chain order.
    this._orderedCandidates = orderedCandidates(CANDIDATES, this.params.actionWeights)
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
    this._playerCellValid = false
    this._canMoveComputed = 0
  }

  // ================================================================
  // Core decision loop (think)
  // ================================================================

  private think(): void {
    thinkImpl(this)
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
  findEnemyFacingPlayer(
    pcx: number,
    pcy: number,
    aimDir: Direction,
  ): { enemy: Tank; dist: number } | null {
    return findEnemyFacingPlayerImpl(this, pcx, pcy, aimDir)
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
    const p = this.controlledTank(this.world)
    const pc = p ? this.playerCell() : null
    const playerDistToBase = pc ? Math.abs(pc.col - bc) + Math.abs(pc.row - br) : Infinity
    // Cluster C: reuse the per-tick snapshot (falls back to a fresh scan only
    // if think() hasn't populated it yet — should never happen in normal flow).
    const list = this._enemies.length > 0 ? this._enemies : this.world.tanks
    let result = false
    for (let li = 0; li < list.length; li++) {
      const t = list[li]
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
    // §88 rule 1: an enemy at/near a threat point (威胁点外 margin 格) also
    // puts the base into the threatened state — the enemy can shoot the base
    // from there, so defense must outrank MID-tier pickups and chokepoint
    // holding. OR'd with the existing box/race detection (never reduces it).
    if (!result && this.params.chokepointMode > 0) {
      this.chokepointPlan() // ensure the throttled threat-point cache
      const plan = this._chokepointPlan
      if (plan && plan.threatPoints.length > 0 && isThreatStateImpl(this, plan.threatPoints)) {
        result = true
      }
    }
    this._baseUnderThreatCache = result
    return result
  }

  // M0.5 (2026-08-03): D1 hasFastThreatNearBase + SmartThreatModel wrappers
  // (threatScore / smartIsBaseUnderThreat) retired — archived in experimental.ts.

  scanAhead(
    pcx: number,
    pcy: number,
    dir: Direction,
  ): {
    enemy: boolean
    wall: boolean
    steel: boolean
    baseWall: boolean
    enemyDist: number
    enemyKind: TankKind
    enemyHp: number
    enemyMaxHp: number
  } {
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
  hasEnemyBulletInLine(pcx: number, pcy: number, aimDir: Direction): boolean {
    return hasEnemyBulletInLineImpl(this, pcx, pcy, aimDir)
  }
  /** §M3-revisit round 3 (DECISIONS §101): terrain-only pinning — true only
   * when BOTH perpendicular dodge directions are impassable (corridor/corner).
   * Counter-fire only in that case; open-ground timing pressure keeps the
   * normal (partial) dodge moving. */
  isTerrainPinned(bullet: Bullet): boolean {
    const p = this.controlledTank(this.world)
    if (!p) return false
    return isTerrainPinnedImpl(this, p, bullet)
  }
  /** M4: check if other bullets are approaching within range (crossfire gate). */
  hasCrossFireBullet(
    pcx: number,
    pcy: number,
    excludeId: number,
    rangeCells: number,
    threshold = 1,
  ): boolean {
    return hasCrossFireBulletImpl(this, pcx, pcy, excludeId, rangeCells, threshold)
  }
  findPathThreat(pcx: number, pcy: number, moveDir: Direction, playerSpeed: number): Bullet | null {
    return findPathThreatImpl(this, pcx, pcy, moveDir, playerSpeed)
  }
  findSafeMoveDir(
    pcx: number,
    pcy: number,
    threatenedDir: Direction,
    playerSpeed: number,
  ): Direction | null {
    return findSafeMoveDirImpl(this, pcx, pcy, threatenedDir, playerSpeed)
  }
  /** §85: Check if the player's moveDir exposes it to a close enemy's fire. */
  closeCombatExposure(
    pcx: number,
    pcy: number,
    moveDir: Direction | null,
    range: number,
  ): Direction | null {
    if (this.params.closeCombatDangerCheck <= 0) return null
    return closeCombatExposureImpl(this, pcx, pcy, moveDir, range)
  }

  // --- StrategyPlanner ---
  findPowerUpTarget(pcx: number, pcy: number): Cell | null {
    return findPowerUpTargetImpl(this, pcx, pcy)
  }
  /** §87: urgent power-up target (close + safe path), see StrategyPlanner.
   * §88: `tier` restricts to 'high' (bomb/freeze/fence) or 'midlow' — the
   * reordered priority chain; default 'all' = pre-§88 behavior. */
  findUrgentPowerUpTarget(
    pcx: number,
    pcy: number,
    tier: 'all' | 'high' | 'midlow' = 'all',
  ): Cell | null {
    return findUrgentPowerUpTargetImpl(this, pcx, pcy, tier)
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

  // --- Chokepoint (§88) ---
  /**
   * §88: throttled chokepoint plan accessor. Recomputed every
   * chokepointReplanTicks (or when missing) — pure function of World state +
   * frame, so the throttle is deterministic and replay-safe. OFF when
   * chokepointMode <= 0 (returns null, byte-identical to pre-§88).
   */
  chokepointPlan(): ChokepointPlan | null {
    if (this.params.chokepointMode <= 0) return null
    const p = this._chokepointPlan
    if (p && this.world.frame - p.tick < this.params.chokepointReplanTicks) return p
    this._chokepointPlan = computeChokepointPlanImpl(this)
    return this._chokepointPlan
  }
  /** §88: the selected 咽喉要地 cell, or null (OFF / no coverage). */
  chokepointCell(): Cell | null {
    const plan = this.chokepointPlan()
    return plan ? plan.chokepoint : null
  }
  /** §88 rule 1: base-threat state — enemy within threatPointMargin of a threat point. */
  isThreatState(): boolean {
    if (this.params.chokepointMode <= 0) return false
    const plan = this.chokepointPlan()
    return !!plan && plan.threatPoints.length > 0 && isThreatStateImpl(this, plan.threatPoints)
  }
  /** §88 rule 2: cell of the enemy nearest a threat point (chase arm), or null. */
  threatChaseTarget(): Cell | null {
    if (this.params.chokepointMode <= 0) return null
    const plan = this.chokepointPlan()
    return plan && plan.threatPoints.length > 0
      ? threatChaseTargetImpl(this, plan.threatPoints)
      : null
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
  // M0.5 (2026-08-03): trapAvoidance + computeThreatCosts wrappers retired —
  // archived in experimental.ts for the v2 survive candidate.
}
