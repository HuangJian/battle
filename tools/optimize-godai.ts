#!/usr/bin/env bun
/**
 * optimize-godai.ts — CMA-ES optimizer for GodAIParams.
 *
 * Uses sep-CMA-ES (separable Covariance Matrix Adaptation Evolution
 * Strategy) to automatically optimize the God AI's parameters and
 * threshold constants. Each candidate is evaluated by running a batch
 * of headless simulations and computing a fitness score.
 *
 * After optimization, the best parameters are saved and detailed
 * decision traces are recorded for the best and worst candidates.
 *
 * Usage:
 *   bun tools/optimize-godai.ts --generations 30 --pop 12 --seeds 5
 *   bun tools/optimize-godai.ts --stage 0 --difficulty classic --generations 50
 */

import { STAGES } from '../src/config/stages'
import { runSimulation } from './simulation-runner'
import { traceSimulation, analyzeTrace, type DecisionTrace } from './decision-trace'
import { GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { StageData } from '../src/types'
import { writeFileSync, mkdirSync } from 'fs'
import { join } from 'path'

// ============================================================
// Search Space Definition
// ============================================================

interface ParamSpec {
  name: keyof GodAIParams
  min: number
  max: number
  isInteger: boolean
  /** Initial value (from DEFAULT_GOD_AI_PARAMS). */
  init: number
  /** Initial step size for CMA-ES (as fraction of range). */
  stepFrac: number
}

const SEARCH_SPACE: ParamSpec[] = [
  { name: 'reactionDelay', min: 0, max: 8, isInteger: true, init: 2, stepFrac: 0.25 },
  { name: 'aimError', min: 0, max: 0.15, isInteger: false, init: 0.02, stepFrac: 0.3 },
  { name: 'suboptimalPathProb', min: 0, max: 0.3, isInteger: false, init: 0.1, stepFrac: 0.3 },
  { name: 'defenseRowOffset', min: 1, max: 7, isInteger: true, init: 3, stepFrac: 0.25 },
  { name: 'defenseColSpread', min: 3, max: 13, isInteger: true, init: 8, stepFrac: 0.25 },
  { name: 'threatRangeCells', min: 8, max: 26, isInteger: true, init: 30, stepFrac: 0.2 },
  { name: 'maxPlayerDistFromBase', min: 4, max: 22, isInteger: true, init: 12, stepFrac: 0.25 },
  { name: 't8MaxInterceptDistCells', min: 2, max: 12, isInteger: true, init: 6, stepFrac: 0.3 },
  { name: 'baseWallScanRadius', min: 1, max: 5, isInteger: true, init: 3, stepFrac: 0.3 },
  { name: 'replanInterval', min: 3, max: 50, isInteger: true, init: 20, stepFrac: 0.25 },
  { name: 'powerupMaxDivertDistance', min: 3, max: 25, isInteger: true, init: 15, stepFrac: 0.25 },
  { name: 'endgameEnemyThreshold', min: 1, max: 5, isInteger: true, init: 2, stepFrac: 0.3 },
]

const DIM = SEARCH_SPACE.length

// ============================================================
// Parameter encoding/decoding
// ============================================================

/** Convert a GodAIParams object to a flat number array for CMA-ES. */
function paramsToVector(params: GodAIParams): number[] {
  return SEARCH_SPACE.map((s) => params[s.name])
}

/** Convert a flat number array back to a GodAIParams object, with clipping and rounding. */
function vectorToParams(vec: number[]): GodAIParams {
  const params = { ...DEFAULT_GOD_AI_PARAMS }
  for (let i = 0; i < DIM; i++) {
    const spec = SEARCH_SPACE[i]
    let val = vec[i]
    // Clip to bounds.
    val = Math.max(spec.min, Math.min(spec.max, val))
    // Round integers.
    if (spec.isInteger) val = Math.round(val)
    params[spec.name] = val
  }
  return params
}

// ============================================================
// Fitness Evaluation
// ============================================================

interface EvalConfig {
  stage: StageData
  difficulty: string
  seeds: number[]
  maxTicks: number
}

interface EvalResult {
  fitness: number
  winRate: number
  baseSurvivalRate: number
  avgKills: number
  avgTicks: number
  perSeed: Array<{
    seed: number
    outcome: string
    kills: number
    ticks: number
    baseAlive: boolean
  }>
}

/**
 * Evaluate a parameter set by running simulations.
 * Fitness = winRate * 1000 + baseSurvivalRate * 300 + avgKills * 20 + avgTicks/100
 */
function evaluateParams(params: GodAIParams, config: EvalConfig): EvalResult {
  const perSeed: EvalResult['perSeed'] = []
  let wins = 0
  let baseSurvived = 0
  let totalKills = 0
  let totalTicks = 0

  for (const seed of config.seeds) {
    try {
      const result = runSimulation({
        seed,
        stage: config.stage,
        difficulty: config.difficulty,
        godAIParams: params,
        maxTicks: config.maxTicks,
        sampleInterval: 60, // minimal sampling for speed
      })

      const won = result.outcome === 'stage_clear'
      if (won) wins++
      if (result.finalState.baseAlive) baseSurvived++
      totalKills += result.finalState.killCount
      totalTicks += result.ticks

      perSeed.push({
        seed,
        outcome: result.outcome,
        kills: result.finalState.killCount,
        ticks: result.ticks,
        baseAlive: result.finalState.baseAlive,
      })
    } catch (e) {
      // Parameter combination caused a runtime error (e.g. invalid pathfinding).
      // Penalize heavily — this candidate is invalid.
      perSeed.push({
        seed,
        outcome: 'error',
        kills: 0,
        ticks: 0,
        baseAlive: false,
      })
    }
  }

  const n = config.seeds.length
  const winRate = wins / n
  const baseSurvivalRate = baseSurvived / n
  const avgKills = totalKills / n
  const avgTicks = totalTicks / n

  const fitness =
    winRate * 1000 + baseSurvivalRate * 300 + avgKills * 20 + Math.min(avgTicks / 100, 50)

  return { fitness, winRate, baseSurvivalRate, avgKills, avgTicks, perSeed }
}

// ============================================================
// Sep-CMA-ES Implementation
// ============================================================
//
// Reference: Ros & Hansen (2008), "Simple Principles in Continuous
// Black-Box Optimization". This is the separable (diagonal) variant
// which only maintains per-dimension variances — efficient for 10-20
// dimensions with expensive evaluations.

interface CMAESState {
  mean: number[]
  sigma: number
  D: number[] // diagonal scales
  pc: number[] // evolution path for C
  ps: number[] // evolution path for sigma
  generation: number
  // Strategy parameters
  lambda: number
  mu: number
  weights: number[]
  mueff: number
  cs: number
  ds: number
  cc: number
  c1: number
  cmu: number
  chiN: number
  // History
  bestFitness: number
  bestVector: number[]
  stagnationCount: number
}

/** Box-Muller transform for standard normal random number. */
function randNormal(): number {
  let u = 0,
    v = 0
  while (u === 0) u = Math.random()
  while (v === 0) v = Math.random()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

function initCMAES(initialVector: number[]): CMAESState {
  const dim = initialVector.length
  const lambda = 4 + Math.floor(3 * Math.log(dim))
  const mu = Math.floor(lambda / 2)

  // Recombination weights (log-decreasing).
  const rawWeights: number[] = []
  for (let i = 0; i < mu; i++) {
    rawWeights.push(Math.log(mu + 1) - Math.log(i + 1))
  }
  const wSum = rawWeights.reduce((a, b) => a + b, 0)
  const weights = rawWeights.map((w) => w / wSum)

  // Effective mass.
  const wSqSum = weights.reduce((a, w) => a + w * w, 0)
  const mueff = 1 / wSqSum

  // Step-size control.
  const cs = (mueff + 2) / (dim + mueff + 5)
  const ds = 1 + 2 * Math.max(0, Math.sqrt((mueff - 1) / (dim + 1)) - 1) + cs

  // Covariance update rates.
  const cc = (4 + mueff / dim) / (dim + 4 + (2 * mueff) / dim)
  const c1 = 2 / ((dim + 1.3) ** 2 + mueff)
  const cmu = Math.min(1 - c1, (2 * (mueff - 2 + 1 / mueff)) / ((dim + 2) ** 2 + mueff))

  // E[||N(0,I)||]
  const chiN = Math.sqrt(dim) * (1 - 1 / (4 * dim) + 1 / (21 * dim * dim))

  // Initial step sizes per dimension.
  const D = SEARCH_SPACE.map((s) => (s.max - s.min) * s.stepFrac)

  return {
    mean: [...initialVector],
    sigma: 1.0,
    D,
    pc: new Array(dim).fill(0),
    ps: new Array(dim).fill(0),
    generation: 0,
    lambda,
    mu,
    weights,
    mueff,
    cs,
    ds,
    cc,
    c1,
    cmu,
    chiN,
    bestFitness: -Infinity,
    bestVector: [...initialVector],
    stagnationCount: 0,
  }
}

interface Candidate {
  vector: number[]
  params: GodAIParams
  fitness: number
  evalResult: EvalResult
}

/** Sample lambda candidates from the search distribution. */
function sampleCandidates(state: CMAESState): number[][] {
  const candidates: number[][] = []
  for (let i = 0; i < state.lambda; i++) {
    const vec: number[] = []
    for (let d = 0; d < DIM; d++) {
      const z = randNormal()
      vec.push(state.mean[d] + state.sigma * state.D[d] * z)
    }
    candidates.push(vec)
  }
  return candidates
}

/** Update CMA-ES state based on ranked candidates. */
function updateCMAES(state: CMAESState, sortedCandidates: Candidate[]): void {
  const oldMean = [...state.mean]

  // Update mean: weighted average of top-mu candidates.
  const newMean: number[] = new Array(DIM).fill(0)
  for (let i = 0; i < state.mu; i++) {
    for (let d = 0; d < DIM; d++) {
      newMean[d] += state.weights[i] * sortedCandidates[i].vector[d]
    }
  }

  // Mean step (normalized by sigma).
  const yw: number[] = new Array(DIM).fill(0)
  for (let d = 0; d < DIM; d++) {
    yw[d] = (newMean[d] - oldMean[d]) / state.sigma
  }

  // Inverse sqrt of diagonal C = 1/D.
  const invSqrtC: number[] = state.D.map((d) => 1 / Math.max(1e-10, d))

  // Update evolution path for sigma.
  const psNorm: number[] = new Array(DIM).fill(0)
  for (let d = 0; d < DIM; d++) {
    psNorm[d] = invSqrtC[d] * yw[d]
  }
  for (let d = 0; d < DIM; d++) {
    state.ps[d] =
      (1 - state.cs) * state.ps[d] + Math.sqrt(state.cs * (2 - state.cs) * state.mueff) * psNorm[d]
  }

  // Update evolution path for C.
  const psNorm2 = state.ps.reduce((s, p) => s + p * p, 0)
  // Guard: if psNorm2 is NaN, skip C update this generation.
  if (!Number.isFinite(psNorm2)) {
    state.mean = newMean
    state.generation++
    return
  }

  // hSig: Heaviside function for sigma-update damping.
  const denom = 1 - Math.pow(1 - state.cs, 2 * (state.generation + 1))
  const hSig = denom > 1e-10 && psNorm2 / denom / (DIM * 2) < 1 + 1 / (2 * DIM) ? 1 : 0

  for (let d = 0; d < DIM; d++) {
    state.pc[d] =
      (1 - state.cc) * state.pc[d] +
      hSig * Math.sqrt(state.cc * (2 - state.cc) * state.mueff) * yw[d]
  }

  // Update diagonal D with numerical stability guards.
  for (let d = 0; d < DIM; d++) {
    // Rank-1 update.
    const pc2 = Number.isFinite(state.pc[d]) ? state.pc[d] * state.pc[d] : 0
    const rank1 = state.c1 * (pc2 - 1)
    // Rank-mu update.
    let rankMu = 0
    for (let i = 0; i < state.mu; i++) {
      const yi =
        (sortedCandidates[i].vector[d] - oldMean[d]) / (state.sigma * Math.max(1e-10, state.D[d]))
      rankMu += state.weights[i] * (yi * yi - 1)
    }
    rankMu *= state.cmu

    // Clamp deltaD to prevent sqrt of negative or overflow.
    const deltaD = (rank1 + rankMu) / 2
    const factor = Math.max(0.5, Math.min(2.0, 1 + deltaD)) // clamp to [0.5, 2.0]
    state.D[d] = state.D[d] * Math.sqrt(factor)

    // Hard bounds on D to prevent runaway.
    const range = SEARCH_SPACE[d].max - SEARCH_SPACE[d].min
    state.D[d] = Math.max(range * 0.01, Math.min(range * 0.5, state.D[d]))
  }

  // Update sigma with guards.
  const psLength = Math.sqrt(psNorm2)
  const sigmaExp = (state.cs / state.ds) * (psLength / state.chiN - 1)
  // Clamp exponent to prevent sigma explosion/collapse.
  const clampedExp = Math.max(-1, Math.min(1, sigmaExp))
  state.sigma = state.sigma * Math.exp(clampedExp)

  // Hard clamp sigma.
  state.sigma = Math.max(0.05, Math.min(5, state.sigma))

  // Update mean.
  state.mean = newMean
  state.generation++
}

// ============================================================
// Optimization Loop
// ============================================================

interface OptimizationResult {
  bestParams: GodAIParams
  bestFitness: number
  bestEvalResult: EvalResult
  history: Array<{
    generation: number
    bestFitness: number
    meanFitness: number
    sigma: number
    bestParams: GodAIParams
    bestEvalResult: EvalResult
  }>
  allCandidates: Array<{
    generation: number
    candidateIndex: number
    params: GodAIParams
    fitness: number
    evalResult: EvalResult
  }>
}

function optimize(
  evalConfig: EvalConfig,
  generations: number,
  verbose: boolean,
): OptimizationResult {
  const initialVector = paramsToVector(DEFAULT_GOD_AI_PARAMS)
  const state = initCMAES(initialVector)

  const history: OptimizationResult['history'] = []
  const allCandidates: OptimizationResult['allCandidates'] = []

  let bestFitness = -Infinity
  let bestParams = DEFAULT_GOD_AI_PARAMS
  let bestEvalResult: EvalResult | null = null

  process.stderr.write(`\n${'='.repeat(70)}\n`)
  process.stderr.write(
    `CMA-ES Optimization: ${DIM} params, ${state.lambda} pop, ${generations} generations\n`,
  )
  process.stderr.write(
    `Evaluation: stage "${evalConfig.stage.name}", ${evalConfig.difficulty}, seeds ${evalConfig.seeds.join(',')}\n`,
  )
  process.stderr.write(`${'='.repeat(70)}\n\n`)

  for (let gen = 0; gen < generations; gen++) {
    // Sample candidates.
    const vectors = sampleCandidates(state)

    // Evaluate each candidate.
    const candidates: Candidate[] = []
    for (let i = 0; i < vectors.length; i++) {
      const params = vectorToParams(vectors[i])
      const evalResult = evaluateParams(params, evalConfig)
      candidates.push({
        vector: vectors[i],
        params,
        fitness: evalResult.fitness,
        evalResult,
      })

      allCandidates.push({
        generation: gen,
        candidateIndex: i,
        params,
        fitness: evalResult.fitness,
        evalResult,
      })

      if (verbose) {
        process.stderr.write(
          `  gen ${gen} cand ${i}: fitness=${evalResult.fitness.toFixed(1)} win=${evalResult.winRate.toFixed(2)} kills=${evalResult.avgKills.toFixed(1)}\n`,
        )
      }
    }

    // Sort by fitness (descending).
    candidates.sort((a, b) => b.fitness - a.fitness)

    // Track best.
    if (candidates[0].fitness > bestFitness) {
      bestFitness = candidates[0].fitness
      bestParams = candidates[0].params
      bestEvalResult = candidates[0].evalResult
      state.stagnationCount = 0
    } else {
      state.stagnationCount++
    }

    // Update CMA-ES.
    updateCMAES(state, candidates)

    // Record history.
    const meanFitness = candidates.reduce((s, c) => s + c.fitness, 0) / candidates.length
    history.push({
      generation: gen,
      bestFitness: candidates[0].fitness,
      meanFitness,
      sigma: state.sigma,
      bestParams: candidates[0].params,
      bestEvalResult: candidates[0].evalResult,
    })

    // Progress report.
    const best = candidates[0]
    process.stderr.write(
      `gen ${gen.toString().padStart(3)} | ` +
        `best=${best.fitness.toFixed(1).padStart(7)} ` +
        `win=${best.evalResult.winRate.toFixed(2)} ` +
        `base=${best.evalResult.baseSurvivalRate.toFixed(2)} ` +
        `kills=${best.evalResult.avgKills.toFixed(1)} ` +
        `σ=${state.sigma.toFixed(3)} ` +
        `stag=${state.stagnationCount}\n`,
    )

    // Early stopping: 10 generations of stagnation with converged sigma.
    if (state.stagnationCount > 15 && state.sigma < 0.05) {
      process.stderr.write(`\nEarly stop: stagnation + sigma convergence.\n`)
      break
    }
  }

  process.stderr.write(`\n${'='.repeat(70)}\n`)
  process.stderr.write(`Optimization complete. Best fitness: ${bestFitness.toFixed(1)}\n`)
  process.stderr.write(`Best params:\n`)
  for (const spec of SEARCH_SPACE) {
    process.stderr.write(`  ${spec.name}: ${bestParams[spec.name]}\n`)
  }
  process.stderr.write(`${'='.repeat(70)}\n\n`)

  return {
    bestParams,
    bestFitness,
    bestEvalResult: bestEvalResult!,
    history,
    allCandidates,
  }
}

// ============================================================
// Comparison: run traces with best params vs default params
// ============================================================

function runComparisonTraces(
  bestParams: GodAIParams,
  evalConfig: EvalConfig,
  outputDir: string,
): void {
  mkdirSync(outputDir, { recursive: true })

  // Run traces with best params.
  process.stderr.write(`\nRecording decision traces with optimized params...\n`)
  for (const seed of evalConfig.seeds) {
    const trace = traceSimulation({
      seed,
      stage: evalConfig.stage,
      difficulty: evalConfig.difficulty,
      params: bestParams,
      maxTicks: evalConfig.maxTicks,
      sampleInterval: 6, // every 100ms
    })
    const analysis = analyzeTrace(trace)
    const filename = `best-seed${seed}-${trace.outcome}.json`
    writeFileSync(join(outputDir, filename), JSON.stringify(trace, null, 2))
    writeFileSync(join(outputDir, filename.replace('.json', '.txt')), analysis)
    process.stderr.write(
      `  seed ${seed}: ${trace.outcome} kills=${trace.finalState.killCount} -> ${filename}\n`,
    )
  }

  // Run traces with default params for comparison.
  process.stderr.write(`\nRecording decision traces with default params...\n`)
  for (const seed of evalConfig.seeds) {
    const trace = traceSimulation({
      seed,
      stage: evalConfig.stage,
      difficulty: evalConfig.difficulty,
      params: DEFAULT_GOD_AI_PARAMS,
      maxTicks: evalConfig.maxTicks,
      sampleInterval: 6,
    })
    const analysis = analyzeTrace(trace)
    const filename = `default-seed${seed}-${trace.outcome}.json`
    writeFileSync(join(outputDir, filename), JSON.stringify(trace, null, 2))
    writeFileSync(join(outputDir, filename.replace('.json', '.txt')), analysis)
    process.stderr.write(
      `  seed ${seed}: ${trace.outcome} kills=${trace.finalState.killCount} -> ${filename}\n`,
    )
  }
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  function arg(name: string, fallback?: string): string | undefined {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }

  const stageIdx = parseInt(arg('stage', '0')!, 10)
  const difficulty = arg('difficulty', 'classic')!
  const seedCount = parseInt(arg('seeds', '5')!, 10)
  const generations = parseInt(arg('generations', '30')!, 10)
  const popSize = parseInt(arg('pop', '0')!, 10) // 0 = auto
  const maxTicks = parseInt(arg('max-ticks', '18000')!, 10)
  const verbose = process.argv.includes('--verbose')
  const outputDir = arg('output', '.workbuddy/optimization')

  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const stage = STAGES[stageIdx]

  if (!stage) {
    console.error(`Stage ${stageIdx} not found (0..${STAGES.length - 1})`)
    process.exit(1)
  }

  process.stderr.write(`\nGod AI CMA-ES Optimizer\n`)
  process.stderr.write(
    `Stage: ${stage.name} (idx ${stageIdx}) | Difficulty: ${difficulty} | Seeds: ${seeds.join(',')}\n`,
  )
  process.stderr.write(`Generations: ${generations} | Max ticks: ${maxTicks}\n`)

  const evalConfig: EvalConfig = { stage, difficulty, seeds, maxTicks }

  // Run optimization.
  const result = optimize(evalConfig, generations, verbose)

  // Save optimization results.
  mkdirSync(outputDir, { recursive: true })

  const summary = {
    timestamp: new Date().toISOString(),
    config: { stageIdx, stageName: stage.name, difficulty, seeds, generations, maxTicks },
    searchSpace: SEARCH_SPACE.map((s) => ({
      name: s.name,
      min: s.min,
      max: s.max,
      isInteger: s.isInteger,
      init: s.init,
    })),
    bestParams: result.bestParams,
    bestFitness: result.bestFitness,
    bestEvalResult: result.bestEvalResult,
    defaultParams: DEFAULT_GOD_AI_PARAMS,
    defaultEvalResult: evaluateParams(DEFAULT_GOD_AI_PARAMS, evalConfig),
    history: result.history.map((h) => ({
      gen: h.generation,
      bestFit: Math.round(h.bestFitness * 10) / 10,
      meanFit: Math.round(h.meanFitness * 10) / 10,
      sigma: Math.round(h.sigma * 1000) / 1000,
      win: h.bestEvalResult.winRate,
      base: h.bestEvalResult.baseSurvivalRate,
      kills: Math.round(h.bestEvalResult.avgKills * 10) / 10,
      params: h.bestParams,
    })),
  }

  const summaryFile = join(outputDir, 'optimization-summary.json')
  writeFileSync(summaryFile, JSON.stringify(summary, null, 2))
  process.stderr.write(`\nSaved summary to ${summaryFile}\n`)

  // Save all candidate details (for analysis).
  const allCandidatesFile = join(outputDir, 'all-candidates.json')
  writeFileSync(
    allCandidatesFile,
    JSON.stringify(
      result.allCandidates.map((c) => ({
        gen: c.generation,
        idx: c.candidateIndex,
        params: c.params,
        fitness: Math.round(c.fitness * 10) / 10,
        win: c.evalResult.winRate,
        base: c.evalResult.baseSurvivalRate,
        kills: c.evalResult.avgKills,
        perSeed: c.evalResult.perSeed,
      })),
      null,
      2,
    ),
  )
  process.stderr.write(`Saved all candidates to ${allCandidatesFile}\n`)

  // Run comparison traces.
  const traceDir = join(outputDir, 'traces')
  runComparisonTraces(result.bestParams, evalConfig, traceDir)

  // Generate optimization log.
  const logLines: string[] = []
  logLines.push(`# CMA-ES Optimization Log`)
  logLines.push(``)
  logLines.push(`**Date**: ${summary.timestamp}`)
  logLines.push(
    `**Stage**: ${stage.name} (idx ${stageIdx}) | **Difficulty**: ${difficulty} | **Seeds**: ${seeds.join(',')}`,
  )
  logLines.push(
    `**Generations**: ${generations} | **Population**: ${result.history[0]?.bestFitness ?? 'N/A'}`,
  )
  logLines.push(``)
  logLines.push(`## Results Summary`)
  logLines.push(``)
  logLines.push(`| Metric | Default Params | Optimized Params | Δ |`)
  logLines.push(`|--------|---------------|-----------------|---|`)

  const def = summary.defaultEvalResult
  const opt = summary.bestEvalResult
  logLines.push(
    `| Fitness | ${def.fitness.toFixed(1)} | ${opt.fitness.toFixed(1)} | ${(opt.fitness - def.fitness).toFixed(1)} |`,
  )
  logLines.push(
    `| Win Rate | ${(def.winRate * 100).toFixed(0)}% | ${(opt.winRate * 100).toFixed(0)}% | ${((opt.winRate - def.winRate) * 100).toFixed(0)}% |`,
  )
  logLines.push(
    `| Base Survival | ${(def.baseSurvivalRate * 100).toFixed(0)}% | ${(opt.baseSurvivalRate * 100).toFixed(0)}% | ${((opt.baseSurvivalRate - def.baseSurvivalRate) * 100).toFixed(0)}% |`,
  )
  logLines.push(
    `| Avg Kills | ${def.avgKills.toFixed(1)} | ${opt.avgKills.toFixed(1)} | ${(opt.avgKills - def.avgKills).toFixed(1)} |`,
  )
  logLines.push(
    `| Avg Ticks | ${def.avgTicks.toFixed(0)} | ${opt.avgTicks.toFixed(0)} | ${(opt.avgTicks - def.avgTicks).toFixed(0)} |`,
  )
  logLines.push(``)
  logLines.push(`## Parameter Changes`)
  logLines.push(``)
  logLines.push(`| Parameter | Default | Optimized | Δ |`)
  logLines.push(`|-----------|---------|-----------|---|`)
  for (const spec of SEARCH_SPACE) {
    const dv = DEFAULT_GOD_AI_PARAMS[spec.name]
    const ov = result.bestParams[spec.name]
    const delta = typeof dv === 'number' && typeof ov === 'number' ? ov - dv : '?'
    logLines.push(`| ${spec.name} | ${dv} | ${ov} | ${delta} |`)
  }
  logLines.push(``)
  logLines.push(`## Generation History`)
  logLines.push(``)
  logLines.push(`| Gen | Best Fit | Mean Fit | σ | Win | Base | Kills |`)
  logLines.push(`|-----|----------|----------|---|-----|------|-------|`)
  for (const h of summary.history) {
    logLines.push(
      `| ${h.gen} | ${h.bestFit} | ${h.meanFit} | ${h.sigma} | ${(h.win * 100).toFixed(0)}% | ${(h.base * 100).toFixed(0)}% | ${h.kills} |`,
    )
  }

  const logFile = join(outputDir, 'optimization-log.md')
  writeFileSync(logFile, logLines.join('\n'))
  process.stderr.write(`Saved log to ${logFile}\n`)

  // Output to stdout.
  console.log(
    JSON.stringify(
      {
        bestParams: result.bestParams,
        bestFitness: result.bestFitness,
        bestEvalResult: result.bestEvalResult,
      },
      null,
      2,
    ),
  )
}
