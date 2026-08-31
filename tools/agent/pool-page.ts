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

// ---------------- 模块级常量（进程启动 = 本次部署时刻） ----------------

/** 本进程启动（= 本次部署）时刻；更早的 dist-agent-meta 行不参与统计（用户指令：历史
 *  数据从本次更新部署时重新汇总；v1/update 触发重启亦视为新部署）。 */
const POOL_EPOCH_MS = Date.now()

// ---------------- 配置 ----------------

interface PoolNodeCfg {
  id: string
  url: string
  authKey: string
  /** rl-config 的 enabled 字段；disabled 节点不 ping、状态列显示 disabled、历史仍展示。 */
  enabled: boolean
}

const REPO_ROOT = resolve(import.meta.dir ?? process.cwd(), '..', '..')

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
  const hourAgoStr = fmtTs(POOL_EPOCH_MS - 3_600_000)
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
          // 只统计本次部署后的行（ts 缺失无法判定新旧 → 忽略，保守）。
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
  if (n === 0) return `<span style="color:#999">无数据</span>`
  if (okN / n >= 0.9) return `<span style="color:#0a0">健康 ${okN}/${n}</span>`
  if (okN / n >= 0.7) return `<span style="color:#c60">波动 ${okN}/${n}</span>`
  return `<span style="color:#c00">异常 ${okN}/${n}</span>`
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
        statusCell = `<span style="color:#999">disabled</span>`
      } else if (ping) {
        const hashOk = String((ping as any)?.codeHash) === localHash
        verCell = `v${String((ping as any)?.codeHash ?? '?').slice(0, 7)}`
        if (!hashOk) verCell += ` <span style="color:#c60">(旧)</span>`
        statusCell = poolStatusCell(h)
        specCell = `${(ping as any)?.cpus ?? '?'} 核`
      } else {
        // ping 失败：状态仍以最近 10 次完成率表征（用户指令 8），ping 列如实显示失败。
        statusCell = poolStatusCell(h)
        if (h.recent.length === 0) statusCell = `<span style="color:#c00">无 ping · 无历史</span>`
      }
      const errCell = h.lastError
        ? `<span style="color:#c00">${h.lastError}</span><br><span style="color:#999">${h.lastFailTs || ''}</span>`
        : '-'
      rows.push(
        `<tr><td>${n.id}</td><td>${statusCell}</td>` +
          `<td>${specCell}</td><td>${verCell}</td>` +
          `<td data-v="${disabled || !ping ? 9999 : ms}">${!disabled && ping ? `${ms}ms` : '-'}</td>` +
          `<td data-v="${h.ok}" style="text-align:right">${h.ok}</td><td data-v="${h.fail}" style="text-align:right">${h.fail}</td>` +
          `<td data-v="${h.lastIterOk}" style="text-align:right">${h.lastIter >= 0 ? h.lastIterOk : '-'}</td>` +
          `<td data-v="${h.avgElapsedSec ?? 9999}">${h.avgElapsedSec !== null ? `${h.avgElapsedSec}s` : '-'}</td>` +
          `<td data-v="${h.lastOkTs}">${h.lastOkTs || '-'}</td>` +
          `<td data-v="${h.lastFailTs}">${errCell}</td></tr>`,
      )
    }
    // 本机直跑（训练器 local 槽位）随表展示：无 ping/规格，状态与贡献度来自历史聚合。
    const localH = hist.get('local')
    if (localH) {
      const errCellL = localH.lastError
        ? `<span style="color:#c00">${localH.lastError}</span><br><span style="color:#999">${localH.lastFailTs || ''}</span>`
        : '-'
      rows.push(
        `<tr><td>local（本机直跑）</td><td>${poolStatusCell(localH)}</td>` +
          `<td>-</td><td>-</td>` +
          `<td data-v="9999">-</td>` +
          `<td data-v="${localH.ok}" style="text-align:right">${localH.ok}</td><td data-v="${localH.fail}" style="text-align:right">${localH.fail}</td>` +
          `<td data-v="${localH.lastIterOk}" style="text-align:right">${localH.lastIter >= 0 ? localH.lastIterOk : '-'}</td>` +
          `<td data-v="${localH.avgElapsedSec ?? 9999}">${localH.avgElapsedSec !== null ? `${localH.avgElapsedSec}s` : '-'}</td>` +
          `<td data-v="${localH.lastOkTs}">${localH.lastOkTs || '-'}</td>` +
          `<td data-v="${localH.lastFailTs}">${errCellL}</td></tr>`,
      )
    }
  }
  // 默认按「已结算局」倒序（点击表头仍可手动排）。
  rows.sort((a, b) => {
    const ma = a.match(
      /data-v="(\d+)" style="text-align:right">(\d+)<\/td><td data-v="(\d+)" style="text-align:right">/,
    )
    const mb = b.match(
      /data-v="(\d+)" style="text-align:right">(\d+)<\/td><td data-v="(\d+)" style="text-align:right">/,
    )
    if (!ma || !mb) return 0
    return Number(mb[2]) - Number(ma[2])
  })
  const inflightRows = [...ctx.inflight.values()]
    .map((v) => `s${v.stage}/seed${v.seed}（${((Date.now() - v.startedAt) / 1000).toFixed(0)}s）`)
    .join('，')
  const weightsRows =
    [...ctx.weightsByKindSha.entries()].map(([kind, m]) => `${kind}×${m.size}桶`).join('，') || '—'
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>sampler-agent 节点池</title></head>
<body style="font-family:system-ui,sans-serif;margin:24px">
<h2>节点池监控（每 60s 自动刷新 · 本次刷新 ${nowStr}）</h2>
<p>本机 agent：workers=${ctx.workers}，在飞=${ctx.inflight.size}${inflightRows ? `（${inflightRows}）` : ''}，
累计完成=${ctx.gamesDoneTotal}，权重桶=${weightsRows}${ctx.lastError ? `，<span style="color:#c00">lastError=${ctx.lastError}</span>` : ''}</p>
<table border="1" cellpadding="6" cellspacing="0" id="pool" style="border-collapse:collapse;font-size:14px">
<thead><tr style="background:#eee">
<th onclick="sortTbl(0,this)">节点</th><th onclick="sortTbl(1,this)">状态</th><th onclick="sortTbl(2,this)">规格</th>
<th onclick="sortTbl(3,this)">版本</th><th onclick="sortTbl(4,this)">ping</th><th onclick="sortTbl(5,this)">已结算局</th>
<th onclick="sortTbl(6,this)">失败局</th><th onclick="sortTbl(7,this)">上轮贡献度</th><th onclick="sortTbl(8,this)">平均耗时</th>
<th onclick="sortTbl(9,this)">最近成功</th><th onclick="sortTbl(10,this)">最近错误</th>
</tr></thead>
<tbody>${rows.join(String.fromCharCode(10))}</tbody>
</table>
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
<p style="color:#666">状态 = 最近 10 次 rollout/eval 结算完成率（健康≥90% · 波动≥70% · 异常&lt;70%），替代单次 ping 判断；
ping 列仅作实时参考。最近错误仅显示最近 1 小时内。数据源：dist-agent-meta.jsonl（tmp/* 各训练流聚合，<b>自 ${fmtTs(POOL_EPOCH_MS)}（本次部署）起重新汇总</b>）——历史数据从本次更新部署时清零重计。只读页面，不含密钥。默认按「已结算局」倒序，点击表头排序。</p>
</body></html>`
}
