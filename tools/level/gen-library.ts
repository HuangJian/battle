#!/usr/bin/env bun
/**
 * gen-library.ts — Generated stage library pipeline (plan §6.3 / Phase 3c).
 *
 * This is the "filtering → library" glue that was missing. It:
 * 1. Generates N stages via the procedural generator
 * 2. Simulates each with the God AI across multiple seeds
 * 3. Evaluates each against the baseline
 * 4. Filters: keeps only stages that PASS
 * 5. Writes the filtered `StageData[]` to `generated-stages.json`
 *
 * Usage:
 *   bun tools/level/gen-library.ts --count 30 --difficulty hard --seeds 3 --output generated-stages.json
 *   bun tools/level/gen-library.ts --count 50 --difficulty chaos --theme mixed --seeds 5 --pretty
 */

import { generateStages, validateStage, type Theme } from './level-gen'
import { runSimulation } from '../sim/simulation-runner'
import { evaluate, DEFAULT_BASELINE, type BaselineConfig } from '../eval/evaluator'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import type { StageData } from '../../src/types'

import { arg } from '../lib/cli'
// ============================================================
// Types
// ============================================================

export interface GenLibraryOptions {
  count: number
  difficulty: string
  theme?: Theme
  seeds: number[]
  maxTicks?: number
  sampleInterval?: number
  baseline?: BaselineConfig
  /** Minimum pass rate across seeds to include a stage (default: 0.5). */
  minPassRate?: number
  baseSeed?: number
}

export interface StageEvaluation {
  stage: StageData
  valid: boolean
  passRate: number
  avgKillCount: number
  avgPlayTimeMs: number
  evalReports: Array<{ seed: number; pass: boolean; totalScore: number }>
}

export interface GenLibraryResult {
  totalGenerated: number
  totalValid: number
  totalPassed: number
  stages: StageData[]
  evaluations: StageEvaluation[]
}

// ============================================================
// Pipeline
// ============================================================

/**
 * Run the full generate → simulate → evaluate → filter pipeline.
 * Returns the passing stages and their evaluation details.
 */
export function generateLibrary(opts: GenLibraryOptions): GenLibraryResult {
  const {
    count,
    difficulty,
    theme,
    seeds,
    maxTicks = 36000,
    sampleInterval = 6,
    baseline = DEFAULT_BASELINE,
    minPassRate = 0.5,
    baseSeed = 1,
  } = opts

  // 1. Generate stages
  process.stderr.write(`[gen-library] Generating ${count} stages...\n`)
  const stages = generateStages(count, difficulty, theme, baseSeed)

  const evaluations: StageEvaluation[] = []
  let validCount = 0
  let passCount = 0

  // 2. Validate + simulate + evaluate each stage
  for (let i = 0; i < stages.length; i++) {
    const stage = stages[i]
    const validation = validateStage(stage)
    const valid = validation.valid

    if (!valid) {
      evaluations.push({
        stage,
        valid: false,
        passRate: 0,
        avgKillCount: 0,
        avgPlayTimeMs: 0,
        evalReports: [],
      })
      process.stderr.write(`\r  [gen-library] ${i + 1}/${count}: INVALID (${validation.errors[0]})`)
      continue
    }
    validCount++

    // Simulate across seeds
    let passCount = 0
    let totalKills = 0
    let totalPlayTime = 0
    const evalReports: Array<{ seed: number; pass: boolean; totalScore: number }> = []

    for (const seed of seeds) {
      const result = runSimulation({
        seed,
        stage,
        difficulty,
        godAIParams: DEFAULT_GOD_AI_PARAMS,
        maxTicks,
        sampleInterval,
      })

      const report = evaluate(result, stage, baseline)
      evalReports.push({ seed, pass: report.pass, totalScore: report.totalScore })
      if (report.pass) passCount++
      totalKills += result.finalState.killCount
      totalPlayTime += result.finalState.playTimeMs
    }

    const passRate = seeds.length > 0 ? passCount / seeds.length : 0

    evaluations.push({
      stage,
      valid: true,
      passRate,
      avgKillCount: seeds.length > 0 ? totalKills / seeds.length : 0,
      avgPlayTimeMs: seeds.length > 0 ? totalPlayTime / seeds.length : 0,
      evalReports,
    })

    const status = passRate >= minPassRate ? 'PASS' : 'FAIL'
    process.stderr.write(
      `\r  [gen-library] ${i + 1}/${count}: ${status} (${(passRate * 100).toFixed(0)}% pass rate)   `,
    )
  }
  process.stderr.write('\n')

  // 3. Filter: keep only stages that pass at the minimum rate
  const passedStages = evaluations
    .filter((e) => e.valid && e.passRate >= minPassRate)
    .map((e) => e.stage)
  passCount = passedStages.length

  process.stderr.write(
    `[gen-library] ${validCount}/${count} valid, ${passCount}/${count} passed (${((passCount / count) * 100).toFixed(0)}% yield)\n`,
  )

  return {
    totalGenerated: count,
    totalValid: validCount,
    totalPassed: passCount,
    stages: passedStages,
    evaluations,
  }
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  const count = parseInt(arg('count', '20')!, 10)
  const difficulty = arg('difficulty', 'hard')!
  const theme = (arg('theme', 'mixed') ?? 'mixed') as Theme
  const seedCount = parseInt(arg('seeds', '3')!, 10)
  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const minPassRate = parseFloat(arg('min-pass-rate', '0.5')!)
  const baseSeed = parseInt(arg('base-seed', '1')!, 10)
  const pretty = process.argv.includes('--pretty')
  const outputFile = arg('output', 'generated-stages.json')!

  const result = generateLibrary({
    count,
    difficulty,
    theme,
    seeds,
    maxTicks,
    minPassRate,
    baseSeed,
  })

  // Write the filtered stages to the output file
  const json = JSON.stringify(result.stages, null, pretty ? 2 : 0)
  await Bun.write(outputFile, json)
  process.stderr.write(`[gen-library] Wrote ${result.stages.length} stages to ${outputFile}\n`)

  // Print summary to stdout
  const summary = {
    totalGenerated: result.totalGenerated,
    totalValid: result.totalValid,
    totalPassed: result.totalPassed,
    yieldRate: result.totalGenerated > 0 ? result.totalPassed / result.totalGenerated : 0,
    stages: result.evaluations.map((e) => ({
      name: e.stage.name,
      valid: e.valid,
      passRate: e.passRate,
      avgKills: e.avgKillCount,
      avgPlayTimeMs: e.avgPlayTimeMs,
    })),
  }
  console.log(pretty ? JSON.stringify(summary, null, 2) : JSON.stringify(summary))
}
