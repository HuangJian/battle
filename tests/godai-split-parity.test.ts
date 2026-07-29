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

// Baseline re-captured 2026-07-29 for P1 survival/defense fixes.
// P1 changes on top of P0:
// - Dodge detection widened from CELL*0.75 to TANK (more bullets detected).
// - baseUnderThreat widened to row>=18; !canHunt gate removed (always defend).
// - Power-ups and T2a skipped when base is under threat.
// Seeds 1, 7, 12345 improved (more lives / faster clear). Seeds 999, 55555
// regressed to timeout (RNG perturbation from wider dodge). Net: Stage 0
// 70%→87.5%, Stage 1 87.5%→92.5%, Stage 1 gameovers 2→0.
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 5227,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  2: {
    outcome: 'gameover',
    ticks: 4379,
    score: 300,
    lives: 3,
    killCount: 3,
    baseAlive: false,
    playerLevel: 0,
  },
  7: {
    outcome: 'stage_clear',
    ticks: 5242,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2420,
    score: 4700,
    lives: 5,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  100: {
    outcome: 'stage_clear',
    ticks: 6321,
    score: 4700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  999: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 3400,
    lives: 3,
    killCount: 18,
    baseAlive: true,
    playerLevel: 1,
  },
  12345: {
    outcome: 'stage_clear',
    ticks: 3638,
    score: 4700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  55555: {
    outcome: 'max_ticks',
    ticks: 36000,
    score: 3300,
    lives: 3,
    killCount: 17,
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
