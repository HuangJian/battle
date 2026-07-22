import type { TankKind, IntelligenceLevel } from '../types'
import { DIFFICULTIES } from '../config/difficulty'
import type {
  DifficultyAIScaling,
  IntelligenceConfig,
  ResolvedConfig,
} from './types'

/**
 * ai/config.ts — the heart of "configuration defines intelligence".
 *
 * Every enemy tank runs the exact same decision pipeline
 * (`TacticalIntelligence`). What makes a Rookie different from a Commander is
 * entirely described by the data below. Adding a new tier = appending one
 * entry to `INTELLIGENCE_LEVELS` (plus, optionally, a `KIND_TO_LEVEL` entry).
 * No engine code changes.
 *
 * Values follow the plan's §16 example, extended with goal weights and the
 * imperfection levers (reactionTime / aimError / routeNoise) required by the
 * Imperfection Model (§13) and Testing Strategy (§18: "lower levels exhibit
 * believable mistakes").
 */

export const INTELLIGENCE_LEVELS: Record<IntelligenceLevel, IntelligenceConfig> = {
  // ----- Rookie: shortsighted, jumpy, forgetful -----
  rookie: {
    name: 'Rookie',
    strategicThinking: false,
    teamwork: false,
    dodgeProbability: 0.2,
    predictionDepth: 1,
    routeLookAhead: 2,
    aggression: 0.45,
    reactionTime: 420,
    aimError: 0.35,
    routeNoise: 0.4,
    weights: {
      attackBase: 1.0,
      attackPlayer: 0.7,
      destroyWall: 0.4,
      retreat: 0.5,
      regroup: 0.2,
      advance: 0.6,
    },
  },

  // ----- Soldier: better routing + basic dodging -----
  soldier: {
    name: 'Soldier',
    strategicThinking: false,
    teamwork: false,
    dodgeProbability: 0.45,
    predictionDepth: 2,
    routeLookAhead: 4,
    aggression: 0.6,
    reactionTime: 300,
    aimError: 0.2,
    routeNoise: 0.22,
    weights: {
      attackBase: 1.3,
      attackPlayer: 1.0,
      destroyWall: 0.7,
      retreat: 0.5,
      regroup: 0.3,
      advance: 0.7,
    },
  },

  // ----- Veteran: advanced prediction, strong base pressure -----
  veteran: {
    name: 'Veteran',
    strategicThinking: true,
    teamwork: true,
    dodgeProbability: 0.75,
    predictionDepth: 4,
    routeLookAhead: 6,
    aggression: 0.72,
    reactionTime: 200,
    aimError: 0.1,
    routeNoise: 0.12,
    weights: {
      attackBase: 1.8,
      attackPlayer: 1.2,
      destroyWall: 1.1,
      retreat: 0.7,
      regroup: 0.5,
      advance: 0.8,
    },
  },

  // ----- Commander: full capability (elected role, never flawless) -----
  commander: {
    name: 'Commander',
    strategicThinking: true,
    teamwork: true,
    dodgeProbability: 0.9,
    predictionDepth: 8,
    routeLookAhead: 10,
    aggression: 0.8,
    reactionTime: 150,
    aimError: 0.05,
    routeNoise: 0.05,
    weights: {
      attackBase: 2.0,
      attackPlayer: 1.3,
      destroyWall: 1.3,
      retreat: 0.8,
      regroup: 0.9,
      advance: 0.9,
    },
  },
}

/**
 * Base intelligence tier for each enemy kind.
 * Difficulty does NOT change tiers — it scales capabilities (see DIFFICULTY_AI)
 * and the chance a commander is elected. This keeps "intelligence" and
 * "difficulty" as orthogonal axes, exactly as the plan's Vision demands
 * ("Difficulty should primarily arise from better decisions, not stronger
 * enemy statistics").
 */
export const KIND_TO_LEVEL: Record<TankKind, IntelligenceLevel> = {
  player: 'rookie', // unused for the player, but keeps the map total
  basic: 'rookie',
  fast: 'soldier',
  power: 'veteran',
  armor: 'veteran',
}

/**
 * Per-difficulty capability scaling. Applied on top of a tier's base config so
 * that "Hard" makes the *same* tanks smarter (better dodging, earlier
 * prediction, more likely to have a commander) rather than just faster/tougher.
 */
export const DIFFICULTY_AI: Record<string, DifficultyAIScaling> = {
  relax: { dodgeMult: 0.6, predictAdd: 0, reactionMult: 1.4, aggressionMult: 0.8, commanderChance: 0.0 },
  classic: { dodgeMult: 1.0, predictAdd: 0, reactionMult: 1.0, aggressionMult: 1.0, commanderChance: 0.15 },
  hard: { dodgeMult: 1.2, predictAdd: 1, reactionMult: 0.8, aggressionMult: 1.15, commanderChance: 0.3 },
  chaos: { dodgeMult: 1.4, predictAdd: 2, reactionMult: 0.6, aggressionMult: 1.3, commanderChance: 0.5 },
}

/**
 * Resolve the effective config for a tier on a given difficulty.
 *
 * Memoized: there are only (levels × difficulties) distinct combinations, and
 * the returned object is immutable (the AI only ever reads it), so sharing one
 * reference is safe and keeps the per-tick path allocation-free (AGENTS §25).
 */
const _configCache = new Map<string, ResolvedConfig>()
export function resolveConfig(level: IntelligenceLevel, difficultyKey: string): ResolvedConfig {
  const key = level + ':' + difficultyKey
  const cached = _configCache.get(key)
  if (cached) return cached
  const base = INTELLIGENCE_LEVELS[level]
  const scale = DIFFICULTY_AI[difficultyKey] ?? DIFFICULTY_AI.classic
  const dodge = Math.min(0.95, base.dodgeProbability * scale.dodgeMult)
  const resolved: ResolvedConfig = {
    ...base,
    level,
    difficultyKey,
    dodgeProbability: dodge,
    predictionDepth: base.predictionDepth + scale.predictAdd,
    reactionTime: Math.round(base.reactionTime * scale.reactionMult),
    aggression: Math.min(1, base.aggression * scale.aggressionMult),
    // Weights are not scaled — they express judgement, which is tier-owned.
    weights: { ...base.weights },
  }
  _configCache.set(key, resolved)
  return resolved
}

/** Base tier for a freshly spawned enemy of the given kind. */
export function levelForKind(kind: TankKind): IntelligenceLevel {
  return KIND_TO_LEVEL[kind] ?? 'rookie'
}

/** Commander-election probability for a difficulty (0 = never). */
export function commanderChanceFor(difficultyKey: string): number {
  return DIFFICULTY_AI[difficultyKey]?.commanderChance ?? 0
}

/** True if `difficultyKey` is a known key (defensive guard). */
export function hasDifficultyAI(difficultyKey: string): boolean {
  return difficultyKey in DIFFICULTIES
}
