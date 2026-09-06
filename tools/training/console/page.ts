/** page.ts — 神经网络训练控制台页（服务端渲染 + 极小原生 JS，§346 技术选型延续）。
 *
 *  数据源：api.buildStateView() 快照（GET /api/state 轮询 3s）；动作按钮 POST /api/*
 *  后立即拉一次快照刷新。只读区块（组件表/节点表/模式/指标）与动作区块（启/停/
 *  冒烟/预设/编辑）同页分区；无 vite/svelte——单页控制台，构建链只添依赖。
 */

import type { ConsoleStateView, ComponentView, NodeView } from './api'
import { pageCss } from '../monitor/theme'

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// ────────────────────────── 组件表 ──────────────────────────

function statusBadge(c: ComponentView): string {
  if (c.status === 'running')
    return c.healthy === false
      ? `<span class="pill pill-y">运行中·未就绪</span>`
      : `<span class="pill pill-g">运行中${c.healthy ? '·就绪' : ''}</span>`
  if (c.status === 'exited') return `<span class="pill pill-r">已退出</span>`
  return `<span class="pill pill-gray">未启动</span>`
}

function componentRow(c: ComponentView): string {
  const urlCell = c.url
    ? `<span class="mono">${esc(c.url)}</span>`
    : c.key === 'selfNode'
      ? '<span class="mono muted">:8443</span>'
      : '<span class="muted">—</span>'
  const meta = [
    c.pid ? `PID ${c.pid}` : null,
    c.course ? `course=${esc(c.course)}` : null,
    c.mode ? `mode=${esc(c.mode)}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  const tail = c.logTail.length
    ? `<details><summary class="muted">日志尾行 (${c.logTail.length})</summary><pre class="logtail">${esc(c.logTail.join('\n'))}</pre></details>`
    : ''
  const dis = c.busy ? ' disabled' : ''
  return `<tr>
  <td><b>${esc(c.label)}</b><div class="muted small">${meta || '&nbsp;'}</div>${tail}</td>
  <td>${statusBadge(c)}</td>
  <td>${urlCell}</td>
  <td class="acts">
    <button data-act="start" data-component="${c.key}"${dis}>启动</button>
    <button data-act="stop" data-component="${c.key}"${dis}>停止</button>
    <button data-act="smoke" data-component="${c.key}"${dis}>冒烟</button>
  </td>
</tr>`
}

function componentsTable(s: ConsoleStateView): string {
  return `<div class="card"><table>
<thead><tr><th>组件</th><th>状态</th><th>端点</th><th>操作</th></tr></thead>
<tbody>${s.components.map(componentRow).join('')}</tbody>
</table></div>`
}

// ────────────────────────── 模式区块 ──────────────────────────

function modesSection(s: ConsoleStateView): string {
  const m = s.modes
  const btn = (mode: string, label: string, hint: string): string =>
    `<button class="preset${m.trainerPpo === mode ? ' on' : ''}" data-act="preset" data-mode="${mode}"
      title="${esc(hint)}">${label}</button>`
  const toggle = (key: string, val: number, label: string, hint: string): string =>
    `<label class="toggle" title="${esc(hint)}">
      <input type="checkbox" data-mode-key="${key}"${val ? ' checked' : ''}/>
      <span>${label}</span>
      <code class="small muted">${key}</code>
    </label>`
  return `<div class="card pad">
  <h3>运行模式</h3>
  <div class="row">
    <div class="col">
      <h4>trainer 基建编排（启动预设）</h4>
      <div class="btnrow">
        ${btn('pull', 'Pull（Kaggle 拉取）', 'self-node+hub-server+隧道+trainer（--ppo remote），Kaggle 入站拉取')}
        ${btn('push', 'Push（推送到 GPU）', 'self-node+hub-server+trainer（--ppo remote），GPU 在 Kaggle 侧推送')}
        ${btn('local', 'Local（本机 PPO）', '仅 trainer，本机 CPU PPO；需 rl.stream=0')}
      </div>
      <p class="muted small">预设=按顺序拉起组件组合；也可在组件表单独启/停。变更即时持久化（console-state）。</p>
    </div>
    <div class="col">
      <h4>trainer 行为开关（回写 rl-config.json）</h4>
      <div class="btnrow colbtn">
        ${toggle('rl.stream', m.stream, 'stream 流式派发', 'run_rl 的 --stream；远程模式内部强制 0，本地 PPO 互斥')}
        ${toggle('rl.double_buffer', m.doubleBuffer, '双缓冲预采', '迭代收尾提前采集下一轮（θ_N 快照）')}
        ${toggle('rl.precollect_early', m.precollectEarly, '预采提前 spawn', 'precollect_early=1 时提前一轮 spawn')}
      </div>
      <p class="muted small">开关改的是 rl-config.json 的真实键——下次 trainer 启动生效，不影响在跑进程。</p>
    </div>
  </div>
</div>`
}

// ────────────────────────── 节点表 ──────────────────────────

function nodeRow(n: NodeView): string {
  const dis = n.busy ? ' disabled' : ''
  const state =
    n.online === null
      ? `<span class="pill pill-gray">停用</span>`
      : n.online
        ? `<span class="pill pill-g">在线</span>`
        : `<span class="pill pill-r">离线</span>`
  const kind = n.gpuPush ? '<span class="pill pill-a">GPU push</span>' : ''
  return `<tr>
  <td><b>${esc(n.id)}</b> ${kind}<div class="mono muted small">${esc(n.url)}</div></td>
  <td>${state}${n.cpus ? `<div class="muted small">cpus ${n.cpus}</div>` : ''}</td>
  <td>${n.codeHash ? `<span class="mono small">${esc(n.codeHash)}…</span>` : '<span class="muted">—</span>'}</td>
  <td>
    <label class="toggle inline">
      <input type="checkbox" data-node-enable="${esc(n.id)}"${n.enabled ? ' checked' : ''}/>
      <span>${n.enabled ? '启用' : '停用'}</span>
    </label>
  </td>
  <td>
    <input class="conc" type="number" min="1" max="64" value="${n.concurrency}" data-node-conc="${esc(n.id)}"/>
    <button class="small" data-act="nodeConc" data-node="${esc(n.id)}"${dis}>保存</button>
  </td>
  <td><button class="small" data-act="nodeSmoke" data-node="${esc(n.id)}"${dis || !n.enabled ? ' disabled' : ''}>冒烟</button></td>
</tr>`
}

function nodesTable(s: ConsoleStateView): string {
  return `<div class="card"><table>
<thead><tr><th>节点</th><th>状态</th><th>codeHash</th><th>启用</th><th>并行采集数</th><th>操作</th></tr></thead>
<tbody>${s.nodes.map(nodeRow).join('')}</tbody>
</table>
<div class="pad muted small">启用/停用与并行数直接回写 nn-training/rl-config.json；「冒烟」对该节点跑一局真 rollout（weights → task → result）。</div>
</div>`
}

// ────────────────────────── 指标区块 ──────────────────────────

function fmtPct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—'
}

function metricsSection(s: ConsoleStateView): string {
  if (!s.metrics.available)
    return `<div class="card pad"><h3>训练指标${s.course ? ` — ${esc(s.course)}` : ''}</h3>
<p class="muted">该课程暂无 training_log.jsonl 数据${s.metrics.error ? `（${esc(s.metrics.error)}）` : ''}。</p></div>`
  const rows = s.metrics.iters
    .map(
      (r) => `<tr>
  <td><b>${r.iter}</b></td>
  <td class="mono small">${esc(r.time)}</td>
  <td>${fmtPct(r.winRate)}</td>
  <td>${r.scoreMean.toFixed(2)} ± ${r.scoreStd.toFixed(2)}</td>
  <td>${r.samples}</td>
  <td>${r.kl.toFixed(4)}</td>
  <td>${r.entropy.toFixed(3)}</td>
  <td>${r.rolloutSec.toFixed(0)}s / ${r.ppoSec.toFixed(0)}s</td>
  <td>${r.evalData ? fmtPct(r.evalData.winRate) : '<span class="muted">—</span>'}</td>
  <td>${r.actuals ? `${r.actuals.games}局·均${r.actuals.avgTicks}t` : '<span class="muted">—</span>'}</td>
  ${r.halted ? '<td><span class="pill pill-r">熔断</span></td>' : '<td></td>'}
</tr>`,
    )
    .join('')
  return `<div class="card"><table>
<thead><tr><th>轮</th><th>时刻</th><th>胜率</th><th>得分</th><th>样本</th><th>KL</th><th>熵</th>
<th>采集/PPO</th><th>eval 胜率</th><th>实际终局</th><th></th></tr></thead>
<tbody>${rows || '<tr><td colspan="11" class="muted">尚无完整迭代记录</td></tr>'}</tbody>
</table></div>`
} // ────────────────────────── 页面组装 ──────────────────────────

function clientScript(): string {
  return `
const EDITING = () => {
  const a = document.activeElement
  if (a && (a.tagName === 'INPUT' || a.tagName === 'SELECT' || a.tagName === 'TEXTAREA')) return true
  if (document.querySelector('details[open]')) return true
  return false
}
function showFlash() {
  try {
    const f = JSON.parse(sessionStorage.getItem('consoleFlash') || 'null')
    if (!f) return
    sessionStorage.removeItem('consoleFlash')
    const el = document.getElementById('flash')
    el.textContent = f.msg
    el.className = 'flash ' + (f.ok ? 'flash-ok' : 'flash-bad')
    el.style.display = 'block'
    setTimeout(() => { el.style.display = 'none' }, 8000)
  } catch {}
}
async function post(body) {
  document.body.style.cursor = 'wait'
  try {
    const r = await fetch('/api/' + body.act, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    let data = {}
    try { data = await r.json() } catch {}
    if (!r.ok && !data.message) data.message = 'HTTP ' + r.status
    if (data.detail && data.detail.length) data.message += '\\n' + data.detail.join('\\n')
    sessionStorage.setItem('consoleFlash', JSON.stringify({ msg: (data.ok ? '\\u2705 ' : '\\u274c ') + (data.message || '完成'), ok: !!data.ok }))
  } catch (e) {
    sessionStorage.setItem('consoleFlash', JSON.stringify({ msg: '\\u274c ' + e, ok: false }))
  } finally {
    document.body.style.cursor = ''
  }
  location.reload()
}
document.addEventListener('click', (ev) => {
  const b = ev.target.closest('button[data-act]')
  if (!b || b.disabled) return
  const act = b.dataset.act
  if (act === 'start' || act === 'stop' || act === 'smoke') {
    post({ act, component: b.dataset.component })
  } else if (act === 'stopAll') {
    if (confirm('停止全部受管进程？')) post({ act })
  } else if (act === 'preset') {
    post({ act, mode: b.dataset.mode })
  } else if (act === 'nodeConc') {
    const id = b.dataset.node
    const v = document.querySelector('[data-node-conc="' + id + '"]').value
    post({ act: 'setNodeConcurrency', id, concurrency: Number(v) })
  } else if (act === 'nodeSmoke') {
    post({ act: 'nodeSmoke', id: b.dataset.node })
  }
})
document.addEventListener('change', (ev) => {
  const t = ev.target
  if (t.matches('[data-mode-key]')) {
    post({ act: 'setMode', key: t.dataset.modeKey, value: t.checked ? '1' : '0' })
  } else if (t.matches('[data-node-enable]')) {
    post({ act: 'setNodeEnabled', id: t.dataset.nodeEnable, enabled: t.checked })
  } else if (t.matches('#courseSel')) {
    post({ act: 'setCourse', course: t.value })
  }
})
showFlash()
setInterval(() => { if (!EDITING()) location.reload() }, 3000)
`
}

/** 页面外壳（样式/头部/flash 容器 + clientScript）：initial 仅取课程下拉与时刻。
 *  自动刷新 3s（整页 reload，服务端重渲染）；输入焦点/展开详情时暂停，防止冲掉
 *  用户正在编辑的并发数/开关状态。 */
function renderShell(s: ConsoleStateView, bodyHtml: string): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>NN 训练控制台</title>
<style>${pageCss()}
.flash{position:fixed;top:14px;right:14px;max-width:420px;padding:10px 14px;border-radius:10px;
font-size:13px;white-space:pre-wrap;display:none;z-index:9;box-shadow:0 4px 16px rgba(16,24,40,.18)}
.flash-ok{background:var(--green-bg);color:var(--green);border:1px solid var(--green)}
.flash-bad{background:var(--red-bg);color:var(--red);border:1px solid var(--red)}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600}
.pill-g{background:var(--green-bg);color:var(--green)} .pill-y{background:var(--yellow-bg);color:var(--yellow)}
.pill-r{background:var(--red-bg);color:var(--red)} .pill-gray{background:var(--gray-bg);color:var(--gray)}
.pill-a{background:var(--accent-bg);color:var(--accent)}
.acts button{margin-right:4px} .btnrow{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.preset{padding:7px 14px;border:1.5px solid var(--border);border-radius:9px;background:var(--card);cursor:pointer}
.preset.on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);font-weight:700}
.row{display:flex;gap:24px;flex-wrap:wrap} .col{flex:1;min-width:320px}
.toggle{display:inline-flex;align-items:center;gap:7px;margin:4px 10px 4px 0;cursor:pointer}
.toggle.inline{margin:0}
.logtail{max-height:150px;overflow:auto;font-size:11.5px;background:#f7f8fa;padding:6px 9px;border-radius:8px;margin-top:5px}
.mono{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace}
.muted{color:var(--muted)} .small{font-size:12px} .pad{padding:13px 16px}
.conc{width:64px;padding:4px 7px;border:1px solid var(--border);border-radius:7px}
button{padding:5px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);cursor:pointer}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.45;cursor:not-allowed}
button.small{padding:3px 9px;font-size:12px}
h3{margin:0 0 8px} h4{margin:4px 0 2px;font-size:13px;color:var(--muted)}
select{padding:5px 9px;border:1px solid var(--border);border-radius:8px;background:var(--card)}
.card+.card,.card+.cardpad{margin-top:16px} section{margin-top:18px}
.stop-all{border-color:var(--red);color:var(--red)}
</style>
</head>
<body>
<div class="wrap">
  <div class="pool-header">
    <h2><span class="dot"></span>NN 训练控制台</h2>
    <div>
      <select id="courseSel">${s.courses.map((c) => `<option value="${esc(c)}"${c === s.course ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>
      <button data-act="stopAll" class="stop-all">停止全部</button>
      <span class="ts">快照 ${esc(s.time.slice(0, 19).replace('T', ' '))}</span>
    </div>
  </div>
  <div id="flash" class="flash"></div>
  ${bodyHtml}
</div>
<script>${clientScript()}</script>
</body>
</html>`
}

/** 服务端渲染整页（首屏 + 轮询重渲染共用：api.buildStateView → 本函数）。 */
export function renderConsolePage(s: ConsoleStateView): string {
  const body = `
<section>${componentsTable(s)}</section>
<section>${modesSection(s)}</section>
<section>${nodesTable(s)}</section>
<section><h3 style="margin:0 0 8px">训练指标${s.course ? ` — ${esc(s.course)}` : ''}</h3>${metricsSection(s)}</section>`
  return renderShell(s, body)
}
