#!/usr/bin/env bun
/**
 * m1-eval.ts — M1 sim evaluation of the NN player policy (plan §NN-M1).
 *
 * Runs the trained NN policy headlessly across stages × seeds in parallel
 * (Bun worker pool) and reports the stage-clear win rate against the ≥60%
 * gate. Concurrency is adaptive: the live worker count tracks system CPU load
 * (>90% −1, <85% +1, capped at the physical core count, floored at 1) so a
 * long run never oversubscribes the machine or stalls. Uses `tools/sim
 * --policy nn` plumbing: each worker runs
 * `runSimulation({ policy: 'nn' })`, which builds an `NNInput` (InputLike)
 * driven by the auto-discovered latest weights.
 *
 * Evaluation dimensions: every run is also scored with the God AI score-v7
 * model (tools/eval/godai-score.ts, plan/God-AI-Evaluation-Redesign.md §3) so
 * the eval emits, per stage, the full 11-dimension breakdown plus a risk-
 * adjusted v7 composite. This is what makes "0 kills vs 19 kills" and
 * "3 lives left vs 0" distinguishable in the report — the binary win/lose
 * signal alone cannot. Telemetry is collected read-only (AGENTS §2.1) and does
 * not change any outcome.
 *
 * After the sim, a sortable HTML scorecard is written to --out (default
 * tmp/m1_eval_scorecard.html), in the same style as
 * tmp/god-ai-hard-35stage-scorecard.html.
 *
 * Usage:
 *   bun tools/sim/m1-eval.ts --stages all --seeds 1-10 --difficulty hard
 *   bun tools/sim/m1-eval.ts --stages 1-5 --seeds 1-3 --policy nn
 *   bun tools/sim/m1-eval.ts --stages 1 --seeds 1 --policy nn   # 1-game sanity
 *   bun tools/sim/m1-eval.ts --stages all --seeds 1-12 --out tmp/m1_eval_scorecard.html
 */

import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { AdaptiveSimWorkerPool, physicalCores } from './sim-pool'
import type { SimTask } from './sim-worker'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  V7_SCORE_CONFIG,
  DEFAULT_AGGREGATION,
  type DimensionKey,
  type RunScore,
  type ScorableRun,
  type StageAggregate,
} from '../eval/godai-score'
import { writeScorecardHtml, type ScorecardRow, type ScorecardSuite } from './scorecard-html'

/** The 11 scored dimensions of the God AI score-v7 model (design §3). */
const DIM_KEYS: DimensionKey[] = [
  'progress', // π  kills / enemies
  'lives', // λ  lives remaining / start lives
  'baseIntegrity', // β  base alive + protection-ring survival
  'clearSpeed', // σ  how fast the stage was cleared (clears only)
  'tempo', // τ  kills per minute vs the stage reference
  'accuracy', // ε  kills per shot vs the stage reference
  'loot', // ρ  power-ups captured / power-ups offered
  'growth', // γ  final star level / max star level
  'baseSafety', // θ  1 − mean base pressure
  'openingTempo', // ω  how quickly the first kill landed
  'mobility', // μ  distinct cells visited (anti-oscillation)
]

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [a, b] = spec.split('-').map(Number)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [Number(spec)]
}

async function main(): Promise<void> {
  const difficulty = arg('difficulty', 'hard')!
  const stageSpec = arg('stages', 'all')!
  const seedSpec = arg('seeds', '1-10')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const policy =
    (arg('policy', 'nn') as 'god' | 'nn' | 'intent' | 'intent-exec' | 'intent-oracle') ?? 'nn'
  const weightsDir = arg('weights-dir')
  const intentWeights = arg('intent-weights')
  const replan = parseInt(arg('replan', '0')!, 10) // M7① cadence 扫描（0 = 策略默认）
  const riskGated = arg('risk-gated') === '1' || arg('risk-gated') === 'true'
  const baseCadence = parseInt(arg('base-cadence', '0')!, 10)
  const dangerCadence = parseInt(arg('danger-cadence', '0')!, 10)
  // Max concurrency is the real physical core count (never oversubscribe the
  // machine). `--workers` may LOWER the cap for a conservative run but can never
  // exceed it. Live concurrency then tracks system CPU load via AdaptiveSimWorkerPool:
  //   load > 90% → −1 worker ; load < 85% → +1 worker ; floor = 1.
  const physical = physicalCores()
  const workers = Math.min(parseInt(arg('workers', String(physical))!, 10), physical)
  const outPath = arg('out', 'tmp/m1_eval_scorecard.html')!

  let stages
  let stageNames: string[]
  if (stageSpec === 'all') {
    stages = STAGES
    stageNames = STAGES.map((s) => s.name)
  } else if (stageSpec.includes('-')) {
    const [a, b] = stageSpec.split('-').map(Number)
    stages = STAGES.slice(a - 1, b)
    stageNames = stages.map((s) => s.name)
  } else {
    const idx = parseInt(stageSpec, 10) - 1
    stages = [STAGES[idx]]
    stageNames = [STAGES[idx].name]
  }
  const seeds = parseRange(seedSpec)

  process.stderr.write(
    `[m1-eval] policy=${policy} ${stages.length} stages × ${seeds.length} seeds = ${stages.length * seeds.length} games (workers=${workers}) -> ${outPath}\n`,
  )

  // Build tasks. `telemetry: true` makes the worker return RunTelemetry so the
  // score-v7 dimensions can be computed — read-only, outcome-preserving.
  const tasks: SimTask[] = []
  let id = 0
  for (let si = 0; si < stages.length; si++) {
    for (const seed of seeds) {
      tasks.push({
        id: id++,
        seed,
        stage: stages[si],
        difficulty,
        params: DEFAULT_GOD_AI_PARAMS as GodAIParams,
        maxTicks,
        stageIndex: si,
        policy,
        nnWeightsDir: weightsDir,
        intentWeightsDir: intentWeights,
        replanEvery: replan || undefined,
        riskGated,
        baseCadence: baseCadence || undefined,
        dangerCadence: dangerCadence || undefined,
        telemetry: true,
      })
    }
  }

  // Staged progress reporter (to stderr, so stdout stays clean JSON).
  // Prints ~20 waypoints + a final 100% line — no blind waiting.
  const totalGames = tasks.length
  const t0 = Date.now()
  const stepSize = Math.max(1, Math.ceil(totalGames / 20))
  let lastPrinted = 0
  const fmt = (ms: number): string => {
    const s = Math.round(ms / 1000)
    if (s < 60) return `${s}s`
    return `${Math.floor(s / 60)}m${s % 60}s`
  }
  const reportProgress = (doneN: number, totalN: number): void => {
    if (doneN < totalN && doneN - lastPrinted < stepSize) return
    lastPrinted = doneN
    const pct = Math.round((doneN / totalN) * 100)
    const elapsed = Date.now() - t0
    const rate = doneN / (elapsed / 1000) // games/sec
    const eta = rate > 0 ? (totalN - doneN) / rate : 0
    process.stderr.write(
      `[m1-eval] progress ${doneN}/${totalN} (${pct}%) elapsed ${fmt(elapsed)} eta ${fmt(eta * 1000)}\n`,
    )
  }

  const pool = new AdaptiveSimWorkerPool(workers, 1)
  pool.setAdjustHook((desired, load) => {
    process.stderr.write(`[m1-eval] concurrency ${desired} (cpu ${load}%)\n`)
  })
  const results = await pool.runAdaptive(tasks, reportProgress)
  const simSeconds = (Date.now() - t0) / 1000
  process.stderr.write(
    `[m1-eval] progress ${totalGames}/${totalGames} (100%) done in ${fmt(Date.now() - t0)}\n`,
  )

  // Aggregate.
  const total = results.length
  let cleared = 0
  const outcomes: Record<string, number> = {}
  let totalKills = 0
  let totalTicks = 0
  let nTicks = 0
  // Per-stage accumulators for the score-v7 dimensions. Tasks are built
  // stage-major / seed-minor, so the stage index is floor(id / seeds.length).
  const perStage: Record<
    number,
    {
      total: number
      cleared: number
      kills: number
      dimSums: Record<string, number>
      dimCounts: Record<string, number>
      runScores: RunScore[]
    }
  > = {}
  for (const r of results) {
    const si = r.id !== undefined ? Math.floor(r.id / seeds.length) : 0
    if (!perStage[si]) {
      perStage[si] = {
        total: 0,
        cleared: 0,
        kills: 0,
        dimSums: {},
        dimCounts: {},
        runScores: [],
      }
    }
    const acc = perStage[si]
    if (!r.ok) {
      outcomes['error'] = (outcomes['error'] ?? 0) + 1
      continue
    }
    outcomes[r.outcome] = (outcomes[r.outcome] ?? 0) + 1
    if (r.outcome === 'stage_clear') cleared++
    totalKills += r.killCount
    if (typeof r.ticks === 'number') {
      totalTicks += r.ticks
      nTicks++
    }
    acc.total++
    if (r.outcome === 'stage_clear') acc.cleared++
    acc.kills += r.killCount

    // Score this run with the God AI score-v7 model.
    const scorable: ScorableRun = {
      outcome: r.outcome,
      ticks: r.ticks,
      finalState: { killCount: r.killCount, lives: r.lives ?? 0, baseAlive: r.baseAlive },
      firstKillTick: r.firstKillTick,
      telemetry: r.telemetry,
    }
    const runScore = scoreRun(scorable, V7_SCORE_CONFIG)
    acc.runScores.push(runScore)
    for (const k of DIM_KEYS) {
      const v = runScore.dims[k]?.value
      if (v === null || v === undefined) continue
      acc.dimSums[k] = (acc.dimSums[k] ?? 0) + v
      acc.dimCounts[k] = (acc.dimCounts[k] ?? 0) + 1
    }
  }

  const winRate = total > 0 ? cleared / total : 0
  const gate = 0.6
  const pass = winRate >= gate

  // Per-stage breakdown: full risk-adjusted aggregate (score/mean/cvar/se/
  // winRate) + per-dimension means + average kills, in stage order.
  const stageAgg: StageAggregate[] = []
  const stageReports = Object.entries(perStage)
    .map(([siStr, acc]) => {
      const si = Number(siStr)
      const dims: Record<string, number> = {}
      for (const k of DIM_KEYS) {
        const c = acc.dimCounts[k] ?? 0
        dims[k] = c > 0 ? Number((acc.dimSums[k] / c).toFixed(3)) : 0
      }
      const sa = aggregateStage(
        stageNames[si] ?? `stage${si + 1}`,
        acc.runScores,
        DEFAULT_AGGREGATION,
      )
      stageAgg[si] = sa
      return {
        stage: stageNames[si] ?? `stage${si + 1}`,
        total: acc.total,
        cleared: acc.cleared,
        winRate: acc.total > 0 ? Number((acc.cleared / acc.total).toFixed(3)) : 0,
        avgKills: acc.total > 0 ? Number((acc.kills / acc.total).toFixed(2)) : 0,
        scoreV7: Number(sa.score.toFixed(4)),
        dims,
      }
    })
    .sort((a, b) => stageNames.indexOf(a.stage) - stageNames.indexOf(b.stage))

  // Top-level score-v7 suite (risk-adjusted headline, L4 aggregation).
  const suite = aggregateSuite(stageAgg.filter(Boolean), DEFAULT_AGGREGATION)
  const scoreV7 = {
    suite: Number(suite.suite.toFixed(4)),
    lcb: Number(suite.lcb.toFixed(4)),
    powerMean: Number(suite.powerMean.toFixed(4)),
    stageCvar: Number(suite.stageCvar.toFixed(4)),
    arithmeticMean: Number(suite.arithmeticMean.toFixed(4)),
    meanWinRate: Number(suite.meanWinRate.toFixed(4)),
    worstStage: suite.worstStage
      ? {
          name: suite.worstStage.name,
          score: Number(suite.worstStage.score.toFixed(4)),
          winRate: Number(suite.worstStage.winRate.toFixed(4)),
        }
      : null,
  }

  const report = {
    policy,
    difficulty,
    stages: stages.length,
    seeds: seeds.length,
    total,
    outcomes,
    winRate: Number(winRate.toFixed(4)),
    gate,
    pass,
    totalKills,
    avgKills: total > 0 ? Number((totalKills / total).toFixed(2)) : 0,
    avgTicks: nTicks > 0 ? Math.round(totalTicks / nTicks) : 0,
    scoreV7,
    perStage: stageReports,
  }

  console.log(JSON.stringify(report, null, 2))
  process.stderr.write(
    `\n[m1-eval] WIN RATE ${(winRate * 100).toFixed(1)}% (gate ${gate * 100}%) -> ${pass ? 'PASS' : 'FAIL'}\n`,
  )
  process.stderr.write(
    `[m1-eval] SCORE V7 suite=${scoreV7.suite} lcb=${scoreV7.lcb} meanWinRate=${scoreV7.meanWinRate}` +
      (scoreV7.worstStage
        ? ` worst=${scoreV7.worstStage.name}(${scoreV7.worstStage.winRate})\n`
        : '\n'),
  )

  // ---- HTML scorecard (mirrors tmp/god-ai-hard-35stage-scorecard.html) ----
  const htmlRows: ScorecardRow[] = stageReports.map((sr, i) => {
    const sa = stageAgg[stageNames.indexOf(sr.stage)]!
    return {
      idx: i + 1,
      name: sr.stage,
      score: sa.score,
      mean: sa.mean,
      cvar: sa.cvar,
      se: sa.se,
      winRate: sa.winRate,
      avgKills: sr.avgKills,
      dims: sr.dims,
    }
  })
  const htmlSuite: ScorecardSuite = {
    suite: suite.suite,
    lcb: suite.lcb,
    arithmeticMean: suite.arithmeticMean,
    meanWinRate: suite.meanWinRate,
    worstStage: suite.worstStage
      ? { name: suite.worstStage.name, winRate: suite.worstStage.winRate }
      : null,
  }
  try {
    const written = writeScorecardHtml(outPath, htmlRows, htmlSuite, {
      title: `NN 策略 关卡评分卡 — ${difficulty} 难度`,
      difficulty,
      stages: stages.length,
      seeds: seeds.length,
      maxTicks,
      simSeconds,
      note: `policy=${policy} · v7 宽带`,
      extraCols: [{ key: 'avgKills', label: '平均击杀', get: (r) => r.avgKills, digits: 2 }],
    })
    process.stderr.write(`[m1-eval] wrote HTML scorecard -> ${written}\n`)
  } catch (e) {
    process.stderr.write(`[m1-eval] HTML scorecard failed: ${(e as Error).message}\n`)
  }
}

await main()
