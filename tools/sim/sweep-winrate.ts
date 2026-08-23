#!/usr/bin/env bun
/**
 * sweep-winrate.ts — 3-mode × 35-stage × N-seed God-AI win-rate sweep.
 *
 * Runs the current code's shipped God AI (DEFAULT_GOD_AI_PARAMS) across every
 * classic stage, every requested difficulty, and a fixed seed set, then emits:
 *   - overall summary per difficulty (win rate, avg kills, base-destroyed ratio, timeouts)
 *   - per-stage breakdown (win rate, avg kills, base-destroyed ratio)
 *   - worst-stage ("劣关") ranking per difficulty + a side-by-side comparison
 *
 * Uses the Bun worker pool (tools/sim/sim-pool) for parallelism. Each run is a
 * pure function of (seed, stage, difficulty) so parallel == serial (byte-identical).
 *
 * History comparison: every snapshot under `--history` (see snapshot-winrate.ts)
 * is embedded into the HTML report, where any subset can be selected to render
 * overall + per-stage win-rate deltas against the current run.
 *
 * Usage:
 *   bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60 --out tmp/winrate
 *   bun tools/sim/sweep-winrate.ts --save-failures --replay-dir replays/winrate
 *
 * Default: --difficulties classic,hard,chaos  --seeds 1-60  --history reports/winrate/history
 *          --save-failures is OFF (losing-run replays are not written unless opted in)
 */
import { STAGES } from '../../src/config/stages'
import { SimWorkerPool } from './sim-pool'
import type { SimTask, SimTaskResult } from './sim-worker'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { EVAL_DIFFICULTY_KEYS } from '../../src/config/difficulty'
import { MAX_TICKS } from './simulation-runner'
import { writeFileSync, mkdirSync } from 'node:fs'
import { DEFAULT_HISTORY_DIR, loadSnapshots, type WinrateSnapshot } from './winrate-history'
import {
  buildHtml,
  pct,
  mean,
  compactCurrent,
  compactSnapshot,
  type DiffAgg,
  type StageAgg,
} from './report-html'
import { writeReplayFile } from './replay-writer'

import { arg } from '../lib/cli'
function parseSeedSpec(spec: string): number[] {
  // DIALECT NOTE (renamed from `parseSeeds`, refactor.zcode.md §2.3): this is
  // the SINGLE-SEED dialect — a bare count ("60") means the seed 60, NOT
  // 1..60. tools/lib/cli's parseSeeds means the opposite; the two coexist on
  // purpose (§213: silent-dialect drift, don't unify).
  if (spec.includes('-')) {
    const [start, end] = spec.split('-').map(Number)
    if (!Number.isInteger(start) || !Number.isInteger(end) || end < start) {
      throw new Error(`--seeds: illegal range "${spec}"`)
    }
    return Array.from({ length: end - start + 1 }, (_, i) => start + i)
  }
  const n = Number.parseInt(spec, 10)
  if (!Number.isInteger(n) || n < 1) {
    throw new Error(`--seeds: illegal value "${spec}" (use "1-60" or a single seed number)`)
  }
  return [n]
}

const difficulties = arg('difficulties') ?? EVAL_DIFFICULTY_KEYS.join(',')
  .split(',')
  .map((s) => s.trim())
const seeds = parseSeedSpec(arg('seeds', '1-60')!)
const outDir = arg('out', 'reports/winrate')!
const historyDir = arg('history', DEFAULT_HISTORY_DIR)!
const useHistory = !process.argv.includes('--no-history')
// Persist losing runs' replays to replays/winrate (default off — opt-in only,
// since recording every run adds transfer/serialization overhead in the pool).
const saveFailures = process.argv.includes('--save-failures')
const replayDir = arg('replay-dir', 'replays/winrate')!
const coop = process.argv.includes('--coop')
const spectateDual = process.argv.includes('--dual')
const modeTag = coop ? ' [coop]' : spectateDual ? ' [dual supervise]' : ''
const modeSuffix = coop ? ' — 双人(躺赢)' : spectateDual ? ' — 督战双玩家' : ''
const params: GodAIParams = DEFAULT_GOD_AI_PARAMS
const stageCount = STAGES.length

mkdirSync(outDir, { recursive: true })
if (saveFailures) mkdirSync(replayDir, { recursive: true })

async function sweepDifficulty(diff: string): Promise<DiffAgg> {
  const tasks: SimTask[] = []
  const meta: Array<{ stageIndex: number; seed: number }> = []
  for (let si = 0; si < stageCount; si++) {
    for (const seed of seeds) {
      const id = tasks.length
      tasks.push({
        id,
        seed,
        stage: STAGES[si],
        difficulty: diff,
        params,
        maxTicks: MAX_TICKS,
        coop,
        spectateDual,
        recordReplay: saveFailures,
      })
      meta.push({ stageIndex: si, seed })
    }
  }
  const pool = new SimWorkerPool()
  const t0 = performance.now()
  const results: SimTaskResult[] = await pool.runBatch(tasks)
  pool.terminate()
  const wallMs = performance.now() - t0

  // Persist losing runs' replays (only when --save-failures). Wins and sim
  // errors are skipped; each failure result already carries its recorded frames.
  const savePromises: Promise<string | null>[] = []
  if (saveFailures) {
    for (const r of results) {
      if (!r.ok || !r.replayResult || r.outcome === 'stage_clear') continue
      const m = meta[r.id]
      savePromises.push(
        writeReplayFile({
          result: r.replayResult,
          dir: replayDir,
          stageIndex: m.stageIndex,
          stageName: STAGES[m.stageIndex].name,
          godAIParams: params as unknown as Record<string, unknown>,
        }),
      )
    }
  }

  const stages: StageAgg[] = Array.from({ length: stageCount }, () => ({
    wins: 0,
    killsSum: 0,
    baseDestroyed: 0,
    gameovers: 0,
    timeouts: 0,
    total: 0,
  }))
  let wins = 0,
    killsSum = 0,
    baseDestroyed = 0,
    gameovers = 0,
    timeouts = 0,
    total = 0
  for (const r of results) {
    if (!r.ok) {
      process.stderr.write(`  ! task ${r.id} failed (${diff})\n`)
      continue
    }
    const m = meta[r.id]
    const sa = stages[m.stageIndex]
    sa.total++
    total++
    if (r.outcome === 'stage_clear') {
      wins++
      sa.wins++
    } else if (r.outcome === 'gameover') {
      gameovers++
      sa.gameovers++
    } else if (r.outcome === 'max_ticks') {
      timeouts++
      sa.timeouts++
    }
    sa.killsSum += r.killCount
    killsSum += r.killCount
    if (!r.baseAlive) {
      baseDestroyed++
      sa.baseDestroyed++
    }
  }
  let savedFailures = 0
  if (savePromises.length > 0) {
    const paths = await Promise.all(savePromises)
    savedFailures = paths.filter((p): p is string => p !== null).length
  }
  process.stderr.write(
    `  [${diff}] ${total} runs in ${(wallMs / 1000).toFixed(1)}s — winRate ${((wins / total) * 100).toFixed(1)}%${savedFailures ? ` ｜ saved ${savedFailures} failure replay(s) → ${replayDir}` : ''}\n`,
  )
  return { name: diff, stages, wins, killsSum, baseDestroyed, gameovers, timeouts, total, wallMs }
}

async function main() {
  process.stderr.write(
    `[sweep] ${difficulties.length} difficulties × ${stageCount} stages × ${seeds.length} seeds = ${difficulties.length * stageCount * seeds.length} runs${modeTag}\n`,
  )
  const aggs: DiffAgg[] = []
  for (const d of difficulties) {
    process.stderr.write(`[sweep] running difficulty=${d} ...\n`)
    aggs.push(await sweepDifficulty(d))
  }

  // ----- Worst-stage ranking (劣关) per difficulty -----
  const worstPerDiff = new Map<string, number[]>()
  for (const a of aggs) {
    const ranked = a.stages
      .map((s, i) => ({ i, winRate: pct(s.wins, s.total), avgKills: mean(s.killsSum, s.total) }))
      .sort((x, y) => x.winRate - y.winRate || x.avgKills - y.avgKills)
    worstPerDiff.set(
      a.name,
      ranked.slice(0, 12).map((r) => r.i),
    )
  }

  // ----- Union of worst stages for the comparison table -----
  const unionSet = new Set<number>()
  for (const idxs of worstPerDiff.values()) for (const i of idxs) unionSet.add(i)
  const unionWorst = [...unionSet].sort((a, b) => a - b)

  // ===== Build raw JSON =====
  const raw = {
    scope: { difficulties, stageCount, seeds, seedsCount: seeds.length, maxTicks: MAX_TICKS },
    generatedAt: new Date().toISOString(),
    perDifficulty: aggs.map((a) => ({
      name: a.name,
      totalRuns: a.total,
      winRate: pct(a.wins, a.total),
      avgKills: mean(a.killsSum, a.total),
      baseDestroyedRate: pct(a.baseDestroyed, a.total),
      baseDestroyedAmongLosses: pct(a.baseDestroyed, a.total - a.wins),
      gameovers: a.gameovers,
      timeouts: a.timeouts,
      wallMs: a.wallMs,
    })),
    perStage: aggs.map((a) => ({
      name: a.name,
      stages: a.stages.map((s, i) => ({
        index: i,
        name: STAGES[i].name,
        winRate: pct(s.wins, s.total),
        avgKills: mean(s.killsSum, s.total),
        baseDestroyedRate: pct(s.baseDestroyed, s.total),
      })),
    })),
    worstStages: Object.fromEntries(
      [...worstPerDiff.entries()].map(([name, idxs]) => [
        name,
        idxs.map((i) => ({
          index: i,
          name: STAGES[i].name,
          winRate: pct(a_for(aggs, name).stages[i].wins, a_for(aggs, name).stages[i].total),
          avgKills: mean(a_for(aggs, name).stages[i].killsSum, a_for(aggs, name).stages[i].total),
          baseDestroyedRate: pct(
            a_for(aggs, name).stages[i].baseDestroyed,
            a_for(aggs, name).stages[i].total,
          ),
        })),
      ]),
    ),
  }
  writeFileSync(`${outDir}/results.json`, JSON.stringify(raw, null, 2))

  // ===== Load history snapshots for delta comparison =====
  const history = useHistory ? loadSnapshots(historyDir) : []
  if (useHistory) {
    process.stderr.write(`[sweep] history: ${history.length} snapshot(s) from ${historyDir}\n`)
  }

  // ===== Build markdown + HTML reports =====
  const md = buildMarkdown(aggs, unionWorst, history)
  writeFileSync(`${outDir}/report.md`, md)
  const html = buildHtml(aggs, unionWorst, raw.scope, history, {
    modeSuffix,
  })
  writeFileSync(`${outDir}/report.html`, html)

  process.stderr.write(`[sweep] wrote ${outDir}/report.md, report.html, results.json\n`)
  // Echo a compact summary to stdout for quick reading.
  console.log(
    aggs
      .map(
        (a) =>
          `${a.name.padEnd(8)} win=${pct(a.wins, a.total).toFixed(1)}% kills=${mean(a.killsSum, a.total).toFixed(2)} baseLost=${pct(a.baseDestroyed, a.total).toFixed(1)}% (timeout=${a.timeouts})`,
      )
      .join('\n'),
  )
}

function a_for(aggs: DiffAgg[], name: string): DiffAgg {
  const a = aggs.find((x) => x.name === name)
  if (!a) throw new Error(`missing agg ${name}`)
  return a
}


/* ===== Compact run shape shared by the current sweep and every history snapshot.
   Kept tiny because it is embedded verbatim into the HTML for client-side diffing. */
function buildMarkdown(aggs: DiffAgg[], unionWorst: number[], history: WinrateSnapshot[]): string {
  const lines: string[] = []
  lines.push(`# God AI 胜率扫描报告 (classic / hard / chaos)${modeSuffix}`)
  lines.push('')
  lines.push(
    `> 范围：35 关 × 60 种子 × ${aggs.length} 难度 = ${aggs.reduce((n, a) => n + a.total, 0)} 局。`,
  )
  lines.push(`> 当前代码默认 God AI 参数（DEFAULT_GOD_AI_PARAMS），无人工操作。`)
  lines.push('')
  lines.push(...historyMarkdown(aggs, history))
  lines.push('## 总体概览')
  lines.push('')
  lines.push('| 难度 | 胜率 | 平均击杀 | 基地被毁比例 | 败局中基地被毁 | 超时 |')
  lines.push('|---|---|---|---|---|---|')
  for (const a of aggs) {
    const losses = a.total - a.wins
    lines.push(
      `| ${a.name} | ${pct(a.wins, a.total).toFixed(1)}% | ${mean(a.killsSum, a.total).toFixed(2)} | ${pct(a.baseDestroyed, a.total).toFixed(1)}% | ${pct(a.baseDestroyed, losses).toFixed(1)}% | ${a.timeouts} |`,
    )
  }
  lines.push('')
  lines.push('## 各难度劣关排名（胜率升序，并列按平均击杀升序）')
  lines.push('')
  for (const a of aggs) {
    lines.push(`### ${a.name}`)
    lines.push('')
    lines.push('| 排名 | 关 # | 关名 | 胜率 | 平均击杀 | 基地被毁比例 |')
    lines.push('|---|---|---|---|---|---|')
    const ranked = a.stages
      .map((s, i) => ({
        i,
        winRate: pct(s.wins, s.total),
        avgKills: mean(s.killsSum, s.total),
        baseLost: pct(s.baseDestroyed, s.total),
      }))
      .sort((x, y) => x.winRate - y.winRate || x.avgKills - y.avgKills)
    ranked.slice(0, 10).forEach((r, rank) => {
      lines.push(
        `| ${rank + 1} | ${r.i + 1} | ${STAGES[r.i].name} | ${r.winRate.toFixed(1)}% | ${r.avgKills.toFixed(2)} | ${r.baseLost.toFixed(1)}% |`,
      )
    })
    lines.push('')
  }
  lines.push('## 劣关横向对比（三难度并排）')
  lines.push('')
  lines.push('| 关 # | 关名 | 难度 | 胜率 | 平均击杀 | 基地被毁比例 |')
  lines.push('|---|---|---|---|---|---|')
  for (const i of unionWorst) {
    for (const a of aggs) {
      const s = a.stages[i]
      lines.push(
        `| ${i + 1} | ${STAGES[i].name} | ${a.name} | ${pct(s.wins, s.total).toFixed(1)}% | ${mean(s.killsSum, s.total).toFixed(2)} | ${pct(s.baseDestroyed, s.total).toFixed(1)}% |`,
      )
    }
  }
  lines.push('')
  lines.push('## 全 35 关逐关明细（附录）')
  lines.push('')
  lines.push(
    `| 关 # | 关名 | ${aggs.map((a) => `${a.name} 胜率`).join(' | ')} | ${aggs.map((a) => `${a.name} 击杀`).join(' | ')} | ${aggs.map((a) => `${a.name} 基地毁`).join(' | ')} |`,
  )
  lines.push(
    `|---|---|${aggs.map(() => '---').join('|')}|${aggs.map(() => '---').join('|')}|${aggs.map(() => '---').join('|')}|`,
  )
  for (let i = 0; i < stageCount; i++) {
    const cells = aggs
      .map((a) => {
        const s = a.stages[i]
        return `${pct(s.wins, s.total).toFixed(1)}%`
      })
      .join(' | ')
    const kills = aggs
      .map((a) => mean(a.stages[i].killsSum, a.stages[i].total).toFixed(2))
      .join(' | ')
    const base = aggs
      .map((a) => pct(a.stages[i].baseDestroyed, a.stages[i].total).toFixed(1) + '%')
      .join(' | ')
    lines.push(`| ${i + 1} | ${STAGES[i].name} | ${cells} | ${kills} | ${base} |`)
  }
  lines.push('')
  return lines.join('\n')
}

/** Markdown history block: full timeline + biggest per-stage movers vs the newest snapshot. */
function historyMarkdown(aggs: DiffAgg[], history: WinrateSnapshot[]): string[] {
  if (history.length === 0) return []
  const cur = compactCurrent(aggs, {
    ranAt: new Date().toISOString(),
    seedsCount: seeds.length,
  })
  const runs = history.map((s) =>
      compactSnapshot(s, { ranAt: s.generatedAt || s.savedAt, seedsCount: s.scope?.seedsCount ?? 0 }),
  )
  const baseline = runs[runs.length - 1]
  const names = aggs.map((a) => a.name)
  const sign = (v: number) => (v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1))

  const out: string[] = []
  out.push('## 历史对比')
  out.push('')
  out.push(`> 基准快照：**${baseline.label}** (${baseline.id})`)
  out.push('')
  out.push(`| 快照 | 运行时间 | git | ${names.map((n) => `${n} 胜率`).join(' | ')} |`)
  out.push(`|---|---|---|${names.map(() => '---').join('|')}|`)
  for (const r of [...runs].reverse()) {
    const cells = names.map((n) => (r.diffs[n] ? `${r.diffs[n].w.toFixed(1)}%` : '—')).join(' | ')
    out.push(`| ${r.label} | ${r.at} | ${r.git || '—'} | ${cells} |`)
  }
  const curCells = names
    .map((n) => {
      const c = cur.diffs[n]
      const b = baseline.diffs[n]
      return b ? `${c.w.toFixed(1)}% (${sign(c.w - b.w)})` : `${c.w.toFixed(1)}%`
    })
    .join(' | ')
  out.push(`| **当前运行** | ${cur.at} | — | ${curCells} |`)
  out.push('')

  out.push(`### 逐关变化 Top（当前 vs ${baseline.label}）`)
  out.push('')
  out.push('| 难度 | 关 # | 关名 | 基准胜率 | 当前胜率 | Δ |')
  out.push('|---|---|---|---|---|---|')
  for (const n of names) {
    const c = cur.diffs[n]
    const b = baseline.diffs[n]
    if (!b || b.stages.length === 0) continue
    const movers = c.stages
      .map((s, i) => ({
        i,
        delta: s.w - (b.stages[i]?.w ?? s.w),
        now: s.w,
        was: b.stages[i]?.w ?? 0,
      }))
      .filter((m) => Math.abs(m.delta) >= 0.05)
      .sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta))
      .slice(0, 8)
    if (movers.length === 0) {
      out.push(`| ${n} | — | (无变化) | — | — | 0.0 |`)
      continue
    }
    for (const m of movers) {
      out.push(
        `| ${n} | ${m.i + 1} | ${STAGES[m.i].name} | ${m.was.toFixed(1)}% | ${m.now.toFixed(1)}% | ${sign(m.delta)} |`,
      )
    }
  }
  out.push('')
  return out
}


await main()
