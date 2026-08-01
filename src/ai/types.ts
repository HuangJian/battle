import type { Direction } from '../constants'
import type { GoalType, CommanderDirective } from '../types'

/**
 * ai/types.ts — shared data structures for the Tactical Intelligence Framework.
 *
 * These types are pure data (no behavior). The World is passed by reference
 * into the framework's functions; the framework never stores references to the
 * World, only the serializable observations/analyses below.
 */

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

export type { GoalType, CommanderDirective }
