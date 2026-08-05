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
 * Usage:
 *   bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60 --out tmp/winrate
 *
 * Default: --difficulties classic,hard,chaos  --seeds 1-60
 */
import { STAGES } from '../../src/config/stages'
import { SimWorkerPool } from './sim-pool'
import type { SimTask, SimTaskResult } from './sim-worker'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { MAX_TICKS } from './simulation-runner'
import { writeFileSync, mkdirSync } from 'node:fs'

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

  // ===== Build markdown + HTML reports =====
  const md = buildMarkdown(aggs, unionWorst)
  writeFileSync(`${outDir}/report.md`, md)
  const html = buildHtml(aggs, unionWorst, raw.scope)
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

function buildMarkdown(aggs: DiffAgg[], unionWorst: number[]): string {
  const lines: string[] = []
  lines.push('# God AI 胜率扫描报告 (classic / hard / chaos)')
  lines.push('')
  lines.push(
    `> 范围：35 关 × 60 种子 × ${aggs.length} 难度 = ${aggs.reduce((n, a) => n + a.total, 0)} 局。`,
  )
  lines.push(`> 当前代码默认 God AI 参数（DEFAULT_GOD_AI_PARAMS），无人工操作。`)
  lines.push('')
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

function buildHtml(
  aggs: DiffAgg[],
  unionWorst: number[],
  scope: { seedsCount: number; maxTicks: number },
): string {
  const totalRuns = aggs.reduce((n, a) => n + a.total, 0)
  const maxWin = Math.max(...aggs.map((a) => pct(a.wins, a.total)))

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
</style></head><body>
<h1>God AI 胜率扫描报告</h1>
<p class="sub">范围：${stageCount} 关 × ${scope.seedsCount} 种子 × ${aggs.length} 难度 = ${totalRuns} 局 ｜ 默认 God AI 参数，无人工操作 ｜ 生成于 ${new Date().toLocaleString()}</p>

<div class="card">
  <h2>重新生成报告</h2>
  <p>在仓库根目录运行以下命令（默认输出到 <code>reports/winrate/</code>，已被 gitignore）：</p>
  <pre class="cmd" id="cmd">bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60</pre>
  <button onclick="copyCmd(this,'cmd')">复制命令</button>
  <p class="hint">可自定义：<code>--difficulties classic,hard,chaos,relax</code> ｜ <code>--seeds 1-60</code> ｜ <code>--out reports/winrate</code></p>
</div>

<div class="card">
  <h2>生成失败录像（劣关取证）</h2>
  <p>对指定关卡批量跑 God AI，只保存未通关的录像（用于复盘劣关 / 爆基地原因）：</p>
  <pre class="cmd" id="cmd2">bun tools/optimize/level-sim.ts --stage 33 --difficulty hard --max-ticks 300000 --size 20 --save-replays --replay-failures-only --no-output</pre>
  <button onclick="copyCmd(this,'cmd2')">复制命令</button>
  <p class="hint">替换 <code>--stage 33</code> / <code>--difficulty hard</code> 指向具体劣关；<code>--size 20</code> 为并行样本数。</p>
</div>

<div class="card">
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

<div class="card">
  <h2>劣关横向对比（三难度并排）</h2>
  <table><thead><tr>${cmpHeadFlat}</tr></thead>
  <tbody>${cmpRows}</tbody></table>
</div>

<div class="card">
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
(function(){
  function makeSortable(table){
    var thead=table.querySelector('thead');if(!thead)return;
    var headRow=thead.rows[thead.rows.length-1];
    var ths=Array.prototype.slice.call(headRow.cells);
    ths.forEach(function(th){
      th.addEventListener('click',function(){
        var col=th.cellIndex;
        var tbody=table.tBodies[0];if(!tbody)return;
        var rows=Array.prototype.slice.call(tbody.rows);
        var asc=th.dataset.asc!=='1';
        ths.forEach(function(o){o.classList.remove('sort-asc','sort-desc');o.dataset.asc='';});
        th.classList.add(asc?'sort-asc':'sort-desc');
        th.dataset.asc=asc?'1':'0';
        rows.sort(function(a,b){
          var va=a.cells[col].textContent.trim();
          var vb=b.cells[col].textContent.trim();
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
  document.querySelectorAll('table').forEach(makeSortable);
})();
</script>
</body></html>`
}

await main()
