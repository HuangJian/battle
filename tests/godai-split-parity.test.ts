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

// Baseline re-captured 2026-07-29 after merging `main` into `god-ai` (HEAD
// b21baba). The god-ai split code itself (src/ai/god/*, GodAIInput.ts,
// tools/simulation-runner.ts) is UNCHANGED by that merge — the parity break was
// caused solely by `main`'s intentional speed/config tuning:
//   - src/config/speed.ts: BALANCED_ENEMY_CPS 2.5→3.75 (modern=classic FC speed),
//     PLAYER_SPEED_PER_STAR_CPS 0.125→0.25 ("Modern mode adjustment 2026-07-28")
//   - plus powerups/rules/Simulation changes on main (all validated by main's
//     own test suite; every other test still passes).
// This shifts the full-simulation baseline, which is exactly the case the test
// docstring anticipates: re-capture the baseline from the deterministic harness
// rather than deleting the test. The god-ai split remains behavior-preserving.
// NOTE: the split guard only certifies god/* behavior; it does NOT lock main's
// game params. Re-run tools/recapture-godai-baseline.ts after any future
// intentional tuning to refresh these numbers.
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 4299,
    score: 4700,
    lives: 5,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  2: {
    outcome: 'stage_clear',
    ticks: 2803,
    score: 3700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  7: {
    outcome: 'stage_clear',
    ticks: 4229,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2180,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  100: {
    outcome: 'stage_clear',
    ticks: 2367,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  999: {
    outcome: 'stage_clear',
    ticks: 2970,
    score: 4200,
    lives: 2,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  12345: {
    outcome: 'stage_clear',
    ticks: 2408,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  55555: {
    outcome: 'stage_clear',
    ticks: 3501,
    score: 3700,
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
