#!/usr/bin/env bun
/**
 * recapture-score-truth.ts — re-capture tests/score-gate-core.ts TRUTH_SCORES.
 *
 * The score gate is deterministic (sims are a pure function of seed × stage ×
 * difficulty × shipped defaults), so its per-stage truth means must be
 * re-captured whenever a *default-moving* change lands (seed-set change §233,
 * code-review behavior fixes §276, knob retirement — e.g. the super-item OFF
 * default, AI-No-Items-Warmstart M0). This tool IS the documented re-capture
 * procedure (see the header comment on TRUTH_SCORES): it runs the gate's own
 * harness (runGodAIScoreGate → runSimulation + scoreRun, telemetry on, v7)
 * over all difficulties and prints TS literal arrays ready to paste into
 * `TRUTH_SCORES`, plus the old-vs-new delta table for the DECISIONS record.
 *
 * Usage:
 *   bun tools/diag/recapture-score-truth.ts                # full 3-difficulty recapture (~15s)
 *   bun tools/diag/recapture-score-truth.ts --diffs hard   # subset
 */
import { runGodAIScoreGate, STAGE_COUNT, TRUTH_SCORES } from '../../tests/score-gate-core'
import { EVAL_DIFFICULTY_KEYS } from '../../src/config/difficulty'

const diffArg = process.argv.find((a) => a.startsWith('--diffs'))
const diffs = diffArg ? (diffArg.split('=')[1] ?? 'hard').split(',') : [...EVAL_DIFFICULTY_KEYS]

const scores = await runGodAIScoreGate(diffs, STAGE_COUNT)

for (const d of diffs) {
  const next: number[] = []
  for (let i = 0; i < STAGE_COUNT; i++) next.push(scores.get(`${d}:${i}`) ?? 0)
  const prev = TRUTH_SCORES[d]
  console.log(`  ${d}: [`)
  for (let i = 0; i < STAGE_COUNT; i += 12) {
    console.log(
      '    ' +
        next
          .slice(i, i + 12)
          .map((v) => v.toFixed(4))
          .join(', ') +
        ',',
    )
  }
  console.log('  ],')
  if (prev) {
    const drops = next.map((v, i) => ({ i, d: v - prev[i] })).filter((x) => Math.abs(x.d) > 0.005)
    const mean = next.reduce((a, b) => a + b, 0) / STAGE_COUNT
    const pmean = prev.reduce((a, b) => a + b, 0) / STAGE_COUNT
    console.error(
      `# ${d}: aggregate ${pmean.toFixed(4)} -> ${mean.toFixed(4)} (${
        mean >= pmean ? '+' : ''
      }${(mean - pmean).toFixed(4)}); moved stages:` +
        drops.map((x) => ` S${x.i + 1} ${(x.d >= 0 ? '+' : '') + x.d.toFixed(3)}`).join(''),
    )
  }
}
