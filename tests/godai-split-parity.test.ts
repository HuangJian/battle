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

// Baseline re-captured 2026-07-29 for P0 T2a deadlock fix (plan/God-AI-Next-Round).
// P0.1: Anti-camp escape — campTimeoutTicks=90, antiCampSuppressTicks=60.
// P0.2: T2a camping threshold — only camp when scan.enemy==true.
// P0.3: Navigate stuck escape — navStuckTicks=180, roam to map center.
// These changes fix the T2a deadlock that caused 77.5% of games to timeout.
// Seeds 1, 100, 999, 55555 went from timeout to stage_clear; seed 2 and 12345
// went from timeout to gameover (aggressive movement exposes the player to
// more bullets — a known trade-off of fixing the deadlock).
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 5331,
    score: 4200,
    lives: 1,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
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
    ticks: 5620,
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
    outcome: 'stage_clear',
    ticks: 2448,
    score: 4700,
    lives: 2,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  12345: {
    outcome: 'gameover',
    ticks: 1235,
    score: 0,
    lives: 0,
    killCount: 0,
    baseAlive: true,
    playerLevel: 0,
  },
  55555: {
    outcome: 'stage_clear',
    ticks: 2802,
    score: 4700,
    lives: 2,
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
