/**
 * scorecard-html.ts — Render a sortable, heat-mapped HTML scorecard table
 * (one row per stage) in the exact style of
 * tmp/god-ai-hard-35stage-scorecard.html.
 *
 * Pure presentation: takes already-aggregated rows + a suite summary and
 * returns an HTML string. No simulation, no scoring — those live in the
 * caller (m1-eval.ts or gen-stage-html.ts).
 *
 * The 11 score-v7 dimensions, column order, CSS palette and click-to-sort
 * behaviour all mirror the canonical GOD AI scorecard so the two reports are
 * visually interchangeable.
 */

import { writeFileSync } from 'node:fs'
import type { DimensionKey } from '../eval/godai-score'

export const DIM_ORDER: DimensionKey[] = [
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

export const DIM_LABEL: Record<DimensionKey, string> = {
  progress: '进度 π',
  lives: '残命 λ',
  baseIntegrity: '基地完整 β',
  clearSpeed: '通关速度 σ',
  tempo: '节奏 τ',
  accuracy: '命中率 ε',
  loot: '道具率 ρ',
  growth: '火力成长 γ',
  baseSafety: '基地安全 θ',
  openingTempo: '开局节奏 ω',
  mobility: '机动性 μ',
}

export const DIM_DESC: Record<DimensionKey, string> = {
  progress: 'kills / enemies',
  lives: 'lives remaining / start lives',
  baseIntegrity: 'base alive + protection-ring survival',
  clearSpeed: 'clear speed (clears only)',
  tempo: 'kills per minute vs stage reference',
  accuracy: 'kills per shot vs stage reference',
  loot: 'power-ups captured / power-ups offered',
  growth: 'final star level / max star level',
  baseSafety: '1 − mean base pressure',
  openingTempo: 'how quickly the first kill landed',
  mobility: 'distinct cells visited (anti-oscillation)',
}

/** One stage row. `dims` holds the per-dimension mean (∈[0,1]) across seeds. */
export interface ScorecardRow {
  idx: number
  name: string
  score: number
  mean: number
  cvar: number
  se: number
  winRate: number
  avgKills: number
  dims: Record<string, number>
}

/** Top-line suite summary shown in the blue banner. */
export interface ScorecardSuite {
  suite: number
  lcb: number
  arithmeticMean: number
  meanWinRate: number
  worstStage: { name: string; winRate: number } | null
}

export interface ScorecardOptions {
  title: string
  difficulty: string
  stages: number
  seeds: number
  maxTicks: number
  simSeconds: number
  /** Extra meta line, e.g. policy=nn · 引用说明. */
  note?: string
  /** Optional extra columns after 胜率. Each entry renders a numeric cell. */
  extraCols?: Array<{ key: string; label: string; get: (r: ScorecardRow) => number; digits?: number }>
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

function buildHtml(rows: ScorecardRow[], suite: ScorecardSuite, opts: ScorecardOptions): string {
  const dimHeaders = DIM_ORDER.map(
    (k) => `      <th class="num dim" data-key="${k}" title="${esc(DIM_DESC[k])}">${esc(DIM_LABEL[k])}</th>`,
  ).join('\n')
  const extraHeaders = (opts.extraCols ?? [])
    .map((c) => `      <th data-key="${c.key}">${esc(c.label)}</th>`)
    .join('\n')

  const data = rows.map((r) => ({
    ...r,
    winRatePct: Math.round(r.winRate * 1000) / 10,
    dims: r.dims,
  }))
  const dataJson = JSON.stringify(data)

  const suiteLine =
    `套件分 suite = <b>${suite.suite.toFixed(4)}</b> &nbsp;|&nbsp; LCB = ${suite.lcb.toFixed(4)} ` +
    `&nbsp;|&nbsp; 均值胜率 = <b>${(suite.meanWinRate * 100).toFixed(1)}%</b> ` +
    `&nbsp;|&nbsp; 算术均 = ${suite.arithmeticMean.toFixed(4)} ` +
    (suite.worstStage
      ? `&nbsp;|&nbsp; 最弱关: ${esc(suite.worstStage.name)} (${(suite.worstStage.winRate * 100).toFixed(0)}%)`
      : '')

  const meta =
    `配置：<b>${opts.stages}</b> 关 × <b>${opts.seeds}</b> seeds = <b>${opts.stages * opts.seeds}</b> 局 ` +
    `· maxTicks = ${opts.maxTicks} · 评分 = <b>v7 宽带</b>（clear 0.70–1.0 / loss 0–0.40）` +
    (opts.note ? ` · ${esc(opts.note)}` : '') +
    `<br>生成时间：${new Date().toISOString()} · 仿真耗时：${opts.simSeconds.toFixed(1)}s ` +
    `· 点击任意表头排序（再次点击切换升/降序）。`

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(opts.title)}</title>
<style>
  :root { --good:#1a7f37; --bad:#cf222e; --line:#d0d7de; --bg:#fff; --th:#f6f8fa; --ink:#1f2328; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin:0; padding:24px; background:#fafbfc; color:var(--ink); }
  h1 { font-size:20px; margin:0 0 4px; }
  .meta { color:#57606a; font-size:13px; margin-bottom:14px; line-height:1.6; }
  .suite { background:#eef6ff; border:1px solid #c8e1ff; border-radius:8px; padding:10px 14px; font-size:13px; margin-bottom:16px; }
  .wrap { overflow:auto; border:1px solid var(--line); border-radius:8px; background:var(--bg); }
  table { border-collapse:collapse; font-size:13px; width:100%; }
  thead th { position:sticky; top:0; background:var(--th); border-bottom:2px solid var(--line); padding:8px 10px; text-align:right; white-space:nowrap; cursor:pointer; user-select:none; z-index:1; }
  thead th.txt { text-align:left; }
  thead th:hover { background:#eaeef2; }
  thead th .arrow { color:#0969da; font-size:11px; margin-left:3px; }
  tbody td { padding:6px 10px; text-align:right; border-bottom:1px solid #eaecef; white-space:nowrap; }
  tbody td.txt { text-align:left; }
  tbody tr:nth-child(even) { background:#f9fafb; }
  tbody tr:hover { background:#fff8e1; }
  td.dim { color:#57606a; }
  .score-cell { font-weight:700; border-radius:4px; }
  .win-cell { font-weight:600; }
  .na { color:#bbb; }
  footer { margin-top:14px; color:#8c959f; font-size:12px; line-height:1.6; }
  code { background:#eef1f4; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<h1>${esc(opts.title)}</h1>
<div class="meta">${meta}</div>
<div class="suite">${suiteLine}</div>
<div class="wrap">
<table id="t">
  <thead><tr>
    <th class="txt" data-key="idx">#</th>
    <th class="txt" data-key="name">关卡</th>
    <th data-key="score">评分 score</th>
    <th data-key="mean">均值 mean</th>
    <th data-key="cvar">CVaR</th>
    <th data-key="se">±SE</th>
    <th data-key="winRate">胜率</th>
${extraHeaders}
${dimHeaders}
  </tr></thead>
  <tbody></tbody>
</table>
</div>
<footer>
  维度均为该关所有 seed 的归一化均值（∈[0,1]，n/a 时该维度不参与加权，列显示 <span class="na">—</span>）。<br>
  score = 风险调整关卡分 = (1−0.35)·mean + 0.35·seedCVaR(最差25%)；color：绿=高(好) / 红=低(差)。
</footer>
<script>
const DATA = ${dataJson};
const DIM_KEYS = ${JSON.stringify(DIM_ORDER)};
const EXTRA = ${JSON.stringify((opts.extraCols ?? []).map((c) => ({ key: c.key, digits: c.digits ?? 2 })))};
const tbody = document.querySelector('#t tbody');
let sortKey = 'score', sortDir = -1;

function fmt(x, d){ return x==null ? '—' : Number(x).toFixed(d); }
function heat(v){
  if(v==null) return '';
  var t = Math.max(0, Math.min(1, v));
  var hue = Math.round(t*125);
  return 'background:hsl(' + hue + ' 70% 88%); color:#1f2328;';
}

function render(){
  var rows = DATA.slice().sort(function(a,b){
    var av=a[sortKey], bv=b[sortKey];
    if(sortKey==='name' || sortKey==='idx'){ return sortDir*String(av).localeCompare(String(bv), undefined, {numeric:true}); }
    if(av==null) av = -Infinity;
    if(bv==null) bv = -Infinity;
    return sortDir*(av-bv);
  });
  var html = rows.map(function(r){
    var dimTds = DIM_KEYS.map(function(k){
      var val = r.dims[k];
      return '<td class="dim" title="'+k+'">'+(val==null?'<span class="na">—</span>':fmt(val,3))+'</td>';
    }).join('');
    var extraTds = EXTRA.map(function(c){
      return '<td>'+fmt(r[c.key], c.digits)+'</td>';
    }).join('');
    return '<tr>'+
      '<td class="txt">'+r.idx+'</td>'+
      '<td class="txt">'+r.name+'</td>'+
      '<td class="score-cell" style="'+heat(r.score)+'">'+fmt(r.score,3)+'</td>'+
      '<td>'+fmt(r.mean,3)+'</td>'+
      '<td>'+fmt(r.cvar,3)+'</td>'+
      '<td>'+fmt(r.se,3)+'</td>'+
      '<td class="win-cell" style="'+heat(r.winRate)+'">'+Math.round(r.winRate*100)+'%</td>'+
      extraTds+
      dimTds+
    '</tr>';
  }).join('');
  tbody.innerHTML = html;
  document.querySelectorAll('#t thead th').forEach(function(th){
    var base = th.dataset.key;
    var old = th.querySelector('.arrow'); if(old) th.removeChild(old);
    if(base===sortKey){ var s=document.createElement('span'); s.className='arrow'; s.textContent = sortDir<0?'▼':'▲'; th.appendChild(s); }
  });
}
document.querySelectorAll('#t thead th').forEach(function(th){
  th.addEventListener('click', function(){
    var k = th.dataset.key;
    if(k===sortKey){ sortDir = sortDir*-1; } else { sortKey=k; sortDir = (k==='name'||k==='idx')?1:-1; }
    render();
  });
});
render();
</script>
</body>
</html>`
}

/** Render the scorecard and write it to `outPath`. Returns the path written. */
export function writeScorecardHtml(
  outPath: string,
  rows: ScorecardRow[],
  suite: ScorecardSuite,
  opts: ScorecardOptions,
): string {
  writeFileSync(outPath, buildHtml(rows, suite, opts))
  return outPath
}
