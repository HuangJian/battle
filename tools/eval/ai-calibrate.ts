#!/usr/bin/env bun
/**
 * ai-calibrate.ts — AI joint calibration tool (plan §3.3 / Phase 3a.3).
 *
 * Runs God AI and Skilled Human proxy on classic stages to establish the
 * "standard challenge strength" baseline. The calibration has three steps:
 *
 * A. God AI baseline — run 35 stages × N seeds × difficulties
 * B. Enemy AI fallback — (manual analysis if God AI underperforms)
 * C. Skilled Human proxy — verify God AI isn't calibrated against weak enemies
 *
 * Output: ai-baseline.json with fixed AI parameters and pass rates.
 *
 * Usage:
 *   bun tools/eval/ai-calibrate.ts --seeds 10 --difficulty hard
 *   bun tools/eval/ai-calibrate.ts --seeds 100 --difficulty all --pretty
 */

import { STAGES } from '../../src/config/stages'
import { EVAL_DIFFICULTY_KEYS } from '../../src/config/difficulty'
import { runSimulation } from '../sim/simulation-runner'
import {
  DEFAULT_GOD_AI_PARAMS,
  SKILLED_HUMAN_PARAMS,
  type GodAIParams,
} from '../../src/ai/GodAIInput'
import type { StageData } from '../../src/types'

import { arg } from '../lib/cli'
// ============================================================
// Types
// ============================================================

export interface DifficultyResult {
  totalRuns: number
  passRate: number
  outcomes: Record<string, number>
  avgPlayTimeMs: number
  avgLivesRemaining: number
  avgKills: number
}

export interface AICalibrationResult {
  godAI: {
    params: GodAIParams
    perDifficulty: Record<string, DifficultyResult>
  }
  skilledHuman: {
    params: GodAIParams
    perDifficulty: Record<string, DifficultyResult>
  }
  /** Acceptance gates (plan §3.3A/§3.3C). */
  gates: {
    godAIHardPass: boolean // ≥70%
    godAIChaosPass: boolean // ≥30%
    skilledHumanHardPass: boolean // ≥50%
    skilledHumanChaosPass: boolean // ≥15%
  }
  timestamp: string
}

export interface CalibrationOptions {
  seeds: number[]
  difficulties: string[]
  maxTicks?: number
  stages?: StageData[]
  /** Sparse sampling for calibration (default: 60 = every 1s). */
  sampleInterval?: number
}

// ============================================================
// Calibration runner
// ============================================================

function runDifficulty(
  stages: StageData[],
  seeds: number[],
  difficulty: string,
  params: GodAIParams,
  maxTicks: number,
  sampleInterval: number,
): DifficultyResult {
  const outcomes: Record<string, number> = {}
  let stageClearCount = 0
  let totalPlayTime = 0
  let totalLives = 0
  let totalKills = 0
  let totalRuns = 0

  for (const stage of stages) {
    for (const seed of seeds) {
      const result = runSimulation({
        seed,
        stage,
        difficulty,
        godAIParams: params,
        maxTicks,
        sampleInterval,
      })

      outcomes[result.outcome] = (outcomes[result.outcome] ?? 0) + 1
      if (result.outcome === 'stage_clear') stageClearCount++
      totalPlayTime += result.finalState.playTimeMs
      totalLives += result.finalState.lives
      totalKills += result.finalState.killCount
      totalRuns++
    }
  }

  return {
    totalRuns,
    passRate: totalRuns > 0 ? stageClearCount / totalRuns : 0,
    outcomes,
    avgPlayTimeMs: totalRuns > 0 ? totalPlayTime / totalRuns : 0,
    avgLivesRemaining: totalRuns > 0 ? totalLives / totalRuns : 0,
    avgKills: totalRuns > 0 ? totalKills / totalRuns : 0,
  }
}

/**
 * Run the full AI calibration pipeline.
 *
 * For each difficulty, runs:
 * 1. God AI (perfect player + injected imperfections)
 * 2. Skilled Human proxy (God AI + double reaction delay + 20% aim error)
 *
 * Checks acceptance gates:
 * - God AI: Hard ≥70%, Chaos ≥30%
 * - Skilled Human: Hard ≥50%, Chaos ≥15%
 */
export function runAICalibration(opts: CalibrationOptions): AICalibrationResult {
  const stages = opts.stages ?? STAGES
  const maxTicks = opts.maxTicks ?? 36000
  const sampleInterval = opts.sampleInterval ?? 60
  const difficulties = opts.difficulties

  const godAIResults: Record<string, DifficultyResult> = {}
  const skilledHumanResults: Record<string, DifficultyResult> = {}

  for (const diff of difficulties) {
    const totalRuns = stages.length * opts.seeds.length
    process.stderr.write(`[ai-calibrate] God AI — ${diff} (${totalRuns} runs)...\n`)
    godAIResults[diff] = runDifficulty(
      stages,
      opts.seeds,
      diff,
      DEFAULT_GOD_AI_PARAMS,
      maxTicks,
      sampleInterval,
    )

    process.stderr.write(`[ai-calibrate] Skilled Human — ${diff} (${totalRuns} runs)...\n`)
    skilledHumanResults[diff] = runDifficulty(
      stages,
      opts.seeds,
      diff,
      SKILLED_HUMAN_PARAMS,
      maxTicks,
      sampleInterval,
    )
  }

  // Check acceptance gates
  const godHard = godAIResults['hard']?.passRate ?? 0
  const godChaos = godAIResults['chaos']?.passRate ?? 0
  const humanHard = skilledHumanResults['hard']?.passRate ?? 0
  const humanChaos = skilledHumanResults['chaos']?.passRate ?? 0

  return {
    godAI: {
      params: DEFAULT_GOD_AI_PARAMS,
      perDifficulty: godAIResults,
    },
    skilledHuman: {
      params: SKILLED_HUMAN_PARAMS,
      perDifficulty: skilledHumanResults,
    },
    gates: {
      godAIHardPass: godHard >= 0.7,
      godAIChaosPass: godChaos >= 0.3,
      skilledHumanHardPass: humanHard >= 0.5,
      skilledHumanChaosPass: humanChaos >= 0.15,
    },
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
  const difficulties = diffSpec === 'all' ? [...EVAL_DIFFICULTY_KEYS] : [diffSpec]
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const pretty = process.argv.includes('--pretty')
  const outputFile = arg('output', 'ai-baseline.json')!

  const result = runAICalibration({
    seeds,
    difficulties,
    maxTicks,
  })

  // Print gate summary to stderr
  process.stderr.write('\n=== AI Calibration Gates ===\n')
  process.stderr.write(
    `God AI Hard ≥70%:     ${result.gates.godAIHardPass ? 'PASS' : 'FAIL'} (${((result.godAI.perDifficulty['hard']?.passRate ?? 0) * 100).toFixed(1)}%)\n`,
  )
  process.stderr.write(
    `God AI Chaos ≥30%:    ${result.gates.godAIChaosPass ? 'PASS' : 'FAIL'} (${((result.godAI.perDifficulty['chaos']?.passRate ?? 0) * 100).toFixed(1)}%)\n`,
  )
  process.stderr.write(
    `Skilled Hard ≥50%:    ${result.gates.skilledHumanHardPass ? 'PASS' : 'FAIL'} (${((result.skilledHuman.perDifficulty['hard']?.passRate ?? 0) * 100).toFixed(1)}%)\n`,
  )
  process.stderr.write(
    `Skilled Chaos ≥15%:   ${result.gates.skilledHumanChaosPass ? 'PASS' : 'FAIL'} (${((result.skilledHuman.perDifficulty['chaos']?.passRate ?? 0) * 100).toFixed(1)}%)\n`,
  )

  const json = pretty ? JSON.stringify(result, null, 2) : JSON.stringify(result)
  await Bun.write(outputFile, json)
  process.stderr.write(`[ai-calibrate] Wrote AI baseline to ${outputFile}\n`)

  console.log(json)
}
