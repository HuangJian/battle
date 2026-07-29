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

// Baseline re-captured 2026-07-29 for P2 anti-camp zone fix + nav-stuck
// fallback + predictive firing.
// P2 changes on top of P1:
// - Anti-camp zone tracking (±1 cell instead of exact cell) — fixes
//   deadlock where player oscillates between two cells at TANK/CELL
//   boundary, resetting camp cell each time, preventing anti-camp escape.
// - Nav-stuck fallback: A* to center → try directions toward center →
//   any open direction (instead of directMove which re-selects enemy).
// - Predictive firing (lead the target): pure check (no RNG) for enemy
//   crossing — fires preemptively when enemy moving perpendicular will
//   cross bullet path at the right time.
// Seed 2: gameover→stage_clear (anti-camp fix). Seed 7: clear→gameover
// (RNG perturbation). Seed 42: lives 5→2. Net: Stage 0 85%→86.7%,
// Stage 1 95%→100%, Stage 3 50%→66.7%.
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 5056,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  2: {
    outcome: 'stage_clear',
    ticks: 6930,
    score: 4700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  7: {
    outcome: 'gameover',
    ticks: 6919,
    score: 700,
    lives: 3,
    killCount: 7,
    baseAlive: false,
    playerLevel: 0,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2577,
    score: 4700,
    lives: 2,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  100: {
    outcome: 'stage_clear',
    ticks: 5622,
    score: 4700,
    lives: 4,
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
    ticks: 3665,
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
