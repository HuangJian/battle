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
// baseline below was re-locked after the CMA-ES v3 param tuning
// (2026-07-28) so it pins the current refactored behavior under the v3
// params. Any future accidental behavior change in the god/* sub-modules
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

// Baseline updated for CMA-ES v3 params (2026-07-28). The v3 optimizer
// found a kill-centric strategy that trades slightly lower kills on some
// seeds for 100% base survival (was 5/8 base alive with old params).
// Key changes: seeds 1, 999, 55555 no longer lose the base (gameover →
// max_ticks), seed 7 clears with more lives saved (1→4).
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 1900,
    lives: 3,
    killCount: 17,
    baseAlive: true,
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
    ticks: 3376,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2499,
    score: 4700,
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
    outcome: 'max_ticks',
    ticks: 36000,
    score: 1200,
    lives: 3,
    killCount: 12,
    baseAlive: true,
    playerLevel: 0,
  },
  12345: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 200,
    lives: 3,
    killCount: 2,
    baseAlive: true,
    playerLevel: 0,
  },
  55555: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 1200,
    lives: 2,
    killCount: 7,
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
