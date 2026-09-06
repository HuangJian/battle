/** page.ts — /pool 节点池监控页组装（pool-page.ts 的视图层移植）。

 *  组成：history.ts（节点历史）+ iters.ts（每轮指标）+ theme.ts（样式/脚本）。
 *  本文件只做渲染与编排，不读业务文件；实时 ping 与聚合在此处一次请求内完成。
 *  保持服务端渲染 + 极小原生 JS（自动刷新/排序/过滤）——不加 vite/svelte：
 *  单页只读监控，构建链只会添依赖（MANIFEST §14）而不会有体验增益。
 */

import { join } from 'path'
import { REPO_ROOT } from '../paths'
import type { NodeConf } from '../types'
import { aggregateNodeHistory, contribCell, emptyHistory, poolStatusCell } from './history'
import { readIterMetrics, type EvalSummary, type IterRow } from './iters'
import { fmtTs, pageCss, sortScript } from './theme'

/** 渲染上下文（sampler-agent 侧状态注入）。 */
export interface MonitorCtx {
  /** 采样并发槽位数。 */
  workers: number
  /** 在飞局数（结构性类型：渲染只需 size）。 */
  inflight: { size: number }
  gamesDoneTotal: number
  /** 本机当前 codeHash（节点版本比对）。 */
  localHash: () => string
  /** rl-config nodes（主控机判定 + 节点清单）；null = 非主控机。 */
  nodes: NodeConf[] | null
  /** rl.local_slots（训练机直跑槽位；缺省 null）。 */
  localSlots: number | null
}

/** eval 子行：插在对应 iter 主行下，13 列与主行对齐。 */
function renderEvalRow(iter: number, e: EvalSummary): string {
  const dash = `<td class="num"><span class="muted">-</span></td>`
  const label =
    `eval<span class="evit"> it${iter}</span>` +
    (e.dropped > 0
      ? `<span class="pill" title="评估窗口内未收官、被下轮权重分发清场的评估局数">缺${e.dropped}</span>`
      : '')
  let winCell = dash
  if (e.winRate !== null) {
    const cls = e.winRate >= 0.3 ? 'b-green' : e.winRate >= 0.1 ? 'b-yellow' : 'b-red'
    const outcomeTxt =
      Object.entries(e.outcomes)
        .map(([k, v]) => `${k}×${v}`)
        .join(' ') || '-'
    const title =
      `干净评估（greedy 固定语料）· 评估权重 = 第 ${iter} 轮 PPO 更新前（与该轮 rollout 同权重，对照上行采样胜率）· ` +
      `${e.games} 局 ${e.wins} 胜 · 全歼 ${e.clears} · outcomes: ${outcomeTxt} · 用时 ${e.sec}s · wver ${e.wver.slice(0, 12)}…`
    winCell =
      `<td><span class="badge ${cls}" title="${title}">${(e.winRate * 100).toFixed(1)}%</span>` +
      `<span class="muted"> ${e.wins}/${e.games}</span></td>`
  }
  const scoreCell =
    e.scoreMean !== null
      ? `<td class="num">${e.scoreMean.toFixed(4)}<span class="muted">±${(e.scoreStd ?? 0).toFixed(4)}</span></td>`
      : dash
  const ticksCell = e.avgTicks !== null ? `<td class="num">${e.avgTicks}</td>` : dash
  const killsCell =
    e.totalKills !== null
      ? `<td class="num">${e.totalKills}<span class="muted"> /${e.games}局</span></td>`
      : dash
  const puCell =
    e.totalPU !== null
      ? `<td class="num">${e.totalPU}<span class="muted"> /${e.games}局</span></td>`
      : dash
  return `<tr class="evalrow">
<td class="muted" style="white-space:nowrap" title="第 ${iter} 轮的干净评估（对齐上方同 iter 主行；评估的是该轮 PPO 更新前的权重，全歼 ${e.clears}/${e.games}）">${label}</td>
<td class="muted">${e.time}</td>
${winCell}
${ticksCell}
${killsCell}
${puCell}
${scoreCell}
<td class="num" title="eval 窗口用时">${e.sec.toFixed(0)}s</td>
${dash}${dash}${dash}${dash}${dash}
</tr>`
}

function renderIterTable(rows: IterRow[]): string {
  if (rows.length === 0) return ''
  const hdr = `<thead><tr>
<th>iter</th><th>时间</th><th>胜率</th><th>存活</th><th>击杀</th><th>道具</th><th>得分</th>
<th>rollout</th><th>PPO</th>
<th>KL</th><th>entropy</th><th>mean_ret</th>
<th>lr</th>
</tr></thead>`
  const cells: string[] = []
  for (const r of rows) {
    const winPct = (r.winRate * 100).toFixed(1)
    const winCls = r.winRate >= 0.3 ? 'b-green' : r.winRate >= 0.1 ? 'b-yellow' : 'b-red'
    const klCls = r.kl > 0.05 ? 'b-red' : r.kl > 0.02 ? 'b-yellow' : 'b-green'
    const retCls = r.meanRet > -0.5 ? 'b-green' : r.meanRet > -1.0 ? 'b-yellow' : 'b-red'
    const halted = r.halted ? ' <span class="pill">halted</span>' : ''
    const a = r.actuals
    const ticksCell = a
      ? `<td class="num">${a.avgTicks}</td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${r.avgTicks}≈</span></td>`
    const killsCell = a
      ? `<td class="num">${a.totalKills}<span class="muted"> /${a.games}局</span></td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${r.kills.toFixed(1)}≈</span></td>`
    const lootCell = a
      ? `<td class="num">${a.totalPU}<span class="muted"> /${a.games}局</span></td>`
      : `<td class="num"><span class="muted" title="该轮磁盘数据已清理，估算值">${(r.loot * 100).toFixed(0)}%≈</span></td>`
    cells.push(`<tr>
<td class="num">${r.iter}${halted}</td>
<td class="muted">${r.time}</td>
<td><span class="badge ${winCls}">${winPct}%</span></td>
${ticksCell}
${killsCell}
${lootCell}
<td class="num">${r.scoreMean.toFixed(4)}<span class="muted">±${r.scoreStd.toFixed(4)}</span></td>
<td class="num">${r.rolloutSec.toFixed(0)}s</td>
<td class="num">${r.ppoSec.toFixed(0)}s</td>
<td><span class="badge ${klCls}">${r.kl.toFixed(4)}</span></td>
<td class="num">${r.entropy.toFixed(3)}</td>
<td><span class="badge ${retCls}">${r.meanRet.toFixed(3)}</span></td>
<td class="num">${r.lr}</td>
</tr>`)
    if (r.evalData) cells.push(renderEvalRow(r.iter, r.evalData))
  }
  return `<div class="card" style="margin-top:16px">
<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:14px 14px 0">
<h3 style="margin:0;font-size:15px;font-weight:600;color:var(--text)">📈 每轮训练指标</h3>
<div style="display:flex;gap:12px;align-items:center;font-size:12.5px;color:var(--muted)">
<span>行过滤</span>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="all">全部</label>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="rollout">rollout only</label>
<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="radio" name="iterFilter" value="eval">eval only</label>
</div>
</div>
<div style="max-height:800px;overflow:auto;margin-top:8px">
<table id="iters">${hdr}
<tbody>${cells.join(String.fromCharCode(10))}</tbody>
</table>
</div>
<p class="foot" style="margin:8px 2px 0">存活/击杀/道具 = <b>实际值</b>（该轮 <code>it{N}/**/manifest.json</code> 逐局聚合，按 stage+seed 去重；<b>计算一次即留底</b> <code>.pool-actuals-cache.json</code>）。带 <code>≈</code> 的为估算。eval 子行 = <b>干净评估</b>（greedy 固定语料），插在对应 iter 行下、列位对齐——<b>iter=N 的 eval 评估的是第 N 轮 PPO 更新前的权重</b>；<code>缺N</code> = 窗口内未收官被清场的评估局。行过滤选择记录在 localStorage；eval only 模式下子行标签带轮号。</p>
<script>
(function () {
  const KEY = 'pool.iterFilter';
  const apply = (mode) => {
    const tbl = document.getElementById('iters');
    if (!tbl) return;
    tbl.classList.toggle('f-eval', mode === 'eval');
    for (const tr of tbl.tBodies[0].rows) {
      const isEval = tr.classList.contains('evalrow');
      tr.style.display = mode === 'all' || (mode === 'eval' ? isEval : !isEval) ? '' : 'none';
    }
  };
  let mode = 'all';
  try { mode = localStorage.getItem(KEY) || 'all'; } catch {}
  for (const el of document.querySelectorAll('input[name="iterFilter"]')) {
    el.checked = el.value === mode;
    el.addEventListener('change', function () {
      if (!this.checked) return;
      try { localStorage.setItem(KEY, this.value); } catch {}
      apply(this.value);
    });
  }
  apply(mode);
})();
</script>
</div>`
}

/** 主渲染入口：一次请求内完成 ping + 聚合 + 渲染。 */
export async function renderMonitorPage(ctx: MonitorCtx): Promise<string> {
  const agg = aggregateNodeHistory()
  const localHash = ctx.localHash()
  const nowStr = fmtTs(Date.now())
  const trajDir = agg.activeFlow ? join(REPO_ROOT, 'tmp', agg.activeFlow.dir) : null
  const { rows: iterRows } = trajDir ? readIterMetrics(trajDir) : { rows: [] as IterRow[] }
  const iterTableHtml = renderIterTable(iterRows)

  // 实时 ping（enabled 节点并行；disabled 跳过）。
  const probes = ctx.nodes
    ? await Promise.all(
        ctx.nodes.map(async (n) => {
          if (!n.enabled)
            return { n, ping: null as Record<string, unknown> | null, ms: -1, skip: true }
          const started = Date.now()
          try {
            const resp = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
              headers: { Authorization: `Bearer ${n.authKey}` },
              signal: AbortSignal.timeout(2500),
            })
            if (!resp.ok) return { n, ping: null, ms: Date.now() - started, skip: false }
            return {
              n,
              ping: (await resp.json()) as Record<string, unknown>,
              ms: Date.now() - started,
              skip: false,
            }
          } catch {
            return { n, ping: null, ms: Date.now() - started, skip: false }
          }
        }),
      )
    : []

  const rows: string[] = []
  const disabledRows: string[] = []
  for (const { n, ping, ms } of probes) {
    const h = agg.hist.get(n.id) ?? emptyHistory()
    const disabled = n.enabled === false
    let statusCell: string
    let specCell = '-'
    let verCell = '-'
    if (disabled) {
      statusCell = `<span class="badge b-gray">disabled</span>`
    } else if (ping) {
      const hashOk = String(ping.codeHash) === localHash
      verCell = `v${String(ping.codeHash ?? '?').slice(0, 7)}`
      if (!hashOk) verCell += `<span class="pill">旧</span>`
      statusCell = poolStatusCell(h)
      specCell = `${ping.cpus ?? '?'} 核`
    } else {
      statusCell = poolStatusCell(h)
      if (h.recent.length === 0) statusCell = `<span class="badge b-red">无 ping · 无历史</span>`
    }
    const errCell = h.lastError
      ? `<span class="err">${h.lastError}</span><br><span class="muted">${h.lastFailTs || ''}</span>`
      : '<span class="muted">-</span>'
    const rowHtml =
      `<tr><td class="name">${n.id}</td><td>${statusCell}</td>` +
      `<td>${specCell}</td><td class="ver">${verCell}</td>` +
      `<td class="num" data-v="${disabled || !ping ? 9999 : ms}">${!disabled && ping ? `${ms}ms` : '-'}</td>` +
      `<td class="num" data-v="${h.ok}">${h.ok}</td><td class="num" data-v="${h.fail}">${h.fail}</td>` +
      `${contribCell(h, agg.globalMaxIt)}` +
      `<td class="num" data-v="${h.avgElapsedSec ?? 9999}">${h.avgElapsedSec !== null ? `${h.avgElapsedSec}s` : '-'}</td>` +
      `<td data-v="${h.lastOkTs}">${h.lastOkTs || '-'}</td>` +
      `<td data-v="${h.lastFailTs}">${errCell}</td></tr>`
    if (disabled) disabledRows.push(rowHtml)
    else rows.push(rowHtml)
  }
  // 本机直跑（训练器 local 槽位）固定入表一行：无 ping/版本，状态按最近完成率。
  if (ctx.nodes) {
    const localH = agg.hist.get('local') ?? emptyHistory()
    const errCellL = localH.lastError
      ? `<span class="err">${localH.lastError}</span><br><span class="muted">${localH.lastFailTs || ''}</span>`
      : '<span class="muted">-</span>'
    rows.push(
      `<tr><td class="name">local<span class="dim">（本机直跑）</span></td><td>${poolStatusCell(localH)}</td>` +
        `<td>${ctx.localSlots !== null ? `${ctx.localSlots} 槽` : '-'}</td><td class="ver">-</td>` +
        `<td class="num" data-v="9999">-</td>` +
        `<td class="num" data-v="${localH.ok}">${localH.ok}</td><td class="num" data-v="${localH.fail}">${localH.fail}</td>` +
        `${contribCell(localH, agg.globalMaxIt)}` +
        `<td class="num" data-v="${localH.avgElapsedSec ?? 9999}">${localH.avgElapsedSec !== null ? `${localH.avgElapsedSec}s` : '-'}</td>` +
        `<td data-v="${localH.lastOkTs}">${localH.lastOkTs || '-'}</td>` +
        `<td data-v="${localH.lastFailTs}">${errCellL}</td></tr>`,
    )
  }
  // 默认按「已结算局」倒序（点击表头仍可手动排）。
  const bySettledDesc = (a: string, b: string): number => {
    const okA = Number((a.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    const okB = Number((b.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    return okB - okA
  }
  rows.sort(bySettledDesc)
  disabledRows.sort(bySettledDesc)

  const inflightN = ctx.inflight?.size ?? 0
  return `<!doctype html><html><head><meta charset="utf-8">
<title>节点池监控</title>
<style>${pageCss()}</style></head>
<body><div class="wrap">
<div class="pool-header"><h2><span class="dot"></span>节点池监控</h2>
<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
<span class="badge b-accent">workers ${ctx.workers} · inflight ${inflightN} · done ${ctx.gamesDoneTotal}</span>
${agg.activeFlow ? `<span class="badge b-accent">当前训练流: ${agg.activeFlow.dir}（${agg.activeFlow.lines} 条 · 更新于 ${fmtTs(agg.activeFlow.mtimeMs)}）</span>` : `<span class="badge b-gray">无活跃训练流</span>`}
<span class="ts" id="status">本次刷新 ${nowStr}</span>
<select id="interval" style="font-size:12px;padding:2px 6px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer">
<option value="60">1 分钟</option>
<option value="300" selected>5 分钟</option>
<option value="600">10 分钟</option>
<option value="1800">30 分钟</option>
</select>
<button onclick="location.reload()" style="font-size:12px;padding:2px 10px;border:1px solid var(--border);border-radius:6px;background:var(--accent-bg);color:var(--accent);cursor:pointer;font-weight:600">⟳ 立即刷新</button>
</div></div>
<div class="card"><table id="pool">
<thead><tr>
<th onclick="sortTbl(0,this)">节点</th><th onclick="sortTbl(1,this)">状态</th><th onclick="sortTbl(2,this)">规格</th>
<th onclick="sortTbl(3,this)">版本</th><th onclick="sortTbl(4,this)">ping</th><th onclick="sortTbl(5,this)">已结算局</th>
<th onclick="sortTbl(6,this)">失败局</th><th onclick="sortTbl(7,this)">上轮贡献度</th><th onclick="sortTbl(8,this)">平均耗时</th>
<th onclick="sortTbl(9,this)">最近成功</th><th onclick="sortTbl(10,this)">最近错误</th>
</tr></thead>
<tbody>${rows.join(String.fromCharCode(10))}</tbody>
${
  disabledRows.length > 0
    ? `<tbody id="disabledBody">
<tr id="disabledToggle" class="grp" onclick="toggleDisabled()" title="点击展开/折叠"><td colspan="11"><span id="disabledCaret" class="caret">▸</span>&nbsp;已禁用节点（${disabledRows.length}）</td></tr>
${disabledRows.join(String.fromCharCode(10)).replaceAll('<tr>', '<tr class="drow">')}
</tbody>`
    : ''
}
</table></div>
${iterTableHtml}
<script>${sortScript()}
// 自动刷新定时器（间隔记录到 localStorage，刷新后恢复）
let _timer = null;
const INTERVAL_KEY = 'pool.refreshSec';
function readInterval() {
  try {
    const v = parseInt(localStorage.getItem(INTERVAL_KEY), 10);
    const opts = document.querySelectorAll('#interval option');
    for (let i = 0; i < opts.length; i++) if (Number(opts[i].value) === v) return v;
  } catch {}
  return parseInt(document.getElementById('interval').value, 10);
}
function _startTimer() {
  if (_timer) clearInterval(_timer);
  const sec = readInterval();
  document.getElementById('interval').value = String(sec);
  var label = sec >= 60 ? (sec/60)+' 分钟' : sec+'s';
  document.getElementById('status').textContent = '\\u6bcf ' + label + ' \\u81ea\\u52a8\\u5237\\u65b0 \\u00b7 \\u672c\\u6b21\\u5237\\u65b0 ' + '${nowStr}';
  _timer = setInterval(function() { location.reload(); }, sec * 1000);
}
function onIntervalChange() {
  const sel = document.getElementById('interval');
  try { localStorage.setItem(INTERVAL_KEY, sel.value) } catch {}
  _startTimer();
}
document.getElementById('interval').addEventListener('change', onIntervalChange);
_startTimer();
// 折叠 disabled 节点组：用户展开/折叠状态记录到 localStorage。默认折叠。
const DISABLED_KEY = 'pool.disableCollapsed';
function isDisabledCollapsed() {
  try { return localStorage.getItem(DISABLED_KEY) !== '0' } catch { return true }
}
function setDisabledCollapsed(v) {
  try { localStorage.setItem(DISABLED_KEY, v ? '1' : '0') } catch {}
}
function applyDisabledView() {
  const toggle = document.getElementById('disabledToggle');
  if (!toggle) return;
  const collapsed = isDisabledCollapsed();
  const rows = document.querySelectorAll('#disabledBody tr.drow');
  for (let i = 0; i < rows.length; i++) rows[i].style.display = collapsed ? 'none' : '';
  const caret = document.getElementById('disabledCaret');
  if (caret) caret.textContent = collapsed ? '▸' : '▾';
}
function toggleDisabled() {
  setDisabledCollapsed(!isDisabledCollapsed());
  applyDisabledView();
}
applyDisabledView();
</script>
<p class="foot">状态 = 最近 10 次 rollout/eval 结算完成率（<b>健康</b>≥90% · <b>波动</b>≥70% · <b>异常</b>&lt;70%），替代单次 ping 判断；ping 列仅作实时参考。
上轮贡献度 = <b>全局最新轮</b>该节点的成功局数；灰色 0 = 该节点最近贡献已落后当前轮（悬停显示其最近贡献轮次）。
平均耗时 = 每局<b>端到端服务时长</b>（取<b>最近 50 局滑动平均</b>）。
最近错误仅显示最近 1 小时内。数据源：按 mtime 自动选取最新训练流的 dist-agent-meta.jsonl ·
${agg.epochMs > 0 ? `<b>历史自 ${fmtTs(agg.epochMs)} 起重新累计</b>（此后部署不再重置）` : `<b>累计全部历史</b>`}。
只读页面，不含密钥。默认按「已结算局」倒序，点击表头排序。</p>
</div></body></html>`
}
