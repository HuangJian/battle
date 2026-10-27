/**
 * gen-godai-report.ts — 当前 God-AI 表现的 HTML 评分卡（单页 / 多难度）。
 *
 * 与 `tools/eval/eval-suite.ts` 的区别：**这里给每个 SimTask 传真实 stageIndex**，
 * 并用同一套 seed 做 CRN（与 `paired-serial` / `tmp/ab.ts` 同口径）。eval-suite 的
 * runCorpus 不传 stageIndex（默认 0），而 index 0 与 33 在同一 seed 上是不同的一局
 * （kill score 随 index 缩放，并影响 dropOnScoreMilestone 的道具掉落）。
 *
 * 输出走 `tools/sim/scorecard-html.ts` 的渲染器（v7 十一维、热力表、点击排序），
 * 多难度合并成一页：顶部三张难度卡片 + 标签页切换关卡表。
 *
 * 用法：
 *   bun tools/eval/gen-godai-report.ts
 *   bun tools/eval/gen-godai-report.ts --seeds 20 --diffs hard,chaos
 *   bun tools/eval/gen-godai-report.ts --params '{"armorChasePenalty":1}'
 *   bun tools/eval/gen-godai-report.ts --out reports/godai-current.html --max-ticks 18000
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'
import { arg } from '../lib/cli'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  DEFAULT_STAGE_REFS,
  V7_SCORE_CONFIG,
  type DimensionKey,
  type RunScore,
  type ScorableRun,
} from './godai-score'
import { DIM_ORDER } from '../sim/scorecard-html'
import {
  writeMultiScorecardHtml,
  type DifficultyScorecard,
  type ScorecardRow,
} from '../sim/scorecard-html'

const DIFF_LABEL: Record<string, string> = {
  hard: 'Hard',
  classic: 'Classic',
  chaos: 'Chaos',
  relax: 'Relax',
}

const seeds = Number(arg('seeds', '60'))
const maxTicks = Number(arg('max-ticks', '18000'))
const diffs = (arg('diffs', 'hard,classic,chaos') ?? '').split(',').filter(Boolean)
const outPath = arg('out', 'reports/godai-current.html')!
const paramsJson = arg('params', '{}')!
const note = arg('note', '') ?? ''

if (!Number.isInteger(seeds) || seeds <= 0) throw new Error(`--seeds must be a positive int`)
if (diffs.length === 0) throw new Error(`--diffs must list at least one difficulty`)

const params = {
  ...DEFAULT_GOD_AI_PARAMS,
  ...(JSON.parse(paramsJson) as Partial<GodAIParams>),
} as GodAIParams

const cfg = { ...V7_SCORE_CONFIG, refs: DEFAULT_STAGE_REFS }

// ---- build every (difficulty, stage, seed) task; ids are batch-local ----
interface Slot {
  difficulty: string
  stageIndex: number
  seed: number
  id: number
}
const slots: Slot[] = []
const tasks: SimTask[] = []
let nextId = 0
for (const d of diffs) {
  for (let s = 0; s < STAGES.length; s++) {
    for (let k = 0; k < seeds; k++) {
      const id = nextId++
      slots.push({ difficulty: d, stageIndex: s, seed: k, id })
      tasks.push({
        id,
        seed: k,
        stage: STAGES[s],
        difficulty: d,
        params,
        maxTicks,
        stageIndex: s,
        telemetry: true,
      })
    }
  }
}

const t0 = performance.now()
const pool = new SimWorkerPool()
const results = await pool.runBatch(tasks)
pool.terminate()
const simSeconds = (performance.now() - t0) / 1000

// ---- score every run, bucket by (difficulty, stage) ----
const byStage = new Map<string, { scores: RunScore[]; kills: number }>()
for (let i = 0; i < results.length; i++) {
  const r = results[i]
  const slot = slots[i]
  if (!r.ok) {
    throw new Error(`run failed: ${slot.difficulty} stage ${slot.stageIndex + 1} seed ${slot.seed}`)
  }
  const scorable: ScorableRun = {
    outcome: r.outcome as ScorableRun['outcome'],
    ticks: r.ticks,
    finalState: {
      killCount: r.killCount,
      lives: r.lives ?? 0,
      baseAlive: r.baseAlive,
    },
    firstKillTick: r.firstKillTick,
    telemetry: r.telemetry!,
  }
  const s = scoreRun(scorable, cfg)
  const key = `${slot.difficulty}|${slot.stageIndex}`
  let bucket = byStage.get(key)
  if (!bucket) {
    bucket = { scores: [], kills: 0 }
    byStage.set(key, bucket)
  }
  bucket.scores.push(s)
  bucket.kills += r.killCount
}

const meanDim = (scores: RunScore[], key: DimensionKey): number | null => {
  let sum = 0
  let n = 0
  for (const s of scores) {
    const d = s.dims[key]
    if (d && d.value !== null) {
      sum += d.value
      n++
    }
  }
  return n > 0 ? sum / n : null
}

const cards: DifficultyScorecard[] = []
for (const d of diffs) {
  const rows: ScorecardRow[] = []
  const stages = []
  for (let s = 0; s < STAGES.length; s++) {
    const bucket = byStage.get(`${d}|${s}`)!
    const agg = aggregateStage(STAGES[s].name, bucket.scores)
    stages.push(agg)
    const dims: Record<string, number> = {}
    for (const key of DIM_ORDER) {
      const v = meanDim(bucket.scores, key)
      if (v !== null) dims[key] = v
    }
    rows.push({
      idx: s + 1,
      name: STAGES[s].name,
      score: agg.score,
      mean: agg.mean,
      cvar: agg.cvar,
      se: agg.se,
      winRate: agg.winRate,
      avgKills: bucket.kills / bucket.scores.length,
      dims,
    })
  }
  const suite = aggregateSuite(stages)
  cards.push({ difficulty: d, label: DIFF_LABEL[d] ?? d, rows, suite })
  process.stderr.write(
    `[gen-godai-report] ${d}: win ${(100 * suite.meanWinRate).toFixed(1)}% · ` +
      `suite ${suite.suite.toFixed(4)} · score ${suite.arithmeticMean.toFixed(4)}` +
      (suite.worstStage
        ? ` · worst ${suite.worstStage.name}(${(100 * suite.worstStage.winRate).toFixed(0)}%)`
        : '') +
      `\n`,
  )
}

const wrote = writeMultiScorecardHtml(outPath, cards, {
  title: `God-AI 关卡评分卡 — 当前表现（${diffs.map((d) => DIFF_LABEL[d] ?? d).join(' / ')}）`,
  stages: STAGES.length,
  seeds,
  maxTicks,
  simSeconds,
  note: `参数=默认表${paramsJson === '{}' ? '' : ` + ${paramsJson}`}` + (note ? ` · ${note}` : ''),
  extraCols: [{ key: 'avgKills', label: '平均击杀', get: (r) => r.avgKills, digits: 2 }],
})

process.stdout.write(`wrote ${wrote}\n`)
