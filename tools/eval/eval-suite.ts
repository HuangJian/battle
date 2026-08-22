/**
 * eval-suite.ts — v6 God AI evaluation harness.
 *
 * Design: plan/God-AI-Evaluation-Redesign.md
 *
 * Replaces `eval-all-stages.ts` (win rate per stage, then a flat average)
 * with the four-layer scorecard from godai-score.ts. The old tool is kept as
 * the human-facing sanity check; this one is what tuning should rank on.
 *
 * Usage
 *   bun tools/eval/eval-suite.ts                       full 35-stage scorecard
 *   bun tools/eval/eval-suite.ts --seeds 60            more seeds (see §6: ≥60 to conclude)
 *   bun tools/eval/eval-suite.ts --stages 7,19,33      subset
 *   bun tools/eval/eval-suite.ts --params best.json    score a tuned parameter set
 *   bun tools/eval/eval-suite.ts --dims                per-dimension breakdown
 *   bun tools/eval/eval-suite.ts --weights             nominal vs effective weight audit
 *   bun tools/eval/eval-suite.ts --calibrate           regenerate tools/eval/eval-refs.json
 *   bun tools/eval/eval-suite.ts --compare a.json b.json    paired A/B under CRN
 *   bun tools/eval/eval-suite.ts --json out.json       machine-readable dump
 *
 * Every run uses common random numbers: the same seed list is applied to
 * every stage and every parameter set, so A/B differences are paired and the
 * "some seeds are just harder" variance cancels out (design §6).
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask, SimTaskResult } from '../sim/sim-worker'
import { runSimulation } from '../sim/simulation-runner'
import { parseStageSpec, StageSpecError, runHeader } from '../lib/stage-spec'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  diagnoseWeights,
  comparePaired,
  fitnessV6,
  DEFAULT_STAGE_REFS,
  DEFAULT_SCORE_CONFIG,
  V7_SCORE_CONFIG,
  DEFAULT_WIN_WEIGHTS,
  DEFAULT_LOSS_WEIGHTS,
  type StageRefs,
  type ScorableRun,
  type RunScore,
  type StageAggregate,
  type SuiteScore,
  type DimensionKey,
  type ScoreConfig,
  type Weights,
} from './godai-score'

const REFS_FILE = join(import.meta.dir, 'eval-refs.json')
const DEFAULT_MAX_TICKS = 18000

// ============================================================
// CLI plumbing
// ============================================================

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback
}
function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}

/**
 * Accept any of the shapes our tooling writes: a bare GodAIParams object, an
 * optimizer summary (`{ bestParams }`), or a candidate record (`{ params }`).
 */
function loadParams(path: string): GodAIParams {
  const raw = JSON.parse(readFileSync(path, 'utf8'))
  const obj = raw.bestParams ?? raw.params ?? raw
  return { ...DEFAULT_GOD_AI_PARAMS, ...obj } as GodAIParams
}

/** Per-stage references, keyed by stage name. */
type RefsFile = Record<string, StageRefs>

function loadRefs(): RefsFile {
  if (!existsSync(REFS_FILE)) return {}
  try {
    return JSON.parse(readFileSync(REFS_FILE, 'utf8')).stages ?? {}
  } catch {
    return {}
  }
}

/**
 * Select the base score config from the --fitness flag.
 *
 * v7 is the default evaluation standard (DECISIONS §57): its wider band gap
 * (0.40 → 0.70) realigns the optimizer with win rate — converting a loss to a
 * win is the single highest-margin improvement available. Pass `--fitness v6`
 * to compare against the legacy v6 bands.
 */
function scoreConfigBase(): ScoreConfig {
  return arg('fitness') === 'v6' ? DEFAULT_SCORE_CONFIG : V7_SCORE_CONFIG
}

function configFor(stageName: string, refsFile: RefsFile): ScoreConfig {
  return { ...scoreConfigBase(), refs: refsFile[stageName] ?? DEFAULT_STAGE_REFS }
}

// ============================================================
// Running the corpus
// ============================================================

interface Cell {
  stageIndex: number
  stageName: string
  seed: number
  run: ScorableRun
}

/**
 * Run every (stage, seed) cell with telemetry on.
 *
 * Ordering is stage-major, seed-minor and results are re-indexed by task id,
 * so the corpus is identical whether it came from the pool or the serial
 * fallback — the same property the v5 optimizer relies on (sim-pool.ts).
 */
async function runCorpus(
  params: GodAIParams,
  stageIdxs: number[],
  seeds: number[],
  difficulty: string,
  maxTicks: number,
  pool: SimWorkerPool | null,
): Promise<Cell[]> {
  const cells: Array<{ stageIndex: number; stageName: string; seed: number }> = []
  for (const si of stageIdxs) {
    for (const seed of seeds) {
      cells.push({ stageIndex: si, stageName: STAGES[si].name, seed })
    }
  }

  if (!pool) {
    return cells.map((c) => {
      const r = runSimulation({
        seed: c.seed,
        stage: STAGES[c.stageIndex],
        difficulty,
        godAIParams: params,
        maxTicks,
        sampleInterval: maxTicks,
        telemetry: true,
      })
      return { ...c, run: r as ScorableRun }
    })
  }

  const tasks: SimTask[] = cells.map((c, i) => ({
    id: i,
    seed: c.seed,
    stage: STAGES[c.stageIndex],
    difficulty,
    params,
    maxTicks,
    telemetry: true,
  }))
  const results = await pool.runBatch(tasks)
  return cells.map((c, i) => ({ ...c, run: toScorable(results[i]) }))
}

/** A failed worker task is scored as the worst possible run, not dropped. */
function toScorable(r: SimTaskResult): ScorableRun {
  if (!r.ok) {
    return {
      outcome: 'error',
      ticks: 0,
      finalState: { killCount: 0, lives: 0, baseAlive: false },
    }
  }
  return {
    outcome: r.outcome,
    ticks: r.ticks,
    finalState: { killCount: r.killCount, lives: r.lives ?? 0, baseAlive: r.baseAlive },
    firstKillTick: r.firstKillTick,
    telemetry: r.telemetry,
  }
}

interface ScoredCorpus {
  cells: Cell[]
  runScores: RunScore[]
  stages: StageAggregate[]
  suite: SuiteScore
}

function scoreCorpus(cells: Cell[], refsFile: RefsFile): ScoredCorpus {
  const runScores = cells.map((c) => scoreRun(c.run, configFor(c.stageName, refsFile)))
  const byStage = new Map<string, RunScore[]>()
  const order: string[] = []
  for (let i = 0; i < cells.length; i++) {
    const name = cells[i].stageName
    if (!byStage.has(name)) {
      byStage.set(name, [])
      order.push(name)
    }
    byStage.get(name)!.push(runScores[i])
  }
  const stages = order.map((name) => aggregateStage(name, byStage.get(name)!))
  return { cells, runScores, stages, suite: aggregateSuite(stages) }
}

// ============================================================
// Reporting
// ============================================================

function pct(x: number): string {
  return `${(x * 100).toFixed(0)}%`
}

function printScorecard(sc: ScoredCorpus, stageIdxs: number[]): void {
  console.log('\n' + '─'.repeat(86))
  console.log(
    'stage'.padEnd(26) +
      'score'.padStart(8) +
      'mean'.padStart(8) +
      'CVaR'.padStart(8) +
      '±se'.padStart(8) +
      'win'.padStart(7) +
      '  distribution',
  )
  console.log('─'.repeat(86))
  for (let i = 0; i < sc.stages.length; i++) {
    const s = sc.stages[i]
    const label = `S${stageIdxs[i] + 1} ${s.stageName}`
    console.log(
      label.slice(0, 25).padEnd(26) +
        s.score.toFixed(3).padStart(8) +
        s.mean.toFixed(3).padStart(8) +
        s.cvar.toFixed(3).padStart(8) +
        s.se.toFixed(3).padStart(8) +
        pct(s.winRate).padStart(7) +
        '  ' +
        sparkline(s.runScores),
    )
  }
  console.log('─'.repeat(86))

  const q = sc.suite
  console.log(`\nSUITE   ${q.suite.toFixed(4)}   (lcb ${q.lcb.toFixed(4)}  ±se ${q.se.toFixed(4)})`)
  console.log(
    `  power mean (p=-1) ${q.powerMean.toFixed(4)}   stage CVaR ${q.stageCvar.toFixed(4)}   arithmetic ${q.arithmeticMean.toFixed(4)}`,
  )
  console.log(`  mean win rate     ${pct(q.meanWinRate)}   (the old headline metric)`)
  if (q.worstStage) {
    console.log(
      `  weakest stage     ${q.worstStage.name} — score ${q.worstStage.score.toFixed(3)}, win ${pct(q.worstStage.winRate)}`,
    )
  }
  console.log(`  fitness v6        ${fitnessV6(q).toFixed(1)}`)
}

/** Eight-level bar sparkline of the per-seed score distribution. */
function sparkline(values: number[]): string {
  const blocks = ' ▁▂▃▄▅▆▇█'
  return values.map((v) => blocks[Math.min(8, Math.max(0, Math.round(v * 8)))]).join('')
}

const DIM_ORDER: DimensionKey[] = [
  'progress',
  'lives',
  'baseIntegrity',
  'clearSpeed',
  'tempo',
  'accuracy',
  'loot',
  'growth',
  'baseSafety',
  'openingTempo',
  'mobility',
]

function printDimensions(sc: ScoredCorpus): void {
  console.log('\nPer-dimension means (n/a = dimension not applicable, weight redistributed)')
  console.log('─'.repeat(86))
  console.log(
    'dimension'.padEnd(16) +
      'all runs'.padStart(10) +
      'clears'.padStart(10) +
      'losses'.padStart(10) +
      'n/a'.padStart(8),
  )
  console.log('─'.repeat(86))
  for (const key of DIM_ORDER) {
    const all: number[] = []
    const wins: number[] = []
    const losses: number[] = []
    let na = 0
    for (const r of sc.runScores) {
      const v = r.dims[key].value
      if (v === null) {
        na++
        continue
      }
      all.push(v)
      ;(r.cleared ? wins : losses).push(v)
    }
    const m = (a: number[]): string =>
      a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(3) : '  n/a'
    console.log(
      key.padEnd(16) +
        m(all).padStart(10) +
        m(wins).padStart(10) +
        m(losses).padStart(10) +
        String(na).padStart(8),
    )
  }
  console.log('─'.repeat(86))
}

function printWeights(sc: ScoredCorpus): void {
  for (const [label, weights, filter] of [
    ['CLEARS (win band)', DEFAULT_WIN_WEIGHTS, (r: RunScore) => r.cleared],
    ['LOSSES (loss band)', DEFAULT_LOSS_WEIGHTS, (r: RunScore) => !r.cleared],
  ] as const) {
    const rows = diagnoseWeights(sc.runScores, weights, filter)
    const n = sc.runScores.filter(filter).length
    console.log(`\nWeight audit — ${label}   (n=${n})`)
    console.log('  A dimension only influences ranking through w·σ, not w alone.')
    console.log('─'.repeat(70))
    console.log(
      'dimension'.padEnd(16) +
        'nominal'.padStart(10) +
        'stdev'.padStart(10) +
        'effective'.padStart(12) +
        'drift'.padStart(10),
    )
    console.log('─'.repeat(70))
    for (const r of rows) {
      const drift = r.effective - r.nominal
      console.log(
        r.key.padEnd(16) +
          r.nominal.toFixed(3).padStart(10) +
          r.stdev.toFixed(3).padStart(10) +
          r.effective.toFixed(3).padStart(12) +
          (drift >= 0 ? '+' : '') +
          drift.toFixed(3).padStart(drift >= 0 ? 9 : 10),
      )
    }
    console.log('─'.repeat(70))
  }
}

// ============================================================
// --calibrate: derive per-stage references from a baseline corpus
// ============================================================

function percentile(values: number[], p: number): number {
  if (values.length === 0) return NaN
  const s = [...values].sort((a, b) => a - b)
  const idx = Math.min(s.length - 1, Math.max(0, Math.round(p * (s.length - 1))))
  return s[idx]
}

/**
 * Fit stage references from measured behaviour rather than guessing them.
 *
 * Every ref is a corpus percentile, which is what makes the time- and
 * rate-shaped dimensions difficulty-neutral (axiom A6): "fast for this stage"
 * is defined by what this stage actually produces, so a 20-enemy maze and an
 * open field are held to their own standards instead of a global constant.
 *
 * Percentile choices:
 *   clearTicksFast  P25 of clears — the quarter that went well
 *   clearTicksSlow  P95 of clears — beyond this, speed stops discriminating
 *   kpmRef          P75 of all runs — reachable, not exceptional
 *   accuracyRef     P75 of all runs
 *   openingTicksRef P90 of first kills — a genuinely late opening
 *   mobilityRef     P75 of all runs
 */
function calibrateStage(cells: Cell[]): StageRefs {
  const clearTicks: number[] = []
  const kpm: number[] = []
  const acc: number[] = []
  const opening: number[] = []
  const mobility: number[] = []

  for (const c of cells) {
    const r = c.run
    const t = r.telemetry
    if (r.outcome === 'stage_clear') clearTicks.push(r.ticks)
    const minutes = r.ticks / 3600
    if (minutes > 0) kpm.push(r.finalState.killCount / minutes)
    if (t && t.playerShots > 0) acc.push(r.finalState.killCount / t.playerShots)
    if (r.firstKillTick !== undefined) opening.push(r.firstKillTick)
    if (t) mobility.push(t.cellsVisited)
  }

  const d = DEFAULT_STAGE_REFS
  // A stage nobody clears has no speed evidence; keep the defaults rather
  // than inventing a reference from an empty sample.
  const fast = clearTicks.length >= 3 ? percentile(clearTicks, 0.25) : d.clearTicksFast
  const slow = clearTicks.length >= 3 ? percentile(clearTicks, 0.95) : d.clearTicksSlow
  return {
    clearTicksFast: Math.round(fast),
    // Guard the degenerate case where every clear took the same time.
    clearTicksSlow: Math.round(Math.max(slow, fast + 600)),
    kpmRef: round3(Math.max(0.5, kpm.length ? percentile(kpm, 0.75) : d.kpmRef)),
    accuracyRef: round3(Math.max(0.02, acc.length ? percentile(acc, 0.75) : d.accuracyRef)),
    openingTicksRef: Math.round(
      Math.max(300, opening.length ? percentile(opening, 0.9) : d.openingTicksRef),
    ),
    mobilityRef: Math.round(
      Math.max(10, mobility.length ? percentile(mobility, 0.75) : d.mobilityRef),
    ),
  }
}

function round3(x: number): number {
  return Math.round(x * 1000) / 1000
}

/**
 * Fit loss-band weights by logistic regression.
 *
 * The win band is a value judgement, but "which losses were nearly wins" is a
 * prediction problem with ground truth, so it should be regressed rather than
 * asserted (design §5.3). A dimension earns weight in proportion to how much
 * it actually predicts winning.
 *
 * CRITICAL — the features must come from a *checkpoint*, not from the finished
 * run. Fitting P(clear | final dimensions) looks superb (99.9% accuracy) and
 * is worthless: `progress` is kills/enemyTotal, which is exactly 1.0 if and
 * only if the run cleared, so the model just rediscovers its own label. The
 * caller supplies features observed at tick T and labels from the full run, on
 * cells that were still undecided at T — see `checkpointDataset`.
 *
 * Negative coefficients are clipped to zero — a dimension that anti-predicts
 * victory must not be allowed to *subtract* score, or the optimizer gains an
 * incentive to game it downward (axiom A4).
 */
/** A dataset row: dimension values at a checkpoint, the eventual label, and the
 *  identity of the run it came from (so CV can split without leaking). */
interface FitSample {
  features: RunScore
  cleared: boolean
  /** Run identity (stage×seed). Several checkpoints share one group. */
  group: number
}

/** Design matrix built from samples, dropping rows with inapplicable dimensions. */
function buildMatrix(
  samples: FitSample[],
  keys: DimensionKey[],
): { X: number[][]; y: number[]; groups: number[] } {
  const X: number[][] = []
  const y: number[] = []
  const groups: number[] = []
  for (const s of samples) {
    const row: number[] = []
    let usable = true
    for (const k of keys) {
      const v = s.features.dims[k].value
      if (v === null) {
        usable = false
        break
      }
      row.push(v)
    }
    if (!usable) continue
    X.push(row)
    y.push(s.cleared ? 1 : 0)
    groups.push(s.group)
  }
  return { X, y, groups }
}

/**
 * Class-weighted logistic regression on standardised features.
 *
 * Standardisation stats come from the training rows only — computing them over
 * the full dataset would leak test-set distribution into the fit and inflate
 * cross-validated AUC.
 */
function trainLogistic(
  X: number[][],
  y: number[],
): { w: number[]; b: number; mean: number[]; sd: number[] } {
  const d = X[0]?.length ?? 0
  const mean: number[] = Array.from({ length: d }, () => 0)
  const sd: number[] = Array.from({ length: d }, () => 0)
  for (const row of X) for (let j = 0; j < d; j++) mean[j] += row[j]
  for (let j = 0; j < d; j++) mean[j] /= Math.max(1, X.length)
  for (const row of X) for (let j = 0; j < d; j++) sd[j] += (row[j] - mean[j]) ** 2
  for (let j = 0; j < d; j++) sd[j] = Math.sqrt(sd[j] / Math.max(1, X.length - 1)) || 1

  const Z = X.map((row) => row.map((v, j) => (v - mean[j]) / sd[j]))

  // Class weighting. Most undecided runs eventually clear (base rate ~90%),
  // so an unweighted fit can minimise loss by ignoring the rare failures —
  // which are precisely the runs the loss band exists to rank. Weighting each
  // class to equal total mass makes the two errors equally expensive.
  const rate = y.length > 0 ? y.reduce((a, b) => a + b, 0) / y.length : 0
  const wPos = rate > 0 ? 0.5 / rate : 1
  const wNeg = rate < 1 ? 0.5 / (1 - rate) : 1
  const sampleW = y.map((label) => (label === 1 ? wPos : wNeg))
  const wSum = sampleW.reduce((a, x) => a + x, 0) || 1

  const w: number[] = Array.from({ length: d }, () => 0)
  let b = 0
  const lr = 0.1
  const l2 = 1e-3
  for (let epoch = 0; epoch < 4000; epoch++) {
    const gw: number[] = Array.from({ length: d }, () => 0)
    let gb = 0
    for (let i = 0; i < Z.length; i++) {
      let z = b
      for (let j = 0; j < d; j++) z += w[j] * Z[i][j]
      const p = 1 / (1 + Math.exp(-z))
      const err = (p - y[i]) * sampleW[i]
      for (let j = 0; j < d; j++) gw[j] += err * Z[i][j]
      gb += err
    }
    for (let j = 0; j < d; j++) w[j] -= lr * (gw[j] / wSum + l2 * w[j])
    b -= lr * (gb / wSum)
  }
  return { w, b, mean, sd }
}

/** Decision score for a raw (unstandardised) feature row. */
function logisticScore(
  model: { w: number[]; b: number; mean: number[]; sd: number[] },
  row: number[],
): number {
  let z = model.b
  for (let j = 0; j < row.length; j++) z += model.w[j] * ((row[j] - model.mean[j]) / model.sd[j])
  return z
}

/**
 * Grouped k-fold cross-validated AUC.
 *
 * Plain k-fold would be dishonest here: each run contributes one row per
 * checkpoint, and those rows share a label and are highly correlated. A random
 * split puts tick 1800 of a run in train and tick 3000 of the *same* run in
 * test, so the model can half-memorise the answer. Splitting by run identity
 * (stage×seed) removes that path and makes the number an honest estimate of
 * "does board shape predict the outcome of a run I have never seen".
 */
function groupedCvAuc(samples: FitSample[], keys: DimensionKey[], folds = 5): number {
  const { X, y, groups } = buildMatrix(samples, keys)
  if (X.length < 40) return 0.5
  const uniq = [...new Set(groups)].sort((a, b) => a - b)
  if (uniq.length < folds) return 0.5
  const foldOf = new Map<number, number>()
  uniq.forEach((g, i) => foldOf.set(g, i % folds))

  const scores: number[] = []
  const labels: number[] = []
  for (let f = 0; f < folds; f++) {
    const trX: number[][] = []
    const trY: number[] = []
    const teX: number[][] = []
    const teY: number[] = []
    for (let i = 0; i < X.length; i++) {
      if (foldOf.get(groups[i]) === f) {
        teX.push(X[i])
        teY.push(y[i])
      } else {
        trX.push(X[i])
        trY.push(y[i])
      }
    }
    if (trX.length === 0 || teX.length === 0) continue
    const model = trainLogistic(trX, trY)
    for (let i = 0; i < teX.length; i++) {
      scores.push(logisticScore(model, teX[i]))
      labels.push(teY[i])
    }
  }
  return rankAuc(scores, labels)
}

/**
 * Blend the fitted weights toward the hand prior in proportion to how much the
 * fit has actually earned.
 *
 * Adopting the raw fit is the wrong move even when the fit is good. The
 * regression answers one question — "which mid-run boards go on to clear" —
 * and answers it under multicollinearity, so it happily assigns exactly zero
 * to any dimension whose information is already carried by another. Zero is
 * different from small: a dimension at zero weight contributes no gradient at
 * all, and the loss band stops shaping behaviour it was deliberately built to
 * shape. Shrinkage keeps those dimensions alive at reduced influence while
 * still applying the corrections the data genuinely supports.
 *
 * λ is tied to cross-validated AUC, not in-sample fit: no skill (0.5) keeps the
 * prior untouched, and even a strong fit is capped at 0.8 so the prior always
 * retains a voice. This is ridge-toward-prior, the standard treatment for an
 * estimate that is informative but neither unbiased nor complete.
 *
 * Both inputs sum to 1, so the convex combination does too.
 */
function shrinkToPrior(
  fitted: Record<string, number>,
  prior: Weights,
  cvAuc: number,
): { weights: Record<string, number>; lambda: number } {
  const lambda = Math.max(0, Math.min(0.8, 2 * (cvAuc - 0.5)))
  const keys = new Set([...Object.keys(fitted), ...Object.keys(prior)])
  const raw: Record<string, number> = {}
  let total = 0
  for (const k of keys) {
    const v = lambda * (fitted[k] ?? 0) + (1 - lambda) * ((prior as Record<string, number>)[k] ?? 0)
    raw[k] = v
    total += v
  }
  const weights: Record<string, number> = {}
  if (total > 0) for (const k of Object.keys(raw)) weights[k] = round3(raw[k] / total)
  return { weights, lambda: round3(lambda) }
}

/**
 * AUC of each dimension used alone, plus its correlation with `progress`.
 *
 * This is the diagnostic that separates the two reasons a coefficient can come
 * out negative:
 *
 *   - genuinely anti-predictive: univariate AUC below 0.5 too. The dimension
 *     really does track losing, and zero weight is correct.
 *   - a collinearity artifact: univariate AUC above 0.5 while the multivariate
 *     coefficient is negative. At a fixed checkpoint tick, `tempo` is close to
 *     a rescaling of `progress`, so the regression can hand all the credit to
 *     one and a negative correction to the other. The dimension is not harmful;
 *     it is merely redundant.
 *
 * Both end up clipped to zero weight, but only the second case means "the
 * information is already counted elsewhere" — which is what you need to know
 * before concluding the dimension is worthless.
 */
function marginalDiagnostics(
  samples: FitSample[],
  keys: DimensionKey[],
): Record<string, { auc: number; corrWithProgress: number }> {
  const { X, y } = buildMatrix(samples, keys)
  const out: Record<string, { auc: number; corrWithProgress: number }> = {}
  if (X.length === 0) return out
  const pIdx = keys.indexOf('progress' as DimensionKey)
  const col = (j: number): number[] => X.map((r) => r[j])
  const pCol = pIdx >= 0 ? col(pIdx) : null
  keys.forEach((k, j) => {
    const c = col(j)
    out[k] = { auc: round3(rankAuc(c, y)), corrWithProgress: pCol ? round3(pearson(c, pCol)) : 0 }
  })
  return out
}

function pearson(a: number[], b: number[]): number {
  const n = a.length
  if (n === 0) return 0
  const ma = a.reduce((x, v) => x + v, 0) / n
  const mb = b.reduce((x, v) => x + v, 0) / n
  let num = 0
  let da = 0
  let db = 0
  for (let i = 0; i < n; i++) {
    const u = a[i] - ma
    const v = b[i] - mb
    num += u * v
    da += u * u
    db += v * v
  }
  const den = Math.sqrt(da * db)
  return den > 0 ? num / den : 0
}

function fitLossWeights(
  samples: FitSample[],
  keys: DimensionKey[],
): {
  weights: Record<string, number>
  accuracy: number
  /** Ranking quality, in-sample. 0.5 = coin flip. Optimistic — see `cvAuc`. */
  auc: number
  /** Grouped 5-fold cross-validated AUC. This is the number to trust. */
  cvAuc: number
  baseRate: number
  coefficients: Record<string, number>
  marginal: Record<string, { auc: number; corrWithProgress: number }>
  n: number
  groups: number
} {
  const { X, y, groups } = buildMatrix(samples, keys)
  const baseRate = y.length > 0 ? y.reduce((a, b) => a + b, 0) / y.length : 0
  const groupCount = new Set(groups).size
  if (X.length < 20) {
    return {
      weights: { ...DEFAULT_LOSS_WEIGHTS } as Record<string, number>,
      accuracy: 0,
      auc: 0.5,
      cvAuc: 0.5,
      baseRate,
      coefficients: {},
      marginal: {},
      n: X.length,
      groups: groupCount,
    }
  }

  const model = trainLogistic(X, y)
  const scores: number[] = []
  let correct = 0
  for (let i = 0; i < X.length; i++) {
    const z = logisticScore(model, X[i])
    scores.push(z)
    if ((z > 0 ? 1 : 0) === y[i]) correct++
  }
  const auc = rankAuc(scores, y)
  const cvAuc = groupedCvAuc(samples, keys)

  const coefficients: Record<string, number> = {}
  keys.forEach((k, j) => (coefficients[k] = round3(model.w[j])))

  const positive = keys.map((_, j) => Math.max(0, model.w[j]))
  const total = positive.reduce((a, x) => a + x, 0)
  const weights: Record<string, number> = {}
  if (total > 0) {
    keys.forEach((k, j) => {
      const v = positive[j] / total
      if (v >= 0.005) weights[k] = round3(v)
    })
  }
  return {
    weights,
    accuracy: correct / X.length,
    auc,
    cvAuc,
    baseRate,
    coefficients,
    marginal: marginalDiagnostics(samples, keys),
    n: X.length,
    groups: groupCount,
  }
}

/**
 * AUC via the Mann–Whitney U identity: the probability that a randomly chosen
 * winner is ranked above a randomly chosen loser.
 *
 * This is the metric that matters here. Accuracy is meaningless at a 90% base
 * rate — "always predict clear" scores 90% while ranking nothing — whereas AUC
 * is invariant to class balance and measures exactly what the loss band needs:
 * ordering runs by how close they came. Ties count as half, so a constant
 * predictor scores 0.5 rather than looking good by accident.
 */
function rankAuc(scores: number[], labels: number[]): number {
  const idx = scores.map((s, i) => ({ s, y: labels[i] })).sort((a, b) => a.s - b.s)
  // Average ranks over ties.
  const ranks: number[] = Array.from({ length: idx.length }, () => 0)
  let i = 0
  while (i < idx.length) {
    let j = i
    while (j + 1 < idx.length && idx[j + 1].s === idx[i].s) j++
    const avg = (i + j) / 2 + 1
    for (let k = i; k <= j; k++) ranks[k] = avg
    i = j + 1
  }
  let sumPosRanks = 0
  let nPos = 0
  for (let k = 0; k < idx.length; k++) {
    if (idx[k].y === 1) {
      sumPosRanks += ranks[k]
      nPos++
    }
  }
  const nNeg = idx.length - nPos
  if (nPos === 0 || nNeg === 0) return 0.5
  return (sumPosRanks - (nPos * (nPos + 1)) / 2) / (nPos * nNeg)
}

/**
 * Build the leakage-free calibration dataset.
 *
 * Exploits a property the determinism contract gives us for free: a run capped
 * at `T` ticks is the same run paused at `T` — the trajectory is a pure
 * function of (seed, stage, difficulty, params) and nothing in the Simulation
 * reads `maxTicks`. So re-running the corpus with a smaller cap yields exact
 * checkpoint states with no extra instrumentation.
 *
 * Cells already decided at T are excluded: a finished run is not a prediction
 * problem, and keeping them would reintroduce the very leakage we are avoiding.
 */
async function checkpointDataset(
  params: GodAIParams,
  stageIdxs: number[],
  seeds: number[],
  difficulty: string,
  checkpoints: number[],
  fullCells: Cell[],
  refsFile: RefsFile,
  pool: SimWorkerPool | null,
): Promise<FitSample[]> {
  const out: FitSample[] = []
  // Several checkpoints are pooled rather than picking one "best" T. A single
  // early T sees too few decided outcomes; a single late T loses the runs that
  // already finished (and the survivors are a biased sample). Pooling asks the
  // time-invariant question the loss band actually needs answered: "given a
  // board in this shape, at any point mid-run, how likely is a clear?"
  for (const T of checkpoints) {
    const cpCells = await runCorpus(params, stageIdxs, seeds, difficulty, T, pool)
    for (let i = 0; i < cpCells.length; i++) {
      // Still running at T ⇒ genuinely undecided ⇒ a real prediction target.
      if (cpCells[i].run.outcome !== 'max_ticks') continue
      out.push({
        features: scoreRun(cpCells[i].run, configFor(cpCells[i].stageName, refsFile)),
        cleared: fullCells[i].run.outcome === 'stage_clear',
        // Cell index is the stage×seed identity: the same run observed at a
        // different checkpoint keeps the same group, so CV can hold it out whole.
        group: i,
      })
    }
  }
  return out
}

// ============================================================
// main
// ============================================================

async function main(): Promise<void> {
  const seedCount = Number(arg('seeds', '20'))
  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const difficulty = arg('difficulty', 'classic')!
  const maxTicks = Number(arg('max-ticks', String(DEFAULT_MAX_TICKS)))
  const stageArg = arg('stages')
  let stageIdxs: number[]
  if (stageArg) {
    try {
      stageIdxs = parseStageSpec(stageArg, STAGES.length)
    } catch (e) {
      console.error(
        e instanceof StageSpecError ? e.message : `eval-suite: invalid --stages: ${stageArg}`,
      )
      process.exit(1)
    }
  } else {
    stageIdxs = STAGES.map((_, i) => i)
  }
  const serial = flag('serial')
  const pool = serial ? null : new SimWorkerPool()

  // Load candidate params BEFORE the caliber line — the header must trace the
  // params actually run, not the defaults (review P1: printed default hash
  // while running --params candidates).
  const params = arg('params') ? loadParams(arg('params')!) : DEFAULT_GOD_AI_PARAMS

  const totalRuns = stageIdxs.length * seeds.length
  process.stderr.write(
    `eval-suite v6 · ${stageIdxs.length} stages × ${seeds.length} seeds = ${totalRuns} runs` +
      `${pool ? ` · ${pool.size} workers` : ' · serial'}\n`,
  )
  // M0 §3.2 official caliber line (stageIndex=0 keeps gate/eval parity).
  process.stderr.write(
    runHeader({
      difficulty,
      stageCount: stageIdxs.length,
      seedCount: seeds.length,
      stageIndex: 0,
      maxTicks,
      params,
    }) + '\n',
  )
  if (seeds.length < 60) {
    process.stderr.write(
      `note: ${seeds.length} seeds is a screening budget. Use --seeds 60 before` +
        ` calling a difference real (design §6).\n`,
    )
  }

  try {
    // ---- --compare A B: paired A/B under common random numbers ----
    const compareIdx = process.argv.indexOf('--compare')
    if (compareIdx >= 0) {
      const pathA = process.argv[compareIdx + 1]
      const pathB = process.argv[compareIdx + 2]
      if (!pathA || !pathB) {
        console.error('usage: --compare <a.json> <b.json>')
        process.exit(1)
      }
      const refsFile = loadRefs()
      const t0 = Date.now()
      const cellsA = await runCorpus(
        loadParams(pathA),
        stageIdxs,
        seeds,
        difficulty,
        maxTicks,
        pool,
      )
      const cellsB = await runCorpus(
        loadParams(pathB),
        stageIdxs,
        seeds,
        difficulty,
        maxTicks,
        pool,
      )
      const scA = scoreCorpus(cellsA, refsFile)
      const scB = scoreCorpus(cellsB, refsFile)
      process.stderr.write(
        `ran ${totalRuns * 2} sims in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
      )

      console.log(`\nA = ${pathA}`)
      printScorecard(scA, stageIdxs)
      console.log(`\nB = ${pathB}`)
      printScorecard(scB, stageIdxs)

      const cmp = comparePaired(
        scA.runScores.map((r) => r.score),
        scB.runScores.map((r) => r.score),
      )
      console.log('\n' + '═'.repeat(70))
      console.log('PAIRED COMPARISON  (B − A, matched on stage+seed)')
      console.log('═'.repeat(70))
      console.log(`  paired cells    ${cmp.n}`)
      console.log(
        `  mean Δscore     ${cmp.meanDelta >= 0 ? '+' : ''}${cmp.meanDelta.toFixed(4)} ± ${cmp.se.toFixed(4)}`,
      )
      console.log(`  t / p           ${cmp.t.toFixed(2)} / ${cmp.p.toFixed(4)}`)
      console.log(`  B better/worse/tied  ${cmp.wins} / ${cmp.losses} / ${cmp.ties}`)
      console.log(`  suite A → B     ${scA.suite.suite.toFixed(4)} → ${scB.suite.suite.toFixed(4)}`)
      console.log(`  win rate A → B  ${pct(scA.suite.meanWinRate)} → ${pct(scB.suite.meanWinRate)}`)

      // Per-stage breakdown. The suite number is a harmonic mean over stages,
      // so it deliberately reacts more to the weak tail than a flat average
      // would — a suite delta can therefore be several times the mean paired
      // delta if the change happened to land on the stages that were already
      // struggling. Without this table you cannot tell "slightly worse
      // everywhere" from "fine everywhere except two stages that collapsed",
      // and those call for completely different responses.
      console.log('\n  per-stage Δ (paired within stage, B − A):')
      const perStage: Array<{ name: string; d: number; p: number; a: number; b: number }> = []
      for (const st of stageIdxs) {
        const name = STAGES[st].name
        const ia = scA.runScores
          .map((r, i) => ({ r, i }))
          .filter(({ i }) => scA.cells[i].stageName === name)
        const a = ia.map(({ r }) => r.score)
        const b = ia.map(({ i }) => scB.runScores[i].score)
        const c = comparePaired(a, b)
        const aggA = scA.stages.find((s) => s.stageName === name)
        const aggB = scB.stages.find((s) => s.stageName === name)
        perStage.push({
          name,
          d: c.meanDelta,
          p: c.p,
          a: aggA?.score ?? 0,
          b: aggB?.score ?? 0,
        })
      }
      perStage.sort((x, y) => x.d - y.d)
      const notable = perStage.filter((s) => s.p < 0.05)
      if (notable.length === 0) {
        console.log('    no individual stage moved significantly (all p ≥ 0.05)')
      } else {
        for (const s of notable) {
          const arrow = s.d > 0 ? '▲' : '▼'
          console.log(
            `    ${arrow} ${s.name.slice(0, 20).padEnd(21)}` +
              `Δ ${(s.d >= 0 ? '+' : '') + s.d.toFixed(4)}  p=${s.p.toFixed(4)}  ` +
              `stage ${s.a.toFixed(3)} → ${s.b.toFixed(3)}`,
          )
        }
        console.log(
          `    (${notable.filter((s) => s.d < 0).length} worse, ` +
            `${notable.filter((s) => s.d > 0).length} better, ` +
            `${perStage.length - notable.length} unchanged)`,
        )
      }

      const verdict =
        cmp.p < 0.05
          ? cmp.meanDelta > 0
            ? 'B is better (p < 0.05)'
            : 'B is worse (p < 0.05)'
          : 'no significant difference — do not ship on this evidence'
      console.log(`\n  VERDICT: ${verdict}`)
      if (cmp.p >= 0.05 && notable.length > 0) {
        console.log(
          `  NOTE: the suite is flat overall, but ${notable.length} stage(s) moved\n` +
            `  significantly. A wash on average can still be a real regression on\n` +
            `  specific stages — check the table before dismissing the change.`,
        )
      }
      return
    }

    // ---- single-corpus paths ----
    // params already loaded above (before the caliber line).
    const t0 = Date.now()
    const cells = await runCorpus(params, stageIdxs, seeds, difficulty, maxTicks, pool)
    process.stderr.write(`ran ${totalRuns} sims in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`)

    if (flag('calibrate')) {
      // Calibrate against the DEFAULT parameters unless told otherwise: the
      // references describe the stage, so they must not drift every time a
      // candidate parameter set happens to play the stage differently.
      const byStage = new Map<string, Cell[]>()
      for (const c of cells) {
        if (!byStage.has(c.stageName)) byStage.set(c.stageName, [])
        byStage.get(c.stageName)!.push(c)
      }
      const stages: RefsFile = {}
      for (const [name, group] of byStage) stages[name] = calibrateStage(group)

      // Loss weights are regressed from checkpoint states, not finished runs.
      const sc = scoreCorpus(cells, stages)
      const lossKeys = Object.keys(DEFAULT_LOSS_WEIGHTS) as DimensionKey[]
      const checkpoints = (arg('checkpoints', '1800,3000,4200') as string)
        .split(',')
        .map((s) => Number(s.trim()))
        .filter((t) => t > 0)
      process.stderr.write(
        `fitting loss weights from checkpoints at ticks ${checkpoints.join(', ')}...\n`,
      )
      const dataset = await checkpointDataset(
        params,
        stageIdxs,
        seeds,
        difficulty,
        checkpoints,
        cells,
        stages,
        pool,
      )
      const fit = fitLossWeights(dataset, lossKeys)
      // The same fit on finished runs — kept only to show how badly the naive
      // formulation lies. `progress` == 1 iff cleared, so it scores ~100%.
      const leaky = fitLossWeights(
        sc.runScores.map((r, i) => ({ features: r, cleared: r.cleared, group: i })),
        lossKeys,
      )

      const adopt = shrinkToPrior(fit.weights, DEFAULT_LOSS_WEIGHTS, fit.cvAuc)

      const out = {
        generatedAt: new Date().toISOString(),
        source: {
          tool: 'tools/eval/eval-suite.ts --calibrate',
          stages: stageIdxs.length,
          seeds: seeds.length,
          difficulty,
          maxTicks,
          params: arg('params') ?? 'DEFAULT_GOD_AI_PARAMS',
        },
        stages,
        lossWeightFit: {
          note:
            `Class-weighted logistic P(final clear | dimensions observed mid-run), ` +
            'standardised, negative coefficients clipped, fitted only on cells still ' +
            'undecided at each checkpoint. Judge it by AUC, not accuracy. See ' +
            'plan/God-AI-Evaluation-Redesign.md §5.3.',
          checkpoints,
          samples: fit.n,
          runs: fit.groups,
          baseRate: round3(fit.baseRate),
          cvAuc: round3(fit.cvAuc),
          aucInSample: round3(fit.auc),
          accuracy: round3(fit.accuracy),
          coefficients: fit.coefficients,
          marginal: fit.marginal,
          rawFittedWeights: fit.weights,
          currentWeights: DEFAULT_LOSS_WEIGHTS,
          shrinkage: {
            note:
              'Recommended weights: convex blend of the fit and the hand prior, ' +
              'lambda = clamp(2*(cvAuc-0.5), 0, 0.8). Prevents a good-but-collinear ' +
              'fit from zeroing dimensions the loss band exists to shape.',
            lambda: adopt.lambda,
            recommendedWeights: adopt.weights,
          },
          leakyControl: {
            note:
              'Same fit on FINISHED runs. progress == 1 iff cleared, so this is ' +
              'label leakage; reported only as a warning against using it.',
            accuracy: round3(leaky.accuracy),
            auc: round3(leaky.auc),
            cvAuc: round3(leaky.cvAuc),
            suggestedWeights: leaky.weights,
          },
        },
      }
      writeFileSync(REFS_FILE, JSON.stringify(out, null, 2))
      console.log(`\nWrote ${REFS_FILE}`)
      console.log('\nPer-stage references:')
      console.log('─'.repeat(86))
      console.log(
        'stage'.padEnd(24) +
          'fast'.padStart(8) +
          'slow'.padStart(8) +
          'kpm'.padStart(8) +
          'acc'.padStart(8) +
          'open'.padStart(8) +
          'mob'.padStart(8),
      )
      console.log('─'.repeat(86))
      for (const [name, r] of Object.entries(stages)) {
        console.log(
          name.slice(0, 23).padEnd(24) +
            String(r.clearTicksFast).padStart(8) +
            String(r.clearTicksSlow).padStart(8) +
            r.kpmRef.toFixed(2).padStart(8) +
            r.accuracyRef.toFixed(3).padStart(8) +
            String(r.openingTicksRef).padStart(8) +
            String(r.mobilityRef).padStart(8),
        )
      }
      console.log('─'.repeat(86))
      console.log(
        `\nLoss-weight fit — P(clear | mid-run state), checkpoints ${checkpoints.join('/')}, ` +
          `${fit.n} observations from ${fit.groups} undecided runs, ` +
          `base rate ${(fit.baseRate * 100).toFixed(1)}%`,
      )
      const auc = fit.cvAuc
      const verdict =
        auc >= 0.75
          ? 'strong'
          : auc >= 0.65
            ? 'usable'
            : auc >= 0.58
              ? 'weak'
              : 'no better than chance'
      console.log(
        `  grouped 5-fold CV AUC ${auc.toFixed(3)} (${verdict})` +
          `   [in-sample ${fit.auc.toFixed(3)}, optimism ${(fit.auc - auc >= 0 ? '+' : '') + (fit.auc - auc).toFixed(3)}]`,
      )
      console.log(
        `  accuracy ${(fit.accuracy * 100).toFixed(1)}% — ignore it, the base rate alone scores ` +
          `${(Math.max(fit.baseRate, 1 - fit.baseRate) * 100).toFixed(1)}%`,
      )
      if (auc < 0.65) {
        console.log(
          `  ⚠ CV AUC below 0.65: mid-run state does not predict the outcome well here.\n` +
            `    Keep the hand prior rather than adopting these weights.`,
        )
      }
      console.log(
        '  dimension'.padEnd(18) +
          'coef'.padStart(9) +
          'rawFit'.padStart(9) +
          'current'.padStart(9) +
          'ADOPT'.padStart(9) +
          'soloAUC'.padStart(10) +
          'r(prog)'.padStart(10) +
          '  note',
      )
      for (const k of lossKeys) {
        const m = fit.marginal[k]
        const coef = fit.coefficients[k] ?? 0
        // Why a coefficient came out negative. Only the first case means the
        // dimension is actually useless; the others mean its information is
        // already counted, or that it tracks losing.
        let note = ''
        if (coef < 0 && m) {
          const r = Math.abs(m.corrWithProgress)
          if (m.auc < 0.48) note = 'tracks losing on its own'
          else if (m.auc <= 0.52) note = 'no signal (solo AUC ~ chance)'
          else if (r > 0.3) note = `redundant: r=${r.toFixed(2)} with progress`
          else note = 'suppressed — predicts alone but not jointly'
        }
        console.log(
          ('  ' + k).padEnd(18) +
            coef.toFixed(3).padStart(9) +
            (fit.weights[k] ?? 0).toFixed(3).padStart(9) +
            (DEFAULT_LOSS_WEIGHTS[k] ?? 0).toFixed(3).padStart(9) +
            (adopt.weights[k] ?? 0).toFixed(3).padStart(9) +
            (m ? m.auc.toFixed(3) : '  -  ').padStart(10) +
            (m ? m.corrWithProgress.toFixed(2) : '  -  ').padStart(10) +
            '  ' +
            note,
        )
      }
      console.log(
        `\n  ADOPT = ${adopt.lambda.toFixed(2)}·fit + ${(1 - adopt.lambda).toFixed(2)}·prior. ` +
          `The fit is trusted in proportion to its cross-validated skill, and the\n` +
          `  prior keeps weak dimensions at a non-zero floor so the loss band still shapes them.`,
      )
      console.log(
        `\n  [leakage control] the same fit on FINISHED runs scores AUC ` +
          `${leaky.auc.toFixed(3)} / ${(leaky.accuracy * 100).toFixed(1)}% accuracy — and is worthless:\n` +
          `  progress == 1.0 exactly when a run clears, so it just reads its own label.\n` +
          `  Never calibrate loss weights on finished-run dimensions.`,
      )
      console.log(
        '\nWeights are NOT applied automatically. Copy the ADOPT column into\n' +
          'DEFAULT_LOSS_WEIGHTS in tools/eval/godai-score.ts only after reviewing the fit.',
      )
      return
    }

    const sc = scoreCorpus(cells, loadRefs())
    printScorecard(sc, stageIdxs)
    if (flag('dims')) printDimensions(sc)
    if (flag('weights')) printWeights(sc)

    const jsonOut = arg('json')
    if (jsonOut) {
      writeFileSync(
        jsonOut,
        JSON.stringify(
          {
            generatedAt: new Date().toISOString(),
            config: {
              seeds: seeds.length,
              difficulty,
              maxTicks,
              params: arg('params') ?? 'default',
            },
            suite: { ...sc.suite, stages: undefined },
            stages: sc.stages,
            cells: sc.cells.map((c, i) => ({
              stage: c.stageName,
              seed: c.seed,
              outcome: c.run.outcome,
              score: round3(sc.runScores[i].score),
              kills: c.run.finalState.killCount,
              lives: c.run.finalState.lives,
              ticks: c.run.ticks,
            })),
          },
          null,
          2,
        ),
      )
      console.log(`\nWrote ${jsonOut}`)
    }
  } finally {
    pool?.terminate()
  }
}

await main()
