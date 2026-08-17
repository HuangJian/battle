// God-AI SCORE gate — Bun Worker pool edition.
//
// Runs all 35 stages × 3 difficulties (classic/hard/chaos) × 10 seeds = 1050
// headless `runSimulation` calls (with telemetry on), fans them out across cores
// via the pool in `score-gate-core.ts`, scores every run with godai-score v7,
// and asserts:
//   * per-stage floors (truth mean − MARGIN_SCORE) for every stage/difficulty, and
//   * aggregate floors (truth mean − AGG_MARGIN_SCORE) per difficulty.
//
// §233 (2026-08-17): seeds 20 → 10 to bring the full suite under 20s; truth
// re-captured at 10 seeds, margins widened to ~2 SE (0.05→0.07 / 0.03→0.04).
//
// This guards GOD AI *behavior logic*, not just the pass rate: a regression that
// keeps a stage clearing but wrecks how it clears (turtling, no kills, maxed
// clear-time) still drops the v7 composite below its floor. Per Phase III this
// is the primary guard; the clear-count gate (god-ai-gate.test.ts) is disabled.

import { expect, test } from 'bun:test'
import {
  runGodAIScoreGate,
  STAGE_COUNT,
  TRUTH_SCORES,
  AGGREGATE_FLOOR_SCORE,
  MARGIN_SCORE,
} from './score-gate-core'

const DIFFS = ['classic', 'hard', 'chaos'] as const

test(
  'God-AI score gate (classic+hard+chaos, godai-score v7, worker pool)',
  async () => {
    const t0 = Date.now()
    const scores = await runGodAIScoreGate([...DIFFS], STAGE_COUNT)
    const wall = ((Date.now() - t0) / 1000).toFixed(1)
    console.log(`[godai-score-gate] pool finished in ${wall}s`)

    for (const d of DIFFS) {
      const truth = TRUTH_SCORES[d]
      // (1) per-stage floors
      const perStageFailures: string[] = []
      let total = 0
      for (let i = 0; i < STAGE_COUNT; i++) {
        const got = scores.get(`${d}:${i}`) ?? 0
        total += got
        const floor = Math.max(0, truth[i] - MARGIN_SCORE)
        if (got < floor) {
          perStageFailures.push(
            `  ${d} S${i + 1}: mean score ${got.toFixed(3)} / 1.0 < floor ${floor.toFixed(3)} (truth ${truth[i].toFixed(3)})`,
          )
        }
      }
      // (2) aggregate floor (mean of per-stage means — `total` is already the
      // sum of per-stage *mean* scores, so divide by STAGE_COUNT, not by n)
      const aggFloor = AGGREGATE_FLOOR_SCORE[d]
      const aggMean = total / STAGE_COUNT
      expect(
        perStageFailures,
        `\n${d} per-stage score failures:\n${perStageFailures.join('\n')}`,
      ).toEqual([])
      expect(
        aggMean,
        `${d} aggregate mean score ${aggMean.toFixed(3)} / 1.0 < floor ${aggFloor.toFixed(3)}`,
      ).toBeGreaterThanOrEqual(aggFloor)
      console.log(
        `[godai-score-gate] ${d}: aggregate mean score ${aggMean.toFixed(3)} / 1.0 (floor ${aggFloor.toFixed(3)})`,
      )
    }
  },
  { timeout: 300000 },
)
