import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Split Parity Test (plan/God-AI-Curriculum §0.5)
//
// The God AI "God class" (src/ai/GodAIInput.ts, ~1537 lines) was
// extracted into functional sub-modules (src/ai/god/*) during the
// §0.5 split. This is a PURE REFACTOR — runtime behavior must be
// identical to the pre-split single-class implementation.
//
// The baseline below was captured BEFORE the split (from commit
// 28683be's single-class GodAIInput.ts) and re-verified to match the
// refactored version exactly across 8 diverse seeds (outcomes span
// gameover / max_ticks / stage_clear). Any future behavior change in
// the AI core will break this test, catching regressions the moment
// they land.
//
// Run: bun test tests/godai-split-parity.test.ts
// ============================================================

interface Expected {
  outcome: 'stage_clear' | 'gameover' | 'max_ticks'
  ticks: number
  score: number
  lives: number
  killCount: number
  baseAlive: boolean
  playerLevel: number
}

const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'gameover',
    ticks: 9309,
    score: 1500,
    lives: 3,
    killCount: 15,
    baseAlive: false,
    playerLevel: 0,
  },
  2: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 2000,
    lives: 3,
    killCount: 18,
    baseAlive: true,
    playerLevel: 0,
  },
  7: {
    outcome: 'stage_clear',
    ticks: 2845,
    score: 4700,
    lives: 1,
    killCount: 20,
    baseAlive: true,
    playerLevel: 2,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2727,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  100: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 0,
    lives: 3,
    killCount: 0,
    baseAlive: true,
    playerLevel: 0,
  },
  999: {
    outcome: 'gameover',
    ticks: 5809,
    score: 300,
    lives: 3,
    killCount: 3,
    baseAlive: false,
    playerLevel: 0,
  },
  12345: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 600,
    lives: 3,
    killCount: 6,
    baseAlive: true,
    playerLevel: 0,
  },
  55555: {
    outcome: 'gameover',
    ticks: 2237,
    score: 800,
    lives: 2,
    killCount: 8,
    baseAlive: false,
    playerLevel: 0,
  },
}

describe('god-ai-split-parity', () => {
  for (const [seedStr, expected] of Object.entries(BASELINE)) {
    const seed = Number(seedStr)
    it(`seed=${seed} reproduces pre-split baseline exactly`, () => {
      const result = runSimulation({
        seed,
        stage: STAGES[0],
        difficulty: 'classic',
        maxTicks: 36000,
        sampleInterval: 36000, // we don't need per-frame metrics here
      })

      expect(result.outcome).toBe(expected.outcome)
      expect(result.ticks).toBe(expected.ticks)
      expect(result.finalState.score).toBe(expected.score)
      expect(result.finalState.lives).toBe(expected.lives)
      expect(result.finalState.killCount).toBe(expected.killCount)
      expect(result.finalState.baseAlive).toBe(expected.baseAlive)
      expect(result.finalState.playerLevel).toBe(expected.playerLevel)
    }, 30000)
  }
})
