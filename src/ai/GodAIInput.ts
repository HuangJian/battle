import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank, Bullet } from '../types'
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
  makeScanResult,
  bulletPathSteelBlockedImpl,
} from './god/FireControl'
import type { ScanResult } from './god/FireControl'
import { thinkImpl, CANDIDATES } from './god/think'
import { orderedCandidates, type DecisionContext, type Candidate } from './god/DecisionCore'
import {
  DEFAULT_GOD_AI_PARAMS,
  computeStageAdaptedParams,
  CLASSIC_MODEL_PARAMS,
  detectCentralBreachRisk,
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
  findUrgentPowerUpTargetWithCommitImpl,
  findDireItemTargetImpl,
  findDualFencePickupImpl,
  findFreezePickupTargetImpl,
  findClosePickupTargetImpl,
  calculateRouteDangerImpl,
  getDefaultDefensePositionImpl,
  computeBaseGuardAnchorImpl,
  findDualCentralHoldImpl,
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
import { enemyCanShootBase } from './god/SmartThreatModel'

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

/**
 * (perf §129) One direct-mapped slot of the pickup-reachability memo used by
 * powerUpCellReachable (StrategyPlanner). Key: (playerCell, target) +
 * tileMap.revision at fill time. valid=false slots are inert; the revision
 * key makes any stale slot miss the SAME tick terrain changes (strict pure
 * memo — see the StrategyPlanner doc comment for the correctness argument).
 */
export interface PickupReachSlot {
  valid: boolean
  pcCol: number
  pcRow: number
  col: number
  row: number
  rev: number
  reachable: boolean
}

function emptyPickupReachSlot(): PickupReachSlot {
  return { valid: false, pcCol: 0, pcRow: 0, col: 0, row: 0, rev: -1, reachable: false }
}

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
  /** §167 / B4: super-item press flags for this tick (set by think() via
   * superItemPressesImpl, consumed by wasItemPressed(), cleared endFrame()). */
  _pressGuard = false
  _pressFrenzy = false
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

  /**
   * Dual central breach strategy (plan/dual-central-breach-strategy.md):
   * Cached in reset() from detectCentralBreachRisk(world). When true AND
   * world.spectateDual, the dual role assignment + defense enhancement knobs
   * are active. Pure terrain function — same as hasBase, not serialized.
   */
  _centralBreachRisk = false

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
   * §152-W2: aggressive-branch MOVEMENT stuck guard state (distinct from the
   * §84 stop-and-aim camp guard — this one covers the navigate fallback).
   * Zone anchor (±1 cell, like the T2a camp zone) + ticks + kill baseline:
   * the freeze-window oscillation between two adjacent cells (A* path
   * first-step blocked by a frozen enemy's body / water, followPath fallback
   * ping-pongs back) accumulates here and trips aggNavStuckTicks, committing
   * a navigate-to-center escape (hard S12 seed 934391936 W2). AI-internal,
   * not serialized — same semantics as _campCell/_campTicks.
   */
  _aggNavStuckCell: Cell | null = null
  _aggNavStuckTicks = 0
  _aggNavKillsAtStart = 0
  /** countdown forcing the navigate-to-center escape after a movement stall. */
  _aggNavSuppress = 0

  /**
   * §152-W3: urgent-pickup commit state. Once PICKUP_HIGH/MID commits to an
   * item, the pursuit persists for pickupCommitTicks while the item stays
   * alive — the transient dist>range exclusion (the player moving toward the
   * item) must not cancel it (hard S12 seed 934391936 W3 oscillation).
   * Stores the ITEM cell (existence re-verify) + the COLLECT cell (nav
   * target — differs when the item sits on blocking terrain). AI-internal,
   * not serialized.
   */
  _pickupCommitActive = false
  _pickupCommitTicks = 0
  _pickupCommitCol = 0
  _pickupCommitRow = 0
  _pickupCommitItemCol = 0
  _pickupCommitItemRow = 0

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
   * §168: nav-stuck escape suppression window — while > 0, HUNT keeps the
   * center escape instead of normal targeting (the trigger alone is not
   * enough: once the counter resets on leaving the zone, the oscillating
   * target selection pulls the player straight back into it).
   */
  _navStuckSuppress = 0

  /**
   * §169: base-threat sticky hold — while > 0, isBaseUnderThreat() reports
   * true even if the underlying detection cleared (flicker gap). Refreshed
   * to params.threatStickyTicks every tick the underlying signal is true;
   * decremented in endFrame(). Default param 0 ⇒ never set ⇒ byte-identical.
   */
  _threatStickyHold = 0

  /**
   * §170: hunt commit state — the enemy id currently committed to in the
   * normal hunt branch and the frame the commit expires. While the window
   * is open and the committed tank is alive, selectTarget keeps chasing it
   * instead of re-picking the momentarily nearest enemy. Written only in
   * the !baseUnderThreat nearest-selection branch; reset() clears. Default
   * param 0 ⇒ never written ⇒ byte-identical.
   */
  _huntCommitId = -1
  _huntCommitUntil = 0

  /**
   * §171: path-cost memo for pathTargetMode (normal-hunt target scoring).
   * Fixed 8-slot table (≤4 enemies alive + churn), keyed by (playerCell,
   * enemyId, enemyCell, tileMap.revision); `_pathCostEId` = −1 marks an
   * empty slot and `_pathCostNext` rotates eviction. `findPath` draws no
   * RNG, so the memo is byte-identical; default param 0 ⇒ never touched.
   * reset() invalidates all slots per stage.
   */
  _pathCostPKey = new Int32Array(8)
  _pathCostEKey = new Int32Array(8)
  _pathCostRev = new Int32Array(8)
  _pathCostEId = new Int32Array(8).fill(-1)
  _pathCostVal = new Float64Array(8)
  _pathCostNext = 0

  /**
   * M3 diag: total counter-fire ticks (think.ts dodge branch, DECISIONS §101).
   * Pure observation — no RNG, no gameplay effect; reset per stage.
   */
  _counterFireTicks = 0

  /**
   * §116: mid-suicide standing state. When the suicide candidate commits, the
   * player stands still to take the lethal bullet. It keeps standing ONLY while
   * a lethal bullet is still approaching within the window; once the bullet is
   * cancelled (or the player dies+respawns behind its shield), it clears and the
   * player resumes normal play. Prevents the pathological per-tick re-commit that
   * froze the player standing forever (found via hard S30: 14.2 commits/run).
   * AI-internal (not serialized) — same semantics as _campTicks / _campCell.
   */
  _suicideStanding = false

  /**
   * §121: total ticks the self-fire base guard suppressed a t2a/aggressive
   * stop-and-aim fire or a shouldFireInDir fire whose bullet's center line
   * would have reached the base. Pure observation (like _counterFireTicks /
   * branchCounts) — never feeds back into gameplay. Read by ab-fire-guard.ts
   * as the A/B trigger-rate proxy (0 = the arm never diverged = vacuous).
   */
  _selfFireGuardBlocks = 0

  /**
   * §117: mode-2 (STAND) standing tick counter — how many consecutive ticks
   * the player has been standing still waiting to die. Capped by
   * `suicideReturnStandMaxTicks`; when exceeded, the trade aborts and normal
   * play resumes (a healthy stationary player may simply never get shot).
   * AI-internal (not serialized) — same semantics as _campTicks.
   */
  _suicideStandTicks = 0

  /**
   * §117: post-timeout re-commit suppress countdown (mode 2). After a STAND
   * trade aborts via the timeout, the candidate must NOT instantly re-commit
   * on the next tick — all preconditions (enemy at threat point + base bullet)
   * may still hold, which would re-freeze the player standing forever. The
   * suppress keeps normal play for `suicideReturnStandMaxTicks` before the
   * trade may try again. Same pattern as _antiCampSuppress / _aggCampSuppress.
   */
  _suicideStandSuppress = 0

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
    // §134: 防守位停射拦截候选提交计数（纯观察）。
    defenseIntercept: 0,
    // §116: 自杀秒回候选提交计数（纯观察）。
    suicideReturn: 0,
    // §139: 火力死区解除候选提交计数（纯观察）。
    firingLane: 0,
    // §161: 开路策略候选提交计数（纯观察）。
    carvePath: 0,
    // §189: 开局联通清墙候选提交计数（纯观察）。
    baseConnectClear: 0,
    // §163: 中路防守候选提交计数（纯观察）。
    midLaneDefense: 0,
    // §164: 中路列旁主动驻守候选提交计数（纯观察）。
    midLaneHold: 0,
  }

  /**
   * Death attribution (M0, plan/God-AI-Redesign-v2 §6): the think() branch
   * taken this tick, set at every return point in thinkImpl. Pure observation
   * — read by tools/diag/death-attribution.ts via runSimulation telemetry.
   * No gameplay effect, no RNG, no serialization.
   */
  _lastBranch: string = 'navigate'

  /**
   * Reusable scan results for scanAheadImpl — one buffer per direction index
   * (0=up, 1=down, 2=left, 3=right). Avoids allocating a result object per call.
   *
   * (perf §123) They also back a per-tick memo: `_scanCacheMask` marks which
   * direction slots are already computed for the scan origin recorded in
   * `_scanCacheX/_scanCacheY`. A different origin clears the mask; endFrame()
   * clears it every tick. See scanAheadImpl's doc comment for why this is
   * byte-identical.
   */
  _scanResults: ScanResult[] = [
    makeScanResult(),
    makeScanResult(),
    makeScanResult(),
    makeScanResult(),
  ]
  _scanCacheX = NaN
  _scanCacheY = NaN
  _scanCacheMask = 0

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
  /** (perf §125) Per-tick selectTarget memo — see selectTargetImpl for why a
   * within-tick memo is safe where the §68 cross-tick cache was not.
   * `_selTargetBuf` is the single stable result cell handed to every caller. */
  _selTargetValid = false
  _selTargetKeyCol = 0
  _selTargetKeyRow = 0
  _selTargetNull = false
  _selTargetBuf: Cell = { col: 0, row: 0 }

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
  // §187: blocked cell cache key for navigateTowards — invalidates when
  // the player (obstacle) moves to a new cell.
  _navBlockedCol = -99
  _navBlockedRow = -99
  // §187: blocked cell cache key for replan — same discipline as nav cache.
  _replanBlockedCol = -99
  _replanBlockedRow = -99

  /**
   * (perf §127) Cross-tick replan cache — same discipline as the §68
   * navigateTowards cache, applied to the followPath→replanImpl MAIN
   * navigation path that §68 missed (replanInterval defaults to 1, so
   * replanImpl ran full A* EVERY tick — measured 73-89% of all findPath
   * calls, docs/perf-optimization.progress.md §2.10).
   *
   * Unlike navigateTowards's cache, replanImpl draws NO RNG (no
   * suboptimalPathProb gate), so skipping identical recomputation is
   * BYTE-IDENTICAL — the determinism signature does not move (no
   * §68-style relaxation needed). The cached array is only consumed by
   * followPath's shift on player-cell change, which changes the cache key
   * → miss → fresh path, so the cache never serves a pre-consumed path.
   *
   * Invalidation: same-key hits expire after _replanMax ticks (safety
   * timer) or when `world.tileMap.revision` differs from `_replanRev` — the
   * terrain revision counter bumps on EVERY terrain mutation, so a brick
   * destroyed by a bullet invalidates the cache the SAME tick, making the
   * cache a strict pure memo of (playerCell, target, terrain) — byte-
   * identical to per-tick replanning (A/B smoke initially showed 72/700
   * divergent cells from the 60-tick staleness window; §127-revision fix
   * closed it, 700/700 identical). followPathImpl clears
   * _replanCacheValid when it declares a stuck (the cached path is what
   * failed — force a fresh A* next tick). reset() clears it per stage.
   * Gate: params.replanCache (0 = byte-identical pre-§127).
   */
  _replanCacheValid = false
  _replanPcCol = 0
  _replanPcRow = 0
  _replanTgtCol = 0
  _replanTgtRow = 0
  /** terrain revision at cache fill time (§127) — see doc above. */
  _replanRev = -1
  _replanCache: Direction[] | null = null
  _replanTimer = 0
  _replanMax = 60

  /**
   * (perf §129) Cross-tick pickup-reachability memo — 8 direct-mapped slots
   * keyed on (playerCell, target) + tileMap.revision, mirroring the §127
   * replan-cache discipline. powerUpCollectCell re-evaluates the same items
   * every think (urgent + bonus-window paths, per tick in hard/chaos), and
   * the player crosses a cell boundary only every ~8-23 ticks, so repeat
   * queries dominate. findPath draws no RNG — identical keys always yield
   * identical booleans, byte-identical to uncached. reset() clears per stage.
   * Gate: params.pickupReachCache (0 = byte-identical pre-§129).
   *
   * 60-tick defence-in-depth timer (review note): bounds staleness if
   * findPath ever gains a new input beyond the memo key — same discipline
   * as §127's _replanTimer. A pure memo always recomputes the identical
   * value, so the timer only affects recompute frequency, never results.
   */
  _pickupReachSlots: PickupReachSlot[] = [
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
    emptyPickupReachSlot(),
  ]
  _pickupReachTimer = 0
  _pickupReachMax = 60

  /**
   * §88: throttled chokepoint plan (threat points + selected 咽喉要地 cell).
   * Recomputed every chokepointReplanTicks (default 30) or when missing — the
   * same cross-tick cache discipline as _navCacheValid (threat points only
   * change when bricks are destroyed). Pure function of World state + frame:
   * deterministic, replay-safe. Reset in reset() per stage.
   */
  _chokepointPlan: ChokepointPlan | null = null
  /**
   * §137 / 基地守位格: lazily computed standable defense anchor (per stage).
   * null = not computed yet / no standable guard cell found. Computed once in
   * getBaseGuardAnchor() — pure terrain function, no RNG, recomputed on reset.
   */
  _baseGuardAnchor: Cell | null = null
  /**
   * §139 / 方向 A: cached firing-lane lookout cell + the frame it was found.
   * Throttled re-search (firingLaneReplanTicks); pure observation of World
   * state + params, no RNG. Only written when firingLaneMode > 0.
   */
  _firingLaneCell: Cell | null = null
  _firingLaneTick = 0

  /**
   * §161 / 开路策略 (carve path): cross-tick pure-memo caches (same
   * discipline as the §127 replan cache — keyed on World inputs, terrain
   * revision bumps invalidation). All reset per stage.
   */
  /** Carve target post (standable base-guard anchor), computed once per stage. */
  _carvePost: Cell | null = null
  _carvePostComputed = false
  /** Per-revision carve cost array (ring + base-column bricks = 1e9). */
  _carveCosts: Float64Array | null = null
  _carveCostsRev = -1
  /** Cached carve path query (from cell, to cell, revision). */
  _carvePathCache: Direction[] | null = null
  _carvePathCacheValid = false
  _carvePathCorridor = false
  _carvePathFromCol = -1
  _carvePathFromRow = -1
  _carvePathToCol = -1
  _carvePathToRow = -1
  /**
   * §162: carve-dig session (nav-stuck escape). Once started, the HUNT
   * candidate follows the exact-ring-safe dig path toward the escape target
   * until the pocket is exited (path becomes corridor / empty) or the
   * session times out (carveDigMaxTicks). Pure World-driven state — no RNG.
   */
  _carveDigActive = false
  _carveDigTicks = 0
  _carveDigTarget: Cell | null = null
  /**
   * §162: pixel-level stuck detector — counts ticks since the player last
   * moved > `carveDigNetEscape` px AWAY from an anchor point (net
   * displacement, not per-tick). A free player accumulates ~0.7px/tick and
   * breaks the anchor within ~20 ticks; a wall-blocked player oscillating in
   * a sealed pocket stays within a few px and trips after
   * carveDigBlockTicks. Tracks in endFrame() so it runs EVERY tick
   * regardless of which candidate wins; the cell-level `_navStuckTicks`
   * counter never fires for pocket oscillation (HUNT isn't evaluated every
   * tick, and the coordinate bounces across cell lines without escaping).
   * Pure World read.
   */
  _digBlockTicks = 0
  _digAnchorX = 0
  _digAnchorY = 0
  _carvePathRev = -1
  _carvePathTimer = 0
  /**
   * (perf §131) Second-level carve-path memo — `(fromKey, toKey) -> answer`
   * under one terrain revision + param set. `_carvePathCache` above holds
   * exactly ONE pair, so a scan over many candidate targets from the same
   * cell (findLaneDefensePointImpl walks up to 48) misses it on every single
   * candidate — measured 0.0% hit rate on that path — and re-runs the full
   * corridor + dig A* for each one, tick after tick. This map remembers them
   * all; the measured working set is ~28 live entries.
   *
   * Strict pure memo, same discipline as `_carveCosts`: the stored answer is
   * a function of (tileMap, from, to, carveBaseColumnCost, carveMaxBaseColumn)
   * and nothing else. A revision or param change clears the whole map, and
   * `carveReplanTicks` bounds staleness exactly as it does for the 1-entry
   * cache. Reset per stage.
   */
  _carveMemo: Map<number, { path: Direction[] | null; corridor: boolean }> | null = null
  _carveMemoRev = -1
  _carveMemoBaseCost = -1
  _carveMemoMaxBase = -1
  _carveMemoTtl = 0
  /**
   * §164: per-revision cached mid-lane parry hold cell (findParryHoldCellImpl)
   * + whether the base column is open to the base (laneColumnOpenToBaseImpl
   * — cached together, both pure terrain functions of tileMap.revision).
   * Same strict-pure-memo discipline as _carveCosts._rev.
   */
  _parryHoldRev = -1
  _parryHoldCell: Cell | null = null

  /**
   * §189: separate cache for digPathInfoCached (base connectivity clear).
   * MUST NOT share fields with _carvePathCache — digPathInfoCached uses
   * findPath+breakBrick directly (no pathCarveSafeImpl), so a cache hit
   * served from the wrong cache would be a correctness bug.
   */
  _digPathCache: Direction[] | null = null
  _digPathCacheValid = false
  _digPathCorridor = false
  _digPathFromCol = -1
  _digPathFromRow = -1
  _digPathToCol = -1
  _digPathToRow = -1
  _digPathRev = -1
  _digPathTimer = 0
  /** §189: cached dig-path cost array (ring bricks = 1e9). */
  _digCosts: Float64Array | null = null
  _digCostsRev = -1
  /**
   * §189: true once the candidate starts carving (no corridor to P2 spawn).
   * Stays true while the player travels along the opened corridor to the
   * P2 spawn, then resets when the player arrives (within 2 cells) or the
   * base comes under threat. Prevents the candidate from firing on stages
   * where the corridor always existed (no carving needed).
   */
  _baseConnectClearActive = false
  /** §189: ticks since travel mode activated — bounds the opening-phase duration. */
  _baseConnectClearActiveTicks = 0

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
    this._pressGuard = false // §167
    this._pressFrenzy = false // §167
    // §123/§125 (perf): clear the within-tick memo flags on stage reset —
    // reset() can run between a think and its endFrame (browser stage
    // transition path), and a stale bit would serve a result computed
    // against the OLD world (player spawn origin is identical every stage).
    this._scanCacheMask = 0
    this._scanCacheX = NaN
    this._scanCacheY = NaN
    this._selTargetValid = false
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
    // §152-W2/W3: reset the aggressive-nav stuck guard + pickup commit state.
    this._aggNavStuckCell = null
    this._aggNavStuckTicks = 0
    this._aggNavKillsAtStart = 0
    this._aggNavSuppress = 0
    this._pickupCommitActive = false
    this._pickupCommitTicks = 0
    this._pickupCommitCol = 0
    this._pickupCommitRow = 0
    this._pickupCommitItemCol = 0
    this._pickupCommitItemRow = 0
    // §86: reset dodge direction persistence.
    this._lastDodgeDir = null
    this._lastDodgeThreatId = -1
    this._dodgeFlipCount = 0
    this._navStuckCell = null
    this._navStuckTicks = 0
    this._navStuckSuppress = 0
    this._threatStickyHold = 0 // §169
    this._huntCommitId = -1 // §170
    this._huntCommitUntil = 0 // §170
    this._pathCostEId.fill(-1) // §171
    this._pathCostNext = 0 // §171
    this.aggressive = false
    this._enemies = []
    this._otherTanks = []
    // M3 diag: reset the counter-fire trigger counter per stage.
    this._counterFireTicks = 0
    // §116/§117: reset the suicide-trade state per stage.
    this._suicideStanding = false
    this._suicideStandTicks = 0
    this._suicideStandSuppress = 0
    // §88: invalidate the throttled chokepoint plan on stage reset.
    this._chokepointPlan = null
    this._baseGuardAnchor = null
    this._firingLaneCell = null
    this._firingLaneTick = 0
    // §161: invalidate the carve-path caches on stage reset (new terrain).
    this._carvePost = null
    this._carvePostComputed = false
    this._carveCosts = null
    this._carveCostsRev = -1
    this._carvePathCache = null
    this._carvePathCacheValid = false
    this._carvePathTimer = 0
    // (perf §131) The second-level memo is keyed on terrain — a new stage
    // invalidates every entry.
    if (this._carveMemo !== null) this._carveMemo.clear()
    this._carveMemoRev = -1
    this._carveMemoBaseCost = -1
    this._carveMemoMaxBase = -1
    this._carveMemoTtl = 0
    // §164: invalidate the mid-lane parry-hold cache on stage reset.
    this._parryHoldRev = -1
    this._parryHoldCell = null
    // §189: invalidate the dig-path cache on stage reset.
    this._digPathCache = null
    this._digPathCacheValid = false
    this._digPathTimer = 0
    this._digCosts = null
    this._digCostsRev = -1
    this._baseConnectClearActive = false
    this._baseConnectClearActiveTicks = 0
    // §162: reset the carve-dig session (new stage = new pocket).
    this._carveDigActive = false
    this._carveDigTicks = 0
    this._carveDigTarget = null
    this._digBlockTicks = 0
    this._digAnchorX = 0
    this._digAnchorY = 0
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
    this._navBlockedCol = -99
    this._navBlockedRow = -99
    // (perf §127) Invalidate the cross-tick replan cache on stage reset too.
    this._replanCacheValid = false
    this._replanTimer = 0
    this._replanRev = -1
    this._replanBlockedCol = -99
    this._replanBlockedRow = -99
    // §187: reset target blacklist on stage reset.
    this._blacklistEnemyId = -1
    this._blacklistExpiryFrame = 0
    this._lastSelectTargetId = -1
    this._targetStuckTicks = 0
    // (perf §129) Invalidate the pickup-reachability memo on stage reset too.
    for (let i = 0; i < this._pickupReachSlots.length; i++) {
      this._pickupReachSlots[i].valid = false
    }
    this._pickupReachTimer = 0
    // Gap B (plan §3): cache whether this stage has a base. All BASE_POS-
    // dependent logic checks this flag instead of assuming a base exists.
    this.hasBase = this.world.tileMap.hasBase()
    // Dual central breach strategy: cache the detector result per stage.
    // Pure terrain function — same semantics as hasBase (not serialized).
    this._centralBreachRisk = detectCentralBreachRisk(this.world)
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

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    // §167 / B4: super-item strategic activation. OFF (default) keeps the
    // pre-§167 behavior (never pressed — byte-identical). Rewind is never
    // AI-pressed: it needs the RecoveryController (Game.ts) which headless
    // sims don't run (rewindPending would sit unconsumed).
    if (this.params.superItemMode <= 0 || kind === 'rewind') return false
    // think() sets this tick's flags; updatePlayerTank queries items BEFORE
    // reading movement, so force the decision here (idempotent via _thought).
    if (!this._thought) this.think()
    return kind === 'guard' ? this._pressGuard : this._pressFrenzy
  }

  endFrame(): void {
    this._thought = false
    this._pressGuard = false // §167
    this._pressFrenzy = false // §167
    // Invalidate per-tick lazy caches.
    this._baseUnderThreatCache = null
    this._playerCellValid = false
    this._canMoveComputed = 0
    this._scanCacheMask = 0 // §123
    this._selTargetValid = false // §125
    // §169: threat-signal sticky hold countdown (runs every tick; 0 = OFF ⇒
    // the branch never executes — byte-identical).
    if (this._threatStickyHold > 0) this._threatStickyHold--
    // §162: pixel-level stuck detector — runs every tick (regardless of
    // which candidate wins think() next tick). Net-displacement from an
    // anchor: a free player (> carveDigNetEscape px from anchor) re-anchors
    // and resets the counter; a wall-blocked pocket oscillater stays near
    // the anchor and trips after carveDigBlockTicks. SHIPPED default
    // (navBreakStuck=1); 0 ⇒ the dig never engages ⇒ byte-identical.
    if (this.params.navBreakStuck > 0) {
      const p = this.world.player
      // Skip while spawning (spawnTimer > 0 locks movement — a spawn wait is
      // NOT a pocket lock; counting it would trigger a premature dig every
      // stage start, abandoning the base defense for a fresh pocket dig).
      if (p && p.alive && !(p.spawnTimer > 0)) {
        const dx = p.x - this._digAnchorX
        const dy = p.y - this._digAnchorY
        if (Math.abs(dx) + Math.abs(dy) > this.params.carveDigNetEscape) {
          // Real movement — re-anchor.
          this._digAnchorX = p.x
          this._digAnchorY = p.y
          this._digBlockTicks = 0
        } else {
          this._digBlockTicks++
        }
        // §187: target blacklist trigger — when the player has been
        // pixel-stuck for >= targetBlacklistStuckTicks while targeting
        // enemy A, blacklist A for targetBlacklistDuration ticks so
        // selectTarget picks a different enemy.
        if (
          this.params.targetBlacklistStuckTicks > 0 &&
          this._digBlockTicks >= this.params.targetBlacklistStuckTicks &&
          this._lastSelectTargetId >= 0 &&
          this._blacklistEnemyId < 0
        ) {
          this._blacklistEnemyId = this._lastSelectTargetId
          this._blacklistExpiryFrame = this.world.frame + this.params.targetBlacklistDuration
          this._selTargetValid = false // force re-select next tick
        }
      } else {
        // Re-anchor on spawn / death so the counter starts fresh when play
        // resumes (otherwise the pre-spawn idle would carry over).
        this._digBlockTicks = 0
        if (p && p.alive) {
          this._digAnchorX = p.x
          this._digAnchorY = p.y
        }
      }
    }
  }

  // ================================================================
  // Co-op awareness (双玩家协作) — read-only World queries
  // ================================================================

  /**
   * Returns the OTHER player's tank when in coop/dual-God mode, or null.
   * P1's partner is w.player2; P2's partner is w.player.
   * Pure World read — no hidden state (AGENTS §2.2).
   */
  coopPartner(): Tank | null {
    const w = this.world
    const me = this.controlledTank(w)
    if (me === w.player) return w.player2
    if (me === w.player2) return w.player
    return null
  }

  /**
   * True when the co-op partner exists and is alive. Covers BOTH the
   * Lie-Back-Win (human P1 + God AI P2) and 督战双玩家 (dual God AI) modes —
   * in either case a second, living tank is on the field.
   */
  hasLivingPartner(): boolean {
    const partner = this.coopPartner()
    return !!(partner && partner.alive && partner.spawnTimer <= 0)
  }

  /**
   * Returns true when THIS AI controls player2 (right-side spawn).
   * Derived from controlledTank — no new state.
   */
  isPlayer2(): boolean {
    return this.controlledTank(this.world) === this.world.player2
  }

  /**
   * §190: True when the dual central breach strategy should be active for this
   * AI. In 督战双玩家 (spectateDual): active for BOTH P1 and P2. In 躺赢模式
   * (coop): active only for the God AI controlling P2 (P1 is human-controlled,
   * so P1-specific gates still check spectateDual and remain OFF in coop).
   * This enables P2 to use the same dual strategies (spawn patrol, defense
   * de-confliction, directMove, fence pickup, etc.) in coop mode as in dual
   * spectate mode — the user request "GOD AI 使用 dual 模式下 player2 策略".
   */
  get dualStrategyActive(): boolean {
    return (this.world.spectateDual || this.world.coop) && this._centralBreachRisk
  }

  /**
   * §187: Set to true for guard AI brains (created by SimulationEnemies).
   * When true (or when isPlayer2()), the A* pathfinding treats the primary
   * player (w.player) as an impassable obstacle — preventing the guard/player
   * mutual-block deadlock (S7@seed54: 23.9s stuck).
   */
  isGuardAI = false

  /**
   * §187: Returns the cell of the primary player (P1) to block in A*
   * pathfinding, or null if this brain should NOT block anyone.
   *
   * - Guard → blocks P1 (w.player)
   * - P2 → blocks P1 (w.player)
   * - P1 → blocks nobody (null)
   *
   * Gated by `navAvoidPlayer` param (0 = OFF = null, byte-identical).
   */
  getNavBlockedCell(): Cell | null {
    if (this.params.navAvoidPlayer <= 0) return null
    if (!this.isGuardAI && !this.isPlayer2()) return null
    const p = this.world.player
    if (!p) return null
    return this.tankCell(p)
  }

  /**
   * §187: Target blacklist — the enemy id currently blacklisted (or -1).
   * When the player is stuck for >= targetBlacklistStuckTicks while targeting
   * enemy A, A is blacklisted for targetBlacklistDuration ticks.
   */
  _blacklistEnemyId = -1
  _blacklistExpiryFrame = 0
  /** §187: The enemy id selected by the last selectTarget call (for blacklist trigger). */
  _lastSelectTargetId = -1
  /** §187: How long the player has been pixel-stuck while targeting the current enemy. */
  _targetStuckTicks = 0

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
    // §157: an enemy with a CLEAR SHOT at the base (enemyCanShootBase —
    // aligned + no brick/steel in between) is a threat regardless of
    // distance. The static box (row >= 18) and race check (range ≤ 18)
    // miss enemies firing at the base from far away through cleared lanes.
    // The next bullet could destroy the base, so defense must activate.
    // Gated by baseClearShotThreat (0 = OFF, byte-identical).
    if (!result && this.params.baseClearShotThreat > 0) {
      for (let li2 = 0; li2 < list.length; li2++) {
        const t2 = list[li2]
        if (!t2.alive || t2.spawnTimer > 0) continue
        if (enemyCanShootBase(this, t2)) {
          result = true
          break
        }
      }
    }
    // §173: factual damage recall — once the base has actually TAKEN A HIT
    // (baseHp < baseMaxHp), the threat is no longer a prediction: the ring
    // bricks are breached and direct fire is landing. The predictive checks
    // above flicker (§169: 9.8 flips/10s before the first hit); damage never
    // flickers back. OR'd with the existing detection (never reduces it).
    // baseDamageRecall = 0 → OFF (byte-identical); >0 → the trigger engages
    // only while the player is farther than this many cells from the base
    // (arm 1 = unconditional was net −24: the permanent threat cascade hurt
    // open stages; the probe asymmetry is player-distance, so gate on it).
    if (!result && this.params.baseDamageRecall > 0) {
      if (
        this.world.baseHp < this.world.baseMaxHp &&
        playerDistToBase > this.params.baseDamageRecall
      ) {
        result = true
      }
    }
    // §169: sticky hold — the threat signal flickers as enemies cross the
    // race-range/alignment boundaries (defeat probe: 9.8 flips/10s before
    // the base's first hit). Once true, keep it true for threatStickyTicks
    // so the defense cascade (selectTarget, skipT2aForDefense, item gates,
    // F5 guard summon, carve gate) stays engaged through the gaps. Only
    // extends, never shortens; 0 = OFF = byte-identical.
    if (this.params.threatStickyTicks > 0) {
      if (result) {
        this._threatStickyHold = this.params.threatStickyTicks
      } else if (this._threatStickyHold > 0) {
        result = true
      }
    }
    this._baseUnderThreatCache = result
    return result
  }

  // M0.5 (2026-08-03): D1 hasFastThreatNearBase + SmartThreatModel wrappers
  // (threatScore / smartIsBaseUnderThreat) retired — archived in experimental.ts.

  scanAhead(pcx: number, pcy: number, dir: Direction): ScanResult {
    return scanAheadImpl(this, pcx, pcy, dir)
  }
  isBaseProtectionBrick(col: number, row: number): boolean {
    return isBaseProtectionBrickImpl(this, col, row)
  }
  shouldFireInDir(pcx: number, pcy: number, dir: Direction, allowWallFire = true): boolean {
    return shouldFireInDirImpl(this, pcx, pcy, dir, allowWallFire)
  }
  /** §152-W1: does the bullet's ACTUAL 6px path hit non-ring steel within
   * maxDist? Mirrors SimulationCombat.bulletHitsTerrain — see FireControl. */
  bulletPathSteelBlocked(pcx: number, pcy: number, dir: Direction, maxDist: number): boolean {
    return bulletPathSteelBlockedImpl(this, pcx, pcy, dir, maxDist)
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
  /** §152-W3: urgent power-up target with commit persistence (see
   * StrategyPlanner) — used by PICKUP_HIGH/PICKUP_MID. */
  findUrgentPowerUpTargetWithCommit(
    pcx: number,
    pcy: number,
    tier: 'all' | 'high' | 'midlow' = 'all',
  ): Cell | null {
    return findUrgentPowerUpTargetWithCommitImpl(this, pcx, pcy, tier)
  }
  /** E1 / 道具经济: dire-state item pickup (swarm or ring-damaged → nearby
   * bomb/freeze/fence/emp worth a divert). 0 = OFF (byte-identical). */
  findDireItemTarget(pcx: number, pcy: number): Cell | null {
    return findDireItemTargetImpl(this, pcx, pcy)
  }
  /** §6.3-A: P2 dual central breach fence pickup (bypasses all gates). */
  findDualFencePickup(pcx: number, pcy: number): Cell | null {
    return findDualFencePickupImpl(this, pcx, pcy)
  }
  /** §156: freeze-window power-up pickup (unlimited range). */
  findFreezePickupTarget(pcx: number, pcy: number): Cell | null {
    return findFreezePickupTargetImpl(this, pcx, pcy)
  }
  /** §158: non-freeze close-range power-up pickup. */
  findClosePickupTarget(pcx: number, pcy: number): Cell | null {
    return findClosePickupTargetImpl(this, pcx, pcy)
  }
  calculateRouteDanger(fromX: number, fromY: number, toX: number, toY: number): number {
    return calculateRouteDangerImpl(this, fromX, fromY, toX, toY)
  }
  getDefaultDefensePosition(): Cell {
    return getDefaultDefensePositionImpl(this)
  }
  /** §137: the computed base guard anchor (standable defense hold), or null. */
  getBaseGuardAnchor(): Cell | null {
    if (this._baseGuardAnchor === null && this.params.baseGuardAnchorMode > 0) {
      // §178 (autopsy seed2): dual central breach — P1 holds a CENTRAL position
      // (intercept the col-12 spawn lane) instead of the flank anchor. Gated by
      // spectateDual && centralBreachRisk && !isPlayer2 — single-player and P2
      // keep the computed base-guard anchor (byte-identical).
      if (
        this.world.spectateDual &&
        this._centralBreachRisk &&
        !this.isPlayer2() &&
        this.params.dualCentralBreachP1Anchor > 0
      ) {
        this._baseGuardAnchor = findDualCentralHoldImpl(this)
      } else {
        this._baseGuardAnchor = computeBaseGuardAnchorImpl(this)
      }
    }
    return this._baseGuardAnchor
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
