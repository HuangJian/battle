#!/usr/bin/env bun
/**
 * ab-test-smart-threat.ts — A/B test for the smart threat model.
 *
 * Runs a stage × N seeds with smartThreatModel ON vs OFF, reporting win rate
 * and failure breakdown. Uses the full stage override table (so all other
 * S32 params are active in both arms — only smartThreatModel differs).
 *
 * Usage:
 *   bun tools/ab-test-smart-threat.ts --stage 32 --seeds 120
 *   bun tools/ab-test-smart-threat.ts --stage 6 --seeds 60
 *   bun tools/ab-test-smart-threat.ts --all --seeds 60   # all 35 stages
 */
import { STAGES } from '../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { applyStageOverrides } from '../src/ai/godai-stage-overrides'
import { runSimulation } from './simulation-runner'
import type { StageData } from '../src/types'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

const stageIdx = parseInt(arg('stage', '32')!, 10)
const seedCount = parseInt(arg('seeds', '120')!, 10)
const seedStart = parseInt(arg('seedStart', '1')!, 10)
const runAll = arg('all', 'false') === 'true'
const maxTicks = 18000

interface ArmResult {
  wins: number
  baseDestroyed: number
  livesExhausted: number
  timeout: number
}

function runArm(stage: StageData, smartOn: boolean, seeds: number): ArmResult {
  // Build params: start from defaults, apply stage overrides, then
  // force smartThreatModel on/off for the respective arm.
  const params = applyStageOverrides(stage.name, { ...DEFAULT_GOD_AI_PARAMS })
  if (smartOn) {
    params.smartThreatModel = 1
  } else {
    params.smartThreatModel = 0
  }

  const result: ArmResult = { wins: 0, baseDestroyed: 0, livesExhausted: 0, timeout: 0 }
  for (let seed = seedStart; seed < seedStart + seeds; seed++) {
    const r = runSimulation({
      seed,
      stage,
      difficulty: 'classic',
      godAIParams: params,
      maxTicks,
      // We applied overrides manually above; skip the automatic apply.
      skipStageOverrides: true,
    })
    if (r.outcome === 'stage_clear') result.wins++
    else if (r.failure?.cause === 'base_destroyed') result.baseDestroyed++
    else if (r.failure?.cause === 'lives_exhausted') result.livesExhausted++
    else result.timeout++
  }
  return result
}

function printResult(label: string, r: ArmResult, seeds: number) {
  const pct = ((r.wins / seeds) * 100).toFixed(1).padStart(5)
  console.log(
    `  ${label}: ${r.wins}/${seeds} (${pct}%)  base=${r.baseDestroyed} lives=${r.livesExhausted} timeout=${r.timeout}`,
  )
}

if (runAll) {
  console.log(`All 35 stages × ${seedCount} seeds, classic, ${maxTicks}t`)
  console.log(`Comparing smartThreatModel OFF vs ON (override table active for both arms)\n`)
  let totalOff = 0
  let totalOn = 0
  for (let idx = 0; idx < STAGES.length; idx++) {
    const stage = STAGES[idx]
    const off = runArm(stage, false, seedCount)
    const on = runArm(stage, true, seedCount)
    totalOff += off.wins
    totalOn += on.wins

    const offPct = ((off.wins / seedCount) * 100).toFixed(0).padStart(3)
    const onPct = ((on.wins / seedCount) * 100).toFixed(0).padStart(3)
    const delta = (((on.wins - off.wins) / seedCount) * 100).toFixed(0)
    const deltaStr = on.wins > off.wins ? `+${delta}` : delta
    const flag =
      on.wins < off.wins - 2 ? '  <-- REGRESSION' : on.wins > off.wins + 2 ? '  <-- GAIN' : ''
    console.log(
      `S${idx} ${stage.name.padEnd(16)}: OFF ${offPct}%  ON ${onPct}%  (${deltaStr}pp)${flag}`,
    )
  }
  const meanOff = ((totalOff / (35 * seedCount)) * 100).toFixed(1)
  const meanOn = ((totalOn / (35 * seedCount)) * 100).toFixed(1)
  console.log(`\nMean: OFF ${meanOff}%  ON ${meanOn}%`)
} else {
  const stage = STAGES[stageIdx]
  console.log(`S${stageIdx} ${stage.name} × ${seedCount} seeds, classic, ${maxTicks}t`)
  console.log(`A/B: smartThreatModel OFF vs ON (all other override params active in both)\n`)

  const off = runArm(stage, false, seedCount)
  const on = runArm(stage, true, seedCount)

  printResult('OFF', off, seedCount)
  printResult(' ON', on, seedCount)
  const delta = (((on.wins - off.wins) / seedCount) * 100).toFixed(1)
  console.log(`\nDelta: ${on.wins > off.wins ? '+' : ''}${delta}pp`)
}
