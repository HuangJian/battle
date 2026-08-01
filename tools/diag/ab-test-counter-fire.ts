#!/usr/bin/env bun
/**
 * ab-test-counter-fire.ts — A/B test for §49-revisit 炮口相向对枪抵消 (§52 v2).
 *
 * Compares counterFire=0 (plain T2a, pre-§52) vs counterFire=1 (current
 * shipped behavior: facing-enemy counter-fire + keep-alignment) across a
 * stage × N seeds, reporting win rate, failure breakdown and paired flips.
 * Uses the full stage-adapted params in both arms (only counterFire differs).
 *
 * Usage:
 *   bun tools/diag/ab-test-counter-fire.ts --stage 32 --seeds 60
 *   bun tools/diag/ab-test-counter-fire.ts --all --seeds 60
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { applyStageOverrides } from '../../src/ai/godai-stage-overrides'
import { runSimulation } from '../sim/simulation-runner'
import type { StageData } from '../../src/types'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

const stageIdx = parseInt(arg('stage', '32')!, 10)
const seedCount = parseInt(arg('seeds', '20')!, 10)
const seedStart = parseInt(arg('seedStart', '1')!, 10)
const runAll = process.argv.includes('--all')
const maxTicks = 18000

interface ArmResult {
  wins: number
  baseDestroyed: number
  livesExhausted: number
  timeout: number
  outcomes: number[]
}

function runArm(stage: StageData, counterFire: number, seeds: number): ArmResult {
  const params = applyStageOverrides(stage.name, { ...DEFAULT_GOD_AI_PARAMS })
  params.counterFire = counterFire

  const result: ArmResult = {
    wins: 0,
    baseDestroyed: 0,
    livesExhausted: 0,
    timeout: 0,
    outcomes: [],
  }
  for (let seed = seedStart; seed < seedStart + seeds; seed++) {
    const r = runSimulation({
      seed,
      stage,
      difficulty: 'classic',
      godAIParams: params,
      maxTicks,
      skipStageOverrides: true,
    })
    const won = r.outcome === 'stage_clear' ? 1 : 0
    result.outcomes.push(won)
    if (won) result.wins++
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
  console.log(`Comparing counterFire OFF (plain T2a) vs ON (对枪抵消, current)\n`)
  let totalOff = 0
  let totalOn = 0
  let flippedToWin = 0
  let flippedToLoss = 0
  for (let idx = 0; idx < STAGES.length; idx++) {
    const stage = STAGES[idx]
    const off = runArm(stage, 0, seedCount)
    const on = runArm(stage, 1, seedCount)
    totalOff += off.wins
    totalOn += on.wins

    for (let s = 0; s < seedCount; s++) {
      if (off.outcomes[s] === 0 && on.outcomes[s] === 1) flippedToWin++
      if (off.outcomes[s] === 1 && on.outcomes[s] === 0) flippedToLoss++
    }

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
  console.log(
    `Paired flips: ${flippedToWin} OFF→ON wins, ${flippedToLoss} ON→OFF losses (net ${flippedToWin - flippedToLoss})`,
  )
} else {
  const stage = STAGES[stageIdx]
  console.log(`S${stageIdx} ${stage.name} × ${seedCount} seeds, classic, ${maxTicks}t`)
  console.log(`A/B: counterFire OFF (plain T2a) vs ON (对枪抵消)\n`)

  const off = runArm(stage, 0, seedCount)
  const on = runArm(stage, 1, seedCount)

  printResult('OFF', off, seedCount)
  printResult(' ON', on, seedCount)
  const delta = (((on.wins - off.wins) / seedCount) * 100).toFixed(1)
  console.log(`\nDelta: ${on.wins > off.wins ? '+' : ''}${delta}pp`)

  let flippedToWin = 0
  let flippedToLoss = 0
  const winSeeds: number[] = []
  const lossSeeds: number[] = []
  for (let s = 0; s < seedCount; s++) {
    const seed = seedStart + s
    if (off.outcomes[s] === 0 && on.outcomes[s] === 1) {
      flippedToWin++
      winSeeds.push(seed)
    }
    if (off.outcomes[s] === 1 && on.outcomes[s] === 0) {
      flippedToLoss++
      lossSeeds.push(seed)
    }
  }
  console.log(`\nPaired flips: ${flippedToWin} OFF→ON wins, ${flippedToLoss} ON→OFF losses`)
  if (winSeeds.length > 0) console.log(`  New wins (seeds): ${winSeeds.join(', ')}`)
  if (lossSeeds.length > 0) console.log(`  New losses (seeds): ${lossSeeds.join(', ')}`)
}
