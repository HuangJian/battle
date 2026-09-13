/**
 * teacher-probe.ts —— N4 教师胜率探针（roadmap v2.0 §3-N4 / Phase 1 出口）。
 *
 * c01-c07 × EVAL_SEEDS 200 局 headless God 扫描 → teacherWR 进 I5 台账
 * （nn-training/ladder/LEDGER.jsonc），顺带落盘 score 分布 + 方差（F5）。
 *
 * 口径（严格 §0.2 合规）：探针**只回答 BC 通道可行性**——教师 <80% 的级直接走
 * 「BC 暖启 + PPO」。它不判关卡难度 / 不设门 / 不评梯度（教师是有缺陷的规则系统，
 * 不是天花板）。因此本工具只产出观测值，不产出任何 verdict。
 *
 * 种子 = Python 侧 `nn-training/rl/eval_local.py::EVAL_SEEDS = range(860001, 860201)`
 * 的前缀（200 局）；level 单关 ⇒ eval-course-ckpt 的 seed = seed0 + g，逐值对齐。
 *
 * 用法：
 *   bun tools/sim/teacher-probe.ts [--levels c01,c02 | --levels all] [--games 200]
 *     [--seed0 860001] [--workers 8] [--out nn-training/ladder/reports/teacher-probe.json]
 *     [--ledger nn-training/ladder/LEDGER.jsonc]
 */

import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import { dirname } from 'path'
import {
  levelFilePath,
  percentile,
  runRound,
  upsertLedgerEntry,
  type GateRow,
} from './curriculum-gate'

/** 该级当前 max_ticks（读关卡文件=单一事实来源）：teacherWR 离开 cap 就不可比。 */
export function levelMaxTicks(level: string): number | null {
  try {
    const doc = JSON.parse(readFileSync(levelFilePath(level), 'utf-8'))
    return typeof doc.max_ticks === 'number' ? doc.max_ticks : null
  } catch {
    return null
  }
}

/** N4：先补 c01-c07（1 命 tier 全部）——BC 优先级的探针域。 */
export const TEACHER_LEVELS = [
  'ladder-c01',
  'ladder-c02',
  'ladder-c03',
  'ladder-c04',
  'ladder-c05',
  'ladder-c06',
  'ladder-c07',
] as const

/** 与 nn-training/rl/eval_local.py::EVAL_SEEDS 首值一致（range(860001, 860201)）。 */
export const EVAL_SEED0 = 860001

export interface ScoreStats {
  games: number
  passed: number
  wins: number
  cleared: number
  winRate: number
  scoreMean: number
  scoreP50: number
  scoreP90: number
  scoreStd: number
  scoreVar: number
  scoreMin: number
  scoreMax: number
  outcomes: Record<string, number>
}

/** 过关 = win ∪ cleared（与 I4 门 runner 同口径，D4）。 */
export function scoreStats(rows: GateRow[]): ScoreStats {
  const games = rows.length
  const wins = rows.filter((r) => r.win).length
  const cleared = rows.filter((r) => r.cleared).length
  const passed = rows.filter((r) => r.win || r.cleared).length
  const scores = rows.map((r) => Number(r.score ?? 0)).sort((a, b) => a - b)
  const n = scores.length
  const mean = n > 0 ? scores.reduce((s, v) => s + v, 0) / n : 0
  const variance = n > 0 ? scores.reduce((s, v) => s + (v - mean) ** 2, 0) / n : 0
  const outcomes: Record<string, number> = {}
  for (const r of rows) outcomes[r.outcome] = (outcomes[r.outcome] ?? 0) + 1
  const r3 = (x: number): number => Math.round(x * 1000) / 1000
  return {
    games,
    passed,
    wins,
    cleared,
    winRate: games > 0 ? passed / games : 0,
    scoreMean: r3(mean),
    scoreP50: r3(percentile(scores, 0.5)),
    scoreP90: r3(percentile(scores, 0.9)),
    scoreStd: r3(Math.sqrt(variance)),
    scoreVar: r3(variance),
    scoreMin: r3(n > 0 ? scores[0] : 0),
    scoreMax: r3(n > 0 ? scores[n - 1] : 0),
    outcomes,
  }
}

export interface TeacherProbeReport {
  format: 'ladder-teacher-probe'
  version: 1
  date: string
  seed0: number
  games: number
  workers: number
  levels: Record<string, ScoreStats>
}

/** 跑 c01-c07 教师探针，写报告文件 + LEDGER 各字段（teacherWR + score 分布）。 */
export function runTeacherProbe(opts: {
  levels?: readonly string[]
  seed0?: number
  games?: number
  workers?: number
  out?: string
  ledgerPath?: string
}): TeacherProbeReport {
  const levels = opts.levels ?? TEACHER_LEVELS
  const seed0 = opts.seed0 ?? EVAL_SEED0
  const games = opts.games ?? 200
  const workers = opts.workers ?? 8
  const date = new Date().toISOString()
  const stats: Record<string, ScoreStats> = {}
  for (const level of levels) {
    const rows = runRound(level, [], seed0, games, workers, 'god')
    if (rows.length === 0) throw new Error(`[teacher-probe] ${level}: 无 eval 行（0 局？）`)
    const s = scoreStats(rows)
    stats[level] = s
    upsertLedgerEntry(
      level,
      {
        teacherWR: s.winRate,
        teacherProbe: {
          date,
          games: s.games,
          wins: s.wins,
          cleared: s.cleared,
          seeds: `${seed0}-${seed0 + games - 1}`,
          // 诊断必需：2026-09-13 立案前 c01 的 teacherWR 就是被 600-tick 截断污染的
          // （69.0% → 99.0%），无 cap 字段的 teacherWR 不可比、不可复查。
          maxTicks: levelMaxTicks(level),
          source: 'god-ai eval-course-ckpt',
          outcomes: s.outcomes,
          score: {
            mean: s.scoreMean,
            p50: s.scoreP50,
            p90: s.scoreP90,
            std: s.scoreStd,
            var: s.scoreVar,
            min: s.scoreMin,
            max: s.scoreMax,
          },
        },
      },
      opts.ledgerPath,
    )
    console.log(
      `[teacher-probe] ${level}: WR ${(s.winRate * 100).toFixed(1)}% ` +
        `(wins ${s.wins}/cleared ${s.cleared}/${s.games})  ` +
        `score mean ${s.scoreMean} p50 ${s.scoreP50} p90 ${s.scoreP90} std ${s.scoreStd}`,
    )
  }
  const report: TeacherProbeReport = {
    format: 'ladder-teacher-probe',
    version: 1,
    date,
    seed0,
    games,
    workers,
    levels: stats,
  }
  const out = opts.out ?? 'nn-training/ladder/reports/teacher-probe.json'
  mkdirSync(dirname(out), { recursive: true })
  writeFileSync(out, JSON.stringify(report, null, 2) + '\n')
  console.log(`[teacher-probe] report -> ${out} ; LEDGER updated (${levels.length} levels)`)
  return report
}

/** `c01` / `01` / `1` / `all` / `ladder-c01` 归一成 level id。 */
export function normalizeLevelArg(spec: string): string[] {
  return spec
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => {
      if (s === 'all') return s
      const m = /^(?:ladder-)?c?0*(\d{1,2})$/.exec(s)
      return m ? `ladder-c${m[1].padStart(2, '0')}` : s
    })
    .flatMap((s) => (s === 'all' ? [...TEACHER_LEVELS] : [s]))
}

function main(): void {
  const argv = process.argv.slice(2)
  const arg = (name: string): string | undefined => {
    const i = argv.indexOf(`--${name}`)
    return i >= 0 ? argv[i + 1] : undefined
  }
  const levelsArg = arg('levels')
  const report = runTeacherProbe({
    levels: levelsArg ? normalizeLevelArg(levelsArg) : undefined,
    seed0: arg('seed0') ? parseInt(arg('seed0')!, 10) : undefined,
    games: arg('games') ? parseInt(arg('games')!, 10) : undefined,
    workers: arg('workers') ? parseInt(arg('workers')!, 10) : undefined,
    out: arg('out'),
    ledgerPath: arg('ledger'),
  })
  console.log(
    `[teacher-probe] done: ${Object.keys(report.levels).length} levels, ${report.games} games each`,
  )
}

if (import.meta.main) main()
