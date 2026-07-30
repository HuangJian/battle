/**
 * godai-score.ts — v6 evaluation model for God AI strategy tuning.
 *
 * Design: plan/God-AI-Evaluation-Redesign.md
 *
 * Replaces the binary win/lose signal (and the v5 magic-number fitness sum)
 * with a four-layer pipeline:
 *
 *   L1  dimension normalisation ....... every raw quantity → [0,1]
 *   L2  banded per-run composite ...... loss ∈ [0, 0.55], clear ∈ [0.60, 1.0]
 *   L3  seed aggregation .............. mean blended with CVaR (tail risk)
 *   L4  stage aggregation ............. power mean (soft-min) + stage CVaR
 *                                       → lower confidence bound
 *
 * Six axioms hold by construction and are asserted in tests/godai-score.test.ts:
 *   A1 outcome dominance     any clear scores above any non-clear
 *   A2 monotonicity          more kills / lives / base integrity never hurts
 *   A3 boundedness           every score is in [0,1]
 *   A4 no dominated exploit  follows from A2 + A3
 *   A5 determinism           pure function of the run record (no RNG, no I/O)
 *   A6 difficulty neutrality time/tempo normalisers are calibrated per stage
 *
 * This module lives in tools/ and only observes a finished run — it never
 * touches src/ and never mutates a World (AGENTS §2.1).
 */

import type { RunTelemetry } from './simulation-runner'
import { ENEMIES_PER_STAGE, START_LIVES } from '../src/constants'
import { PLAYER_PROGRESSION } from '../src/config/combat'

/**
 * The minimal shape the scorer needs.
 *
 * Deliberately narrower than `SimResult`: a full `SimResult` satisfies it
 * structurally, but so does a worker-pool payload (which cannot carry the
 * event log across the thread boundary) and so does a hand-written literal in
 * a test. Keeping the scorer decoupled from the runner is what lets the axiom
 * tests assert on synthetic runs without booting a simulation.
 */
export interface ScorableRun {
  outcome: string
  ticks: number
  finalState: {
    killCount: number
    lives: number
    baseAlive: boolean
  }
  firstKillTick?: number
  telemetry?: RunTelemetry
}

// ============================================================
// L1 — Dimensions
// ============================================================

/** Stable identifiers for the 11 scored dimensions (design §3). */
export type DimensionKey =
  | 'progress' // π  kills / enemies
  | 'lives' // λ  lives remaining / start lives
  | 'baseIntegrity' // β  base alive + protection-ring survival
  | 'clearSpeed' // σ  how fast the stage was cleared (clears only)
  | 'tempo' // τ  kills per minute vs the stage reference
  | 'accuracy' // ε  kills per shot vs the stage reference
  | 'loot' // ρ  power-ups captured / power-ups offered
  | 'growth' // γ  final star level / max star level
  | 'baseSafety' // θ  1 − mean base pressure
  | 'openingTempo' // ω  how quickly the first kill landed
  | 'mobility' // μ  distinct cells visited (anti-oscillation)

/**
 * Per-stage reference values. Anything time- or rate-shaped must be normalised
 * against the stage it was measured on, or the optimizer is rewarded for
 * farming easy stages (axiom A6).
 *
 * Produced by `bun tools/eval-suite.ts --calibrate` from a baseline corpus;
 * `DEFAULT_STAGE_REFS` is the cold-start fallback.
 */
export interface StageRefs {
  /** Ticks at/below which a clear counts as maximally fast (corpus P25). */
  clearTicksFast: number
  /** Ticks at/above which a clear scores zero on speed (corpus P95). */
  clearTicksSlow: number
  /** Kills-per-minute that scores 1.0 on tempo (corpus P75). */
  kpmRef: number
  /** Kills-per-shot that scores 1.0 on accuracy (corpus P75). */
  accuracyRef: number
  /** First-kill tick at/above which the opening scores zero (corpus P90). */
  openingTicksRef: number
  /** Distinct visited cells that score 1.0 on mobility (corpus P75). */
  mobilityRef: number
}

export const DEFAULT_STAGE_REFS: StageRefs = {
  clearTicksFast: 3600, // 60 s
  clearTicksSlow: 14400, // 240 s
  kpmRef: 8,
  accuracyRef: 0.3,
  openingTicksRef: 1800, // 30 s
  mobilityRef: 110,
}

/** A scored dimension. `value` is null when the dimension does not apply. */
export interface DimensionScore {
  key: DimensionKey
  /** Normalised score in [0,1], or null when not applicable to this run. */
  value: number | null
  /** The raw quantity the score was derived from (for reporting). */
  raw: number
}

function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/** Linear ramp: `lo` → 0, `hi` → 1, clamped. Handles hi < lo (descending). */
function ramp(x: number, lo: number, hi: number): number {
  if (hi === lo) return x >= hi ? 1 : 0
  return clamp01((x - lo) / (hi - lo))
}

/**
 * Compute all 11 dimensions for one run.
 *
 * Dimensions that cannot be measured return `value: null` and are dropped from
 * the weighted mean, with the surviving weights renormalised (design §4.2).
 * This is what keeps stages without power-ups, or curriculum arenas without a
 * base, from being systematically underscored.
 */
export function computeDimensions(
  result: ScorableRun,
  refs: StageRefs = DEFAULT_STAGE_REFS,
): Record<DimensionKey, DimensionScore> {
  const t: RunTelemetry | undefined = result.telemetry
  const cleared = result.outcome === 'stage_clear'
  const enemyTotal = t?.enemyTotal ?? ENEMIES_PER_STAGE
  const startLives = t?.startLives ?? START_LIVES
  const kills = result.finalState.killCount
  const ticks = result.ticks
  const minutes = ticks / 3600 // 60 ticks/s × 60 s

  const dim = (key: DimensionKey, value: number | null, raw: number): DimensionScore => ({
    key,
    value,
    raw,
  })

  // π — progress. The answer to "0 kills and 19 kills must not score alike".
  const progress = enemyTotal > 0 ? clamp01(kills / enemyTotal) : null

  // λ — lives retained. The answer to "3 lives left and 0 lives left must not
  // score alike". Extra lives from a `tank` power-up can push this above the
  // starting count; that surplus is credited through `loot`, not here.
  const lives = startLives > 0 ? clamp01(result.finalState.lives / startLives) : null

  // β — base integrity. A destroyed base is 0. A surviving base is worth 0.55
  // outright plus up to 0.45 for its protection ring: a base whose wall has
  // been stripped survived, but only barely, and that difference is a leading
  // indicator of the next run's collapse.
  let baseIntegrity: number | null
  if (!result.finalState.baseAlive) {
    baseIntegrity = 0
  } else if (t && t.baseWallTotal > 0) {
    baseIntegrity = 0.55 + 0.45 * clamp01(t.baseWallIntact / t.baseWallTotal)
  } else if (t && t.baseWallTotal === 0) {
    // Stage has no protection ring (or no base at all) — nothing to measure.
    baseIntegrity = null
  } else {
    baseIntegrity = 1 // no telemetry: fall back to the binary signal
  }

  // σ — clear speed, only meaningful when the stage was actually cleared.
  const clearSpeed = cleared ? 1 - ramp(ticks, refs.clearTicksFast, refs.clearTicksSlow) : null

  // τ — tempo. Separates "ground out 19 kills then timed out" from "ten
  // minutes of zero-kill paralysis": both are losses, only one is close.
  const kpm = minutes > 0 ? kills / minutes : 0
  const tempo = refs.kpmRef > 0 ? clamp01(kpm / refs.kpmRef) : null

  // ε — fire efficiency. Catches shooting-at-a-wall idling for near-zero cost.
  const accuracy =
    t && t.playerShots > 0 && refs.accuracyRef > 0
      ? clamp01(kills / t.playerShots / refs.accuracyRef)
      : null

  // ρ — loot capture rate, not raw count: a stage that only offers two
  // power-ups must not be penalised for it.
  const loot = t && t.powerUpsSpawned > 0 ? clamp01(t.powerUpsCollected / t.powerUpsSpawned) : null

  // γ — firepower growth actually retained at the end (dying resets it).
  const maxLevel = PLAYER_PROGRESSION.maximumLevel
  const growth = t && maxLevel > 0 ? clamp01(t.finalPlayerLevel / maxLevel) : null

  // θ — base safety: the dense counterpart to the rare `base_destroyed` event.
  const baseSafety = t && t.basePressureSamples > 0 ? clamp01(1 - t.basePressureMean) : null

  // ω — opening tempo. No kill at all is the worst possible opening.
  const firstKill = result.firstKillTick
  const openingTempo = firstKill === undefined ? 0 : 1 - ramp(firstKill, 0, refs.openingTicksRef)

  // μ — mobility, the anti-oscillation guard.
  const mobility = t && refs.mobilityRef > 0 ? clamp01(t.cellsVisited / refs.mobilityRef) : null

  return {
    progress: dim('progress', progress, kills),
    lives: dim('lives', lives, result.finalState.lives),
    baseIntegrity: dim('baseIntegrity', baseIntegrity, t ? t.baseWallIntact : -1),
    clearSpeed: dim('clearSpeed', clearSpeed, ticks),
    tempo: dim('tempo', tempo, kpm),
    accuracy: dim('accuracy', accuracy, t && t.playerShots > 0 ? kills / t.playerShots : 0),
    loot: dim('loot', loot, t ? t.powerUpsCollected : 0),
    growth: dim('growth', growth, t ? t.finalPlayerLevel : 0),
    baseSafety: dim('baseSafety', baseSafety, t ? t.basePressureMean : 0),
    openingTempo: dim('openingTempo', openingTempo, firstKill ?? -1),
    mobility: dim('mobility', mobility, t ? t.cellsVisited : 0),
  }
}

// ============================================================
// L2 — Banded composite
// ============================================================

export type Weights = Partial<Record<DimensionKey, number>>

/**
 * Quality-of-victory weights. "What does winning well look like" is a value
 * judgement, so these stay a human prior (design §5.1) — there is no ground
 * truth to regress them against.
 */
export const DEFAULT_WIN_WEIGHTS: Weights = {
  lives: 0.34,
  clearSpeed: 0.22,
  baseIntegrity: 0.16,
  baseSafety: 0.1,
  loot: 0.08,
  growth: 0.06,
  accuracy: 0.04,
}

/**
 * Closeness-to-victory weights. Unlike the win band, this one IS a prediction
 * problem — "which losses were nearly wins" has a ground truth — so these are
 * calibrated against it rather than asserted (design §5.3).
 *
 * Fitted by `eval-suite --calibrate` (35 stages × 30 seeds, classic): a
 * class-weighted logistic P(clear | board state observed mid-run), trained on
 * 2138 observations from 1026 runs that were still undecided at ticks
 * 1800/3000/4200. Grouped 5-fold CV AUC 0.780, in-sample 0.795 — an optimism
 * gap of only 0.015, so the skill is real and not memorisation.
 *
 * The stored values are the fit shrunk toward the previous hand prior at
 * λ=0.56 (tied to CV AUC). The raw fit zeroed `tempo`, `openingTempo` and
 * `loot`; shrinkage keeps them at a small floor so the loss band still shapes
 * those behaviours instead of going flat in three directions.
 *
 * The substantive correction is `lives`: 0.12 → 0.256. It ranks outcomes about
 * as well on its own as anything except progress (solo AUC 0.675) and is
 * essentially uncorrelated with it (r = −0.01), so it carries independent
 * information the hand prior discounted roughly threefold. Meanwhile `tempo`
 * looked useful (solo AUC 0.652) but is 53% correlated with `progress` — it was
 * mostly being paid twice for the same fact.
 *
 * Re-run `bun tools/eval-suite.ts --calibrate --seeds 60` after any change that
 * shifts how runs are won or lost.
 */
export const DEFAULT_LOSS_WEIGHTS: Weights = {
  progress: 0.477,
  lives: 0.256,
  baseIntegrity: 0.17,
  baseSafety: 0.044,
  tempo: 0.026,
  openingTempo: 0.018,
  loot: 0.009,
}

/**
 * Band geometry. The gap between `LOSS_BAND_MAX` and `CLEAR_BAND_MIN` is what
 * enforces axiom A1 — no amount of style points can lift a loss past a win.
 */
export const LOSS_BAND_MAX = 0.55
export const CLEAR_BAND_MIN = 0.6

export interface ScoreConfig {
  winWeights: Weights
  lossWeights: Weights
  refs: StageRefs
}

export const DEFAULT_SCORE_CONFIG: ScoreConfig = {
  winWeights: DEFAULT_WIN_WEIGHTS,
  lossWeights: DEFAULT_LOSS_WEIGHTS,
  refs: DEFAULT_STAGE_REFS,
}

export interface RunScore {
  /** Final composite in [0,1]. */
  score: number
  /** Band quality in [0,1] — the score before the band affine transform. */
  quality: number
  cleared: boolean
  dims: Record<DimensionKey, DimensionScore>
  /** Weights actually used after dropping inapplicable dimensions. */
  effectiveWeights: Record<string, number>
}

/**
 * Weighted mean over applicable dimensions, with the weights of dropped
 * dimensions redistributed proportionally across the survivors.
 *
 * Redistribution (rather than treating a missing dimension as 0) is what makes
 * scores comparable across stages that offer different opportunities.
 */
function weightedQuality(
  dims: Record<DimensionKey, DimensionScore>,
  weights: Weights,
): { quality: number; used: Record<string, number> } {
  let totalWeight = 0
  let acc = 0
  const used: Record<string, number> = {}
  for (const [key, w] of Object.entries(weights) as Array<[DimensionKey, number]>) {
    const d = dims[key]
    if (!d || d.value === null || w <= 0) continue
    totalWeight += w
    acc += w * d.value
    used[key] = w
  }
  if (totalWeight === 0) return { quality: 0, used }
  for (const key of Object.keys(used)) used[key] /= totalWeight
  return { quality: clamp01(acc / totalWeight), used }
}

/** Score a single run (L1 + L2). Pure function of `result` (axiom A5). */
export function scoreRun(
  result: ScorableRun,
  config: ScoreConfig = DEFAULT_SCORE_CONFIG,
): RunScore {
  const dims = computeDimensions(result, config.refs)
  const cleared = result.outcome === 'stage_clear'
  const weights = cleared ? config.winWeights : config.lossWeights
  const { quality, used } = weightedQuality(dims, weights)
  const score = cleared ? CLEAR_BAND_MIN + (1 - CLEAR_BAND_MIN) * quality : LOSS_BAND_MAX * quality
  return { score, quality, cleared, dims, effectiveWeights: used }
}

// ============================================================
// L3 — Seed aggregation (risk-adjusted)
// ============================================================

export interface AggregationConfig {
  /** Tail fraction for the within-stage CVaR. */
  seedCvarQ: number
  /** Blend weight of the within-stage CVaR against the mean. */
  seedCvarWeight: number
  /** Exponent of the cross-stage power mean. p<1 favours lifting weak stages. */
  stagePowerP: number
  /** Tail fraction for the cross-stage CVaR. */
  stageCvarQ: number
  /** Blend weight of the cross-stage CVaR against the power mean. */
  stageCvarWeight: number
  /** z multiplier for the lower confidence bound. */
  lcbZ: number
  /** Floor applied before the power mean, so p<0 cannot divide by zero. */
  epsilon: number
}

export const DEFAULT_AGGREGATION: AggregationConfig = {
  seedCvarQ: 0.25,
  seedCvarWeight: 0.3,
  stagePowerP: -1,
  stageCvarQ: 0.2,
  stageCvarWeight: 0.35,
  lcbZ: 1.0,
  epsilon: 0.02,
}

/**
 * Conditional Value at Risk: the mean of the worst `q` fraction of samples.
 *
 * Preferred over a variance penalty because variance punishes upside and
 * downside alike, while only the downside — a run that collapses — is what we
 * actually want to price in. CVaR is also convex, so it does not manufacture
 * spurious local optima for CMA-ES.
 */
export function cvar(values: number[], q: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const k = Math.max(1, Math.ceil(q * sorted.length))
  let sum = 0
  for (let i = 0; i < k; i++) sum += sorted[i]
  return sum / k
}

/**
 * Generalised power mean. One knob interpolates the whole family:
 *   p = 1  arithmetic mean (the old behaviour)
 *   p = 0  geometric mean
 *   p = −1 harmonic mean (default)
 *   p → −∞ minimum
 *
 * This replaces the discontinuous `floorPenalty * 8000` from v5: the marginal
 * value of improving stage s is proportional to s^(p−1), so p<1 automatically
 * makes the weakest stages the most valuable to fix — smoothly, and with a
 * single interpretable parameter instead of a magic magnitude.
 */
export function powerMean(values: number[], p: number, epsilon = 0): number {
  if (values.length === 0) return 0
  const v = values.map((x) => Math.max(epsilon, x))
  if (Math.abs(p) < 1e-9) {
    // p → 0 is the geometric mean.
    let logSum = 0
    for (const x of v) logSum += Math.log(x)
    return Math.exp(logSum / v.length)
  }
  let sum = 0
  for (const x of v) sum += Math.pow(x, p)
  return Math.pow(sum / v.length, 1 / p)
}

export interface StageAggregate {
  stageName: string
  /** Risk-adjusted stage score in [0,1]. */
  score: number
  mean: number
  cvar: number
  /** Standard error of the mean over seeds. */
  se: number
  /** Classic win rate — kept as the human-facing sanity metric. */
  winRate: number
  runs: number
  /** Per-run composites, in seed order. */
  runScores: number[]
}

export function aggregateStage(
  stageName: string,
  runScores: RunScore[],
  config: AggregationConfig = DEFAULT_AGGREGATION,
): StageAggregate {
  const scores = runScores.map((r) => r.score)
  const n = scores.length
  if (n === 0) {
    return {
      stageName,
      score: 0,
      mean: 0,
      cvar: 0,
      se: 0,
      winRate: 0,
      runs: 0,
      runScores: [],
    }
  }
  const mean = scores.reduce((a, b) => a + b, 0) / n
  const tail = cvar(scores, config.seedCvarQ)
  const score = (1 - config.seedCvarWeight) * mean + config.seedCvarWeight * tail
  const variance = n > 1 ? scores.reduce((a, s) => a + (s - mean) ** 2, 0) / (n - 1) : 0
  const se = n > 0 ? Math.sqrt(variance / n) : 0
  const winRate = runScores.filter((r) => r.cleared).length / n
  return { stageName, score, mean, cvar: tail, se, winRate, runs: n, runScores: scores }
}

// ============================================================
// L4 — Suite aggregation
// ============================================================

export interface SuiteScore {
  /** Risk-adjusted suite score in [0,1] — the headline number. */
  suite: number
  /** Lower confidence bound; this is what optimisation should rank on. */
  lcb: number
  /** Cross-stage power mean (soft minimum). */
  powerMean: number
  /** Mean of the weakest `stageCvarQ` fraction of stages. */
  stageCvar: number
  /** Standard error of the suite score. */
  se: number
  /** Plain arithmetic mean of stage scores — for comparison with the old view. */
  arithmeticMean: number
  /** Classic mean win rate — the unchanged external acceptance metric. */
  meanWinRate: number
  /** Weakest stage score, with its name. */
  worstStage: { name: string; score: number; winRate: number } | null
  stages: StageAggregate[]
}

export function aggregateSuite(
  stages: StageAggregate[],
  config: AggregationConfig = DEFAULT_AGGREGATION,
): SuiteScore {
  if (stages.length === 0) {
    return {
      suite: 0,
      lcb: 0,
      powerMean: 0,
      stageCvar: 0,
      se: 0,
      arithmeticMean: 0,
      meanWinRate: 0,
      worstStage: null,
      stages: [],
    }
  }
  const scores = stages.map((s) => s.score)
  const pm = powerMean(scores, config.stagePowerP, config.epsilon)
  const scv = cvar(scores, config.stageCvarQ)
  const suite = (1 - config.stageCvarWeight) * pm + config.stageCvarWeight * scv
  // Stages are independent runs, so their standard errors add in quadrature.
  const se = Math.sqrt(stages.reduce((a, s) => a + s.se ** 2, 0)) / stages.length
  const lcb = suite - config.lcbZ * se
  const arithmeticMean = scores.reduce((a, b) => a + b, 0) / scores.length
  const meanWinRate = stages.reduce((a, s) => a + s.winRate, 0) / stages.length
  let worst = stages[0]
  for (const s of stages) if (s.score < worst.score) worst = s
  return {
    suite,
    lcb,
    powerMean: pm,
    stageCvar: scv,
    se,
    arithmeticMean,
    meanWinRate,
    worstStage: { name: worst.stageName, score: worst.score, winRate: worst.winRate },
    stages,
  }
}

/** The scalar CMA-ES maximises. ×1000 is cosmetic — ranking is scale-free. */
export function fitnessV6(suite: SuiteScore): number {
  return suite.lcb * 1000
}

// ============================================================
// Weight diagnostics (design §5.2)
// ============================================================

export interface WeightDiagnostic {
  key: string
  nominal: number
  /** Standard deviation of the dimension across the corpus. */
  stdev: number
  /** w·σ normalised — the share of ranking influence the dimension really has. */
  effective: number
  /** Runs in which the dimension was applicable. */
  samples: number
}

/**
 * Report nominal vs effective weights over a corpus of runs.
 *
 * A dimension that barely varies contributes nothing to ranking no matter how
 * large its nominal weight, because ranking responds to w·σ, not w. Skipping
 * this check is the most common way a hand-tuned scorecard ends up measuring
 * something other than what its author intended.
 */
export function diagnoseWeights(
  runs: RunScore[],
  weights: Weights,
  filter: (r: RunScore) => boolean = () => true,
): WeightDiagnostic[] {
  const subset = runs.filter(filter)
  const out: WeightDiagnostic[] = []
  let totalInfluence = 0
  const rows: Array<{ key: string; nominal: number; stdev: number; samples: number }> = []
  for (const [key, w] of Object.entries(weights) as Array<[DimensionKey, number]>) {
    const vals: number[] = []
    for (const r of subset) {
      const v = r.dims[key]?.value
      if (v !== null && v !== undefined) vals.push(v)
    }
    if (vals.length === 0) {
      rows.push({ key, nominal: w, stdev: 0, samples: 0 })
      continue
    }
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length
    const variance =
      vals.length > 1 ? vals.reduce((a, v) => a + (v - mean) ** 2, 0) / (vals.length - 1) : 0
    const stdev = Math.sqrt(variance)
    rows.push({ key, nominal: w, stdev, samples: vals.length })
    totalInfluence += w * stdev
  }
  for (const row of rows) {
    out.push({
      key: row.key,
      nominal: row.nominal,
      stdev: row.stdev,
      effective: totalInfluence > 0 ? (row.nominal * row.stdev) / totalInfluence : 0,
      samples: row.samples,
    })
  }
  out.sort((a, b) => b.effective - a.effective)
  return out
}

// ============================================================
// Paired comparison (design §6)
// ============================================================

export interface PairedComparison {
  n: number
  meanDelta: number
  /** Standard error of the paired mean difference. */
  se: number
  /** Paired t statistic (meanDelta / se). */
  t: number
  /** Two-sided normal-approximation p-value. */
  p: number
  wins: number
  losses: number
  ties: number
}

/**
 * Paired comparison of two parameter sets over identical (stage, seed) cells.
 *
 * Pairing is what makes the common-random-numbers discipline pay off: the
 * per-cell difference cancels the "some seeds are just harder" variance, and
 * because the score is continuous rather than binary it carries far more
 * information per run than a win-rate diff of the same size.
 */
export function comparePaired(a: number[], b: number[]): PairedComparison {
  const n = Math.min(a.length, b.length)
  if (n === 0) return { n: 0, meanDelta: 0, se: 0, t: 0, p: 1, wins: 0, losses: 0, ties: 0 }
  const deltas: number[] = []
  let wins = 0
  let losses = 0
  let ties = 0
  for (let i = 0; i < n; i++) {
    const d = b[i] - a[i]
    deltas.push(d)
    if (d > 1e-9) wins++
    else if (d < -1e-9) losses++
    else ties++
  }
  const meanDelta = deltas.reduce((x, y) => x + y, 0) / n
  const variance = n > 1 ? deltas.reduce((x, d) => x + (d - meanDelta) ** 2, 0) / (n - 1) : 0
  const se = Math.sqrt(variance / n)
  // Zero within-pair variance is the degenerate case, and it must not be
  // collapsed to t = 0: a constant non-zero shift across every paired cell is
  // the *most* significant result possible, not the least. Reporting t = 0
  // here would print "no significant difference" for a change that improved
  // literally every run — the worst possible failure mode for an A/B gate.
  const t = se > 0 ? meanDelta / se : meanDelta === 0 ? 0 : meanDelta > 0 ? Infinity : -Infinity
  return { n, meanDelta, se, t, p: twoSidedNormalP(t), wins, losses, ties }
}

/** Two-sided p-value under the normal approximation (n is large here). */
function twoSidedNormalP(z: number): number {
  const x = Math.abs(z) / Math.SQRT2
  // Abramowitz & Stegun 7.1.26 approximation of erf.
  const tt = 1 / (1 + 0.3275911 * x)
  const y =
    1 -
    ((((1.061405429 * tt - 1.453152027) * tt + 1.421413741) * tt - 0.284496736) * tt +
      0.254829592) *
      tt *
      Math.exp(-x * x)
  return clamp01(1 - y)
}
