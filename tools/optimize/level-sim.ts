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
 *   --size <n>               Number of simulations to run (default: 1).
 *                            When N > 1, each seed runs in an isolated child process
 *                            (subprocess isolation, plan/batch-sim-shared-state-hardening.md T2)
 *                            to guarantee no cross-run shared-state contamination.
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

import { arg } from '../lib/cli'
// ---- Parse args ----

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

// --size: number of runs
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

// ============================================================
// size > 1: subprocess isolation (plan/batch-sim-shared-state-hardening.md T2)
// ============================================================
//
// Each seed runs in a fresh child process (`bun level-sim.ts --seed S --size 1 ...`),
// guaranteeing a clean module graph per run. This makes batch sweeps immune to
// any future cross-run shared-state contamination — the ultimate insurance
// policy (DECISIONS §178 "Harness bug" root cause was shared singleton leakage
// in the in-process loop; this design makes it structurally impossible).
//
// Concurrency is capped at 8 to avoid spawning hundreds of processes at once.

if (size > 1) {
  // Build the base args forwarded to each child process.
  // Excluded: --pretty, --no-output (children must emit JSON for parent to parse),
  //           --random (parent generates the seeds), --size (forced to 1),
  //           --seed (set per child).
  const childArgs: string[] = [
    'bun',
    'tools/optimize/level-sim.ts',
    '--stage',
    String(stageIdx + 1),
    '--difficulty',
    difficulty,
    '--size',
    '1',
    '--max-ticks',
    String(maxTicks),
  ]
  if (doEval) childArgs.push('--eval')
  if (saveReplays) childArgs.push('--save-replays')
  if (replayFailuresOnly) childArgs.push('--replay-failures-only')
  if (coop) childArgs.push('--coop')
  if (dual) childArgs.push('--dual')

  // Generate the seed list (parent decides seeds; children always get --seed S).
  const seeds: number[] = []
  for (let i = 0; i < size; i++) {
    seeds.push(randomSeeds ? (Math.random() * 0xffffffff) >>> 0 : (seed + i) >>> 0)
  }

  const MAX_CONCURRENT = 8
  const childOutputs: object[] = Array.from({ length: size })
  let childWinCount = 0

  // Simple async pool: spawn up to MAX_CONCURRENT children at a time.
  let nextIdx = 0
  async function runChild(): Promise<void> {
    while (true) {
      const i = nextIdx++
      if (i >= size) break
      const childSeed = seeds[i]
      const args = [...childArgs, '--seed', String(childSeed)]
      const proc = Bun.spawn({
        cmd: args,
        stdout: 'pipe',
        stderr: 'inherit', // replay-write notices etc. pass through
      })
      const stdoutText = await new Response(proc.stdout).text()
      const exitCode = await proc.exited
      if (exitCode !== 0) {
        console.error(`[batch] seed ${childSeed} child exited ${exitCode}`)
        process.exit(1)
      }
      const parsed = JSON.parse(stdoutText)
      childOutputs[i] = parsed
      if (parsed.result?.outcome === 'stage_clear') childWinCount++
    }
  }

  const workerCount = Math.min(MAX_CONCURRENT, size)
  await Promise.all(Array.from({ length: workerCount }, () => runChild()))

  // ---- Aggregate summary (same output contract as before) ----
  const winRate = Math.round((childWinCount / size) * 1000) / 10
  const summary = {
    stage: { id: stage.id, name: stage.name, index: stageIdx + 1 },
    difficulty,
    coop,
    dual,
    seed: randomSeeds ? 'random' : seed,
    size,
    winCount: childWinCount,
    totalGames: size,
    winRate: `${winRate}%`,
    results: childOutputs,
  }

  if (doOutput) {
    if (pretty) {
      console.log(JSON.stringify(summary, null, 2))
    } else {
      console.log(JSON.stringify(summary))
    }
  }
} else {
  // ============================================================
  // size === 1: in-process single run (existing path)
  // ============================================================

  const gameSeed = seed
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

  if (doOutput) {
    if (pretty) {
      console.log(JSON.stringify(output, null, 2))
    } else {
      console.log(JSON.stringify(output))
    }
  }

  // ---- Write replay file if requested ----
  if (saveReplays && result.replay) {
    const isWin = result.outcome === 'stage_clear'
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
