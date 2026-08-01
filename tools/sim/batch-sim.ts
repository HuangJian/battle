#!/usr/bin/env bun
/**
 * batch-sim.ts — Batch simulation runner.
 *
 * Runs multiple simulations across stages and seeds, collecting results
 * for aggregation and analysis (plan/Automated-Level-Design §Phase 3a.1).
 *
 * Usage:
 *   bun tools/sim/batch-sim.ts --stages 0-4 --difficulty hard --seeds 1-5 --eval
 *   bun tools/sim/batch-sim.ts --generated --count 10 --difficulty hard --seeds 1-3 --eval
 *   bun tools/sim/batch-sim.ts --stages all --difficulty chaos --seeds 1-10 --eval --pretty
 */

import { STAGES } from '../../src/config/stages'
import { runSimulation, type SimResult, type RunOptions } from './simulation-runner'
import {
  evaluate,
  type EvaluationReport,
  type BaselineConfig,
  DEFAULT_BASELINE,
} from '../eval/evaluator'
import { generateStages, type Theme } from '../level/level-gen'
import { writeReplayFile } from './replay-writer'
import type { StageData } from '../../src/types'

// ============================================================
// Types
// ============================================================

export interface BatchOptions {
  stages: StageData[]
  stageNames: string[]
  difficulty: string
  seeds: number[]
  godAIParams?: RunOptions['godAIParams']
  maxTicks?: number
  sampleInterval?: number
  evaluate?: boolean
  baseline?: BaselineConfig
  /** Record replays. 'all' records every run; 'failures' records only non-clear runs. */
  replay?: 'all' | 'failures'
  replayDir?: string
}

export interface BatchResult {
  stageIndex: number
  stageName: string
  seed: number
  simResult: SimResult
  evalReport?: EvaluationReport
}

export interface BatchSummary {
  totalRuns: number
  outcomes: Record<string, number>
  passRate: number
  avgPlayTimeMs: number
  p50PlayTimeMs: number
  p90PlayTimeMs: number
  avgScore: number
  avgKills: number
  avgLivesRemaining: number
  metricStats: Record<string, MetricStat>
}

export interface MetricStat {
  mean: number
  min: number
  max: number
  p25: number
  p50: number
  p75: number
}

// ============================================================
// Statistics helpers
// ============================================================

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0
  const idx = Math.min(Math.floor(sorted.length * p), sorted.length - 1)
  return sorted[idx]
}

function mean(arr: number[]): number {
  if (arr.length === 0) return 0
  return arr.reduce((a, b) => a + b, 0) / arr.length
}

// ============================================================
// Batch runner
// ============================================================

/**
 * Run simulations across multiple stages and seeds.
 * Sequential execution — each run is synchronous (~100-500ms).
 * Progress is reported to stderr.
 */
export function batchRun(opts: BatchOptions): BatchResult[] {
  const results: BatchResult[] = []
  const total = opts.stages.length * opts.seeds.length
  let done = 0

  for (let si = 0; si < opts.stages.length; si++) {
    const stage = opts.stages[si]
    const name = opts.stageNames[si] ?? `Stage ${si}`
    for (const seed of opts.seeds) {
      const simResult = runSimulation({
        seed,
        stage,
        difficulty: opts.difficulty,
        godAIParams: opts.godAIParams,
        maxTicks: opts.maxTicks,
        sampleInterval: opts.sampleInterval ?? 6,
        record: !!opts.replay,
      })

      let evalReport: EvaluationReport | undefined
      if (opts.evaluate) {
        evalReport = evaluate(simResult, stage, opts.baseline ?? DEFAULT_BASELINE)
      }

      results.push({
        stageIndex: si,
        stageName: name,
        seed,
        simResult,
        evalReport,
      })

      done++
      if (done % 10 === 0 || done === total) {
        process.stderr.write(`\r  [batch-sim] ${done}/${total} runs...`)
      }
    }
  }
  if (total > 0) process.stderr.write('\n')

  return results
}

/**
 * Summarize batch results into aggregate statistics.
 */
export function summarize(results: BatchResult[]): BatchSummary {
  const total = results.length
  if (total === 0) {
    return {
      totalRuns: 0,
      outcomes: {},
      passRate: 0,
      avgPlayTimeMs: 0,
      p50PlayTimeMs: 0,
      p90PlayTimeMs: 0,
      avgScore: 0,
      avgKills: 0,
      avgLivesRemaining: 0,
      metricStats: {},
    }
  }

  const outcomes: Record<string, number> = {}
  let passCount = 0
  let hasEval = false
  const playTimes: number[] = []
  const scores: number[] = []
  const kills: number[] = []
  const lives: number[] = []
  const metricValues: Record<string, number[]> = {}

  for (const r of results) {
    const outcome = r.simResult.outcome
    outcomes[outcome] = (outcomes[outcome] ?? 0) + 1

    if (r.evalReport) {
      hasEval = true
      if (r.evalReport.pass) passCount++
      for (const [k, v] of Object.entries(r.evalReport.details)) {
        if (!metricValues[k]) metricValues[k] = []
        metricValues[k].push(v.value)
      }
    }

    playTimes.push(r.simResult.finalState.playTimeMs)
    scores.push(r.simResult.finalState.score)
    kills.push(r.simResult.finalState.killCount)
    lives.push(r.simResult.finalState.lives)
  }

  playTimes.sort((a, b) => a - b)
  scores.sort((a, b) => a - b)
  kills.sort((a, b) => a - b)
  lives.sort((a, b) => a - b)

  const metricStats: Record<string, MetricStat> = {}
  for (const [k, vals] of Object.entries(metricValues)) {
    vals.sort((a, b) => a - b)
    metricStats[k] = {
      mean: mean(vals),
      min: vals[0],
      max: vals[vals.length - 1],
      p25: percentile(vals, 0.25),
      p50: percentile(vals, 0.5),
      p75: percentile(vals, 0.75),
    }
  }

  return {
    totalRuns: total,
    outcomes,
    passRate: hasEval ? passCount / total : 0,
    avgPlayTimeMs: mean(playTimes),
    p50PlayTimeMs: percentile(playTimes, 0.5),
    p90PlayTimeMs: percentile(playTimes, 0.9),
    avgScore: mean(scores),
    avgKills: mean(kills),
    avgLivesRemaining: mean(lives),
    metricStats,
  }
}

// ============================================================
// Seed range parser
// ============================================================

export function parseSeeds(spec: string): number[] {
  if (spec.includes('-')) {
    const [start, end] = spec.split('-').map(Number)
    return Array.from({ length: end - start + 1 }, (_, i) => start + i)
  }
  return [parseInt(spec, 10)]
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  function arg(name: string, fallback?: string): string | undefined {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }

  const difficulty = arg('difficulty', 'hard')!
  const seedSpec = arg('seeds', '1')!
  const seeds = parseSeeds(seedSpec)
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const doEval = process.argv.includes('--eval')
  const pretty = process.argv.includes('--pretty')
  const useGenerated = process.argv.includes('--generated')
  const genCount = parseInt(arg('count', '10')!, 10)
  const genTheme = (arg('theme', 'mixed') ?? 'mixed') as Theme
  const inputFile = arg('input')
  const outputFile = arg('output')

  let stages: StageData[]
  let stageNames: string[]

  if (inputFile) {
    const raw = await Bun.file(inputFile).text()
    stages = JSON.parse(raw) as StageData[]
    stageNames = stages.map((s) => s.name)
    process.stderr.write(`[batch-sim] Loaded ${stages.length} stages from ${inputFile}\n`)
  } else if (useGenerated) {
    stages = generateStages(genCount, difficulty, genTheme, 1)
    stageNames = stages.map((s) => s.name)
  } else {
    const stageSpec = arg('stages', '0')!
    if (stageSpec === 'all') {
      stages = STAGES
      stageNames = STAGES.map((s) => s.name)
    } else if (stageSpec.includes('-')) {
      const [start, end] = stageSpec.split('-').map(Number)
      stages = STAGES.slice(start, end + 1)
      stageNames = stages.map((s) => s.name)
    } else {
      const idx = parseInt(stageSpec, 10)
      stages = [STAGES[idx]]
      stageNames = [STAGES[idx].name]
    }
  }

  process.stderr.write(
    `[batch-sim] ${stages.length} stages × ${seeds.length} seeds = ${stages.length * seeds.length} runs\n`,
  )

  const replayMode = process.argv.includes('--replay-failures')
    ? ('failures' as const)
    : process.argv.includes('--replay')
      ? ('all' as const)
      : undefined
  const replayDir = arg('replay-dir') ?? 'replays'

  const results = batchRun({
    stages,
    stageNames,
    difficulty,
    seeds,
    maxTicks,
    evaluate: doEval,
    replay: replayMode,
    replayDir,
  })

  // Write replay files for non-clear outcomes (if --replay-failures)
  if (replayMode) {
    for (const r of results) {
      const shouldWrite = replayMode === 'all' || r.simResult.outcome !== 'stage_clear'
      if (shouldWrite && r.simResult.replay) {
        await writeReplayFile({
          result: r.simResult,
          dir: replayDir,
          stageIndex: r.stageIndex,
          stageName: r.stageName,
        })
      }
    }
  }

  const summary = summarize(results)

  const output = {
    config: {
      difficulty,
      seeds,
      stageCount: stages.length,
      maxTicks,
      evaluate: doEval,
    },
    summary,
    results: pretty
      ? results.map((r) => ({
          stage: r.stageName,
          seed: r.seed,
          outcome: r.simResult.outcome,
          ticks: r.simResult.ticks,
          playTimeMs: r.simResult.finalState.playTimeMs,
          score: r.simResult.finalState.score,
          kills: r.simResult.finalState.killCount,
          lives: r.simResult.finalState.lives,
          pass: r.evalReport?.pass,
          totalScore: r.evalReport?.totalScore,
        }))
      : undefined,
  }

  if (outputFile) {
    const fullOutput = {
      ...output,
      results: results.map((r) => ({
        stage: r.stageName,
        seed: r.seed,
        outcome: r.simResult.outcome,
        ticks: r.simResult.ticks,
        playTimeMs: r.simResult.finalState.playTimeMs,
        score: r.simResult.finalState.score,
        kills: r.simResult.finalState.killCount,
        lives: r.simResult.finalState.lives,
        pass: r.evalReport?.pass,
        totalScore: r.evalReport?.totalScore,
      })),
    }
    await Bun.write(outputFile, JSON.stringify(fullOutput, null, pretty ? 2 : 0))
    process.stderr.write(`[batch-sim] Wrote results to ${outputFile}\n`)
  }
  console.log(pretty ? JSON.stringify(output, null, 2) : JSON.stringify(output))
}
