/**
 * rl-hourly-inspect.ts — RL 训练每小时巡检三合一：
 *   1. 读 training_log.jsonl（run_start / iteration / circuit_break）+ 进程存活探测；
 *   2. 增量扫描每轮 it 目录里 worker 子目录的 _rl_report.json（+ shard 的
 *      manifest.json），把各关累计战绩（games/wins/kills）与最近扫描段
 *      （lastPass）写回 tmp/rl-traj/inspection-state.json；
 *   3. 生成可排序 HTML 报告 tmp/rl-traj/inspection-report.html
 *      （样式与点击排序对齐 tools/sim/scorecard-html.ts）：累计战绩总表 +
 *      本段各关表现表（胜率/击杀/耗时/余命/拾取道具）。
 *
 * 用法：bun tools/diag/rl-hourly-inspect.ts [--dry-run] [--up-to N]
 * 口径：kills = manifest.dims.progress.raw（缺失时 round(dimLists.progress×20)，
 * 分母恒为 ENEMIES_PER_STAGE=20 且不触顶，无损）；余命 = dims.lives.raw；
 * 拾取道具 = dims.loot.raw；单局耗时 = dims.clearSpeed.raw（ticks）。
 * kills 自 2026-08-23 起累计（更早迭代已被 --keep-iters 轮转，无法回补）。
 */

import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { execSync } from 'node:child_process'

const ROOT = resolve(import.meta.dir, '..', '..')
const TRAJ_DIR = join(ROOT, 'tmp', 'rl-traj')
const LOG_PATH = join(TRAJ_DIR, 'training_log.jsonl')
const META_PATH = join(TRAJ_DIR, 'dist-agent-meta.jsonl')
const STATE_PATH = join(TRAJ_DIR, 'inspection-state.json')
const REPORT_PATH = join(TRAJ_DIR, 'inspection-report.html')
const ENEMY_TOTAL = 20
const START_LIVES = 3
const GAMES_PER_ITER = 70

interface IterEvent {
  iter: number
  time: string
  winRate: number
  outcomes: Record<string, number>
  score_mean: number
  entropy: number
  kl: number
  ticks: number
}

interface RlReport {
  stages: number[]
  seeds: number[]
  outcomes: Record<string, number>
  totalTicks?: number
  scoreList: number[]
  dimLists?: Record<string, number[]>
}

interface DimScore {
  value: number | null
  raw: number
}

type ManifestDims = Record<string, DimScore>

interface WorkerData {
  report: RlReport
  manifest: ManifestDims | null
}

interface StageEntry {
  nameZh: string
  games: number
  wins: number
  kills: number
}

interface Totals {
  iterations: number
  games: number
  wins: number
  kills: number
}

interface PassStageStat {
  games: number
  wins: number
  kills: number
  ticks: number
  livesSum: number
  lootSum: number
  lootGames: number
}

interface LastPass {
  covered: string
  endedAt: string
  stages: Record<string, PassStageStat>
}

interface InspectionState {
  version: number
  purpose: string
  runStartTime: string
  lastScannedIter: number
  scannedIters: number[]
  coverageNote: string
  totals: Totals
  stageStats: Record<string, StageEntry>
  lastPass?: LastPass
}

interface NewWin {
  iter: number
  stage: number
  name: string
  seed: number
  score: number
  kills: number
}

interface AgentRow {
  node: string
  attempts: number
  ok: number
  fail: number
  wins: number
  elapsedSum: number
  elapsedN: number
  lastIt: number
  lastError: string
}

interface HtmlRow {
  idx: number
  dispIdx: number
  name: string
  games: number
  wins: number
  winRate: number
  kills: number
  avgKills: number
}

interface PassHtmlRow {
  idx: number
  dispIdx: number
  name: string
  games: number
  wins: number
  winRate: number
  kills: number
  avgKills: number
  avgSeconds: number
  avgLives: number
  loot: number
}

function parseArgs(): { dryRun: boolean; upTo: number | null; passFrom: number | null } {
  const argv = process.argv.slice(2)
  const dryRun = argv.includes('--dry-run')
  let upTo: number | null = null
  const i = argv.indexOf('--up-to')
  if (i >= 0 && argv[i + 1]) upTo = Number(argv[i + 1])
  let passFrom: number | null = null
  const j = argv.indexOf('--pass-from')
  if (j >= 0 && argv[j + 1]) passFrom = Number(argv[j + 1])
  return { dryRun, upTo, passFrom }
}

interface LogEvent {
  event?: string
  time?: string
  iter?: number
}

function parseLog(): { runStarts: string[]; circuitBreaks: string[]; iters: Map<number, IterEvent> } {
  const runStarts: string[] = []
  const circuitBreaks: string[] = []
  const iters = new Map<number, IterEvent>()
  for (const line of readFileSync(LOG_PATH, 'utf8').split('\n')) {
    if (!line.trim()) continue
    let ev: LogEvent
    try {
      ev = JSON.parse(line) as LogEvent
    } catch {
      continue
    }
    if (ev.event === 'run_start' && typeof ev.time === 'string') runStarts.push(ev.time)
    else if (ev.event === 'circuit_break') circuitBreaks.push(line)
    else if (ev.event === 'iteration' && typeof ev.iter === 'number') iters.set(ev.iter, ev as unknown as IterEvent)
  }
  return { runStarts, circuitBreaks, iters }
}

function pythonProcCount(): number | null {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq python.exe" /NH', {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    const m = out.match(/python\.exe/gi)
    return m ? m.length : 0
  } catch {
    return null
  }
}

function ensureEntry(state: InspectionState, idx: number): StageEntry {
  const key = String(idx)
  let e = state.stageStats[key]
  if (!e) {
    e = { nameZh: `Stage ${idx + 1}`, games: 0, wins: 0, kills: 0 }
    state.stageStats[key] = e
  }
  if (typeof e.kills !== 'number') e.kills = 0
  return e
}

function readWorkerManifest(workerDir: string): ManifestDims | null {
  for (const d of readdirSync(workerDir, { withFileTypes: true })) {
    if (!d.isDirectory() || !/^rl_s\d+_seed\d+$/.test(d.name)) continue
    const mp = join(workerDir, d.name, 'manifest.json')
    if (!existsSync(mp)) continue
    try {
      const m = JSON.parse(readFileSync(mp, 'utf8')) as { dims?: ManifestDims }
      return m.dims ?? null
    } catch {
      return null
    }
  }
  return null
}

function scanIterDir(n: number): WorkerData[] {
  const dir = join(TRAJ_DIR, `it${n}`)
  if (!existsSync(dir)) return []
  const out: WorkerData[] = []
  for (const d of readdirSync(dir, { withFileTypes: true })) {
    if (!d.isDirectory() || !/^w\d+$/.test(d.name)) continue
    const rp = join(dir, d.name, '_rl_report.json')
    if (!existsSync(rp)) continue
    const report = JSON.parse(readFileSync(rp, 'utf8')) as RlReport
    out.push({ report, manifest: readWorkerManifest(join(dir, d.name)) })
  }
  return out
}

interface GameMetrics {
  stage: number
  seed: number
  score: number
  win: boolean
  kills: number
  lives: number
  loot: number
  ticks: number
}

function metricsOf(w: WorkerData): GameMetrics {
  const r = w.report
  const dl = r.dimLists
  const d = w.manifest
  const num = (x: DimScore | undefined): number => (x && typeof x.raw === 'number' ? Math.round(x.raw) : -1)
  const kills =
    num(d?.progress) >= 0
      ? num(d?.progress)
      : dl?.progress && dl.progress.length > 0
        ? Math.round(dl.progress[0] * ENEMY_TOTAL)
        : -1
  const lives =
    num(d?.lives) >= 0
      ? num(d?.lives)
      : dl?.lives && dl.lives.length > 0
        ? Math.round(dl.lives[0] * START_LIVES)
        : -1
  const loot = num(d?.loot)
  const csRaw = d?.clearSpeed && typeof d.clearSpeed.raw === 'number' ? d.clearSpeed.raw : -1
  const ticks = csRaw > 0 ? Math.round(csRaw) : typeof r.totalTicks === 'number' ? r.totalTicks : -1
  return {
    stage: r.stages[0],
    seed: r.seeds[0],
    score: r.scoreList[0],
    win: (r.outcomes?.stage_clear ?? 0) >= 1,
    kills,
    lives,
    loot,
    ticks,
  }
}

function healthVerdict(recent: IterEvent[]): string {
  if (recent.length === 0) return '无数据'
  const entMin = Math.min(...recent.map((e) => e.entropy))
  const tpgMin = Math.min(...recent.map((e) => e.ticks / GAMES_PER_ITER))
  let klStreak = 0
  let klMaxStreak = 0
  for (const e of recent) {
    klStreak = e.kl > 0.15 ? klStreak + 1 : 0
    if (klStreak > klMaxStreak) klMaxStreak = klStreak
  }
  if (entMin <= 0.6 || klMaxStreak >= 3 || tpgMin < 1000) return '异常'
  if (entMin < 0.8 || klMaxStreak >= 2 || tpgMin < 2000) return '观察'
  return '健康'
}

function fmtNow(): string {
  const d = new Date()
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

/**
 * 读调度器落盘的 dist-agent-meta.jsonl，按节点聚合采样元数据。
 * 记录由 run_rl 的 run_rollout_queue 每交付/失败一局写一行（run_rl.py _record_agent_meta）。
 * 行字段：node / it / stage / seed / ok / win / elapsedSec(成功) | reason(失败) / ts。
 */
function readAgentMeta(): AgentRow[] {
  if (!existsSync(META_PATH)) return []
  const by = new Map<string, AgentRow>()
  const ensure = (node: string): AgentRow => {
    let a = by.get(node)
    if (!a) {
      a = { node, attempts: 0, ok: 0, fail: 0, wins: 0, elapsedSum: 0, elapsedN: 0, lastIt: 0, lastError: '' }
      by.set(node, a)
    }
    return a
  }
  for (const line of readFileSync(META_PATH, 'utf8').split('\n')) {
    const t = line.trim()
    if (!t) continue
    let r: Record<string, unknown>
    try {
      r = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    const node = String(r.node ?? '?')
    const a = ensure(node)
    a.attempts++
    if (r.ok) {
      a.ok++
      if (r.win) a.wins++
      if (typeof r.elapsedSec === 'number') {
        a.elapsedSum += r.elapsedSec
        a.elapsedN++
      }
    } else {
      a.fail++
      if (typeof r.reason === 'string') a.lastError = r.reason.slice(0, 40)
    }
    if (typeof r.it === 'number') a.lastIt = Math.max(a.lastIt, r.it)
  }
  return [...by.values()].sort((p, q) => q.ok - p.ok)
}

interface PassSection {
  covered: string
  endedAt: string
  rows: PassHtmlRow[]
}

function buildHtml(rows: HtmlRow[], bannerLines: string[], recent: IterEvent[], newWins: NewWin[], scannedLine: string, pass: PassSection, agent: AgentRow[]): string {
  const dataJson = JSON.stringify(rows)
  const passJson = JSON.stringify(pass.rows)
  const bannerHtml = bannerLines.map((l) => `<div>${l}</div>`).join('\n')

  const recentRows = recent
    .map(
      (e) =>
        `<tr><td class="txt">it${e.iter}</td><td>${esc(e.time)}</td>` +
        `<td>${(e.winRate * 100).toFixed(1)}%</td><td>${e.score_mean.toFixed(4)}</td>` +
        `<td>${e.entropy.toFixed(3)}</td><td>${e.kl.toFixed(4)}</td>` +
        `<td>${Math.round(e.ticks / GAMES_PER_ITER)}</td>` +
        `<td>${e.outcomes.stage_clear ?? 0}</td></tr>`,
    )
    .join('\n')

  const newWinRows =
    newWins.length === 0
      ? '<tr><td colspan="6" class="na">本段无新胜局</td></tr>'
      : newWins
          .map(
            (w) =>
              `<tr><td class="txt">it${w.iter}</td><td class="txt">s${w.stage + 1} ${esc(w.name)}</td>` +
              `<td>${w.seed}</td><td>${w.score.toFixed(3)}</td><td>${w.kills}</td></tr>`,
          )
          .join('\n')

  const totalGames = rows.reduce((a, r) => a + r.games, 0)

  const tablesConfig = [
    `  { id: 't', rows: ${dataJson}, def: 'winRate', dir: -1, textKeys: ['name'],
    row: function(r){
      return '<tr>'
        +'<td class="txt">'+r.dispIdx+'</td>'
        +'<td class="txt">'+r.name+'</td>'
        +'<td>'+r.games+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(r.winRate)+'">'+(r.winRate*100).toFixed(1)+'%</td>'
        +'<td>'+r.kills+'</td>'
        +'<td>'+r.avgKills.toFixed(2)+'</td>'
      +'</tr>';
    } },`,
    `  { id: 'p', rows: ${passJson}, def: 'dispIdx', dir: 1, textKeys: ['name'],
    row: function(r){
      return '<tr>'
        +'<td class="txt">'+r.dispIdx+'</td>'
        +'<td class="txt">'+r.name+'</td>'
        +'<td>'+r.games+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(r.winRate)+'">'+(r.winRate*100).toFixed(1)+'%</td>'
        +'<td>'+r.kills+'</td>'
        +'<td>'+r.avgKills.toFixed(2)+'</td>'
        +'<td>'+r.avgSeconds.toFixed(1)+'</td>'
        +'<td>'+r.avgLives.toFixed(2)+'</td>'
        +'<td>'+r.loot+'</td>'
      +'</tr>';
    } }`,
    `  { id: 'a', rows: ${JSON.stringify(agent)}, def: 'ok', dir: -1, textKeys: ['node'],
    row: function(r){
      var rate = r.attempts>0 ? (100*r.ok/r.attempts).toFixed(1)+'%' : '-';
      var avg = r.elapsedN>0 ? (r.elapsedSum/r.elapsedN).toFixed(1) : '-';
      var wr = r.ok>0 ? r.wins/r.ok : 0;
      return '<tr>'
        +'<td class="txt">'+r.node+'</td>'
        +'<td>'+r.ok+'</td>'
        +'<td>'+r.fail+'</td>'
        +'<td>'+rate+'</td>'
        +'<td>'+avg+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(wr)+'\">'+(wr>0?(wr*100).toFixed(1)+'%':'-')+'</td>'
        +'<td>'+(r.lastIt?('it'+r.lastIt):'-')+'</td>'
        +'<td class="txt na">'+r.lastError+'</td>'
      +'</tr>';
    } }`,
  ].join(',\n')

  const passHeading = pass.rows.length > 0 ? `本段各关表现（${esc(pass.covered)}，截至 ${esc(pass.endedAt)}）` : '本段各关表现（暂无扫描段数据）'

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RL 训练各关战绩巡检报告</title>
<style>
  :root { --good:#1a7f37; --bad:#cf222e; --line:#d0d7de; --bg:#fff; --th:#f6f8fa; --ink:#1f2328; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin:0; padding:24px; background:#fafbfc; color:var(--ink); }
  h1 { font-size:20px; margin:0 0 4px; }
  h2 { font-size:15px; margin:22px 0 8px; }
  .meta { color:#57606a; font-size:13px; margin-bottom:14px; line-height:1.6; }
  .suite { background:#eef6ff; border:1px solid #c8e1ff; border-radius:8px; padding:10px 14px; font-size:13px; margin-bottom:16px; line-height:1.8; }
  .wrap { overflow:auto; border:1px solid var(--line); border-radius:8px; background:var(--bg); margin-bottom:18px; }
  table { border-collapse:collapse; font-size:13px; width:100%; }
  thead th { position:sticky; top:0; background:var(--th); border-bottom:2px solid var(--line); padding:8px 10px; text-align:right; white-space:nowrap; cursor:pointer; user-select:none; z-index:1; }
  thead th.txt { text-align:left; }
  thead th:hover { background:#eaeef2; }
  thead th .arrow { color:#0969da; font-size:11px; margin-left:3px; }
  tbody td { padding:6px 10px; text-align:right; border-bottom:1px solid #eaecef; white-space:nowrap; }
  tbody td.txt { text-align:left; }
  tbody tr:nth-child(even) { background:#f9fafb; }
  tbody tr:hover { background:#fff8e1; }
  .win-cell { font-weight:600; }
  .na { color:#bbb; }
  footer { margin-top:14px; color:#8c959f; font-size:12px; line-height:1.6; }
  code { background:#eef1f4; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<h1>RL 训练各关战绩巡检报告</h1>
<div class="meta">生成时间：${fmtNow()} · ${esc(scannedLine)} · 点击表格任意表头排序（再次点击切换升/降序）。</div>
<div class="suite">${bannerHtml}</div>

<h2>采样机健康（节点采样元数据）</h2>
<div class="wrap">
<table id="a">
  <thead><tr>
    <th class="txt" data-key="node">节点</th><th data-key="ok">成功局</th><th data-key="fail">失败局</th>
    <th data-key="rate">采样成功</th><th data-key="avgSec">局均耗时(s)</th><th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th><th data-key="lastIt">最近迭代</th><th class="txt">最近错误</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>

<h2>最近迭代健康指标</h2>
<div class="wrap">
<table>
  <thead><tr>
    <th class="txt">迭代</th><th class="txt">时间</th><th>胜率</th><th>score_mean</th><th>entropy</th><th>KL</th><th>局均 ticks</th><th>通关局数</th>
  </tr></thead>
  <tbody>
${recentRows}
  </tbody>
</table>
</div>

<h2>本段新胜局明细</h2>
<div class="wrap">
<table>
  <thead><tr>
    <th class="txt">迭代</th><th class="txt">关卡</th><th>种子</th><th>得分</th><th>击杀</th>
  </tr></thead>
  <tbody>
${newWinRows}
  </tbody>
</table>
</div>

<h2>${passHeading}</h2>
<div class="wrap">
<table id="p">
  <thead><tr>
    <th class="txt" data-key="dispIdx">关号</th>
    <th class="txt" data-key="name">关卡名</th>
    <th data-key="games">局数</th>
    <th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th>
    <th data-key="kills">击杀</th>
    <th data-key="avgKills">场均击杀</th>
    <th data-key="avgSeconds">场均耗时(s)</th>
    <th data-key="avgLives">场均余命</th>
    <th data-key="loot">拾取道具</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>

<h2>各关累计战绩（games / wins / kills）</h2>
<div class="wrap">
<table id="t">
  <thead><tr>
    <th class="txt" data-key="dispIdx">关号</th>
    <th class="txt" data-key="name">关卡名</th>
    <th data-key="games">局数</th>
    <th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th>
    <th data-key="kills">击杀</th>
    <th data-key="avgKills">场均击杀</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>
<footer>
  关号显示 = 0 基索引 + 1（与 STAGES / STAGE_NAMES_ZH 对应）。<br>
  「本段」= 最近一次增量扫描覆盖的迭代段；余命含 tank 道具加成可大于 3；拾取道具为该关本段拾取总数。<br>
  击杀口径：manifest.dims.progress.raw（缺失时 round(dimLists.progress × ${ENEMY_TOTAL})）；自 2026-08-23 起累计，此前轮转删除的迭代无击杀数据。<br>
  数据源：<code>tmp/rl-traj/training_log.jsonl</code> · 累计账本：<code>tmp/rl-traj/inspection-state.json</code>（共 ${totalGames} 局入表）。
</footer>
<script>
function heat(v){
  var t = Math.max(0, Math.min(1, v));
  var hue = Math.round(t*125);
  return 'background:hsl(' + hue + ' 70% 88%); color:#1f2328;';
}
function initTable(cfg){
  var tbody = document.querySelector('#'+cfg.id+' tbody');
  var sortKey = cfg.def, sortDir = cfg.dir;
  function render(){
    var rows = cfg.rows.slice().sort(function(a,b){
      var av=a[sortKey], bv=b[sortKey];
      if(cfg.textKeys.indexOf(sortKey)>=0){ return sortDir*String(av).localeCompare(String(bv),'zh-Hans-CN'); }
      return sortDir*(av-bv);
    });
    tbody.innerHTML = rows.map(cfg.row).join('');
    document.querySelectorAll('#'+cfg.id+' thead th').forEach(function(th){
      var old = th.querySelector('.arrow'); if(old) th.removeChild(old);
      if(th.dataset.key===sortKey){ var s=document.createElement('span'); s.className='arrow'; s.textContent = sortDir<0?'\\u25BC':'\\u25B2'; th.appendChild(s); }
    });
  }
  document.querySelectorAll('#'+cfg.id+' thead th').forEach(function(th){
    th.addEventListener('click', function(){
      var k = th.dataset.key;
      if(k===sortKey){ sortDir = sortDir*-1; } else { sortKey=k; sortDir = (cfg.textKeys.indexOf(k)>=0)?1:-1; }
      render();
    });
  });
  render();
}
var TABLES = [
${tablesConfig}
];
TABLES.forEach(initTable);
</script>
</body>
</html>`
}

function main(): void {
  const { dryRun, upTo, passFrom } = parseArgs()
  const state = existsSync(STATE_PATH)
    ? (JSON.parse(readFileSync(STATE_PATH, 'utf8')) as InspectionState)
    : {
        version: 1,
        purpose: '首次初始化（run_rl 自动巡检前 state 不存在）',
        runStartTime: '',
        lastScannedIter: 0,
        scannedIters: [],
        coverageNote: '',
        totals: { iterations: 0, games: 0, wins: 0, kills: 0 },
        stageStats: {},
      }
  const { runStarts, circuitBreaks, iters } = parseLog()

  const procs = pythonProcCount()
  const procStr = procs === null ? '未知(tasklist 失败)' : procs > 0 ? `python.exe ×${procs} 存活` : '未发现 python 进程 ⚠'
  const lastRunStart = runStarts[runStarts.length - 1] ?? '(无记录)'
  const iterNums = [...iters.keys()].sort((a, b) => a - b)
  const lastIter = iterNums.length > 0 ? iters.get(iterNums[iterNums.length - 1]) : undefined
  const cbStr = circuitBreaks.length === 0 ? '无 circuit_break' : `熔断 ${circuitBreaks.length} 次 ⚠ ${circuitBreaks[circuitBreaks.length - 1]}`

  const scanUpTo = upTo ?? (iterNums.length > 0 ? iterNums[iterNums.length - 1] : state.lastScannedIter)

  const fromIter = state.lastScannedIter
  const results: Array<{ iter: number; games: number; wins: number; kills: number }> = []
  const newWins: NewWin[] = []
  const firstEver: string[] = []
  const crossCheckBad: string[] = []
  const passStages: Record<string, PassStageStat> = {}
  let totalGames = 0
  let totalWins = 0
  let totalKills = 0
  let missingDirs = 0

  for (let n = fromIter + 1; n <= scanUpTo; n++) {
    const workers = scanIterDir(n)
    if (workers.length === 0) {
      missingDirs++
      console.log(`WARN it${n} 无可扫报告（目录缺失或为空），跳过`)
      continue
    }
    let g = 0
    let w = 0
    let kSum = 0
    for (const wk of workers) {
      const m = metricsOf(wk)
      const win = m.win
      g++
      if (win) w++
      if (m.kills >= 0) kSum += m.kills
      const entry = ensureEntry(state, m.stage)
      const prevWins = entry.wins
      entry.games++
      if (win) {
        entry.wins++
        newWins.push({ iter: n, stage: m.stage, name: entry.nameZh, seed: m.seed, score: m.score, kills: Math.max(m.kills, 0) })
        if (prevWins === 0) firstEver.push(`s${m.stage + 1} ${entry.nameZh}`)
      }
      if (m.kills >= 0) entry.kills += m.kills
      const pk = String(m.stage)
      let ps = passStages[pk]
      if (!ps) {
        ps = { games: 0, wins: 0, kills: 0, ticks: 0, livesSum: 0, lootSum: 0, lootGames: 0 }
        passStages[pk] = ps
      }
      ps.games++
      if (win) ps.wins++
      if (m.kills >= 0) ps.kills += m.kills
      if (m.ticks > 0) ps.ticks += m.ticks
      if (m.lives >= 0) ps.livesSum += m.lives
      if (m.loot >= 0) {
        ps.lootSum += m.loot
        ps.lootGames++
      }
    }
    totalGames += g
    totalWins += w
    totalKills += kSum
    results.push({ iter: n, games: g, wins: w, kills: kSum })
    if (!state.scannedIters.includes(n)) state.scannedIters.push(n)
    const ev = iters.get(n)
    const logClears = ev ? (ev.outcomes.stage_clear ?? 0) : -1
    if (logClears >= 0 && logClears !== w) crossCheckBad.push(`it${n}: 扫描=${w} 日志=${logClears}`)
  }

  // --pass-from N：对已入账的历史段只读重扫，仅重建本段聚合（不碰累计账本）
  const passIters: number[] = results.map((r) => r.iter)
  if (passFrom !== null && passFrom <= scanUpTo) {
    for (let n = Math.max(passFrom, 1); n <= Math.min(scanUpTo, fromIter); n++) {
      const histWorkers = scanIterDir(n)
      if (histWorkers.length === 0) continue
      for (const wk of histWorkers) {
        const m = metricsOf(wk)
        const pk = String(m.stage)
        let ps = passStages[pk]
        if (!ps) {
          ps = { games: 0, wins: 0, kills: 0, ticks: 0, livesSum: 0, lootSum: 0, lootGames: 0 }
          passStages[pk] = ps
        }
        ps.games++
        if (m.win) ps.wins++
        if (m.kills >= 0) ps.kills += m.kills
        if (m.ticks > 0) ps.ticks += m.ticks
        if (m.lives >= 0) ps.livesSum += m.lives
        if (m.loot >= 0) {
          ps.lootSum += m.loot
          ps.lootGames++
        }
      }
      passIters.push(n)
    }
    passIters.sort((a, b) => a - b)
  }

  if (results.length > 0) {
    state.lastScannedIter = scanUpTo
    state.totals.iterations += results.length
    state.totals.games += totalGames
    state.totals.wins += totalWins
    state.totals.kills += totalKills
    const span = results.length === 1 ? `it${results[0].iter}` : `it${results[0].iter}-it${results[results.length - 1].iter}`
    state.coverageNote += ` Last scan ${fmtNow()} covered ${span} (+${totalGames} games / +${totalWins} wins / +${totalKills} kills).`
  }

  if (passIters.length > 0) {
    const span = passIters.length === 1 ? `it${passIters[0]}` : `it${passIters[0]}-it${passIters[passIters.length - 1]}`
    state.lastPass = { covered: span, endedAt: fmtNow(), stages: passStages }
  }

  if (!dryRun && (results.length > 0 || passIters.length > 0)) writeFileSync(STATE_PATH, JSON.stringify(state, null, 2))

  const recentIters = iterNums
    .slice(-5)
    .map((n) => iters.get(n))
    .filter((e): e is IterEvent => e !== undefined)
  const verdict = healthVerdict(recentIters)

  const rows: HtmlRow[] = Object.keys(state.stageStats)
    .map(Number)
    .sort((a, b) => a - b)
    .map((idx) => {
      const e = ensureEntry(state, idx)
      return {
        idx,
        dispIdx: idx + 1,
        name: e.nameZh,
        games: e.games,
        wins: e.wins,
        winRate: e.games > 0 ? e.wins / e.games : 0,
        kills: e.kills,
        avgKills: e.games > 0 ? e.kills / e.games : 0,
      }
    })

  const lp = state.lastPass
  const passRows: PassHtmlRow[] = []
  if (lp) {
    for (const key of Object.keys(lp.stages).map(Number).sort((a, b) => a - b)) {
      const s = lp.stages[String(key)]
      const name = state.stageStats[String(key)]?.nameZh ?? `Stage ${key + 1}`
      passRows.push({
        idx: key,
        dispIdx: key + 1,
        name,
        games: s.games,
        wins: s.wins,
        winRate: s.games > 0 ? s.wins / s.games : 0,
        kills: s.kills,
        avgKills: s.games > 0 ? s.kills / s.games : 0,
        avgSeconds: s.games > 0 ? s.ticks / s.games / 60 : 0,
        avgLives: s.games > 0 ? s.livesSum / s.games : 0,
        loot: s.lootSum,
      })
    }
  }

  const wrAll = state.totals.games > 0 ? state.totals.wins / state.totals.games : 0
  const bannerLines = [
    `<b>进程</b>：${esc(procStr)}　·　<b>熔断</b>：${esc(cbStr)}`,
    `<b>最后 run_start</b>：${esc(lastRunStart)}　·　<b>最后迭代</b>：it${lastIter ? lastIter.iter : '?'} @ ${lastIter ? esc(lastIter.time) : '?'}`,
    `<b>健康判定</b>：<b>${verdict}</b>（近 ${recentIters.length} 轮：entropy 最小 ${(recentIters.length ? Math.min(...recentIters.map((e) => e.entropy)) : 0).toFixed(3)} · KL 最大 ${(recentIters.length ? Math.max(...recentIters.map((e) => e.kl)) : 0).toFixed(4)} · 局均 ticks 最小 ${recentIters.length ? Math.round(Math.min(...recentIters.map((e) => e.ticks / GAMES_PER_ITER))) : 0}）`,
    `<b>累计</b>：${state.totals.games} 局 / ${state.totals.wins} 胜（<b>${(wrAll * 100).toFixed(1)}%</b>）/ ${state.totals.kills} 击杀 · 已扫至 it${state.lastScannedIter}`,
  ]

  if (!dryRun) {
    const passSection: PassSection = lp
      ? { covered: lp.covered, endedAt: lp.endedAt, rows: passRows }
      : { covered: '', endedAt: '', rows: [] }
    writeFileSync(REPORT_PATH, buildHtml(rows, bannerLines, recentIters, newWins, `扫描范围 it${fromIter + 1}–it${scanUpTo}`, passSection, readAgentMeta()))
  }

  console.log('=== RL 小时巡检 ===')
  console.log(`进程: ${procStr} | ${cbStr}`)
  console.log(`日志: 最后 run_start=${lastRunStart} | 最后迭代=it${lastIter ? `${lastIter.iter}@${lastIter.time}` : '?'} | 已确认迭代 ${iterNums.length} 轮`)
  if (results.length > 0) {
    console.log(`SCAN: ${results.map((r) => `it${r.iter}=${r.games}g/${r.wins}w/${r.kills}k`).join(' ')}`)
    console.log(`CROSS-CHECK: ${crossCheckBad.length === 0 ? '全部一致 ✓' : '不一致 ⚠ ' + crossCheckBad.join('; ')}`)
  } else {
    console.log('SCAN: 无新增已确认迭代' + (missingDirs > 0 ? `（${missingDirs} 个目录缺失）` : ''))
  }
  console.log(`健康判定: ${verdict}`)
  console.log(`TOTALS: games=${state.totals.games} wins=${state.totals.wins} kills=${state.totals.kills} lastScannedIter=${state.lastScannedIter}`)
  if (state.lastPass) console.log(`PASS: 本段 ${state.lastPass.covered}（截至 ${state.lastPass.endedAt}）各关表现已写入报告`)
  if (firstEver.length > 0) console.log(`FIRST-EVER WINS: ${firstEver.join(', ')}`)
  if (newWins.length > 0) {
    console.log('--- NEW WINS ---')
    for (const w of newWins) console.log(`it${w.iter}  s${w.stage + 1} ${w.name}  seed=${w.seed}  score=${w.score.toFixed(3)}  kills=${w.kills}`)
  }
  if (dryRun) console.log('DRY-RUN: 未写回任何文件')
  else console.log(`STATE 写回完成 | HTML 报告: ${REPORT_PATH}`)
}

main()
