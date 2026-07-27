#!/usr/bin/env bun
/**
 * level-sim.ts — Headless level simulation CLI.
 *
 * Runs a single God-AI-driven simulation of a stage and outputs a JSON report.
 *
 * Usage:
 *   bun tools/level-sim.ts --stage 0 --difficulty hard --seed 123
 *   bun tools/level-sim.ts --stage 0 --difficulty chaos --seed 42 --max-ticks 18000
 *
 * Options:
 *   --stage <index>     Stage index in STAGES (default: 0)
 *   --difficulty <key>  Difficulty key: classic|relax|hard|chaos (default: hard)
 *   --seed <number>     RNG seed (default: 1)
 *   --max-ticks <n>     Max simulation ticks (default: 36000 = 10 min)
 *   --eval              Also run the evaluator and include the report
 *   --pretty            Pretty-print JSON output
 */
import { STAGES } from '../src/config/stages'
import { runSimulation } from './simulation-runner'
import { evaluate, DEFAULT_BASELINE } from './evaluator'

// ---- Parse args ----
function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

const stageIdx = parseInt(arg('stage', '0')!, 10)
const difficulty = arg('difficulty', 'hard')!
const seed = parseInt(arg('seed', '1')!, 10)
const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
const doEval = process.argv.includes('--eval')
const pretty = process.argv.includes('--pretty')

// ---- Load stage ----
const stage = STAGES[stageIdx]
if (!stage) {
  console.error(`Stage ${stageIdx} not found (0..${STAGES.length - 1})`)
  process.exit(1)
}

// ---- Run simulation ----
const result = runSimulation({
  seed,
  stage,
  difficulty,
  maxTicks,
  sampleInterval: 6, // sample every 100ms for compact output
})

// ---- Optionally evaluate ----
const report = doEval ? evaluate(result, stage, DEFAULT_BASELINE) : undefined

// ---- Output ----
const output = {
  stage: { id: stage.id, name: stage.name, index: stageIdx },
  difficulty,
  seed,
  result: {
    outcome: result.outcome,
    ticks: result.ticks,
    wallMs: Math.round(result.wallMs * 100) / 100,
    finalState: result.finalState,
    metricsSummary: {
      avgBullets:
        result.metrics.length > 0
          ? Math.round(
              (result.metrics.reduce((s, m) => s + m.bullets, 0) / result.metrics.length) * 100,
            ) / 100
          : 0,
      avgEnemyCount:
        result.metrics.length > 0
          ? Math.round(
              (result.metrics.reduce((s, m) => s + m.enemyCount, 0) / result.metrics.length) * 100,
            ) / 100
          : 0,
    },
    eventCounts: countEvents(result.events),
    failure: result.failure,
  },
  evaluation: report
    ? {
        hardPass: report.hardPass,
        softScore: Math.round(report.softScore * 100) / 100,
        totalScore: Math.round(report.totalScore * 100) / 100,
        pass: report.pass,
        details: Object.fromEntries(
          Object.entries(report.details).map(([k, v]) => [
            k,
            {
              value: Math.round(v.value * 100) / 100,
              normalized: Math.round(v.normalized * 100) / 100,
              weight: v.weight,
            },
          ]),
        ),
      }
    : undefined,
}

if (pretty) {
  console.log(JSON.stringify(output, null, 2))
} else {
  console.log(JSON.stringify(output))
}

// ---- Helpers ----
function countEvents(events: import('../src/types').GameEvent[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const e of events) {
    counts[e.type] = (counts[e.type] ?? 0) + 1
  }
  return counts
}
