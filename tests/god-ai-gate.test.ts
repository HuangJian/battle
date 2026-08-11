// Unified God-AI regression gate — Bun Worker pool edition.
//
// Runs all 35 stages × 3 difficulties (classic/hard/chaos) × 20 seeds = 2100
// headless `runSimulation` calls, fanned out across cores via the pool in
// `gate-core.ts`. Asserts both:
//   * per-stage floors (truth − MARGIN_WINS wins) for every stage/difficulty, and
//   * aggregate floors (truth mean − 3.7pp at n=700) per difficulty.
//
// This replaces the old 14-file `--parallel` split (which was SLOWER, 48.75s,
// than the single-file baseline ~33s) with true in-process parallelism. The
// sims are deterministic per seed, so a passing run reproduces the same win
// counts every time; a regression drops a stage/difficulty below its floor.

import { expect, test } from 'bun:test'
import {
  runGodAIGate,
  GATE_SEEDS,
  STAGE_COUNT,
  TRUTH,
  AGGREGATE_FLOOR,
  MARGIN_WINS,
} from './gate-core'

const DIFFS = ['classic', 'hard', 'chaos'] as const

test(
  'God-AI regression gate (classic+hard+chaos, worker pool)',
  async () => {
    const t0 = Date.now()
    const wins = await runGodAIGate([...DIFFS], STAGE_COUNT)
    const wall = ((Date.now() - t0) / 1000).toFixed(1)
    console.log(`[god-ai-gate] pool finished in ${wall}s`)

    const n = STAGE_COUNT * GATE_SEEDS.length

    for (const d of DIFFS) {
      const truth = TRUTH[d]
      // (1) per-stage floors
      const perStageFailures: string[] = []
      let total = 0
      for (let i = 0; i < STAGE_COUNT; i++) {
        const got = wins.get(`${d}:${i}`) ?? 0
        total += got
        const floor = Math.max(0, truth[i] - MARGIN_WINS)
        if (got < floor) {
          perStageFailures.push(
            `  ${d} S${i + 1}: ${got}/${GATE_SEEDS.length} wins < floor ${floor} (truth ${truth[i]})`,
          )
        }
      }
      // (2) aggregate floor
      const aggFloor = AGGREGATE_FLOOR[d]
      expect(
        perStageFailures,
        `\n${d} per-stage failures:\n${perStageFailures.join('\n')}`,
      ).toEqual([])
      expect(
        total,
        `${d} aggregate ${total}/${n} < floor ${aggFloor} (${((total / n) * 100).toFixed(1)}% vs ${((aggFloor / n) * 100).toFixed(1)}%)`,
      ).toBeGreaterThanOrEqual(aggFloor)
      console.log(
        `[god-ai-gate] ${d}: ${total}/${n} (${((total / n) * 100).toFixed(1)}%), floor ${aggFloor}`,
      )
    }
  },
  { timeout: 180000 },
)
