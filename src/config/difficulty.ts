import type { DifficultyConfig } from '../types'

/**
 * Difficulty presets.
 * Adding a new preset = adding one entry here.
 */
export const DIFFICULTIES: Record<string, DifficultyConfig> = {
  relax: {
    name: 'Relax',
    enemySpeedMult: 0.7,
    enemyFireMult: 0.6,
    enemyHpMult: 0.5,
    startLives: 5,
    playerStartLevel: 1,
  },
  classic: {
    name: 'Classic',
    enemySpeedMult: 1.0,
    enemyFireMult: 1.0,
    enemyHpMult: 1.0,
    startLives: 3,
    playerStartLevel: 0,
  },
  hard: {
    name: 'Hard',
    enemySpeedMult: 1.3,
    enemyFireMult: 1.4,
    enemyHpMult: 1.5,
    startLives: 2,
    playerStartLevel: 0,
  },
  chaos: {
    name: 'Chaos',
    enemySpeedMult: 1.6,
    enemyFireMult: 1.8,
    enemyHpMult: 2.0,
    startLives: 1,
    playerStartLevel: 0,
  },
}

export const DIFFICULTY_KEYS = Object.keys(DIFFICULTIES)
