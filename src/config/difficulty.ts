import type { DifficultyConfig } from '../types'

/**
 * Difficulty presets.
 *
 * IMPORTANT: difficulty must NOT scale enemy combat power (armor / speed /
 * bullet speed / HP). Those dimensions are fixed per-tank-kind and only vary by
 * the Combat Capability System. Difficulty changes difficulty *only* by making
 * the same enemies smarter, through the spawn-time AI tier DISTRIBUTION
 * (`DIFFICULTY_TIER_DISTRIBUTION`) in src/ai/config.ts.
 *
 * The only remaining per-difficulty levers here are player-side resources:
 *   - `startLives`      : how many lives the player gets.
 *   - `playerStartLevel`: the player's starting star level (0 = unbuffed).
 *
 * Adding a new preset = adding one entry here.
 */
export const DIFFICULTIES: Record<string, DifficultyConfig> = {
  relax: {
    name: 'Relax',
    startLives: 5,
    playerStartLevel: 1,
  },
  classic: {
    name: 'Classic',
    startLives: 3,
    playerStartLevel: 0,
  },
  hard: {
    name: 'Hard',
    startLives: 2,
    playerStartLevel: 0,
  },
  chaos: {
    name: 'Chaos',
    startLives: 1,
    playerStartLevel: 0,
  },
}

export const DIFFICULTY_KEYS = Object.keys(DIFFICULTIES)
