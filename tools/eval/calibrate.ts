#!/usr/bin/env bun
/**
 * calibrate.ts — Evaluation baseline calibration tool (plan §3.3B / Phase 3a.4).
 *
 * Runs 35 classic stages with the fixed AI baseline and reverse-fits the
 * evaluation thresholds and weights from the observed metric distributions.
 *
 * The calibration process:
 * 1. Run all classic stages × N seeds × difficulties (using God AI)
 * 2. Collect per-metric distributions (KPM, bullet density, threat rate, etc.)
 * 3. Reverse-fit: P25→target low, P75→target high for each metric
 * 4. Output evaluation-baseline.json
 *
 * Usage:
 *   bun tools/eval/calibrate.ts --seeds 10 --difficulty hard
 *   bun tools/eval/calibrate.ts --seeds 100 --difficulty all --pretty
 */

import { STAGES } from '../../src/config/stages'
import { runSimulation } from '../sim/simulation-runner'
import { evaluate, DEFAULT_BASELINE, type BaselineConfig } from './evaluator'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'

import { arg } from '../lib/cli'
// ============================================================
// Types
// ============================================================

export interface StageCalibrationMetrics {
  stageId: number
  stageName: string
  difficulty: string
  metrics: Record<string, number>
  passRate: number
  avgPlayTimeMs: number
  totalRuns: number
}

export interface CalibrationResult {
  /** The reverse-fitted baseline with P25/P75 target ranges. */
  baseline: BaselineConfig
  /** Per-stage metric averages for analysis. */
  stageMetrics: StageCalibrationMetrics[]
  /** Global metric distributions (all stages pooled). */
  globalDistributions: Record<string, { p25: number; p50: number; p75: number; mean: number }>
  timestamp: string
}

export interface CalibrationOptions {
  seeds: number[]
  difficulties: string[]
  maxTicks?: number
  baseline?: BaselineConfig
  sampleInterval?: number
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

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

// ============================================================
// Calibration runner
// ============================================================

/**
 * Run the evaluation calibration pipeline.
 *
 * For each difficulty and stage, runs N seed simulations and collects
 * metric distributions. Then reverse-fits the baseline thresholds from
 * the observed P25/P75 ranges.
 */
export function runEvaluationCalibration(opts: CalibrationOptions): CalibrationResult {
  const baseline = opts.baseline ?? DEFAULT_BASELINE
  const maxTicks = opts.maxTicks ?? 36000
  const sampleInterval = opts.sampleInterval ?? 60
  const stageMetrics: StageCalibrationMetrics[] = []
  const allMetricValues: Record<string, number[]> = {}

  for (const diff of opts.difficulties) {
    for (let si = 0; si < STAGES.length; si++) {
      const stage = STAGES[si]
      const metricAccum: Record<string, number[]> = {}
      let passCount = 0
      let totalPlayTime = 0

      for (const seed of opts.seeds) {
        const result = runSimulation({
          seed,
          stage,
          difficulty: diff,
          godAIParams: DEFAULT_GOD_AI_PARAMS,
          maxTicks,
          sampleInterval,
        })

        const report = evaluate(result, stage, baseline)
        if (report.pass) passCount++
        totalPlayTime += result.finalState.playTimeMs

        for (const [k, v] of Object.entries(report.details)) {
          if (!metricAccum[k]) metricAccum[k] = []
          metricAccum[k].push(v.value)
          if (!allMetricValues[k]) allMetricValues[k] = []
          allMetricValues[k].push(v.value)
        }
      }

      const totalRuns = opts.seeds.length
      stageMetrics.push({
        stageId: stage.id,
        stageName: stage.name,
        difficulty: diff,
        metrics: Object.fromEntries(
          Object.entries(metricAccum).map(([k, v]) => [k, round2(mean(v))]),
        ),
        passRate: totalRuns > 0 ? passCount / totalRuns : 0,
        avgPlayTimeMs: totalRuns > 0 ? totalPlayTime / totalRuns : 0,
        totalRuns,
      })

      process.stderr.write(`\r  [calibrate] ${diff} stage ${si + 1}/${STAGES.length}...`)
    }
  }
  process.stderr.write('\n')

  // Build global distributions
  const globalDistributions: Record<
    string,
    { p25: number; p50: number; p75: number; mean: number }
  > = {}
  for (const [k, vals] of Object.entries(allMetricValues)) {
    const sorted = [...vals].sort((a, b) => a - b)
    globalDistributions[k] = {
      p25: round2(percentile(sorted, 0.25)),
      p50: round2(percentile(sorted, 0.5)),
      p75: round2(percentile(sorted, 0.75)),
      mean: round2(mean(vals)),
    }
  }

  // Reverse-fit baseline: P25 → target low, P75 → target high
  const fit = (key: string): [number, number] => {
    const dist = globalDistributions[key]
    if (!dist) return [0, 0]
    return [dist.p25, dist.p75]
  }

  const calibratedBaseline: BaselineConfig = {
    ...baseline,
    soft: {
      ...baseline.soft,
      kpm: { target: fit('kpm'), weight: baseline.soft.kpm.weight },
      bulletDensity: {
        target: fit('bulletDensity'),
        weight: baseline.soft.bulletDensity.weight,
      },
      threatRate: {
        target: fit('threatRate'),
        weight: baseline.soft.threatRate.weight,
      },
      formationVar: {
        target: fit('formationVar'),
        weight: baseline.soft.formationVar.weight,
      },
      killDiversity: {
        target: [Math.round(fit('killDiversity')[0]), Math.round(fit('killDiversity')[1])],
        weight: baseline.soft.killDiversity.weight,
      },
      terrainUtil: {
        target: fit('terrainUtil'),
        weight: baseline.soft.terrainUtil.weight,
      },
      noDeadZones: baseline.soft.noDeadZones,
    },
  }

  return {
    baseline: calibratedBaseline,
    stageMetrics,
    globalDistributions,
    timestamp: new Date().toISOString(),
  }
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  const seedCount = parseInt(arg('seeds', '10')!, 10)
  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const diffSpec = arg('difficulty', 'hard')!
  const difficulties = diffSpec === 'all' ? ['hard', 'chaos'] : [diffSpec]
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const pretty = process.argv.includes('--pretty')
  const outputFile = arg('output', 'evaluation-baseline.json')!

  const result = runEvaluationCalibration({
    seeds,
    difficulties,
    maxTicks,
  })

  // Print global distributions to stderr
  process.stderr.write('\n=== Global Metric Distributions ===\n')
  for (const [k, d] of Object.entries(result.globalDistributions)) {
    process.stderr.write(`  ${k}: P25=${d.p25} P50=${d.p50} P75=${d.p75} mean=${d.mean}\n`)
  }

  const json = pretty ? JSON.stringify(result, null, 2) : JSON.stringify(result)
  await Bun.write(outputFile, json)
  process.stderr.write(`[calibrate] Wrote evaluation baseline to ${outputFile}\n`)

  console.log(json)
}
