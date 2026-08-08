#!/usr/bin/env bun
/**
 * level-sim.ts — Headless level simulation CLI.
 *
 * Runs one or more God-AI-driven simulations of a stage and outputs a JSON report.
 *
 * Usage:
 *   bun tools/optimize/level-sim.ts --stage 1 --difficulty hard --seed 123
 *   bun tools/optimize/level-sim.ts --stage 1 --difficulty chaos --seed 42 --max-ticks 18000
 *   bun tools/optimize/level-sim.ts --stage 1 --size 10 --save-replays
 *   bun tools/optimize/level-sim.ts --stage 1 --size 5 --save-replays --replay-failures-only
 *   bun tools/optimize/level-sim.ts --stage 1 --difficulty hard --coop
 *   bun tools/optimize/level-sim.ts --stage 1 --difficulty hard --dual --save-replays
 *
 * Options:
 *   --stage <number>         Stage number in STAGES, 1-based (default: 1)
 *   --difficulty <key>       Difficulty key: classic|relax|hard|chaos (default: hard)
 *   --seed <number>          Starting RNG seed; incremented by 1 per run (default: 1)
 *   --random                 Use a fresh random seed for each run (overrides the
 *                            sequential default; --seed is ignored when --random)
 *   --size <n>               Number of serial simulations to run (default: 1)
 *   --max-ticks <n>          Max simulation ticks (default: 36000 = 10 min)
 *   --eval                   Also run the evaluator and include the report
 *   --pretty                 Pretty-print JSON output
 *   --output                 Output JSON to stdout (default: true; --no-output to suppress)
 *   --save-replays           Save replay files to replays/ directory
 *   --replay-failures-only   Only save replays for failed games (requires --save-replays)
 *   --coop                   Enable coop mode (God AI controls player2, human idle)
 *   --dual                   Enable dual-player supervise mode (God AI controls both P1 and
 *                            P2; the on-screen "督战" x2 path). Mutually exclusive with --coop;
 *                            if both are passed, --dual wins. Replay file embeds both P1 and
 *                            P2 input streams (flagged hasP2) so it replays identically.
 */
import { STAGES } from '../../src/config/stages'
import { runSimulation } from '../sim/simulation-runner'
import { evaluate, DEFAULT_BASELINE } from '../eval/evaluator'
import { writeReplayFile } from '../sim/replay-writer'

// ---- Parse args ----
function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

const stageIdx = parseInt(arg('stage', '1')!, 10) - 1 // CLI is 1-based (1..35)
const difficulty = arg('difficulty', 'hard')!
const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
const doEval = process.argv.includes('--eval')
const pretty = process.argv.includes('--pretty')
const saveReplays = process.argv.includes('--save-replays')
const replayFailuresOnly = process.argv.includes('--replay-failures-only')
const doOutput = !process.argv.includes('--no-output')
const dual = process.argv.includes('--dual')
const coop = process.argv.includes('--coop') && !dual

// --seed: default starts at 1 and increments per run; --random overrides to random seeds
const randomSeeds = process.argv.includes('--random')
const seedArg = arg('seed')
const seed = seedArg !== undefined ? parseInt(seedArg, 10) : 1

// --size: number of serial runs
const size = parseInt(arg('size', '1')!, 10)

// ---- Load stage ----
const stage = STAGES[stageIdx]
if (!stage) {
  console.error(`Stage ${stageIdx + 1} not found (1..${STAGES.length})`)
  process.exit(1)
}

// ---- Helpers ----
function countEvents(events: import('../../src/types').GameEvent[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const e of events) {
    counts[e.type] = (counts[e.type] ?? 0) + 1
  }
  return counts
}

// ---- Run simulations ----
const results: object[] = []
let winCount = 0
let totalGames = 0

for (let i = 0; i < size; i++) {
  const gameSeed = randomSeeds
    ? (Math.random() * 0xffffffff) >>> 0
    : size === 1
      ? seed
      : (seed + i) >>> 0
  totalGames++

  const result = runSimulation({
    seed: gameSeed,
    stage,
    difficulty,
    stageIndex: stageIdx,
    maxTicks,
    sampleInterval: 6, // sample every 100ms for compact output
    record: saveReplays,
    coop,
    spectateDual: dual,
  })

  const isWin = result.outcome === 'stage_clear'
  if (isWin) winCount++

  // ---- Optionally evaluate ----
  const report = doEval ? evaluate(result, stage, DEFAULT_BASELINE) : undefined

  // ---- Output per game ----
  const output = {
    stage: { id: stage.id, name: stage.name, index: stageIdx + 1 },
    difficulty,
    coop,
    dual,
    seed: gameSeed,
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
                (result.metrics.reduce((s, m) => s + m.enemyCount, 0) / result.metrics.length) *
                  100,
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

  if (size === 1) {
    // Single game: output directly
    if (doOutput) {
      if (pretty) {
        console.log(JSON.stringify(output, null, 2))
      } else {
        console.log(JSON.stringify(output))
      }
    }
  } else {
    // Multi-game: collect for summary
    results.push(output)
  }

  // ---- Write replay file if requested ----
  if (saveReplays && result.replay) {
    const shouldWrite = !replayFailuresOnly || !isWin
    if (shouldWrite) {
      const path = await writeReplayFile({
        result,
        dir: 'replays',
        stageIndex: stageIdx,
        stageName: stage.name,
      })
      if (path) console.error(`[replay] wrote ${path}`)
    }
  }
}

// ---- Multi-game summary ----
if (size > 1) {
  const winRate = Math.round((winCount / totalGames) * 1000) / 10
  const summary = {
    stage: { id: stage.id, name: stage.name, index: stageIdx + 1 },
    difficulty,
    coop,
    dual,
    seed: randomSeeds ? 'random' : seed,
    size,
    winCount,
    totalGames,
    winRate: `${winRate}%`,
    results,
  }

  if (doOutput) {
    if (pretty) {
      console.log(JSON.stringify(summary, null, 2))
    } else {
      console.log(JSON.stringify(summary))
    }
  }
}
