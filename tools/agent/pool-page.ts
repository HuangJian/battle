/**
 * pool-page.ts — /pool 节点池监控页渲染（独立文件：2026-08-31 从 sampler-agent.ts 拆出）。
 *
 * 为什么独立：节点池监控的调整不应要求所有远程节点更新重启——sampler-agent.ts 在
 * dist codeHash 集内（dist_common.py + sampler-agent.ts 双语清单：src/nn/** +
 * sampler-agent.ts + 三个 rollout/eval 导出器），改它 = 全节点升级波。本文件**不在
 * codeHash 集**：只动它 → 零升级（节点 agent 升级时 git pull 全仓库会带上它；
 * 特性开关集内文件 import 本文件时旧节点因旧版 sampler-agent 不引用、无裂解）。
 *
 * 调用方：sampler-agent.ts 的 GET /pool handler（仅主控机：rl-config.json 有 nodes）。
 * 本文件只读：聚合 tmp 下各训练流目录的 dist-agent-meta.jsonl + 实时 ping，不渲染任何密钥。
 */

import { readFileSync, readdirSync, existsSync } from 'fs'
import { join, resolve } from 'path'

/** 仓库根（本文件在 tools/agent/ 下）。 */
const REPO_ROOT = resolve(import.meta.dir ?? process.cwd(), '..', '..')

// ---------------- 历史锚点（一次性，受控清空） ----------------

/** 锚点文件：tmp/dist-agent/pool-epoch.txt（毫秒时间戳）。语义（用户 2026-08-31）：
 *  · 本次修正部署写入一次 → 历史自此刻起重新累计；
 *  · 此后任何部署/重启**只读**同一锚点 → 历史持续累计，不再按部署重置；
 *  · 用户明确要求清空时，重写/删除该文件再重启 agent = 新锚点。 */
const EPOCH_FILE = join(REPO_ROOT, 'tmp', 'dist-agent', 'pool-epoch.txt')

function poolEpochMs(): number {
  try {
    if (!existsSync(EPOCH_FILE)) return 0
    const v = parseInt(readFileSync(EPOCH_FILE, 'utf8').trim(), 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  } catch {
    return 0
  }
}

/** 0 = 锚点未建立（累计全部历史）；>0 = 只统计该时刻之后的行。 */
const POOL_EPOCH_MS = poolEpochMs()

// ---------------- 配置 ----------------

interface PoolNodeCfg {
  id: string
  url: string
  authKey: string
  /** rl-config 的 enabled 字段；disabled 节点不 ping、状态列显示 disabled、历史仍展示。 */
  enabled: boolean
}

function loadPoolConfig(): PoolNodeCfg[] | null {
  try {
    const cfgPath = join(REPO_ROOT, 'nn-training', 'rl-config.json')
    if (!existsSync(cfgPath)) return null
    const cfg = JSON.parse(readFileSync(cfgPath, 'utf8')) as {
      nodes?: Array<{ id?: string; url: string; authKey?: string; enabled?: boolean }>
    }
    const nodes = (cfg.nodes ?? [])
      .filter((n) => n && typeof n.url === 'string')
      .map((n) => ({
        id: n.id || n.url,
        url: n.url,
        authKey: n.authKey ?? '',
        enabled: n.enabled !== false,
      }))
    return nodes.length > 0 ? nodes : null
  } catch {
    return null
  }
}

/** 主控机判定（= handler 的 404 门槛）：rl-config 有 nodes 才有 pool 页。 */
export function poolNodes(): PoolNodeCfg[] | null {
  return loadPoolConfig()
}

/** 训练机直跑槽位数（rl-config 的 rl.local_slots；缺省 null = 未配置/不可读）。 */
function poolLocalSlots(): number | null {
  try {
    const cfgPath = join(REPO_ROOT, 'nn-training', 'rl-config.json')
    if (!existsSync(cfgPath)) return null
    const cfg = JSON.parse(readFileSync(cfgPath, 'utf8')) as { rl?: { local_slots?: number } }
    const v = cfg.rl?.local_slots
    return typeof v === 'number' && Number.isInteger(v) && v > 0 ? v : null
  } catch {
    return null
  }
}

// ---------------- 时间格式化 ----------------

function fmtTs(ms: number): string {
  const d = new Date(ms)
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** dist-agent-meta 的 ts 分布带 T（ISO）与空格两种写法；统一为空格格式后再比（字典序可比）。 */
function normTs(s: string | undefined): string {
  return (s ?? '').replace('T', ' ')
}

// ---------------- 历史聚合 ----------------

interface NodeHistory {
  ok: number
  fail: number
  lastTs: string
  lastOkTs: string
  lastFailTs: string
  lastError: string
  avgElapsedSec: number | null
  elapsedSamples: number
  /** 最近至多 10 条结算结果（ok=true），完成率 = 在线状态的判定依据（替代单次 ping）。 */
  recent: boolean[]
  /** 上轮贡献度：最大 it（各训练流取全局最大）那轮的成功局数。 */
  lastIter: number
  lastIterOk: number
}

function aggregateNodeHistory(): Map<string, NodeHistory> {
  const hist = new Map<string, NodeHistory>()
  const bump = (node: string): NodeHistory => {
    let h = hist.get(node)
    if (!h) {
      h = {
        ok: 0,
        fail: 0,
        lastTs: '',
        lastOkTs: '',
        lastFailTs: '',
        lastError: '',
        avgElapsedSec: null,
        elapsedSamples: 0,
        recent: [],
        lastIter: -1,
        lastIterOk: 0,
      }
      hist.set(node, h)
    }
    return h
  }
  const epochStr = fmtTs(POOL_EPOCH_MS)
  // 最近一小时永远相对"当下"（不可复用 epoch——epoch 是部署锚点，过去的部署会使
  // hourAgo 退到远古，昨天错误也会被判"一小时以内"）。
  const hourAgoStr = fmtTs(Date.now() - 3_600_000)
  let roots: string[] = []
  try {
    roots = readdirSync(join(REPO_ROOT, 'tmp'), { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => join(REPO_ROOT, 'tmp', d.name, 'dist-agent-meta.jsonl'))
  } catch {
    /* tmp missing */
  }
  roots.push(join(REPO_ROOT, 'tmp', 'dist-agent-meta.jsonl'))
  for (const metaPath of roots) {
    try {
      if (!existsSync(metaPath)) continue
      for (const line of readFileSync(metaPath, 'utf8').split(String.fromCharCode(10))) {
        if (!line.trim()) continue
        try {
          const r = JSON.parse(line) as {
            node?: string
            ok?: boolean
            elapsedSec?: number
            ts?: string
            reason?: string
            it?: number
          }
          if (!r.node) continue
          const nts = normTs(r.ts)
          // 只统计"清空锚点之后"的行（POOL_HISTORY_FRESH=1 显式清零时才有锚点；
          // 默认 0 = 保留全部历史）。ts 缺失无法判定新旧 → 忽略，保守。
          if (!r.ts || nts < epochStr) continue
          const h = bump(r.node)
          // 最近 10 次完成率（rollout+eval 混合）：状态判定数据源。
          h.recent.push(!!r.ok)
          if (h.recent.length > 10) h.recent.shift()
          // 上轮贡献度：最大 it 为"上一轮"，累计该轮成功局数。
          const it = typeof r.it === 'number' && Number.isInteger(r.it) ? r.it : -1
          if (it > h.lastIter) {
            h.lastIter = it
            h.lastIterOk = r.ok ? 1 : 0
          } else if (it === h.lastIter && it >= 0 && r.ok) {
            h.lastIterOk++
          }
          if (r.ok) {
            h.ok++
            if (nts >= h.lastOkTs) h.lastOkTs = nts
            if (typeof r.elapsedSec === 'number' && r.elapsedSec > 0) {
              const prevTotal = (h.avgElapsedSec ?? 0) * h.elapsedSamples
              h.elapsedSamples++
              h.avgElapsedSec = +((prevTotal + r.elapsedSec) / h.elapsedSamples).toFixed(1)
            }
          } else {
            h.fail++
            // 最新错误只近一小时（用户指令）：窗口外错误不进 lastError。
            h.lastError = nts >= hourAgoStr ? (r.reason ?? '').slice(0, 120) : h.lastError
            if (nts >= hourAgoStr && nts > h.lastFailTs) h.lastFailTs = nts
          }
          if (nts > h.lastTs) h.lastTs = nts
        } catch {
          /* skip bad line */
        }
      }
    } catch {
      /* unreadable */
    }
  }
  return hist
}

function poolStatusCell(h: NodeHistory): string {
  const n = h.recent.length
  const okN = h.recent.filter(Boolean).length
  if (n === 0) return `<span class="badge b-gray">无数据</span>`
  if (okN / n >= 0.9) return `<span class="badge b-green">健康 ${okN}/${n}</span>`
  if (okN / n >= 0.7) return `<span class="badge b-yellow">波动 ${okN}/${n}</span>`
  return `<span class="badge b-red">异常 ${okN}/${n}</span>`
}

// ---------------- 渲染上下文（sampler-agent 侧状态注入） ----------------

export interface PoolPageCtx {
  workers: number
  inflight: Map<string, { stage: number; seed: number; startedAt: number }>
  gamesDoneTotal: number
  /** kind → 桶（结构性类型：渲染只需 size）。 */
  weightsByKindSha: Map<string, { size: number }>
  lastError: string
  localHash: () => string
}

export async function renderPoolPage(ctx: PoolPageCtx): Promise<string> {
  const hist = aggregateNodeHistory()
  const localHash = ctx.localHash()
  const nodes = loadPoolConfig()
  const nowStr = fmtTs(Date.now())
  const rows: string[] = []
  if (nodes) {
    const probes = await Promise.all(
      nodes.map(async (n) => {
        if (!n.enabled) return { n, ping: null, ms: -1, skip: true }
        const started = Date.now()
        try {
          const resp = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
            headers: { Authorization: `Bearer ${n.authKey}` },
            signal: AbortSignal.timeout(2500),
          })
          if (!resp.ok) return { n, ping: null, ms: Date.now() - started }
          return {
            n,
            ping: (await resp.json()) as Record<string, unknown>,
            ms: Date.now() - started,
          }
        } catch {
          return { n, ping: null, ms: Date.now() - started }
        }
      }),
    )
    for (const { n, ping, ms } of probes) {
      const h = hist.get(n.id) ?? {
        ok: 0,
        fail: 0,
        lastTs: '-',
        lastOkTs: '-',
        lastFailTs: '-',
        lastError: '',
        avgElapsedSec: null,
        elapsedSamples: 0,
        recent: [],
        lastIter: -1,
        lastIterOk: 0,
      }
      // disabled：状态固定显示 disabled，规格/版本/ping 为 '-'，历史数据照常展示。
      const disabled = n.enabled === false
      let statusCell: string
      let specCell = '-'
      let verCell = '-'
      if (disabled) {
        statusCell = `<span class="badge b-gray">disabled</span>`
      } else if (ping) {
        const hashOk = String((ping as any)?.codeHash) === localHash
        verCell = `v${String((ping as any)?.codeHash ?? '?').slice(0, 7)}`
        if (!hashOk) verCell += `<span class="pill">旧</span>`
        statusCell = poolStatusCell(h)
        specCell = `${(ping as any)?.cpus ?? '?'} 核`
      } else {
        // ping 失败：状态仍以最近 10 次完成率表征（用户指令 8），ping 列如实显示失败。
        statusCell = poolStatusCell(h)
        if (h.recent.length === 0) statusCell = `<span class="badge b-red">无 ping · 无历史</span>`
      }
      const errCell = h.lastError
        ? `<span class="err">${h.lastError}</span><br><span class="muted">${h.lastFailTs || ''}</span>`
        : '<span class="muted">-</span>'
      rows.push(
        `<tr><td class="name">${n.id}</td><td>${statusCell}</td>` +
          `<td>${specCell}</td><td class="ver">${verCell}</td>` +
          `<td class="num" data-v="${disabled || !ping ? 9999 : ms}">${!disabled && ping ? `${ms}ms` : '-'}</td>` +
          `<td class="num" data-v="${h.ok}">${h.ok}</td><td class="num" data-v="${h.fail}">${h.fail}</td>` +
          `<td class="num" data-v="${h.lastIterOk}">${h.lastIter >= 0 ? h.lastIterOk : '-'}</td>` +
          `<td class="num" data-v="${h.avgElapsedSec ?? 9999}">${h.avgElapsedSec !== null ? `${h.avgElapsedSec}s` : '-'}</td>` +
          `<td data-v="${h.lastOkTs}">${h.lastOkTs || '-'}</td>` +
          `<td data-v="${h.lastFailTs}">${errCell}</td></tr>`,
      )
    }
    // 本机直跑（训练器 local 槽位）固定入表一行：无 ping/版本，状态按最近完成率、
    // 上轮贡献度照常聚合。无历史（部署清零后）也占位显示，槽位数来自 rl-config。
    const localSlots = poolLocalSlots()
    const localH = hist.get('local') ?? {
      ok: 0,
      fail: 0,
      lastTs: '-',
      lastOkTs: '-',
      lastFailTs: '-',
      lastError: '',
      avgElapsedSec: null,
      elapsedSamples: 0,
      recent: [],
      lastIter: -1,
      lastIterOk: 0,
    }
    {
      const errCellL = localH.lastError
        ? `<span class="err">${localH.lastError}</span><br><span class="muted">${localH.lastFailTs || ''}</span>`
        : '<span class="muted">-</span>'
      rows.push(
        `<tr><td class="name">local<span class="dim">（本机直跑）</span></td><td>${poolStatusCell(localH)}</td>` +
          `<td>${localSlots !== null ? `${localSlots} 槽` : '-'}</td><td class="ver">-</td>` +
          `<td class="num" data-v="9999">-</td>` +
          `<td class="num" data-v="${localH.ok}">${localH.ok}</td><td class="num" data-v="${localH.fail}">${localH.fail}</td>` +
          `<td class="num" data-v="${localH.lastIterOk}">${localH.lastIter >= 0 ? localH.lastIterOk : '-'}</td>` +
          `<td class="num" data-v="${localH.avgElapsedSec ?? 9999}">${localH.avgElapsedSec !== null ? `${localH.avgElapsedSec}s` : '-'}</td>` +
          `<td data-v="${localH.lastOkTs}">${localH.lastOkTs || '-'}</td>` +
          `<td data-v="${localH.lastFailTs}">${errCellL}</td></tr>`,
      )
    }
  }
  // 默认按「已结算局」倒序（点击表头仍可手动排）。num cell 列表：ping、已结算局、失败局、
  // 上轮贡献度、平均耗时——第 2 个（index 1）= 已结算局。
  rows.sort((a, b) => {
    const okA = Number((a.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    const okB = Number((b.match(/<td class="num" data-v="(\d+)">/g) ?? [])[1]?.match(/\d+/) ?? 0)
    return okB - okA
  })
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>sampler-agent 节点池</title>
<style>
:root{--bg:#f4f6f9;--card:#ffffff;--border:#e5e8ee;--text:#1c2333;--muted:#7a8395;
--green:#16a34a;--green-bg:#e9f9ef;--yellow:#b45309;--yellow-bg:#fdf3e7;--red:#dc2626;--red-bg:#fdecec;
--gray:#7a8395;--gray-bg:#f1f3f7;--accent:#2f5fe0;--accent-bg:#eef2fe;--row-hover:#f7f9fc}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
margin:0;background:var(--bg);color:var(--text);padding:24px 28px}
.wrap{max-width:1180px;margin:0 auto}
.pool-header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;
margin-bottom:16px}
.pool-header h2{margin:0;font-size:20px;font-weight:700;letter-spacing:.2px}
.pool-header h2 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green);
margin-right:8px;vertical-align:1px}
.pool-header .ts{font-size:12.5px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;
box-shadow:0 1px 3px rgba(16,24,40,.05)}
table{width:100%;border-collapse:collapse}
thead th{background:#fafbfd;border-bottom:2px solid var(--border);text-align:left;padding:11px 14px;
font-size:12px;font-weight:600;color:var(--muted);cursor:pointer;white-space:nowrap;user-select:none;
letter-spacing:.3px}
thead th:hover{background:#f0f3f8;color:var(--accent)}
tbody td{padding:10px 14px;border-bottom:1px solid #f0f2f6;font-size:13px;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--row-hover)}
td.name{font-weight:600}
td.name .dim{color:var(--muted);font-weight:400;font-size:12px}
td.ver{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#4b5563}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:12px;font-weight:600;line-height:18px;white-space:nowrap}
.b-green{background:var(--green-bg);color:var(--green)}
.b-yellow{background:var(--yellow-bg);color:var(--yellow)}
.b-red{background:var(--red-bg);color:var(--red)}
.b-gray{background:var(--gray-bg);color:var(--gray)}
.pill{display:inline-block;margin-left:6px;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600;
background:var(--yellow-bg);color:var(--yellow);vertical-align:1px}
.err{color:var(--red);font-size:12px;word-break:break-all}
.muted{color:var(--muted);font-size:12px}
.foot{margin:16px 2px 0;font-size:12.5px;color:var(--muted);line-height:1.8}
.foot b{color:#59606f}
@media (max-width:900px){body{padding:14px}thead th,tbody td{padding:8px 10px}}
</style></head>
<body><div class="wrap">
<div class="pool-header"><h2><span class="dot"></span>节点池监控</h2>
<span class="ts">每 60s 自动刷新 · 本次刷新 ${nowStr}</span></div>
<div class="card"><table id="pool">
<thead><tr>
<th onclick="sortTbl(0,this)">节点</th><th onclick="sortTbl(1,this)">状态</th><th onclick="sortTbl(2,this)">规格</th>
<th onclick="sortTbl(3,this)">版本</th><th onclick="sortTbl(4,this)">ping</th><th onclick="sortTbl(5,this)">已结算局</th>
<th onclick="sortTbl(6,this)">失败局</th><th onclick="sortTbl(7,this)">上轮贡献度</th><th onclick="sortTbl(8,this)">平均耗时</th>
<th onclick="sortTbl(9,this)">最近成功</th><th onclick="sortTbl(10,this)">最近错误</th>
</tr></thead>
<tbody>${rows.join(String.fromCharCode(10))}</tbody>
</table></div>
<script>
function sortTbl(col, th) {
  const tb = document.querySelector('#pool tbody');
  const rows = Array.from(tb.rows);
  const dir = th.dataset.dir === 'asc' ? -1 : 1;
  th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  rows.sort((a, b) => {
    const av = a.cells[col].dataset.v ?? a.cells[col].innerText;
    const bv = b.cells[col].dataset.v ?? b.cells[col].innerText;
    const an = parseFloat(av), bn = parseFloat(bv);
    const cmp = !isNaN(an) && !isNaN(bn) ? an - bn : String(av).localeCompare(String(bv));
    return dir * cmp;
  });
  for (const r of rows) tb.appendChild(r);
}
</script>
<p class="foot">状态 = 最近 10 次 rollout/eval 结算完成率（<b>健康</b>≥90% · <b>波动</b>≥70% · <b>异常</b>&lt;70%），替代单次 ping 判断；ping 列仅作实时参考。
最近错误仅显示最近 1 小时内。数据源：dist-agent-meta.jsonl（tmp 下各训练流聚合）·
${POOL_EPOCH_MS > 0 ? `<b>历史自 ${fmtTs(POOL_EPOCH_MS)} 起重新累计</b>（此后部署不再重置）` : `<b>累计全部历史</b>`}。
只读页面，不含密钥。默认按「已结算局」倒序，点击表头排序。</p>
</div></body></html>`
}
