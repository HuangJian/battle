#!/usr/bin/env bun
/**
 * play-generated.ts — Load and simulate generated stages for validation (plan §3.6).
 *
 * This tool provides the "runtime consumption" path for generated stages.
 * It loads `generated-stages.json` (or any JSON file containing `StageData[]`)
 * and runs headless simulations on each stage, printing a summary report.
 *
 * For interactive playtesting, the human can load these stages in the game
 * via `World.loadStageData(stage)` — this tool validates them headlessly first.
 *
 * Usage:
 *   bun tools/level/play-generated.ts --input generated-stages.json --difficulty hard --seeds 1-3
 *   bun tools/level/play-generated.ts --input generated-stages.json --difficulty classic --pretty
 */

import { runSimulation } from '../sim/simulation-runner'
import { evaluate, DEFAULT_BASELINE } from '../eval/evaluator'
import { validateStage } from './level-gen'
import { parseSeeds } from '../sim/batch-sim'
import type { StageData } from '../../src/types'

import { arg } from '../lib/cli'
// ============================================================
// CLI
// ============================================================

if (import.meta.main) {

  const inputFile = arg('input', 'generated-stages.json')!
  const difficulty = arg('difficulty', 'hard')!
  const seedSpec = arg('seeds', '1')!
  const seeds = parseSeeds(seedSpec)
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const pretty = process.argv.includes('--pretty')
  const doEval = process.argv.includes('--eval')

  // Load stages from JSON
  const raw = await Bun.file(inputFile).text()
  const stages = JSON.parse(raw) as StageData[]
  process.stderr.write(`[play-generated] Loaded ${stages.length} stages from ${inputFile}\n`)

  const results = stages.map((stage, i) => {
    // Validate the stage first
    const validation = validateStage(stage)
    if (!validation.valid) {
      return {
        index: i,
        name: stage.name,
        valid: false,
        errors: validation.errors,
        simResults: [],
      }
    }

    // Simulate across seeds
    const simResults = seeds.map((seed) => {
      const result = runSimulation({
        seed,
        stage,
        difficulty,
        maxTicks,
        sampleInterval: 6,
      })

      let evalReport: ReturnType<typeof evaluate> | undefined
      if (doEval) {
        evalReport = evaluate(result, stage, DEFAULT_BASELINE)
      }

      return {
        seed,
        outcome: result.outcome,
        ticks: result.ticks,
        playTimeMs: result.finalState.playTimeMs,
        score: result.finalState.score,
        kills: result.finalState.killCount,
        lives: result.finalState.lives,
        baseAlive: result.finalState.baseAlive,
        pass: evalReport?.pass,
        totalScore: evalReport?.totalScore,
      }
    })

    return {
      index: i,
      name: stage.name,
      valid: true,
      simResults,
    }
  })

  // Summary
  const validCount = results.filter((r) => r.valid).length
  const allSimResults = results.flatMap((r) => (r.valid ? r.simResults : []))
  const clearCount = allSimResults.filter((r) => r.outcome === 'stage_clear').length
  const totalRuns = allSimResults.length

  const summary = {
    inputFile,
    stageCount: stages.length,
    validStages: validCount,
    difficulty,
    seeds,
    totalRuns,
    stageClears: clearCount,
    clearRate: totalRuns > 0 ? clearCount / totalRuns : 0,
    avgKills: totalRuns > 0 ? allSimResults.reduce((s, r) => s + r.kills, 0) / totalRuns : 0,
    avgPlayTimeMs:
      totalRuns > 0 ? allSimResults.reduce((s, r) => s + r.playTimeMs, 0) / totalRuns : 0,
    results,
  }

  process.stderr.write(
    `[play-generated] ${clearCount}/${totalRuns} cleared (${(summary.clearRate * 100).toFixed(1)}%), avg ${summary.avgKills.toFixed(1)} kills, ${(summary.avgPlayTimeMs / 1000).toFixed(1)}s avg playtime\n`,
  )

  console.log(pretty ? JSON.stringify(summary, null, 2) : JSON.stringify(summary))
}
