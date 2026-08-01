#!/usr/bin/env bun
/**
 * ab-test-steel-occlusion.ts — A/B test for §48-revisit steel-only evasion
 * occlusion.
 *
 * Runs a stage × N seeds with evasionSteelOcclusion ON vs OFF, reporting win
 * rate and failure breakdown. Uses the full stage-adapted params (so all
 * other adaptations are active in both arms — only evasionSteelOcclusion
 * differs).
 *
 * Usage:
 *   bun tools/diag/ab-test-steel-occlusion.ts --stage 32 --seeds 60
 *   bun tools/diag/ab-test-steel-occlusion.ts --all --seeds 60
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
// Distance gate (cells) for the ON arm. 0 (default) = suppress all steel-blocked.
const range = parseInt(arg('range', '0')!, 10)
// ON-arm mode flags:
//   default:      evasionSteelOcclusion=1 (steel-only + pinned gate)
//   --trap:       evasionSteelOcclusion=1 AND trapAvoidance=1 (combined)
//   --trapOnly:   trapAvoidance=1 only (no steel occlusion)
//   --brickGate R: evasionSteelOcclusionBrickRatio=R (terrain-gated auto-
//                  enable via computeStageAdaptedParams — steel mazes only)
const trapMode = process.argv.includes('--trap')
const trapOnlyMode = process.argv.includes('--trapOnly')
const brickGate = parseFloat(arg('brickGate', '0')!)
const maxTicks = 18000

interface ArmConfig {
  steelOcclusion: number
  trapAvoidance: number
  brickGate: number
}

const OFF_CFG: ArmConfig = { steelOcclusion: 0, trapAvoidance: 0, brickGate: 0 }
const ON_CFG: ArmConfig = trapOnlyMode
  ? { steelOcclusion: 0, trapAvoidance: 1, brickGate: 0 }
  : {
      steelOcclusion: brickGate > 0 ? 0 : 1,
      trapAvoidance: trapMode ? 1 : 0,
      brickGate,
    }

interface ArmResult {
  wins: number
  baseDestroyed: number
  livesExhausted: number
  timeout: number
  // Per-seed outcome for paired comparison (1 = win, 0 = loss)
  outcomes: number[]
}

function runArm(stage: StageData, cfg: ArmConfig, seeds: number): ArmResult {
  const params = applyStageOverrides(stage.name, { ...DEFAULT_GOD_AI_PARAMS })
  params.evasionSteelOcclusion = cfg.steelOcclusion
  // Range applies whenever occlusion is active — either explicitly (ON_CFG
  // steelOcclusion=1) or via the terrain gate (--brickGate mode auto-enables
  // inside computeStageAdaptedParams, so the tool must pass range through).
  params.evasionSteelOcclusionRange = cfg.steelOcclusion || cfg.brickGate > 0 ? range : 0
  // Terrain-gated mode: leave evasionSteelOcclusion=0 and let
  // computeStageAdaptedParams (inside GodAIInput.reset) auto-enable it on
  // steel-maze stages (brickWallRatio < evasionSteelOcclusionBrickRatio).
  params.evasionSteelOcclusionBrickRatio = cfg.brickGate
  params.trapAvoidance = cfg.trapAvoidance

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
  console.log(`Comparing evasionSteelOcclusion OFF vs ON (stage-adapted params active)\n`)
  let totalOff = 0
  let totalOn = 0
  let flippedToWin = 0
  let flippedToLoss = 0
  for (let idx = 0; idx < STAGES.length; idx++) {
    const stage = STAGES[idx]
    const off = runArm(stage, OFF_CFG, seedCount)
    const on = runArm(stage, ON_CFG, seedCount)
    totalOff += off.wins
    totalOn += on.wins

    // Paired flip count
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
  console.log(`A/B: evasionSteelOcclusion OFF vs ON (stage-adapted params active)\n`)

  const off = runArm(stage, OFF_CFG, seedCount)
  const on = runArm(stage, ON_CFG, seedCount)

  printResult('OFF', off, seedCount)
  printResult(' ON', on, seedCount)
  const delta = (((on.wins - off.wins) / seedCount) * 100).toFixed(1)
  console.log(`\nDelta: ${on.wins > off.wins ? '+' : ''}${delta}pp`)

  // Paired flip details
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
