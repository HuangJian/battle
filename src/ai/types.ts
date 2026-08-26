import type { Direction } from '../constants'

/**
 * ai/types.ts — shared data structures for the Tactical Intelligence Framework.
 *
 * These types are pure data (no behavior). The World is passed by reference
 * into the framework's functions; the framework never stores references to the
 * World, only the serializable observations/analyses below.
 *
 * (§2.6) The enemy-brain types ({@link IntelligenceLevel}, {@link GoalType},
 * {@link CommanderDirective}, {@link AIState}) moved here from the root
 * `src/types.ts`; root re-exports them for compatibility.
 */

// ============================================================
// Intelligence tiers & goals
// ============================================================

/**
 * Intelligence tier names. Every AI tank above 'none' runs the same decision
 * pipeline; differences are entirely configuration-driven (see
 * `src/ai/config.ts`). 'none' is a separate minimal classic-behavior branch
 * (random wander + base bias + random fire — AI-Tier-System-Revision §3).
 * The tier is ROLLED AT SPAWN TIME from the difficulty's distribution table;
 * tank kind no longer implies a tier.
 */
export type IntelligenceLevel = 'none' | 'rookie' | 'soldier' | 'veteran' | 'commander'

/**
 * Candidate tactical/strategic goals. Goals compete through dynamic scores
 * (see `src/ai/TacticalIntelligence.ts`) rather than a fixed priority list.
 */
export type GoalType =
  | 'attackBase'
  | 'attackPlayer'
  | 'destroyWall'
  | 'retreat'
  | 'regroup'
  | 'advance'
  | 'defendBase' // 天降神兵 allied guard posture (§31 Phase 2)
  | 'attackAlly' // Decoy: attack ally/decoy targets (new-powerups-plan §4.4)

/**
 * Lightweight cooperation directives broadcast by the (elected) commander.
 * Tanks remain autonomous — they may follow or ignore a directive according
 * to their own intelligence (teamwork flag).
 */
export type CommanderDirective =
  | 'none'
  | 'pushLeft'
  | 'pushRight'
  | 'defendBase'
  | 'attackTogether'
  | 'spreadOut'

/**
 * AIBrain — the complete, serializable decision state for one enemy tank.
 *
 * This is the Tactical Intelligence Framework's per-tank memory and lives on
 * the World (no hidden state outside it — AGENTS.md §2.2). It is a flat
 * structure of primitives only, so the snapshot `WorldSerializer` can
 * shallow-clone it safely when snapshotting the World.
 *
 * The fields `thinkTimer` / `fireTimer` / `currentDir` are kept from the
 * previous AI for backwards compatibility with the determinism tests.
 */
export interface AIState {
  // ---- Identity / intelligence ----
  level: IntelligenceLevel
  /** Born at Commander tier (render flag for crown/aura; NOT command authority —
   *  the active commander is `world.activeCommanderId`). */
  isCommander: boolean
  /** Monotonic per-World birth order (from `world.spawnSeqCounter`). The alive
   *  Commander with the highest spawnSeq holds command authority. */
  spawnSeq: number

  // ---- Tactical layer (reactive + short horizon) ----
  thinkTimer: number // ms until the next tactical re-evaluation
  fireTimer: number // ms until the next fire attempt
  currentDir: Direction // direction the tank intends to move this tick
  tacticalGoal: GoalType // current short-term objective
  targetX: number // route target (px, tank-center aligned)
  targetY: number

  // ---- Strategic layer (long horizon) ----
  strategicTimer: number // ms until the next strategic re-evaluation
  strategicGoal: GoalType // stable long-term objective

  // ---- Reaction / imperfection ----
  reactionTimer: number // ms of remaining "delayed reaction" before dodging
  dodgeLock: number // ms the current dodge direction is committed

  // ---- Dead-end recovery ----
  /** ms spent confined to a single-axis channel (no lateral open direction).
   *  Drives the tunnel-out behavior in TacticalIntelligence. */
  vertOnlyTicks: number

  // ---- Commander ----
  commanderTimer: number // ms until this commander's next broadcast
  directive: CommanderDirective // last directive received (or 'none')
  directiveAge: number // ms since the directive was received
  /** Seq id (world.directiveSeqCounter) of the directive last rolled for. */
  directiveSeq: number
  /** Cached compliance roll for that directive (rolled once, on arrival). */
  directiveCompliant: boolean
}

// ============================================================
// Perception — what the AI can observe of the battlefield
// ============================================================

/** The full observation snapshot for one thinking tank.
 *
 * Field design is allocation-free in the hot path: instead of returning arrays
 * of {BulletObservation} and {TeammateObservation} (which were allocated every
 * perceive call → millions of short-lived objects under the God-AI tuning
 * loop), the snapshot carries the flat aggregates each consumer actually reads:
 *   - threat: tracked as `hasThreat` + `threatDir` (the only fields any
 *     consumer ever reads from a threat — analyze + reactiveDodge).
 *   - teammates: tracked as `teammateCount` + centroid sum (the only fields
 *     any consumer ever reads — `targetForGoal`'s spreadOut directive).
 * Consumers iterate `world.bullets` / `world.allTanks` directly when they need
 * per-element data. (AGENTS.md §14.1 / §14.2.)
 */
export interface Perception {
  selfX: number
  selfY: number
  selfDir: Direction
  hasPlayer: boolean
  playerX: number
  playerY: number
  hasBase: boolean
  baseX: number
  baseY: number
  /** Whether a live decoy (诱饵) exists on the battlefield. */
  hasDecoy: boolean
  /** Pixel center of the nearest decoy, or 0 when none. */
  decoyX: number
  decoyY: number
  /** Whether any incoming hostile bullet threatens this tank. */
  hasThreat: boolean
  /** Direction of the closest incoming threat. Meaningful only when hasThreat. */
  threatDir: Direction
  /** Number of live enemy teammates (self excluded). */
  teammateCount: number
  /** Sum of teammate x-centers — divide by teammateCount for the centroid. */
  teammateSumX: number
  /** Sum of teammate y-centers — divide by teammateCount for the centroid. */
  teammateSumY: number
  /** Number of tanks (self excluded) in the same 8×8 cell neighbourhood. */
  congestion: number
  /** Open directions from the tank's current, grid-aligned position. */
  openDirs: Direction[]
}

// ============================================================
// Situation — tactical knowledge derived from perception
// ============================================================

export interface Situation {
  distToBase: number // Manhattan, px (Infinity if no base)
  distToPlayer: number // Manhattan, px (Infinity if no player)
  baseInLineOfFire: boolean // firing currentDir would reach the base
  playerVisible: boolean // player exists & is roughly on the battlefield
  playerInLineOfFire: boolean // firing currentDir would hit the player
  wallInLineOfFire: boolean // firing currentDir would break a (brick) wall
  decoyInLineOfFire: boolean // firing currentDir would strike a decoy (诱饵)
  pathBlocked: boolean // a wall sits directly ahead toward the objective
  /** Whether an incoming hostile bullet threatens this tank. */
  hasThreat: boolean
  /** Direction of the threat (null when hasThreat is false). */
  threatDir: Direction | null // safe axis to step toward to dodge
  baseDanger: number // 0..1 — how exposed the base currently is
  teammateCount: number
  congestion: number
  openDirs: Direction[]
}

// ============================================================
// Configuration — capabilities & dynamic goal weights
// ============================================================

/**
 * GoalWeights — the dynamic scoring weights for each candidate goal.
 * Higher intelligence tunes these to express better judgement
 * (e.g. veterans weight attackBase higher; rookies are more scatter-shot).
 */
export interface GoalWeights {
  attackBase: number
  attackPlayer: number
  destroyWall: number
  attackAlly: number
  retreat: number
  regroup: number
  advance: number
}

/**
 * IntelligenceConfig — everything that makes one tier "smarter" than another.
 * All gameplay-affecting intelligence lives here; the engine code is shared.
 */
export interface IntelligenceConfig {
  name: string
  /** Does this tier perform strategic (20s) re-evaluation? (Commander only,
   *  and only while holding active command — see AI-Tier-System-Revision §4.) */
  strategicThinking: boolean
  /**
   * 指令遵从度 — probability (0..1) that this unit receives, understands and
   * executes a commander directive. Rolled ONCE per directive on arrival
   * (cached in `aiState.directiveCompliant`). Replaces the old boolean
   * `teamwork`: issuing directives is exclusive to the active Commander;
   * obeying is universal and compliance-gated. None-tier is deaf (0).
   */
  compliance: number
  /** Probability (0..1) the tank successfully dodges an incoming bullet. */
  dodgeProbability: number
  /** How many cells ahead bullets are predicted (1 = reacts late). */
  predictionDepth: number
  /** How many cells of look-ahead the router uses (1 = shortsighted). */
  routeLookAhead: number
  /** Base willingness to fire (0..1). */
  aggression: number
  /** Base delayed-reaction time before dodging, ms (higher = dumber). */
  reactionTime: number
  /** Chance (0..1) of a targeting mistake (fires with no real shot). */
  aimError: number
  /** Chance (0..1) of picking a suboptimal route. */
  routeNoise: number
  /** Dynamic goal scoring weights. */
  weights: GoalWeights
}
