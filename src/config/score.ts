import type { IntelligenceLevel } from '../types'

/**
 * Centralized scoring formulas (user spec 2026-07-27).
 *
 * Every score in the game is derived here so the numbers stay consistent and
 * tunable in one place — no per-kind magic constants scattered through the
 * Simulation (AGENTS §2.4: data over code).
 *
 * Stage numbering: the spec phrases the coefficients in terms of "第 N 关"
 * (the Nth stage), which is 1-based in Chinese. The runtime uses a 0-based
 * `stageIndex`, so callers pass `stageIndex` and we add STAGE_INDEX_OFFSET (=1)
 * internally. Change this single constant if a 0-based convention is ever
 * wanted.
 */

/** Maps a 0-based stageIndex to the 1-based "level number" N used by the spec. */
export const STAGE_INDEX_OFFSET = 1

/** Base points for destroying any enemy, regardless of kind. */
export const KILL_BASE_SCORE = 100

/** Points granted per power-up collected. */
export const ITEM_SCORE = 100

/** Difficulty multiplier on kill score. */
export const DIFFICULTY_SCORE_FACTOR: Record<string, number> = {
  classic: 1.0,
  relax: 1.0,
  hard: 1.2,
  chaos: 1.5,
}

/** AI tier multiplier on kill score. */
export const AI_SCORE_FACTOR: Record<IntelligenceLevel, number> = {
  none: 1.0, // classic "no intelligence" branch
  rookie: 1.0,
  soldier: 1.2,
  veteran: 1.5,
  commander: 2.0,
}

/** Stage (level) coefficient: 1.05 ** N, where N is the 1-based level number. */
export function levelFactor(stageIndex: number): number {
  return Math.pow(1.05, stageIndex + STAGE_INDEX_OFFSET)
}

/**
 * Score for destroying one enemy tank.
 *   base 100 * difficulty * level * AI
 */
export function killScore(
  difficultyKey: string,
  aiLevel: IntelligenceLevel | undefined,
  stageIndex: number,
): number {
  const diff = DIFFICULTY_SCORE_FACTOR[difficultyKey] ?? 1.0
  const ai = aiLevel ? (AI_SCORE_FACTOR[aiLevel] ?? 1.0) : 1.0
  const raw = KILL_BASE_SCORE * diff * levelFactor(stageIndex) * ai
  return Math.round(raw)
}

/** Score awarded for clearing the stage at the given (0-based) index. */
export function stageClearScore(stageIndex: number): number {
  return Math.round(1000 * levelFactor(stageIndex))
}
