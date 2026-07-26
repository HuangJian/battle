import type { DifficultyConfig } from '../types'

/**
 * Difficulty presets.
 *
 * IMPORTANT: difficulty must NOT scale enemy combat power (armor / speed /
 * bullet speed / HP). Those dimensions are fixed per-tank-kind and only vary by
 * the Combat Capability System. Difficulty changes difficulty *only* by making
 * the same enemies smarter, through `DIFFICULTY_AI` (dodge / prediction /
 * reaction / aggression / commander chance) in src/ai/config.ts.
 *
 * The only remaining per-difficulty levers here are player-side resources:
 *   - `startLives`      : how many lives the player gets.
 *   - `playerStartLevel`: the player's starting star level (0 = unbuffed).
 *   - `eliteChance`     : probability (0-1) that a spawned enemy is elite.
 *
 * Adding a new preset = adding one entry here.
 */
export const DIFFICULTIES: Record<string, DifficultyConfig> = {
  relax: {
    name: 'Relax',
    startLives: 5,
    playerStartLevel: 1,
    eliteChance: 0.05,
  },
  classic: {
    name: 'Classic',
    startLives: 3,
    playerStartLevel: 0,
    eliteChance: 0.0,
  },
  hard: {
    name: 'Hard',
    startLives: 2,
    playerStartLevel: 0,
    eliteChance: 0.12,
  },
  chaos: {
    name: 'Chaos',
    startLives: 1,
    playerStartLevel: 0,
    eliteChance: 0.25,
  },
}

export const DIFFICULTY_KEYS = Object.keys(DIFFICULTIES)
