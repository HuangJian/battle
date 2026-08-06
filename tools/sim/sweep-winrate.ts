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
 *
 * Default: --difficulties classic,hard,chaos  --seeds 1-60  --history reports/winrate/history
 */
import { STAGES } from '../../src/config/stages'
import { SimWorkerPool } from './sim-pool'
import type { SimTask, SimTaskResult } from './sim-worker'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { MAX_TICKS } from './simulation-runner'
import { writeFileSync, mkdirSync } from 'node:fs'
import { DEFAULT_HISTORY_DIR, loadSnapshots, type WinrateSnapshot } from './winrate-history'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}
function parseSeeds(spec: string): number[] {
  if (spec.includes('-')) {
    const [start, end] = spec.split('-').map(Number)
    return Array.from({ length: end - start + 1 }, (_, i) => start + i)
  }
  return [parseInt(spec, 10)]
}

const difficulties = arg('difficulties', 'classic,hard,chaos')!
  .split(',')
  .map((s) => s.trim())
const seeds = parseSeeds(arg('seeds', '1-60')!)
const outDir = arg('out', 'reports/winrate')!
const historyDir = arg('history', DEFAULT_HISTORY_DIR)!
const useHistory = !process.argv.includes('--no-history')
const params: GodAIParams = DEFAULT_GOD_AI_PARAMS
const stageCount = STAGES.length

mkdirSync(outDir, { recursive: true })

interface StageAgg {
  wins: number
  killsSum: number
  baseDestroyed: number
  gameovers: number
  timeouts: number
  total: number
}
interface DiffAgg {
  name: string
  stages: StageAgg[]
  wins: number
  killsSum: number
  baseDestroyed: number
  gameovers: number
  timeouts: number
  total: number
  wallMs: number
}

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
      })
      meta.push({ stageIndex: si, seed })
    }
  }
  const pool = new SimWorkerPool()
  const t0 = performance.now()
  const results: SimTaskResult[] = await pool.runBatch(tasks)
  pool.terminate()
  const wallMs = performance.now() - t0

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
  process.stderr.write(
    `  [${diff}] ${total} runs in ${(wallMs / 1000).toFixed(1)}s — winRate ${((wins / total) * 100).toFixed(1)}%\n`,
  )
  return { name: diff, stages, wins, killsSum, baseDestroyed, gameovers, timeouts, total, wallMs }
}

function pct(n: number, d: number): number {
  return d > 0 ? (n / d) * 100 : 0
}
function mean(n: number, d: number): number {
  return d > 0 ? n / d : 0
}

async function main() {
  process.stderr.write(
    `[sweep] ${difficulties.length} difficulties × ${stageCount} stages × ${seeds.length} seeds = ${difficulties.length * stageCount * seeds.length} runs\n`,
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
  const html = buildHtml(aggs, unionWorst, raw.scope, history)
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

/** ISO → local wall clock (`YYYY-MM-DD HH:MM:SS`).
 *  Snapshot ids are stamped in local time, so displayed times must be local too —
 *  otherwise `2026-08-05_190636` would render as `2026-08-05 11:06:36` (UTC) and look wrong. */
function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  )
}

/* ===== Compact run shape shared by the current sweep and every history snapshot.
   Kept tiny because it is embedded verbatim into the HTML for client-side diffing. */
interface CompactStage {
  w: number // win rate %
  k: number // avg kills
  b: number // base-destroyed rate %
}
interface CompactDiff extends CompactStage {
  stages: CompactStage[]
}
interface CompactRun {
  id: string
  label: string
  /** When the sweep ran (drives timeline ordering). */
  ranAt: string
  /** `ranAt` pre-formatted to the generating machine's local time — display only. */
  at: string
  git: string
  seeds: number
  stageCount: number
  diffs: Record<string, CompactDiff>
}

function compactCurrent(aggs: DiffAgg[]): CompactRun {
  const diffs: Record<string, CompactDiff> = {}
  for (const a of aggs) {
    diffs[a.name] = {
      w: pct(a.wins, a.total),
      k: mean(a.killsSum, a.total),
      b: pct(a.baseDestroyed, a.total),
      stages: a.stages.map((s) => ({
        w: pct(s.wins, s.total),
        k: mean(s.killsSum, s.total),
        b: pct(s.baseDestroyed, s.total),
      })),
    }
  }
  const ranAt = new Date().toISOString()
  return {
    id: '__current__',
    label: '当前运行',
    ranAt,
    at: fmtTime(ranAt),
    git: '',
    seeds: seeds.length,
    stageCount,
    diffs,
  }
}

function compactSnapshot(s: WinrateSnapshot): CompactRun {
  const diffs: Record<string, CompactDiff> = {}
  for (const d of s.perDifficulty) {
    const stagesSrc = s.perStage.find((p) => p.name === d.name)?.stages ?? []
    diffs[d.name] = {
      w: d.winRate,
      k: d.avgKills,
      b: d.baseDestroyedRate,
      stages: stagesSrc.map((x) => ({ w: x.winRate, k: x.avgKills, b: x.baseDestroyedRate })),
    }
  }
  const ranAt = s.generatedAt || s.savedAt
  return {
    id: s.id,
    label: s.label,
    ranAt,
    at: fmtTime(ranAt),
    git: s.git ? `${s.git.commit}${s.git.dirty ? '+dirty' : ''}` : '',
    seeds: s.scope?.seedsCount ?? 0,
    stageCount: s.scope?.stageCount ?? 0,
    diffs,
  }
}

function buildMarkdown(aggs: DiffAgg[], unionWorst: number[], history: WinrateSnapshot[]): string {
  const lines: string[] = []
  lines.push('# God AI 胜率扫描报告 (classic / hard / chaos)')
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
  const cur = compactCurrent(aggs)
  const runs = history.map(compactSnapshot)
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

function escHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildHtml(
  aggs: DiffAgg[],
  unionWorst: number[],
  scope: { seedsCount: number; maxTicks: number },
  history: WinrateSnapshot[],
): string {
  const totalRuns = aggs.reduce((n, a) => n + a.total, 0)
  const maxWin = Math.max(...aggs.map((a) => pct(a.wins, a.total)))

  // ----- history payload embedded for client-side diffing -----
  const histRuns = history.map(compactSnapshot)
  const historyPayload = JSON.stringify({
    current: compactCurrent(aggs),
    snapshots: histRuns,
    diffNames: aggs.map((a) => a.name),
    stageNames: STAGES.map((s) => s.name),
  })
  const histList =
    histRuns.length === 0
      ? `<p class="hint">尚无历史快照。先运行上面的「记录历史快照」命令，之后每次重跑 sweep 都会在此显示胜率变化。</p>`
      : `<div class="snaps">${[...histRuns]
          .reverse()
          .map((r, k) => {
            const scopeNote =
              r.stageCount && r.stageCount !== stageCount
                ? `<span class="warn" title="关卡数与当前不一致，逐关对比可能错位">关数 ${r.stageCount}</span>`
                : ''
            return `<label class="snap"><input type="checkbox" class="snapChk" value="${escHtml(r.id)}"${k === 0 ? ' checked' : ''}>
            <span class="snapMain">${escHtml(r.label)}</span>
            <span class="snapMeta">${escHtml(r.at)} ｜ ${escHtml(r.git || 'no-git')} ｜ ${r.seeds} 种子 ${scopeNote}</span></label>`
          })
          .join('')}</div>
      <div class="row">
        <button onclick="selAll(true)">全选</button>
        <button onclick="selAll(false)">清空</button>
        <span class="hint" style="margin-left:8px">勾选任意多个快照进行对比；Δ 以「当前运行」为参照。</span>
      </div>`

  const summaryRows = aggs
    .map((a) => {
      const losses = a.total - a.wins
      return `<tr><td><b>${a.name}</b></td>
        <td>${pct(a.wins, a.total).toFixed(1)}%</td>
        <td>${mean(a.killsSum, a.total).toFixed(2)}</td>
        <td>${pct(a.baseDestroyed, a.total).toFixed(1)}%</td>
        <td>${pct(a.baseDestroyed, losses).toFixed(1)}%</td>
        <td>${a.timeouts}</td></tr>`
    })
    .join('')

  // comparison table
  const cmpRows = unionWorst
    .map((i) => {
      const cells = aggs
        .map((a) => {
          const s = a.stages[i]
          return `<td>${pct(s.wins, s.total).toFixed(1)}%</td><td>${mean(s.killsSum, s.total).toFixed(1)}</td><td>${pct(s.baseDestroyed, s.total).toFixed(1)}%</td>`
        })
        .join('')
      return `<tr><td>${i + 1}</td><td>${STAGES[i].name}</td>${cells}</tr>`
    })
    .join('')
  // Flattened single-row header so the comparison table is uniformly sortable.
  const cmpHeadFlat = ['关 #', '关名']
    .concat(...aggs.map((a) => [`${a.name} 胜率`, `${a.name} 击杀`, `${a.name} 基地毁`]))
    .map((h) => `<th>${h}</th>`)
    .join('')

  // full appendix
  const fullRows = Array.from({ length: stageCount }, (_, i) => {
    const wr = aggs
      .map((a) => pct(a.stages[i].wins, a.stages[i].total).toFixed(1) + '%')
      .join('</td><td>')
    return `<tr><td>${i + 1}</td><td>${STAGES[i].name}</td><td>${wr}</td></tr>`
  }).join('')

  return `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>God AI 胜率扫描</title>
<style>
:root{--bg:#fafafa;--card:#fff;--ink:#1a1a1a;--muted:#666;--line:#e3e3e3;--accent:#2563eb;}
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:24px;}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:center}
th{background:#eef1f5;font-weight:600;cursor:pointer;user-select:none}
th:hover{background:#e2e8f0}
th.sort-asc::after{content:' ▲';font-size:10px}
th.sort-desc::after{content:' ▼';font-size:10px}
td:first-child,th:first-child{text-align:left}
tbody tr:nth-child(odd){background:#fff}
tbody tr:nth-child(even){background:#f4f7fb}
tbody tr:hover{background:#fff2cc}
.bar{height:14px;border-radius:7px;background:linear-gradient(90deg,#2563eb,#60a5fa)}
.cmd{background:#0f172a;color:#e2e8f0;padding:10px 12px;border-radius:6px;font:13px/1.4 ui-monospace,Menlo,Consolas,monospace;overflow-x:auto;margin:8px 0}
.hint{color:var(--muted);font-size:12px;margin:6px 0 0}
button{cursor:pointer;font:13px sans-serif;padding:6px 12px;border:1px solid var(--line);border-radius:6px;background:#fff}
button:hover{background:#f0f4ff}
.grid{display:grid;grid-template-columns:repeat(${aggs.length},1fr);gap:16px}
.col h3{font-size:14px;margin:0 0 6px}
ul{margin:0;padding-left:18px}
li{margin:2px 0}
.legend{display:flex;gap:18px;margin:10px 0}
.tag{display:inline-flex;align-items:center;gap:6px;color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%}
.snaps{display:flex;flex-direction:column;gap:4px;max-height:240px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:8px;margin:8px 0}
.snap{display:grid;grid-template-columns:20px 1fr auto;align-items:center;gap:8px;padding:4px 6px;border-radius:6px;cursor:pointer}
.snap:hover{background:#f4f7fb}
.snapMain{font-weight:600}
.snapMeta{color:var(--muted);font-size:12px}
.warn{color:#b45309;background:#fef3c7;border-radius:4px;padding:0 5px;margin-left:6px;font-size:11px}
.row{display:flex;align-items:center;gap:8px;margin:8px 0;flex-wrap:wrap}
.up{color:#15803d;font-weight:600}
.down{color:#b91c1c;font-weight:600}
.flat{color:var(--muted)}
select{font:13px sans-serif;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:#fff}
.cmds{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.cmdcard{flex:1;min-width:240px;display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.cmdname{font-weight:600;font-size:13px;cursor:help}
.cmdbox{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:#0f172a;color:#e2e8f0;padding:8px 10px;border-radius:6px;font:12px/1.4 ui-monospace,Menlo,Consolas,monospace}
.cmdbox code{white-space:nowrap}
.cmdcard button{align-self:flex-start}
.baseline-cell{font-variant-numeric:tabular-nums;white-space:nowrap}
.up,.down{padding:0 2px;border-radius:3px}
.floating-nav{position:fixed;top:14px;right:14px;z-index:50;display:flex;flex-direction:column;align-items:flex-end;gap:6px}
.nav-toggle{font:13px sans-serif;padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--accent);color:#fff;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.15)}
.nav-body{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 6px;display:flex;flex-direction:column;gap:2px;box-shadow:0 2px 8px rgba(0,0,0,.12);min-width:128px}
.nav-body a{color:var(--ink);text-decoration:none;padding:5px 10px;border-radius:6px;font-size:13px;white-space:nowrap}
.nav-body a:hover{background:#f0f4ff}
.floating-nav.collapsed .nav-body{display:none}
:target{scroll-margin-top:16px}
</style></head><body>
<h1>God AI 胜率扫描报告</h1>
<p class="sub">范围：${stageCount} 关 × ${scope.seedsCount} 种子 × ${aggs.length} 难度 = ${totalRuns} 局 ｜ 默认 God AI 参数，无人工操作 ｜ 生成于 ${new Date().toLocaleString()}</p>

<div class="floating-nav" id="floatingNav">
  <button class="nav-toggle" onclick="toggleNav()">☰ 导航</button>
  <nav class="nav-body">
    <a href="#sec-cmd">命令区</a>
    <a href="#sec-hist">历史对比</a>
    <a href="#sec-overview">总体概览</a>
    <a href="#sec-cmp">劣关对比</a>
    <a href="#sec-detail">全关明细</a>
  </nav>
</div>

<div class="cmds" id="sec-cmd">
  <div class="cmdcard">
    <div class="cmdname" title="默认输出到 reports/winrate/（已 gitignore）。可加 --difficulties / --seeds / --out / --no-history">重新生成报告</div>
    <div class="cmdbox"><code id="cmd">bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60</code></div>
    <button onclick="copyCmd(this,'cmd')">复制</button>
  </div>
  <div class="cmdcard">
    <div class="cmdname" title="对指定关批量跑 God AI，只保存未通关录像。替换 --stage / --difficulty；--size 为并行样本数">生成失败录像</div>
    <div class="cmdbox"><code id="cmd2">bun tools/optimize/level-sim.ts --stage 33 --difficulty hard --max-ticks 300000 --size 20 --save-replays --replay-failures-only --no-output</code></div>
    <button onclick="copyCmd(this,'cmd2')">复制</button>
  </div>
  <div class="cmdcard">
    <div class="cmdname" title="把本次数据存档到 reports/winrate/history/。--label 说明来源 ｜ --list 查看 ｜ --from 归档旧数据">记录历史快照</div>
    <div class="cmdbox"><code id="cmd3">bun tools/sim/snapshot-winrate.ts --label "改动说明"</code></div>
    <button onclick="copyCmd(this,'cmd3')">复制</button>
  </div>
</div>

<div class="card" id="sec-hist">
  <h2>历史对比（胜率变化）</h2>
  ${histList}
  <div id="histBody" style="display:${histRuns.length ? 'block' : 'none'}">
    <h3 style="font-size:14px;margin:14px 0 6px">总体趋势</h3>
    <table id="trendTable"><thead></thead><tbody></tbody></table>
    <div class="row" style="margin-top:14px">
      <label>逐关对比基准：</label>
      <select id="baseSel" onchange="renderStageDelta()"></select>
      <label><input type="checkbox" id="onlyChanged" onchange="renderStageDelta()" checked> 只看有变化的关</label>
    </div>
    <table id="stageDeltaTable"><thead></thead><tbody></tbody></table>
  </div>
</div>

<div class="card" id="sec-overview">
  <h2>总体概览</h2>
  <table><thead><tr><th>难度</th><th>胜率</th><th>平均击杀</th><th>基地被毁比例</th><th>败局中基地被毁</th><th>超时</th></tr></thead>
  <tbody>${summaryRows}</tbody></table>
  <div class="legend">${aggs
    .map(
      (a, k) =>
        `<span class="tag"><span class="dot" style="background:hsl(${k * 120},70%,50%)"></span>${a.name} ${pct(a.wins, a.total).toFixed(1)}%</span>`,
    )
    .join('')}</div>
  <div style="display:flex;gap:24px;align-items:flex-end">
    ${aggs
      .map(
        (a, k) =>
          `<div style="flex:1"><div class="bar" style="width:${(pct(a.wins, a.total) / maxWin) * 100}%;background:hsl(${k * 120},70%,50%)"></div><div style="margin-top:4px;color:var(--muted)">${a.name}</div></div>`,
      )
      .join('')}
  </div>
</div>

<div class="card" id="sec-cmp">
  <h2>劣关横向对比（三难度并排）</h2>
  <table><thead><tr>${cmpHeadFlat}</tr></thead>
  <tbody>${cmpRows}</tbody></table>
</div>

<div class="card" id="sec-detail">
  <h2>全 35 关胜率明细（附录）</h2>
  <table><thead><tr><th>关 #</th><th>关名</th>${aggs.map((a) => `<th>${a.name} 胜率</th>`).join('')}</tr></thead>
  <tbody>${fullRows}</tbody></table>
</div>
<script>
function copyCmd(btn,id){
  var t=document.getElementById(id).textContent;
  navigator.clipboard.writeText(t).then(function(){
    var o=btn.textContent;btn.textContent='已复制!';
    setTimeout(function(){btn.textContent=o;},1200);
  });
}
function toggleNav(){
  var n=document.getElementById('floatingNav');
  if(n)n.classList.toggle('collapsed');
}
(function(){
  function makeSortable(table){
    var thead=table.querySelector('thead');if(!thead)return;
    var hrs=thead.querySelectorAll('tr');if(!hrs.length)return;
    var headRow=hrs[hrs.length-1];
    var ths=Array.prototype.slice.call(headRow.querySelectorAll('th'));
    if(!ths.length)return;
    ths.forEach(function(th,col){
      th.addEventListener('click',function(){
        var tbody=table.querySelector('tbody');if(!tbody)return;
        var rows=Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var asc=th.dataset.asc!=='1';
        ths.forEach(function(o){o.classList.remove('sort-asc','sort-desc');o.dataset.asc='';});
        th.classList.add(asc?'sort-asc':'sort-desc');
        th.dataset.asc=asc?'1':'0';
        var cell=function(r){var c=r.querySelectorAll('td');return c[col]?c[col].textContent.trim():'';};
        rows.sort(function(a,b){
          var va=cell(a);
          var vb=cell(b);
          var na=parseFloat(va.replace(/[%,]/g,''));
          var nb=parseFloat(vb.replace(/[%,]/g,''));
          var c;
          if(!isNaN(na)&&!isNaN(nb))c=na-nb;else c=va.localeCompare(vb,'zh');
          return asc?c:-c;
        });
        rows.forEach(function(r){tbody.appendChild(r);});
      });
    });
  }
  window.makeSortable = makeSortable;
  document.querySelectorAll('table').forEach(makeSortable);
})();

/* ===== history comparison ===== */
var HIST = ${historyPayload};

function esc(s){var d=document.createElement('span');d.textContent=String(s==null?'':s);return d.innerHTML;}
function delta(now, was){
  if(was==null||isNaN(was))return '<span class="flat">—</span>';
  var d=now-was;
  if(Math.abs(d)<0.05)return '<span class="flat">0.0</span>';
  return '<span class="'+(d>0?'up':'down')+'">'+(d>0?'+':'')+d.toFixed(1)+'</span>';
}
function findRun(id){
  if(id==='__current__')return HIST.current;
  for(var i=0;i<HIST.snapshots.length;i++){if(HIST.snapshots[i].id===id)return HIST.snapshots[i];}
  return null;
}
function selectedRuns(){
  var out=[];
  document.querySelectorAll('.snapChk').forEach(function(c){
    if(c.checked){var r=findRun(c.value);if(r)out.push(r);}
  });
  out.sort(function(a,b){return a.ranAt<b.ranAt?1:a.ranAt>b.ranAt?-1:0;});
  return out;
}
function selAll(on){
  document.querySelectorAll('.snapChk').forEach(function(c){c.checked=on;});
  renderHistory();
}
function renderTrend(runs){
  var t=document.getElementById('trendTable');
  var names=HIST.diffNames;
  var head='<tr><th>快照</th><th>运行时间</th><th>git</th><th>种子</th>';
  names.forEach(function(n){head+='<th>'+esc(n)+' 胜率</th><th>'+esc(n)+' Δ</th><th>'+esc(n)+' 击杀</th><th>'+esc(n)+' 基地毁</th>';});
  head+='</tr>';
  t.querySelector('thead').innerHTML=head;
  var cur=HIST.current;
  var body='<tr><td><b>当前运行</b></td><td>'+esc(cur.at)+'</td><td>—</td><td>'+cur.seeds+'</td>';
  names.forEach(function(n){
    var c=cur.diffs[n];
    body+= c?('<td><b>'+c.w.toFixed(1)+'%</b></td><td class="flat">基准</td><td>'+c.k.toFixed(2)+'</td><td>'+c.b.toFixed(1)+'%</td>'):'<td>—</td><td>—</td><td>—</td><td>—</td>';
  });
  body+='</tr>';
  runs.forEach(function(r){
    body+='<tr><td>'+esc(r.label)+'</td><td>'+esc(r.at)+'</td><td>'+esc(r.git||'—')+'</td><td>'+r.seeds+'</td>';
    names.forEach(function(n){
      var s=r.diffs[n], c=cur.diffs[n];
      if(!s){body+='<td>—</td><td>—</td><td>—</td><td>—</td>';return;}
      body+='<td>'+s.w.toFixed(1)+'%</td><td>'+(c?delta(c.w,s.w):'—')+'</td><td>'+s.k.toFixed(2)+'</td><td>'+s.b.toFixed(1)+'%</td>';
    });
    body+='</tr>';
  });
  t.querySelector('tbody').innerHTML=body;
}
function renderStageDelta(){
  var t=document.getElementById('stageDeltaTable');
  var sel=document.getElementById('baseSel');
  var base=sel&&sel.value?findRun(sel.value):null;
  var names=HIST.diffNames, cur=HIST.current;
  if(!base){t.querySelector('thead').innerHTML='';t.querySelector('tbody').innerHTML='<tr><td colspan="3">未选择基准快照</td></tr>';return;}
  var head='<tr><th>关 #</th><th>关名</th>'+names.map(function(n){return '<th>'+esc(n)+'</th>';}).join('')+'</tr>';
  t.querySelector('thead').innerHTML=head;
  var onlyChanged=document.getElementById('onlyChanged').checked;
  var rows='';
  for(var i=0;i<HIST.stageNames.length;i++){
    var cells='',changed=false;
    for(var j=0;j<names.length;j++){
      var n=names[j];
      var cs=cur.diffs[n]&&cur.diffs[n].stages[i];
      var bs=base.diffs[n]&&base.diffs[n].stages[i];
      if(!cs){cells+='<td>—</td>';continue;}
      if(bs&&Math.abs(cs.w-bs.w)>=0.05)changed=true;
      var baseTxt=bs?bs.w.toFixed(1)+'%':'—';
      var dv=bs?delta(cs.w,bs.w):'';
      if(bs&&Math.abs(cs.w-bs.w)<0.05)dv=''; // 无变化时不显示 0.0 噪音
      cells+='<td class="baseline-cell">'+(bs?esc(baseTxt)+dv:'—')+'</td>';
    }
    if(onlyChanged&&!changed)continue;
    rows+='<tr><td>'+(i+1)+'</td><td>'+esc(HIST.stageNames[i])+'</td>'+cells+'</tr>';
  }
  t.querySelector('tbody').innerHTML=rows||'<tr><td colspan="'+(2+names.length)+'">与该快照逐关胜率完全一致</td></tr>';
  if(window.makeSortable)window.makeSortable(t);
}
function renderHistory(){
  var runs=selectedRuns();
  renderTrend(runs);
  var sel=document.getElementById('baseSel');
  var prev=sel.value;
  sel.innerHTML=runs.map(function(r){return '<option value="'+esc(r.id)+'">'+esc(r.label)+'</option>';}).join('');
  if(runs.length===0){sel.innerHTML='<option value="">（未选择）</option>';sel.value='';}
  else if(prev&&runs.some(function(r){return r.id===prev;})){sel.value=prev;}
  else{sel.value=runs[0].id;}
  renderStageDelta();
  if(window.makeSortable)window.makeSortable(document.getElementById('trendTable'));
}
if(HIST.snapshots.length){
  document.querySelectorAll('.snapChk').forEach(function(c){c.addEventListener('change',renderHistory);});
  renderHistory();
}
</script>
</body></html>`
}

await main()
