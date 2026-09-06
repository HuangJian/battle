/** history.ts — 节点历史聚合（从 pool-page.ts 提取的数据层）。

 *  数据源：按 mtime 自动选取 tmp/ 下最新训练流的 dist-agent-meta.jsonl（递归扫
 *  描，只聚合最近活跃目录），聚合出每节点的 ok/fail/最近完成率/上轮贡献度/滑动
 *  平均耗时等。历史锚点 tmp/dist-agent/pool-epoch.txt（受控清空）保留原语义。
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { REPO_ROOT } from '../paths'
import { fmtTs } from './theme'

/** 锚点文件：tmp/dist-agent/pool-epoch.txt（毫秒时间戳）。语义（用户 2026-08-31）：
 *  · 部署写入一次 → 历史自此刻起重新累计；
 *  · 此后任何部署/重启只读同一锚点 → 历史持续累计；
 *  · 用户明确要求清空时，重写/删除该文件 = 新锚点。 */
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

/** dist-agent-meta 的 ts 分布带 T（ISO）与空格两种写法；统一为空格格式后再比。 */
function normTs(s: string | undefined): string {
  return (s ?? '').replace('T', ' ')
}

export interface NodeHistory {
  ok: number
  fail: number
  lastTs: string
  lastOkTs: string
  lastFailTs: string
  lastError: string
  /** 最近至多 50 局的端到端服务时长样本（滑动窗口）。 */
  elapsedRecent: number[]
  /** elapsedRecent 的均值；null = 无样本。 */
  avgElapsedSec: number | null
  /** 最近至多 10 条结算结果（ok=true），完成率 = 在线状态的判定依据。 */
  recent: boolean[]
  /** 该节点自己最近一次成功结算的轮次（-1 = 无成功记录）。 */
  lastIter: number
  /** 全局最新轮（globalMaxIt）该节点的成功结算局数。 */
  lastIterOk: number
}

export function emptyHistory(): NodeHistory {
  return {
    ok: 0,
    fail: 0,
    lastTs: '-',
    lastOkTs: '-',
    lastFailTs: '-',
    lastError: '',
    elapsedRecent: [],
    avgElapsedSec: null,
    recent: [],
    lastIter: -1,
    lastIterOk: 0,
  }
}

/** 活跃训练流信息：mtime 最新的 dist-agent-meta.jsonl 所在目录。 */
export interface ActiveFlow {
  /** 目录短名（如 s3-cap2、rl-traj）。 */
  dir: string
  mtimeMs: number
  lines: number
}

export interface HistoryAggregate {
  hist: Map<string, NodeHistory>
  activeFlow: ActiveFlow | null
  /** 全局最新轮 = 所有节点成功结算的最大 it（上轮贡献度的对齐基准）。 */
  globalMaxIt: number
  /** 历史锚点毫秒（渲染层展示用）。 */
  epochMs: number
}

export function aggregateNodeHistory(): HistoryAggregate {
  const hist = new Map<string, NodeHistory>()
  // 各节点在「轮次 → 成功局数」的分布：上轮贡献度按全局最大轮对齐。
  const okByNodeIt = new Map<string, Map<number, number>>()
  const bump = (node: string): NodeHistory => {
    let h = hist.get(node)
    if (!h) {
      h = emptyHistory()
      h.lastTs = ''
      h.lastOkTs = ''
      h.lastFailTs = ''
      hist.set(node, h)
    }
    return h
  }
  const epochStr = fmtTs(POOL_EPOCH_MS)
  // 最近一小时永远相对"当下"（不可复用 epoch——epoch 是部署锚点）。
  const hourAgoStr = fmtTs(Date.now() - 3_600_000)

  // 收集所有候选 meta 文件（递归扫描 tmp/ 下所有 dist-agent-meta.jsonl）。
  // 训练流的 traj_root 可以是 tmp/X（一层）或 tmp/X/traj（两层），必须递归搜索。
  interface MetaCandidate {
    path: string
    dir: string
    mtimeMs: number
  }
  const candidates: MetaCandidate[] = []
  const tmpDir = join(REPO_ROOT, 'tmp')
  const walk = (base: string, rel: string): void => {
    try {
      for (const d of readdirSync(join(base, rel), { withFileTypes: true })) {
        const childRel = rel ? `${rel}/${d.name}` : d.name
        if (d.isDirectory()) {
          // 限制深度：最多 3 层（tmp/X/traj/it1 级别），避免扫描 node_modules 等
          if (childRel.split('/').length <= 3) walk(base, childRel)
        } else if (d.name === 'dist-agent-meta.jsonl') {
          const p = join(base, childRel)
          try {
            candidates.push({
              path: p,
              dir: childRel.replace(/\/dist-agent-meta\.jsonl$/, ''),
              mtimeMs: statSync(p).mtimeMs,
            })
          } catch {
            /* stat failed */
          }
        }
      }
    } catch {
      /* unreadable */
    }
  }
  try {
    walk(tmpDir, '')
  } catch {
    /* tmp missing */
  }

  // 按 mtime 降序：最新修改的 = 当前活跃训练流。仅聚合最新那个——避免旧训练流
  // 的数千条历史淹没新训练数据。
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs)
  const activeFlow: ActiveFlow | null =
    candidates.length > 0
      ? { dir: candidates[0].dir, mtimeMs: candidates[0].mtimeMs, lines: 0 }
      : null
  const sources = candidates.length > 0 ? [candidates[0]] : []
  for (const src of sources) {
    try {
      if (!existsSync(src.path)) continue
      const lines = readFileSync(src.path, 'utf8').split(String.fromCharCode(10))
      if (activeFlow) activeFlow.lines = lines.filter((l) => l.trim()).length
      for (const line of lines) {
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
          // 只统计"清空锚点之后"的行；ts 缺失无法判定新旧 → 忽略，保守。
          if (!r.ts || nts < epochStr) continue
          const h = bump(r.node)
          h.recent.push(!!r.ok)
          if (h.recent.length > 10) h.recent.shift()
          if (r.ok) {
            h.ok++
            if (nts >= h.lastOkTs) h.lastOkTs = nts
            const it = typeof r.it === 'number' && Number.isInteger(r.it) ? r.it : -1
            if (it >= 0) {
              let m = okByNodeIt.get(r.node)
              if (!m) {
                m = new Map()
                okByNodeIt.set(r.node, m)
              }
              m.set(it, (m.get(it) ?? 0) + 1)
            }
            if (typeof r.elapsedSec === 'number' && r.elapsedSec > 0) {
              h.elapsedRecent.push(r.elapsedSec)
              if (h.elapsedRecent.length > 50) h.elapsedRecent.shift()
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
  // 上轮贡献度修正（2026-09-06，a98 案例）：口径 = globalMaxIt 下各节点成功局数，
  // 落后节点计 0（渲染层灰显 + tooltip 给出其最近贡献轮次）。
  let globalMaxIt = -1
  for (const m of okByNodeIt.values()) {
    for (const it of m.keys()) if (it > globalMaxIt) globalMaxIt = it
  }
  for (const [node, h] of hist) {
    const m = okByNodeIt.get(node)
    let maxIt = -1
    if (m) for (const it of m.keys()) if (it > maxIt) maxIt = it
    h.lastIter = maxIt
    h.lastIterOk = (globalMaxIt >= 0 && m?.get(globalMaxIt)) || 0
    // 滑动窗口均值（最近 ≤50 局）：口径升级/负载变化后即时不被终身历史拖累。
    h.avgElapsedSec = h.elapsedRecent.length
      ? +(h.elapsedRecent.reduce((a, b) => a + b, 0) / h.elapsedRecent.length).toFixed(1)
      : null
  }
  return { hist, activeFlow, globalMaxIt, epochMs: POOL_EPOCH_MS }
}

/** 状态徽章：最近 10 次结算完成率（健康≥90% · 波动≥70% · 异常<70%）。 */
export function poolStatusCell(h: NodeHistory): string {
  const n = h.recent.length
  const okN = h.recent.filter(Boolean).length
  if (n === 0) return `<span class="badge b-gray">无数据</span>`
  if (okN / n >= 0.9) return `<span class="badge b-green">健康 ${okN}/${n}</span>`
  if (okN / n >= 0.7) return `<span class="badge b-yellow">波动 ${okN}/${n}</span>`
  return `<span class="badge b-red">异常 ${okN}/${n}</span>`
}

/** 上轮贡献度单元格：>0 = 在全局最新轮的成功结算局数；0 但有历史 = 落后当前轮
 *  （灰显 + tooltip）；无任何成功记录 = '-'。data-v=0 让落后节点排序沉底。 */
export function contribCell(h: NodeHistory, globalMaxIt: number): string {
  if (h.lastIterOk > 0) return `<td class="num" data-v="${h.lastIterOk}">${h.lastIterOk}</td>`
  if (h.lastIter >= 0 && globalMaxIt >= 0) {
    return (
      `<td class="num" data-v="0"><span class="muted" title="该节点最近一次成功结算在 ` +
      `it${h.lastIter}，已落后当前 it${globalMaxIt}">${h.lastIterOk}</span></td>`
    )
  }
  return `<td class="num" data-v="0">-</td>`
}
