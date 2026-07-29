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

// Baseline re-locked 2026-07-29 after the P4 tuning campaign (rounds 1-7):
//   - DEFAULT_GOD_AI_PARAMS updated to the R7 CMA-ES optimum (GodAIInput.ts)
//   - two new behaviors added during P4: race-to-base check in
//     isBaseUnderThreat() and the outnumbered-retreat rule (StrategyPlanner)
//   - per-stage override table (godai-stage-overrides.ts) now applied by
//     tools/simulation-runner.ts; Stage 0 (Outpost) has no override, so this
//     baseline locks the pure R7 defaults.
// Re-locked 2026-07-29 (Round 5): added t2aMaxRange parameter (default 15 =
//   AIM_RANGE_CELLS). The new T2a condition `scan.enemy && scan.enemyDist
//   <= t2aMaxRange` skips camping at enemies beyond 15 cells in edge cases
//   where scanAhead and findEnemyDirection diverge on alignment. Seed 42
//   shifted (2355→2194 ticks, 3→4 lives) — a beneficial change. All other
//   seeds are byte-identical.
// All are intentional behavior changes — this is the documented re-capture
// path (run `bun tools/relock-parity.ts` and paste the output).
// NOTE: the split guard only certifies god/* behavior; it does NOT lock main's
// game params. Re-run tools/relock-parity.ts after any future intentional
// tuning to refresh these numbers.
const BASELINE: Record<number, Expected> = {
  1: {
    outcome: 'stage_clear',
    ticks: 2770,
    score: 4200,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  2: {
    outcome: 'stage_clear',
    ticks: 5195,
    score: 4700,
    lives: 2,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  7: {
    outcome: 'stage_clear',
    ticks: 3138,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  42: {
    outcome: 'stage_clear',
    ticks: 2194,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  100: {
    outcome: 'stage_clear',
    ticks: 3114,
    score: 4200,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  999: {
    outcome: 'stage_clear',
    ticks: 4274,
    score: 4700,
    lives: 4,
    killCount: 20,
    baseAlive: true,
    playerLevel: 1,
  },
  12345: {
    outcome: 'stage_clear',
    ticks: 2698,
    score: 4700,
    lives: 3,
    killCount: 20,
    baseAlive: true,
    playerLevel: 0,
  },
  55555: {
    outcome: 'stage_clear',
    ticks: 3247,
    score: 4200,
    lives: 3,
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
