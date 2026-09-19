/** pool.ts — /api/pool 数据端点：慢节奏 + 课程键控 TTL 缓存。 */
import { loadConfig } from '../../core/config'
import { pidAlive } from '../../core/net'
import { loadRegistry } from '../../core/registry'
import type { RlConfig } from '../../core/types'
import { type NodeHistoryRow, type PoolView, type SelfStatus, stripIsoPrefix } from '../../web/view'
import { loadConsoleState } from '../actions'
import { aggregateNodeHistory, emptyHistory, poolStatus } from '../pool-history'
import { discoverCourses, effectiveCourse } from './courses'

// ────────────────────────── /api/pool（§3.3 数据端点：独立慢节奏 + 服务端 30s TTL 缓存） ──────────────────────────

const POOL_TTL_MS = 30_000
/** 缓存 key 带 course（DS-E1）：切课程天然 miss 重算，30s 窗口内不错课程数据。 */
const poolCache = new Map<string, { at: number; view: PoolView }>()

// 本机 codeHash 惰性 memo（R9）：sampler-agent 是重模块，lazy import + 5s TTL；失败降级空串。
let localHashMemo = ''
let localHashAt = 0
async function localCodeHash(): Promise<string> {
  if (localHashMemo && Date.now() - localHashAt < 5000) return localHashMemo
  try {
    const mod = (await import('../../../../tools/agent/sampler-agent')) as {
      collectCodeHashEntries: () => { relPath: string; content: Buffer }[]
      computeCodeHashFromFiles: (e: { relPath: string; content: Buffer }[]) => string
    }
    localHashMemo = mod.computeCodeHashFromFiles(mod.collectCodeHashEntries())
  } catch {
    localHashMemo = ''
  }
  localHashAt = Date.now()
  return localHashMemo
}

/** selfNode 存活时拉 /v1/status；失败/未启动 → null（UI 显示「agent 未启动」占位，不伪造）。 */
async function fetchSelfStatus(cfg: RlConfig): Promise<SelfStatus | null> {
  const reg = loadRegistry()
  if (!pidAlive(reg.selfNode?.pid)) return null
  const auth = cfg.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  try {
    const resp = await fetch(`http://127.0.0.1:${cfg.rl.agent_port}/v1/status`, {
      headers: auth ? { Authorization: `Bearer ${auth}` } : {},
      signal: AbortSignal.timeout(4000),
    })
    if (!resp.ok) return null
    const b = (await resp.json()) as {
      workers?: number
      gamesDoneTotal?: number
      inflight?: unknown[]
      diskFreeMB?: number | null
      lastError?: string
      uptimeSec?: number
      resultCache?: { items?: number; bytes?: number }
      recentFailed?: number
    }
    return {
      workers: Number(b.workers ?? 0),
      inflight: Array.isArray(b.inflight) ? b.inflight.length : 0,
      gamesDoneTotal: Number(b.gamesDoneTotal ?? 0),
      diskFreeMB: typeof b.diskFreeMB === 'number' ? b.diskFreeMB : null,
      lastError: b.lastError ? stripIsoPrefix(String(b.lastError)).slice(0, 200) : null,
      uptimeSec: typeof b.uptimeSec === 'number' ? b.uptimeSec : null,
      resultCacheItems: Number(b.resultCache?.items ?? 0),
      resultCacheBytes: Number(b.resultCache?.bytes ?? 0),
      recentFailed: Number(b.recentFailed ?? 0),
    }
  } catch {
    return null
  }
}

function nodeHistoryRow(
  n: {
    id: string
    url: string
    authKey: string
    enabled: boolean
    concurrency: number
    gpu_push?: boolean
  },
  h: ReturnType<typeof emptyHistory>,
  agg: { globalMaxIt: number },
  ping: Record<string, unknown> | null,
  pingMs: number | null,
  localHash: string,
): NodeHistoryRow {
  const disabled = !n.enabled
  let status: NodeHistoryRow['status']
  let spec = '-'
  let version = ''
  let versionOk: boolean | null = null
  if (disabled) {
    status = 'disabled'
  } else if (ping) {
    const codeHash = String(ping.codeHash ?? '')
    version = codeHash ? codeHash.slice(0, 7) : ''
    versionOk = codeHash.length > 0 ? codeHash === localHash : null
    spec = ping.cpus ? `${ping.cpus} 核` : '?核'
    status = poolStatus(h) // 在线但无结算历史 → 'nodata' 灰显
  } else {
    status = h.recent.length === 0 ? 'noping' : poolStatus(h)
  }
  return {
    id: n.id,
    kind: 'node',
    status,
    okN: h.recent.filter(Boolean).length,
    recentN: h.recent.length,
    spec,
    versionOk,
    version,
    versionLocal: localHash.slice(0, 7),
    pingMs: disabled ? null : pingMs,
    ok: h.ok,
    fail: h.fail,
    contrib: h.lastIterOk,
    contribRollout: h.contribRollout,
    contribEval: h.contribEval,
    lastIter: h.lastIter,
    globalMaxIt: agg.globalMaxIt,
    avgElapsedSec: h.avgElapsedSec,
    avgWallSec: h.avgWallSec,
    lastOkTs: h.lastOkTs,
    lastFailTs: h.lastFailTs,
    lastError: h.lastError,
    recent: h.recent,
  }
}

/** 池汇总（首屏不入流：扫描 tmp 太重，客户端异步拉；R7）。courseOverride 为只读视图课程
 *  （?course=，已 sanitize）；空则回退操作员课程。缓存 key 已带 course（DS-E1）。 */
export async function buildPoolView(fresh = false, courseOverride?: string): Promise<PoolView> {
  const cfg = loadConfig()
  const state = loadConsoleState()
  const course = courseOverride || effectiveCourse(state, discoverCourses())
  const key = `pool-view:${course}`
  const cached = poolCache.get(key)
  if (!fresh && cached && Date.now() - cached.at < POOL_TTL_MS) return cached.view

  const hash = await localCodeHash()
  const agg = aggregateNodeHistory()
  const probes = await Promise.all(
    cfg.nodes.map(async (n) => {
      if (!n.enabled)
        return { n, ping: null as Record<string, unknown> | null, ms: null as number | null }
      const started = Date.now()
      try {
        const resp = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
          headers: { Authorization: `Bearer ${n.authKey}` },
          signal: AbortSignal.timeout(2500),
        })
        if (!resp.ok) return { n, ping: null, ms: Date.now() - started }
        return { n, ping: (await resp.json()) as Record<string, unknown>, ms: Date.now() - started }
      } catch {
        return { n, ping: null, ms: Date.now() - started }
      }
    }),
  )

  const nodes: NodeHistoryRow[] = []
  for (const { n, ping, ms } of probes) {
    const h = agg.hist.get(n.id) ?? emptyHistory()
    nodes.push(nodeHistoryRow(n, h, agg, ping, ms, hash))
  }

  const localH = agg.hist.get('local') ?? emptyHistory()
  const slots = Number(cfg.rl.local_slots)
  const local: NodeHistoryRow = {
    id: 'local',
    kind: 'local',
    status: poolStatus(localH),
    okN: localH.recent.filter(Boolean).length,
    recentN: localH.recent.length,
    // 显式 0 也显示「0 槽」（与 pill 芯片同口径）；配置缺失/非法（NaN）才 '-'。
    spec: Number.isInteger(slots) && slots >= 0 ? `${slots} 槽` : '-',
    versionOk: null,
    version: '',
    versionLocal: hash.slice(0, 7),
    pingMs: null,
    ok: localH.ok,
    fail: localH.fail,
    contrib: localH.lastIterOk,
    contribRollout: localH.contribRollout,
    contribEval: localH.contribEval,
    lastIter: localH.lastIter,
    globalMaxIt: agg.globalMaxIt,
    avgElapsedSec: localH.avgElapsedSec,
    avgWallSec: localH.avgWallSec,
    lastOkTs: localH.lastOkTs,
    lastFailTs: localH.lastFailTs,
    lastError: localH.lastError,
    recent: localH.recent,
  }

  const view: PoolView = {
    cachedAt: Date.now(),
    course,
    epochMs: agg.epochMs,
    activeFlow: agg.activeFlow,
    nodes,
    local,
    selfStatus: await fetchSelfStatus(cfg),
    localHash: hash,
  }
  poolCache.set(key, { at: view.cachedAt, view })
  return view
}
