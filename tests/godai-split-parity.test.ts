import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../tools/simulation-runner'
import { STAGES } from '../src/config/stages'

// ============================================================
// God AI Behavior-Lock Test (plan/God-AI-Curriculum §0.5)
//
// During the §0.5 split, src/ai/GodAIInput.ts (~1537-line "God class")
// was extracted into functional sub-modules (src/ai/god/*). The split
// itself was proven behavior-preserving at commit 0d3275b: the
// refactored code was diffed against the pre-split single-class version
// (commit 28683be) across 8 seeds and produced byte-identical
// outcome/ticks/score/lives/kills/baseAlive/playerLevel.
//
// This test is the LIVING guard that resulted from that proof. The
// baseline below was re-locked after each behavior change.
// Any future accidental behavior change in the god/* sub-modules
// will break it, catching regressions the moment they land. If a change
// is intentional (e.g. further tuning), re-capture the baseline from the
// deterministic harness rather than deleting the test.
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

// Baseline re-captured 2026-07-29 for P3 CMA-ES optimized params.
// P3 changes:
// - A* dig-through-brick: findPath accepts { breakBrick: true }
// - followPath: returns direction when blocked by breakable brick
// - Nav-stuck center deadlock fix: chase enemy when at/near center
// - Power-up diversion: skip when enemies within 5 cells
// - CMA-ES multi-stage optimization (S0/S3/S6/S9, 6 seeds, 30 gen)
//   Key param changes: suboptimalPathProb 0.093→0.038, replanInterval 3→50,
//   powerupMaxDivertDistance 9→3, maxPlayerDistFromBase 14→19
// All 8 seeds now stage_clear (previously 2 were max_ticks, 1 was gameover).
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 4293,
    score: 4700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  2: {
    outcome: 'stage_clear',
    ticks: 3417,
    score: 4200,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  7: {
    outcome: 'stage_clear',
    ticks: 6723,
    score: 4200,
    lives: 5,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2887,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  100: {
    outcome: 'stage_clear',
    ticks: 5091,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  999: {
    outcome: 'stage_clear',
    ticks: 2973,
    score: 4700,
    lives: 2,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  12345: {
    outcome: 'stage_clear',
    ticks: 2892,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  55555: {
    outcome: 'stage_clear',
    ticks: 3155,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
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
