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
  extraCols?: Array<{
    key: string
    label: string
    get: (r: ScorecardRow) => number
    digits?: number
  }>
}

export const SCORECARD_CSS = `  :root { --good:#1a7f37; --bad:#cf222e; --line:#d0d7de; --bg:#fff; --th:#f6f8fa; --ink:#1f2328; }
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
  /* multi-difficulty report (additive; unused by the single-suite page) */
  .cards { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
  .card { flex:1 1 220px; min-width:200px; border:1px solid var(--line); border-radius:10px; background:#fff; padding:10px 14px; cursor:pointer; }
  .card.on { border-color:#0969da; box-shadow:0 0 0 2px #c8e1ff inset; }
  .card .d { font-weight:700; font-size:14px; margin-bottom:6px; }
  .card .big { font-size:24px; font-weight:800; border-radius:6px; padding:2px 8px; display:inline-block; margin-bottom:8px; }
  .card .k { color:#8c959f; font-size:11px; margin-top:4px; }
  .card .v { font-size:13px; }
  .tabs { display:flex; gap:6px; margin-bottom:10px; }
  .tab { border:1px solid var(--line); background:#fff; border-radius:8px 8px 0 0; padding:6px 14px; font-size:13px; cursor:pointer; }
  .tab.on { background:#eef6ff; border-color:#0969da; font-weight:700; }
  tfoot td, tfoot th { border-top:2px solid var(--line); background:var(--th); padding:8px 10px; text-align:right; white-space:nowrap; font-size:13px; }
  tfoot th.txt { text-align:left; }
`

function esc(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!,
  )
}

function buildHtml(rows: ScorecardRow[], suite: ScorecardSuite, opts: ScorecardOptions): string {
  const dimHeaders = DIM_ORDER.map(
    (k) =>
      `      <th class="num dim" data-key="${k}" title="${esc(DIM_DESC[k])}">${esc(DIM_LABEL[k])}</th>`,
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
<style>${SCORECARD_CSS}</style>
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

// ============================================================
// Multi-difficulty report (one self-contained page, 2026-10-27)
// ============================================================

/** One difficulty's table + banner, as rendered by the single-suite page. */
export interface DifficultyScorecard {
  /** Difficulty key ('hard' | 'classic' | 'chaos' | …). */
  difficulty: string
  /** Display name ('Hard' / 'Classic' / 'Chaos'). */
  label: string
  rows: ScorecardRow[]
  suite: ScorecardSuite
}

export interface MultiScorecardOptions {
  title: string
  stages: number
  seeds: number
  maxTicks: number
  simSeconds: number
  /** Extra meta line (params hash, policy, …). */
  note?: string
  /** Extra columns after 胜率 (same contract as ScorecardOptions). */
  extraCols?: Array<{
    key: string
    label: string
    get: (r: ScorecardRow) => number
    digits?: number
  }>
}

/**
 * Render every difficulty into ONE page: a summary band (win rate / suite /
 * score / kills per difficulty) plus a tabbed set of stage tables that keep the
 * single-suite scorecard's columns, heat map and click-to-sort behaviour.
 *
 * Pure presentation, same as `writeScorecardHtml` — the caller owns the runs.
 */
export function buildMultiScorecardHtml(
  cards: DifficultyScorecard[],
  opts: MultiScorecardOptions,
): string {
  const dimHeaders = DIM_ORDER.map(
    (k) =>
      `      <th class="num dim" data-key="${k}" title="${esc(DIM_DESC[k])}">${esc(DIM_LABEL[k])}</th>`,
  ).join('\n')
  const extraDefs = (opts.extraCols ?? []).map((c) => ({
    key: c.key,
    label: c.label,
    digits: c.digits ?? 2,
  }))
  const extraHeaders = extraDefs
    .map((c) => `      <th data-key="${c.key}">${esc(c.label)}</th>`)
    .join('\n')

  const data = cards.map((c) => ({
    difficulty: c.difficulty,
    label: c.label,
    rows: c.rows.map((r) => ({ ...r, winRatePct: Math.round(r.winRate * 1000) / 10 })),
    summary: {
      suite: c.suite.suite,
      lcb: c.suite.lcb,
      meanWinRate: c.suite.meanWinRate,
      arithmeticMean: c.suite.arithmeticMean,
      worst: c.suite.worstStage,
    },
  }))

  const meta =
    `配置：<b>${opts.stages}</b> 关 × <b>${opts.seeds}</b> seeds = ` +
    `<b>${opts.stages * opts.seeds}</b> 局/难度 × <b>${cards.length}</b> 难度 ` +
    `· maxTicks = ${opts.maxTicks} · 评分 = <b>v7 宽带</b>（clear 0.70–1.0 / loss 0–0.40）` +
    `· 真实 stageIndex + CRN 配对` +
    (opts.note ? ` · ${esc(opts.note)}` : '') +
    `<br>生成时间：${new Date().toISOString()} · 仿真耗时：${opts.simSeconds.toFixed(1)}s ` +
    `· 点击难度标签切换，点击任意表头排序（再次点击切换升/降序）。`

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(opts.title)}</title>
<style>${SCORECARD_CSS}</style>
</head>
<body>
<h1>${esc(opts.title)}</h1>
<div class="meta">${meta}</div>
<div id="cards" class="cards"></div>
<div id="tabs" class="tabs"></div>
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
  <tfoot id="tf"></tfoot>
</table>
</div>
<footer>
  维度均为该关所有 seed 的归一化均值（∈[0,1]，n/a 时该维度不参与加权，列显示 <span class="na">—</span>）。<br>
  score = 风险调整关卡分 = (1−0.35)·mean + 0.35·seedCVaR(最差25%)；color：绿=高(好) / 红=低(差)。<br>
  suite = 跨关 power mean 与最弱 25% 关的混合（对数艺术分），LCB = 下置信界；胜率按 <code>stage_clear</code> 计。
</footer>
<script>
const DATA = ${JSON.stringify(data)};
const DIM_KEYS = ${JSON.stringify(DIM_ORDER)};
const EXTRA = ${JSON.stringify(extraDefs)};
const tbody = document.querySelector('#t tbody');
const tf = document.querySelector('#tf');
const cardsEl = document.querySelector('#cards');
const tabsEl = document.querySelector('#tabs');
let active = DATA[0] ? DATA[0].difficulty : '';
let sortKey = 'score', sortDir = -1;

function fmt(x, d){ return x==null ? '—' : Number(x).toFixed(d); }
function heat(v){
  if(v==null) return '';
  var t = Math.max(0, Math.min(1, v));
  var hue = Math.round(t*125);
  return 'background:hsl(' + hue + ' 70% 88%); color:#1f2328;';
}
function cur(){ return DATA.find(function(d){ return d.difficulty===active; }) || DATA[0]; }

function renderCards(){
  cardsEl.innerHTML = DATA.map(function(d){
    var s = d.summary;
    var worst = s.worst ? ('<div class="k">最弱关</div><div class="v">'+s.worst.name+' · '+Math.round(s.worst.winRate*100)+'%</div>') : '';
    return '<div class="card'+(d.difficulty===active?' on':'')+'" data-d="'+d.difficulty+'">'+
      '<div class="d">'+d.label+'</div>'+
      '<div class="big" style="'+heat(s.meanWinRate)+'">'+(100*s.meanWinRate).toFixed(1)+'%</div>'+
      '<div class="k">套件 suite</div><div class="v">'+s.suite.toFixed(4)+' · LCB '+s.lcb.toFixed(4)+'</div>'+
      '<div class="k">关卡均分</div><div class="v">'+s.arithmeticMean.toFixed(4)+'</div>'+
      worst+
    '</div>';
  }).join('');
  cardsEl.querySelectorAll('.card').forEach(function(el){
    el.addEventListener('click', function(){ active = el.dataset.d; renderCards(); renderTabs(); render(); });
  });
}
function renderTabs(){
  tabsEl.innerHTML = DATA.map(function(d){
    return '<button class="tab'+(d.difficulty===active?' on':'')+'" data-d="'+d.difficulty+'">'+d.label+'</button>';
  }).join('');
  tabsEl.querySelectorAll('.tab').forEach(function(el){
    el.addEventListener('click', function(){ active = el.dataset.d; renderCards(); renderTabs(); render(); });
  });
}
function render(){
  var d = cur(); if(!d) return;
  var rows = d.rows.slice().sort(function(a,b){
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
  var s = d.summary;
  var heads = ['<th class="txt" colspan="2">'+d.label+' 合计</th>',
    '<th>'+s.suite.toFixed(4)+'</th>',
    '<th>'+(s.arithmeticMean).toFixed(4)+'</th>',
    '<th></th><th></th>',
    '<th>'+Math.round(s.meanWinRate*100)+'%</th>'].join('');
  var extraTf = EXTRA.map(function(){ return '<td></td>'; }).join('');
  var dimTf = DIM_KEYS.map(function(){ return '<td></td>'; }).join('');
  tf.innerHTML = '<tr>'+heads+extraTf+dimTf+'</tr>';
  document.querySelectorAll('#t thead th').forEach(function(th){
    var base = th.dataset.key;
    var old = th.querySelector('.arrow'); if(old) th.removeChild(old);
    if(base===sortKey){ var el=document.createElement('span'); el.className='arrow'; el.textContent = sortDir<0?'▼':'▲'; th.appendChild(el); }
  });
}
document.querySelectorAll('#t thead th').forEach(function(th){
  th.addEventListener('click', function(){
    var k = th.dataset.key;
    if(k===sortKey){ sortDir = sortDir*-1; } else { sortKey=k; sortDir = (k==='name'||k==='idx')?1:-1; }
    render();
  });
});
renderCards(); renderTabs(); render();
</script>
</body>
</html>`
}

/** Render every difficulty into one page and write it. Returns the path. */
export function writeMultiScorecardHtml(
  outPath: string,
  cards: DifficultyScorecard[],
  opts: MultiScorecardOptions,
): string {
  writeFileSync(outPath, buildMultiScorecardHtml(cards, opts))
  return outPath
}
