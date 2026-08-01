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
 *   bun tools/optimize/optimize-godai.ts --generations 30 --seeds 5
 *   bun tools/optimize/optimize-godai.ts --stage 0 --difficulty classic --generations 50
 *
 * Evaluation runs in parallel on a Bun Worker pool (physical cores − 1;
 * override with SIM_POOL_WORKERS). Results are byte-identical to serial —
 * see tools/perf/probe-parallel-parity.ts. Use --serial to force the
 * single-threaded reference path.
 */

import { STAGES } from '../../src/config/stages'
import { ENEMIES_PER_STAGE } from '../../src/constants'
import { runSimulation } from '../sim/simulation-runner'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'
import { traceSimulation, analyzeTrace } from '../diag/decision-trace'
import { GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import type { StageData } from '../../src/types'
import { RNG } from '../../src/utils/RNG'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  fitnessV6,
  fitnessV7,
  DEFAULT_SCORE_CONFIG,
  V7_SCORE_CONFIG,
  DEFAULT_STAGE_REFS,
  type StageRefs,
  type ScorableRun,
  type ScoreConfig,
} from '../eval/godai-score'
import type { RunTelemetry } from '../sim/simulation-runner'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs'
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

// Init values match current DEFAULT_GOD_AI_PARAMS so CMA-ES starts from the
// best known operating point and explores outward.
// Init values synced with DEFAULT_GOD_AI_PARAMS (2026-07-28 v3 results).
// Ranges widened where the optimizer previously hit bounds.
export const SEARCH_SPACE: ParamSpec[] = [
  { name: 'reactionDelay', min: 0, max: 6, isInteger: true, init: 0, stepFrac: 0.25 },
  { name: 'aimError', min: 0, max: 0.15, isInteger: false, init: 0.0024, stepFrac: 0.3 },
  { name: 'suboptimalPathProb', min: 0, max: 0.2, isInteger: false, init: 0.062, stepFrac: 0.3 },
  { name: 'defenseRowOffset', min: 1, max: 5, isInteger: true, init: 1, stepFrac: 0.25 },
  { name: 'defenseColSpread', min: 3, max: 13, isInteger: true, init: 5, stepFrac: 0.25 },
  { name: 'threatRangeCells', min: 8, max: 26, isInteger: true, init: 26, stepFrac: 0.2 },
  { name: 'maxPlayerDistFromBase', min: 4, max: 26, isInteger: true, init: 10, stepFrac: 0.25 },
  { name: 't8MaxInterceptDistCells', min: 2, max: 13, isInteger: true, init: 3, stepFrac: 0.3 },
  { name: 'baseWallScanRadius', min: 1, max: 5, isInteger: true, init: 1, stepFrac: 0.3 },
  { name: 'replanInterval', min: 1, max: 50, isInteger: true, init: 3, stepFrac: 0.25 },
  { name: 'powerupMaxDivertDistance', min: 3, max: 20, isInteger: true, init: 9, stepFrac: 0.25 },
  { name: 'endgameEnemyThreshold', min: 1, max: 10, isInteger: true, init: 1, stepFrac: 0.3 },
  { name: 'huntAllyCount', min: 1, max: 6, isInteger: true, init: 4, stepFrac: 0.3 },
  // P4: race-to-base emergency defense (behavioral fix for S6/S32 flanking
  // runners). Range 0 disables the check entirely — the optimizer can turn
  // it off for stages where it hurts.
  { name: 'baseRaceRangeCells', min: 0, max: 18, isInteger: true, init: 12, stepFrac: 0.25 },
  { name: 'baseRaceMarginCells', min: 0, max: 6, isInteger: true, init: 2, stepFrac: 0.3 },
  // P4.2: outnumbered retreat (S18 crossfire family). Count 5 disables
  // (max 4 enemies alive on field) — the optimizer can turn it off.
  { name: 'outnumberedEnemyCount', min: 2, max: 5, isInteger: true, init: 3, stepFrac: 0.3 },
  { name: 'outnumberedRadiusCells', min: 4, max: 14, isInteger: true, init: 8, stepFrac: 0.25 },
  // §66: campTimeoutTicks — base camp timeout for non-armor, non-steel-maze
  // stages. Armor-heavy stages override to 50, S6 overrides to 20 (§66).
  // CMA-ES optimizes the base value for the majority of stages.
  { name: 'campTimeoutTicks', min: 20, max: 120, isInteger: true, init: 90, stepFrac: 0.25 },
  // t2aHighHpMaxRange — close-combat range for multi-HP (armor) enemies.
  // Default 2 (point-blank). Open-sightline stages override to 4, armor-
  // forest-dense stages to 3. CMA-ES optimizes the base value.
  { name: 't2aHighHpMaxRange', min: 2, max: 5, isInteger: true, init: 2, stepFrac: 0.3 },
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
export function vectorToParams(vec: number[]): GodAIParams {
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

export interface EvalConfig {
  /** Single stage (legacy) or first of multi-stage set. */
  stage: StageData
  /** P3.5: multiple stages for aggregate fitness. If empty, uses `stage` only. */
  stages?: StageData[]
  difficulty: string
  seeds: number[]
  maxTicks: number
  /**
   * P4: per-stage win-rate floor. Any stage whose measured win rate falls
   * below `floor` incurs a proportional penalty, so the optimizer is pushed
   * to lift the *weakest* stages (not just average strength). Default 0.6.
   */
  floor?: number
  /**
   * Which fitness to rank candidates on.
   *
   *   'v5' (default) — the magic-number sum this file has always used.
   *   'v6'           — the banded, risk-adjusted score from godai-score.ts
   *                    (plan/God-AI-Evaluation-Redesign.md).
   *
   * v6 is opt-in so that existing tuning runs, the regression gate and every
   * recorded baseline keep comparing like with like. All the v5 reporting
   * fields are still populated under v6 — only `fitness` changes.
   */
  fitnessVersion?: 'v5' | 'v6' | 'v7'
  /** Per-stage references for v6/v7, keyed by stage name (tools/eval/eval-refs.json). */
  stageRefs?: Record<string, StageRefs>
  /** Base score config (bands + weights). Defaults to v6; v7 uses wider bands. */
  scoreConfigBase?: ScoreConfig
}

/** Load per-stage v6 references if they have been calibrated. */
export function loadStageRefs(): Record<string, StageRefs> {
  const file = join(import.meta.dir, '../eval/eval-refs.json')
  if (!existsSync(file)) return {}
  try {
    return JSON.parse(readFileSync(file, 'utf8')).stages ?? {}
  } catch {
    return {}
  }
}

/** P4 goal: every classic stage must clear above this win rate (floor). */
export const DEFAULT_FLOOR = 0.6
/** P4 goal: penalty weight per unit of floor deficit (0.6 win-rate deficit → 4800). */
const FLOOR_PENALTY_WEIGHT = 8000

export interface EvalResult {
  fitness: number
  winRate: number
  baseSurvivalRate: number
  avgKills: number
  avgTicks: number
  /** Total penalty from remaining enemies on ALL non-win seeds (v4.1). */
  remainingEnemyPenalty: number
  /** Count of non-win seeds with <5 kills (catastrophic paralysis). */
  lowKillNonWins: number
  /** Extra penalty for gameovers (base lost). */
  gameoverPenalty: number
  /** P4: sum over stages of max(0, floor − stageWinRate) * weight. */
  floorPenalty: number
  /** P4: per-stage win rates (stage-major order), for floor monitoring. */
  perStageWin: number[]
  /** P4: minimum per-stage win rate across the eval set (the floor monitor). */
  minStageWin: number
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
 *
 * Fitness v4.1 (clear-speed-centric, gameover-loophole fixed):
 *
 * v4 only penalized timeouts (max_ticks) for remaining enemies. The
 * optimizer exploited this: it found that letting the base die early
 * (gameover) avoids the timeout penalty entirely. It converted 0-kill
 * timeouts into 3-kill gameovers — worse gameplay, higher fitness.
 *
 * v4.1 closes the loophole:
 *   1. remainingEnemyPenalty applies to ALL non-wins (timeout AND gameover).
 *      Per-enemy penalty reduced 30→25 to keep it proportional to win bonus.
 *   2. lowKillNonWins applies to ALL non-wins with <5 kills (was max_ticks
 *      only). A 0-kill gameover is just as catastrophic as a 0-kill timeout.
 *   3. gameoverPenalty: extra 500 per gameover — losing the base is ALWAYS
 *      worse than timing out, because timing out means the base was defended.
 *   4. speedBonus (800) and baseSurvivalRate (200) retained from v4.
 */
/**
 * One simulation record — the exact fields the fitness aggregation consumes.
 * Produced either serially (evaluateParams) or by the worker pool
 * (evaluateGenerationParallel); aggregation is shared so both paths yield
 * byte-identical EvalResults (same record order ⇒ same float summation order).
 */
interface RunRecord {
  seed: number
  ok: boolean
  outcome: string
  ticks: number
  killCount: number
  baseAlive: boolean
  /** v6 only — absent under v5, where nothing reads them. */
  lives?: number
  firstKillTick?: number
  telemetry?: RunTelemetry
}

/** A failed run is scored as the worst possible outcome, never dropped. */
function recordToScorable(rec: RunRecord): ScorableRun {
  if (!rec.ok) {
    return { outcome: 'error', ticks: 0, finalState: { killCount: 0, lives: 0, baseAlive: false } }
  }
  return {
    outcome: rec.outcome,
    ticks: rec.ticks,
    finalState: { killCount: rec.killCount, lives: rec.lives ?? 0, baseAlive: rec.baseAlive },
    firstKillTick: rec.firstKillTick,
    telemetry: rec.telemetry,
  }
}

/** Aggregate run records (stage-major, seed-minor order) into an EvalResult. */
function aggregateEval(records: RunRecord[], config: EvalConfig): EvalResult {
  const perSeed: EvalResult['perSeed'] = []
  let wins = 0
  let baseSurvived = 0
  let totalKills = 0
  let totalTicks = 0
  let lowKillNonWins = 0
  let winTicksSum = 0
  let remainingEnemyPenalty = 0
  let gameoverCount = 0
  let n = 0

  for (const rec of records) {
    n++
    if (!rec.ok) {
      // Mirrors the serial catch branch: error counts as catastrophic.
      perSeed.push({ seed: rec.seed, outcome: 'error', kills: 0, ticks: 0, baseAlive: false })
      lowKillNonWins++
      gameoverCount++
      remainingEnemyPenalty += ENEMIES_PER_STAGE * 25
      continue
    }

    const won = rec.outcome === 'stage_clear'
    if (won) {
      wins++
      winTicksSum += rec.ticks
    }
    if (rec.baseAlive) baseSurvived++
    totalKills += rec.killCount
    totalTicks += rec.ticks

    if (!won) {
      const remaining = ENEMIES_PER_STAGE - rec.killCount
      remainingEnemyPenalty += remaining * 25

      if (rec.killCount < 5) {
        lowKillNonWins++
      }

      if (rec.outcome === 'gameover') {
        gameoverCount++
      }
    }

    perSeed.push({
      seed: rec.seed,
      outcome: rec.outcome,
      kills: rec.killCount,
      ticks: rec.ticks,
      baseAlive: rec.baseAlive,
    })
  }

  const winRate = wins / n
  const baseSurvivalRate = baseSurvived / n
  const avgKills = totalKills / n
  const avgTicks = totalTicks / n

  // P4: per-stage win-rate floor monitoring + penalty.
  // Records arrive stage-major, seed-minor (see runList / evaluateCandidatesParallel),
  // so we can chunk them by stage to recover each stage's win rate.
  const stageCount = config.stages && config.stages.length > 0 ? config.stages.length : 1
  const seedsPerStage = Math.max(1, Math.floor(n / stageCount))
  const perStageWin: number[] = []
  let floorPenalty = 0
  const floor = config.floor ?? DEFAULT_FLOOR
  for (let s = 0; s < stageCount; s++) {
    const groupStart = s * seedsPerStage
    const groupEnd = Math.min(n, groupStart + seedsPerStage)
    let sw = 0
    let sn = 0
    for (let i = groupStart; i < groupEnd; i++) {
      const rec = records[i]
      sn++
      if (rec.ok && rec.outcome === 'stage_clear') sw++
    }
    const wr = sn > 0 ? sw / sn : 0
    perStageWin.push(wr)
    if (wr < floor) floorPenalty += (floor - wr) * FLOOR_PENALTY_WEIGHT
  }
  const minStageWin = perStageWin.length > 0 ? Math.min(...perStageWin) : 0

  const avgWinTicks = wins > 0 ? winTicksSum / wins : config.maxTicks
  const speedBonus = wins > 0 ? Math.max(0, (1 - avgWinTicks / config.maxTicks) * 800) : 0

  const gameoverPenalty = gameoverCount * 500

  const fitnessV5 =
    winRate * 5000 +
    avgKills * 60 +
    baseSurvivalRate * 200 +
    speedBonus -
    remainingEnemyPenalty -
    gameoverPenalty -
    lowKillNonWins * 400 -
    floorPenalty

  // v6: re-score the same records through the banded model. The stage
  // chunking mirrors the floor-penalty loop above, so both fitnesses see
  // exactly the same partition of the same records in the same order.
  let fitness = fitnessV5
  if (config.fitnessVersion === 'v6' || config.fitnessVersion === 'v7') {
    const configBase = config.scoreConfigBase ?? DEFAULT_SCORE_CONFIG
    const stageList = config.stages && config.stages.length > 0 ? config.stages : [config.stage]
    const aggregates = []
    for (let s = 0; s < stageCount; s++) {
      const groupStart = s * seedsPerStage
      const groupEnd = Math.min(n, groupStart + seedsPerStage)
      const name = stageList[s]?.name ?? `stage${s}`
      const refs = config.stageRefs?.[name] ?? DEFAULT_STAGE_REFS
      const scored = []
      for (let i = groupStart; i < groupEnd; i++) {
        scored.push(scoreRun(recordToScorable(records[i]), { ...configBase, refs }))
      }
      aggregates.push(aggregateStage(name, scored))
    }
    fitness =
      config.fitnessVersion === 'v7'
        ? fitnessV7(aggregateSuite(aggregates))
        : fitnessV6(aggregateSuite(aggregates))
  }

  return {
    fitness,
    winRate,
    baseSurvivalRate,
    avgKills,
    avgTicks,
    remainingEnemyPenalty,
    lowKillNonWins,
    gameoverPenalty,
    floorPenalty,
    perStageWin,
    minStageWin,
    perSeed,
  }
}

/** Build the (stage, seed) run list in the canonical serial order. */
function runList(config: EvalConfig): Array<{ stage: StageData; seed: number }> {
  const stages = config.stages && config.stages.length > 0 ? config.stages : [config.stage]
  const list: Array<{ stage: StageData; seed: number }> = []
  for (const stage of stages) {
    for (const seed of config.seeds) {
      list.push({ stage, seed })
    }
  }
  return list
}

/** Evaluate a parameter set by running simulations serially (reference path). */
export function evaluateParams(params: GodAIParams, config: EvalConfig): EvalResult {
  const records: RunRecord[] = []
  const wantTelemetry = config.fitnessVersion === 'v6' || config.fitnessVersion === 'v7'
  for (const { stage, seed } of runList(config)) {
    try {
      const result = runSimulation({
        seed,
        stage,
        difficulty: config.difficulty,
        godAIParams: params,
        maxTicks: config.maxTicks,
        sampleInterval: 60, // minimal sampling for speed
        telemetry: wantTelemetry,
      })
      records.push({
        seed,
        ok: true,
        outcome: result.outcome,
        ticks: result.ticks,
        killCount: result.finalState.killCount,
        baseAlive: result.finalState.baseAlive,
        lives: wantTelemetry ? result.finalState.lives : undefined,
        firstKillTick: wantTelemetry ? result.firstKillTick : undefined,
        telemetry: result.telemetry,
      })
    } catch {
      records.push({ seed, ok: false, outcome: 'error', ticks: 0, killCount: 0, baseAlive: false })
    }
  }
  return aggregateEval(records, config)
}

/**
 * Evaluate a whole batch of candidates in parallel on the worker pool.
 *
 * Task ids are assigned candidate-major, then (stage, seed) in the same
 * order as the serial loop; results are re-ordered by id by the pool, so
 * each candidate's records — and therefore each EvalResult — are
 * byte-identical to the serial evaluateParams() output.
 */
export async function evaluateCandidatesParallel(
  pool: SimWorkerPool,
  paramsList: GodAIParams[],
  config: EvalConfig,
): Promise<EvalResult[]> {
  const runs = runList(config)
  const runsPerCandidate = runs.length
  const tasks: SimTask[] = []
  for (let c = 0; c < paramsList.length; c++) {
    for (const { stage, seed } of runs) {
      tasks.push({
        id: tasks.length,
        seed,
        stage,
        difficulty: config.difficulty,
        params: paramsList[c],
        maxTicks: config.maxTicks,
        telemetry: config.fitnessVersion !== 'v5',
      })
    }
  }

  const results = await pool.runBatch(tasks)

  const evals: EvalResult[] = []
  for (let c = 0; c < paramsList.length; c++) {
    const records: RunRecord[] = []
    for (let r = 0; r < runsPerCandidate; r++) {
      const res = results[c * runsPerCandidate + r]
      records.push({
        seed: runs[r].seed,
        ok: res.ok,
        outcome: res.outcome,
        ticks: res.ticks,
        killCount: res.killCount,
        baseAlive: res.baseAlive,
        lives: res.lives,
        firstKillTick: res.firstKillTick,
        telemetry: res.telemetry,
      })
    }
    evals.push(aggregateEval(records, config))
  }
  return evals
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

/**
 * Search RNG. Seeded so an optimisation run is reproducible.
 *
 * This used to be bare `Math.random()`, which made every invocation explore a
 * different set of candidates. That is fatal for the thing tuning exists to do:
 * if you change a heuristic, re-run, and the fitness moves, you cannot tell
 * whether the change helped or whether CMA-ES simply drew a luckier population.
 * With a fixed seed the search path is fixed, so a fitness delta is attributable
 * to the change. Vary `--opt-seed` deliberately to check a result is not an
 * artefact of one search trajectory.
 *
 * The simulation RNG is separate (`world.rng`, per config seed) and unaffected.
 */
let searchRng = new RNG(1)

/** Box-Muller transform for a standard normal, drawn from the seeded search RNG. */
function randNormal(): number {
  let u = 0,
    v = 0
  while (u === 0) u = searchRng.next()
  while (v === 0) v = searchRng.next()
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
    pc: Array.from({ length: dim }, () => 0),
    ps: Array.from({ length: dim }, () => 0),
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
  const newMean: number[] = Array.from({ length: DIM }, () => 0)
  for (let i = 0; i < state.mu; i++) {
    for (let d = 0; d < DIM; d++) {
      newMean[d] += state.weights[i] * sortedCandidates[i].vector[d]
    }
  }

  // Mean step (normalized by sigma).
  const yw: number[] = Array.from({ length: DIM }, () => 0)
  for (let d = 0; d < DIM; d++) {
    yw[d] = (newMean[d] - oldMean[d]) / state.sigma
  }

  // Inverse sqrt of diagonal C = 1/D.
  const invSqrtC: number[] = state.D.map((d) => 1 / Math.max(1e-10, d))

  // Update evolution path for sigma.
  const psNorm: number[] = Array.from({ length: DIM }, () => 0)
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

/** IPOP-CMA-ES: restart with larger population on stagnation. */
const RESTART_STAGNATION = 20
const MAX_RESTARTS = 3

async function optimize(
  evalConfig: EvalConfig,
  generations: number,
  verbose: boolean,
  pool: SimWorkerPool | null,
  initParams: GodAIParams = DEFAULT_GOD_AI_PARAMS,
  initialSigma = 1.0,
): Promise<OptimizationResult> {
  const initialVector = paramsToVector(initParams)
  let state = initCMAES(initialVector)
  state.sigma = initialSigma
  let restartCount = 0
  let popMultiplier = 1

  const history: OptimizationResult['history'] = []
  const allCandidates: OptimizationResult['allCandidates'] = []

  let bestFitness = -Infinity
  let bestParams = initParams
  let bestEvalResult: EvalResult | null = null

  process.stderr.write(`\n${'='.repeat(70)}\n`)
  process.stderr.write(
    `CMA-ES Optimization (IPOP): ${DIM} params, ${state.lambda} pop, ${generations} generations\n`,
  )
  process.stderr.write(
    `Evaluation: stages ${(evalConfig.stages ?? [evalConfig.stage]).map((s) => s.name).join(', ')}, ${evalConfig.difficulty}, seeds ${evalConfig.seeds.join(',')}\n`,
  )
  if (evalConfig.fitnessVersion === 'v7') {
    const refCount = Object.keys(evalConfig.stageRefs ?? {}).length
    process.stderr.write(
      `Fitness v7: widened band gap (loss≤0.40 < clear≥0.70) for stronger win-rate alignment.\n` +
        `  Same L1–L4 pipeline as v6, same per-stage refs: ${refCount > 0 ? `${refCount} calibrated stages` : 'DEFAULTS'}\n`,
    )
  } else if (evalConfig.fitnessVersion === 'v6') {
    const refCount = Object.keys(evalConfig.stageRefs ?? {}).length
    process.stderr.write(
      `Fitness v6: banded per-run score (loss<=0.55 < clear>=0.60), seed CVaR, ` +
        `cross-stage harmonic mean, lower confidence bound. ×1000.\n` +
        `  per-stage refs: ${refCount > 0 ? `${refCount} calibrated stages` : 'DEFAULTS (run eval-suite --calibrate first)'}\n`,
    )
  } else {
    process.stderr.write(
      `Fitness v5.0 (P4): win*5000 + kills*60 + base*200 + speed*800 - remaining*25 - gameover*500 - lowKill*400 - floorPenalty(floor=${evalConfig.floor ?? DEFAULT_FLOOR}, w=${FLOOR_PENALTY_WEIGHT})\n`,
    )
  }
  process.stderr.write(`${'='.repeat(70)}\n\n`)

  for (let gen = 0; gen < generations; gen++) {
    // Sample candidates.
    const vectors = sampleCandidates(state)
    const paramsList = vectors.map(vectorToParams)

    // Evaluate candidates — parallel across the worker pool (candidates
    // within a generation are independent), or serial as reference path.
    const evalResults = pool
      ? await evaluateCandidatesParallel(pool, paramsList, evalConfig)
      : paramsList.map((p) => evaluateParams(p, evalConfig))

    const candidates: Candidate[] = []
    for (let i = 0; i < vectors.length; i++) {
      const params = paramsList[i]
      const evalResult = evalResults[i]
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
        `minW=${best.evalResult.minStageWin.toFixed(2)} ` +
        `floor=${best.evalResult.floorPenalty.toFixed(0)} ` +
        `base=${best.evalResult.baseSurvivalRate.toFixed(2)} ` +
        `kills=${best.evalResult.avgKills.toFixed(1)} ` +
        `rem=${best.evalResult.remainingEnemyPenalty.toFixed(0)} ` +
        `go=${best.evalResult.gameoverPenalty.toFixed(0)} ` +
        `σ=${state.sigma.toFixed(3)} ` +
        `stag=${state.stagnationCount}` +
        (restartCount > 0 ? ` R${restartCount}` : '') +
        `\n`,
    )

    // IPOP-CMA-ES restart: on stagnation, restart with larger population.
    if (state.stagnationCount >= RESTART_STAGNATION && restartCount < MAX_RESTARTS) {
      restartCount++
      popMultiplier *= 2
      // Restart from best known point with increased sigma.
      state = initCMAESIPOP(paramsToVector(bestParams), popMultiplier)
      process.stderr.write(
        `\n>>> IPOP restart ${restartCount}: pop=${state.lambda}, σ=${state.sigma.toFixed(3)}, from best=${bestFitness.toFixed(1)}\n\n`,
      )
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

/** Initialize CMA-ES with a population multiplier (for IPOP restarts). */
function initCMAESIPOP(initialVector: number[], popMultiplier: number): CMAESState {
  const state = initCMAES(initialVector)
  state.lambda *= popMultiplier
  state.mu = Math.floor(state.lambda / 2)
  // Recompute weights for the larger population.
  const rawWeights: number[] = []
  for (let i = 0; i < state.mu; i++) {
    rawWeights.push(Math.log(state.mu + 1) - Math.log(i + 1))
  }
  const wSum = rawWeights.reduce((a, b) => a + b, 0)
  state.weights = rawWeights.map((w) => w / wSum)
  const wSqSum = state.weights.reduce((a, w) => a + w * w, 0)
  state.mueff = 1 / wSqSum
  // Recompute strategy parameters.
  state.cs = (state.mueff + 2) / (DIM + state.mueff + 5)
  state.ds = 1 + 2 * Math.max(0, Math.sqrt((state.mueff - 1) / (DIM + 1)) - 1) + state.cs
  state.cc = (4 + state.mueff / DIM) / (DIM + 4 + (2 * state.mueff) / DIM)
  state.c1 = 2 / ((DIM + 1.3) ** 2 + state.mueff)
  state.cmu = Math.min(
    1 - state.c1,
    (2 * (state.mueff - 2 + 1 / state.mueff)) / ((DIM + 2) ** 2 + state.mueff),
  )
  state.chiN = Math.sqrt(DIM) * (1 - 1 / (4 * DIM) + 1 / (21 * DIM * DIM))
  // Restart with larger sigma for broader exploration.
  state.sigma = 0.8
  state.stagnationCount = 0
  return state
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
  // Overloaded so that supplying a fallback narrows the result to `string`.
  // Without this every call site with a default still had to be unwrapped with
  // `!`, and the ones that forgot silently passed `string | undefined` into
  // path helpers.
  function arg(name: string, fallback: string): string
  function arg(name: string): string | undefined
  function arg(name: string, fallback?: string): string | undefined {
    const i = process.argv.indexOf(`--${name}`)
    return i >= 0 ? process.argv[i + 1] : fallback
  }

  const stageIdxs = arg('stages', arg('stage', '0'))!
    .split(',')
    .map((s) => parseInt(s.trim(), 10))
  const difficulty = arg('difficulty', 'classic')!
  const seedCount = parseInt(arg('seeds', '8')!, 10)
  const generations = parseInt(arg('generations', '40')!, 10)
  const maxTicks = parseInt(arg('max-ticks', '18000')!, 10)
  const floor = parseFloat(arg('floor', '0.6')!)
  const verbose = process.argv.includes('--verbose')
  const serial = process.argv.includes('--serial')
  const outputDir = arg('output', '.workbuddy/optimization-p4')
  // Warm start: --init <summary.json or params json> seeds the CMA-ES mean
  // from a previous round's bestParams instead of DEFAULT_GOD_AI_PARAMS.
  const initFile = arg('init', '')
  const initialSigma = parseFloat(arg('sigma', '1.0')!)
  let initParams: GodAIParams = DEFAULT_GOD_AI_PARAMS
  if (initFile) {
    const raw = JSON.parse(readFileSync(initFile, 'utf8'))
    initParams = { ...DEFAULT_GOD_AI_PARAMS, ...(raw.bestParams ?? raw) }
    process.stderr.write(`Warm start from ${initFile} (sigma=${initialSigma})\n`)
  }

  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const stages = stageIdxs.map((idx) => STAGES[idx]).filter(Boolean)

  if (stages.length === 0) {
    console.error(`No valid stages found: ${stageIdxs.join(',')}`)
    process.exit(1)
  }

  process.stderr.write(`\nGod AI CMA-ES Optimizer (P4 — floor-aware, all-35 classic)\n`)
  process.stderr.write(
    `Stages: ${stages.map((s, i) => `S${stageIdxs[i]}(${s.name})`).join(', ')} | Difficulty: ${difficulty} | Seeds: ${seeds.join(',')}\n`,
  )
  process.stderr.write(`Generations: ${generations} | Max ticks: ${maxTicks} | Floor: ${floor}\n`)

  const optSeed = Number(arg('opt-seed', '1'))
  searchRng = new RNG(Number.isFinite(optSeed) ? optSeed : 1)
  process.stderr.write(`Search seed: ${optSeed} (--opt-seed to vary; runs are reproducible)\n`)

  // v7 is the default evaluation standard (DECISIONS §57). Pass --fitness v5
  // or --fitness v6 to compare against legacy bands.
  const fitnessVersion = arg('fitness', 'v7')
  const fv = fitnessVersion === 'v5' ? 'v5' : fitnessVersion === 'v6' ? 'v6' : 'v7'
  const evalConfig: EvalConfig = {
    stage: stages[0],
    stages,
    difficulty,
    seeds,
    maxTicks,
    floor,
    fitnessVersion: fv,
    stageRefs: fv !== 'v5' ? loadStageRefs() : undefined,
    scoreConfigBase: fv === 'v7' ? V7_SCORE_CONFIG : DEFAULT_SCORE_CONFIG,
  }

  // Worker pool: hw.ncpu − 1 workers by default (leave one core for the
  // system); --serial forces the single-threaded reference path.
  const pool = serial ? null : new SimWorkerPool()
  process.stderr.write(
    pool
      ? `Parallel evaluation: ${pool.size} workers (1 core reserved for the system)\n`
      : `Serial evaluation (--serial)\n`,
  )

  // Run optimization.
  const result = await optimize(evalConfig, generations, verbose, pool, initParams, initialSigma)
  pool?.terminate()

  // Save optimization results.
  mkdirSync(outputDir, { recursive: true })

  const summary = {
    timestamp: new Date().toISOString(),
    config: {
      stageIdxs,
      stageNames: stages.map((s) => s.name),
      difficulty,
      seeds,
      generations,
      maxTicks,
    },
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
      minW: Math.round(h.bestEvalResult.minStageWin * 1000) / 1000,
      floorPenalty: Math.round(h.bestEvalResult.floorPenalty),
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

  // P3.5: Skip comparison traces for multi-stage (too many traces)
  if (!stages || stages.length === 1) {
    const traceDir = join(outputDir, 'traces')
    runComparisonTraces(result.bestParams, evalConfig, traceDir)
  }

  // Generate optimization log.
  const logLines: string[] = []
  logLines.push(`# CMA-ES Optimization Log`)
  logLines.push(``)
  logLines.push(`**Date**: ${summary.timestamp}`)
  logLines.push(
    `**Stages**: ${stages.map((s, i) => `S${stageIdxs[i]}(${s.name})`).join(', ')} | **Difficulty**: ${difficulty} | **Seeds**: ${seeds.join(',')}`,
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
  logLines.push(`| Gen | Best Fit | Mean Fit | σ | Win | MinW | Base | Kills | FloorPen |`)
  logLines.push(`|-----|----------|----------|---|-----|------|------|-------|----------|`)
  for (const h of summary.history) {
    logLines.push(
      `| ${h.gen} | ${h.bestFit} | ${h.meanFit} | ${h.sigma} | ${(h.win * 100).toFixed(0)}% | ${(h.minW * 100).toFixed(0)}% | ${(h.base * 100).toFixed(0)}% | ${h.kills} | ${h.floorPenalty} |`,
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
