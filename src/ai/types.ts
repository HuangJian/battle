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

/** A bullet observation (the AI never reads Bullet objects directly). */
export interface BulletObservation {
  x: number
  y: number
  dir: Direction
  /** Aligned to the tank's column (vertical bullet) or row (horizontal). */
  aligned: boolean
  /** Approaching the observer (heading toward it along the shared axis). */
  approaching: boolean
  /** Distance from the observer's center, in px (positive = ahead). */
  distance: number
}

/** A teammate observation (another alive enemy tank). */
export interface TeammateObservation {
  id: number
  x: number
  y: number
  dir: Direction
}

/** The full observation snapshot for one thinking tank. */
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
  /** Incoming bullets that could threaten this tank (player bullets only). */
  threats: BulletObservation[]
  teammates: TeammateObservation[]
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
  pathBlocked: boolean // a wall sits directly ahead toward the objective
  threat: BulletObservation | null // most urgent incoming bullet (or null)
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
