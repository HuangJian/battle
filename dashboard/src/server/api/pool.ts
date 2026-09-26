/** pool.ts — /api/pool 数据端点：慢节奏 + 课程键控的 SWR 缓存。
 *
 *  ★ 2026-09-22（用户指令：让节点编辑真的第一帧就上屏）：这个视图曾把两件**快慢差三个数量级**
 *  的事装在同一条缓存里，只能整条一起新生或一起陈旧：
 *    · **结构**：节点行/顺序、`enabled`（只读的那一行显示 disabled/无 ping）、local 槽数——来自
 *      当下 cfg，**毫秒级**，而且就是操作员刚刚拨下的那个开关；
 *    · **探测**：逐节点 ping（2.5s 超时 ∥）、池历史聚合（递归扫 tmp、读 MB 级 meta）、codeHash
 *      （sampler-agent 算全部源码）、selfNode `/v1/status`——实测冷算 **2448–2552ms**。
 *  于是「停用节点」这个动作无论怎么处置缓存都不对：不碰 ⇒ 池表还显示旧状态 30s（TTL）/5min
 *  （面板轮询）而同一页的注册表行已经写「已停用」；硬清 ⇒ 面板被按住 2.5s；整条软作废 ⇒ 首帧
 *  给的还是旧状态（要等后台重算落地，实测 ~2.5s）。
 *
 *  根上的观察：**这份视图里没有任何“按课程”的东西**——cfg.nodes / rl.local_slots / tmp 下所有
 *  训练流 / 本机 codeHash / selfNode 存活性全是**机器事实**（`aggregateNodeHistory` 不收课程参数，
 *  扫 `tmp/**` 合并**所有** meta）。所以拆成两层：
 *    · **探测层** `PoolProbes`：单条目全局 SWR 缓存（与 `snapshot-cache.FleetProbes` 同规）
 *      ——机器级、跨课程共用、陈旧先给旧值 + 重算丢后台；
 *    · **结构层**：每请求现算（`assemblePoolView`）——节点行/顺序/停用/local 槽数、`disabled` 行的
 *      `status`/`pingMs` 都是 cfg 事实，**第一帧就是新的**。
 *  `course` 只作为回显字段，不再当缓存键。**2026-09-26（plan/nodes-decouple-from-course.plan.md）：
 *  `?course=` 已移除，视图加 `?days=` 窗口**——聚合缓存的是**全量** `agg`（与窗口无关），
 *  `days` 只在 `assemblePoolView` 之后做**纯投影** ⇒ 切天零重算。
 *  探测列（enabled 节点的 status/成功率/版本、selfStatus…）允许粗一个重算周期；客户端以
 *  `cachedAt`（= 探测层算完时刻）推进为「新的一份到了」的判据，做一次有界再校验（见 NodeStats）。
 *  显式 `?fresh=1`（手动刷新 / 重试）仍是**硬清 + 等重算**——那时操作员的诉求就是“现在就给我新的”。 */
import { loadConfig } from '../../core/config'
import { pidAlive } from '../../core/net'
import { loadRegistry } from '../../core/registry'
import type { RlConfig } from '../../core/types'
import { type NodeHistoryRow, type PoolView, type SelfStatus, stripIsoPrefix } from '../../web/view'
import { loadConsoleState } from '../actions'
import {
  type HistoryAggregate,
  type NodeHistory,
  type WindowAggregate,
  aggregateNodeHistory,
  emptyHistory,
  poolStatus,
  projectWindow,
  resolveWindow,
} from '../pool-history'
import { createSwrCache } from '../../core/swr-cache'
import { discoverCourses, effectiveCourse } from './courses'

// ────────────────────────── /api/pool（§3.3 数据端点：结构现算 ⊕ 机器级探测 SWR） ──────────────────────────

const POOL_TTL_MS = 30_000

/** **探测层**（机器级、贵）：逐节点 ping ⊕ 池历史聚合 ⊕ codeHash ⊕ selfNode 状态。
 *  与「看哪门课」无关 ⇒ 单条目全局缓存（与 `snapshot-cache.FleetProbes` 同规）。 */
interface PoolProbes {
  /** 本份探测的算完时刻 —— `PoolView.cachedAt` 的来源：客户端靠它推进判定「新的一份到了」。 */
  at: number
  agg: HistoryAggregate
  /** 节点 ping（id → 应答与耗时）；**探测时**停用的节点缺席（现启用后要等下一次重算才有）。 */
  pingById: Map<string, { ping: Record<string, unknown> | null; ms: number | null }>
  hash: string
  selfStatus: SelfStatus | null
}

const poolProbeCache = createSwrCache<PoolProbes>(POOL_TTL_MS)

/** 取探测层（SWR：新鲜直接给、陈旧先给旧值再后台重算）。请求路径上**永不**等探测。 */
function getPoolProbes(cfg: RlConfig): Promise<PoolProbes> {
  return poolProbeCache.get(() => computePoolProbes(cfg))
}

/** **动作后的软作废**（`snapshot-refresher.invalidateAfterAction` 调用）：保留旧探测供读路径
 *  立即返回，重算丢后台。
 *
 *  注意它**不再**负责「动作结果上屏」：结构（节点行的 enabled/status）已经在 `assemblePoolView`
 *  里现算，所以“刚停用的节点”第一帧就新；这里只是让探测列（完成率/版本/ping）跟着更新，
 *  不必等 30s（TTL）/5min（面板轮询）。 */
export function refreshPoolViews(): void {
  poolProbeCache.refresh()
}

/** **硬作废**（下一次读**必须**等重算）：生产路径上显式 `?fresh=1` 自带（见 `buildPoolView`），
 *  这里留给测试夹具归零。 */
export function invalidatePoolViews(): void {
  poolProbeCache.clear()
}

/** 本机 codeHash：`tools/agent/sampler-agent`（重模块，惰性 import 一次）按 SSOT 清单
 *  （`codehash-files.txt`）算内容摘要；失败降级空串。
 *
 *  ★ 2026-09-22：**删掉了原来的 5s TTL memo** —— 实测这份摘要只要 **3–5ms**
 *  （145 文件 / 2.1MB），而调用方（池探测层）自带 30s 采样节奏、自身要等 ~2.5s 的逐节点 ping。
 *  给一个 4ms 的计算挂窗口，换不到任何东西，只多一个陈旧面（长驻控制台改了代码也要等窗口）；
 *  而**内容摘要的输入就是全部内容，指纹不可能更便宜** ⇒ 这一类的正确处置是「不用缓存」。 */
async function localCodeHash(): Promise<string> {
  try {
    const mod = (await import('../../../../tools/agent/sampler-agent')) as {
      collectCodeHashEntries: () => { relPath: string; content: Buffer }[]
      computeCodeHashFromFiles: (e: { relPath: string; content: Buffer }[]) => string
    }
    return mod.computeCodeHashFromFiles(mod.collectCodeHashEntries())
  } catch {
    return ''
  }
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
  h: NodeHistory,
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
    status = poolStatus(h) // 在线但无结算历史（窗口内无 recent）→ 'nodata' 灰显
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
    winRollout: h.winRollout,
    winEval: h.winEval,
    lastContrib: h.lastContrib,
    avgElapsedSec: h.avgElapsedSec,
    avgWallSec: h.avgWallSec,
    lastOkTs: h.lastOkTs,
    lastFailTs: h.lastFailTs,
    lastError: h.lastError,
    recent: h.recent,
  }
}

/** 池汇总（首屏不入流：扫描 tmp 太重，客户端异步拉；R7）。
 *
 *  `days` = 本地日窗口（`today`/`yesterday`/`all`/`7`/`N`；缺省 today）。
 *  **2026-09-26：`?course=` 已移除** —— 池视图与课程无关，`course` 只是回显。
 *
 *  `fresh=true`（`?fresh=1`：手动刷新 / 重试）＝**硬清 + 等重算**——这是操作员的明确要求
 *  （“现在就给我新的”），不是动作路径（那走 `refreshPoolViews` 的软作废）。 */
export async function buildPoolView(fresh = false, days = 'today'): Promise<PoolView> {
  const cfg = loadConfig()
  const state = loadConsoleState()
  const course = effectiveCourse(state, discoverCourses())
  if (fresh) poolProbeCache.clear()
  const p = await getPoolProbes(cfg)
  return assemblePoolView(cfg, course, p, days)
}

/** **结构现算 ⊕ 探测取自缓存**：节点行/顺序、`enabled`（disabled 行的 status/无 ping）、local 槽数
 *  都是**当下 cfg** 的事实 —— 所以动作后的**第一帧**就是新的，不必等探测重算。
 *
 *  探测列（enabled 节点的 status/完成率/版本、selfStatus…）允许粗一个重算周期：它们本来就是
 *  “上一次探测的结论”。`cachedAt` 仍是**探测层**的时刻（客户端拿它判断重算何时落地）。 */
function assemblePoolView(cfg: RlConfig, course: string, p: PoolProbes, days: string): PoolView {
  // ★ 切天零重算：`p.agg` 是**全量**（与窗口无关，探测层缓存），这里只做纯投影。
  const w = resolveWindow(days, Date.now(), p.agg.epochMs)
  const proj: WindowAggregate = projectWindow(p.agg, w)
  const nodes: NodeHistoryRow[] = cfg.nodes.map((n) => {
    const h = proj.hist.get(n.id) ?? emptyHistory()
    const probe = p.pingById.get(n.id)
    return nodeHistoryRow(n, h, probe?.ping ?? null, probe?.ms ?? null, p.hash)
  })

  const localH = proj.hist.get('local') ?? emptyHistory()
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
    versionLocal: p.hash.slice(0, 7),
    pingMs: null,
    ok: localH.ok,
    fail: localH.fail,
    winRollout: localH.winRollout,
    winEval: localH.winEval,
    lastContrib: localH.lastContrib,
    avgElapsedSec: localH.avgElapsedSec,
    avgWallSec: localH.avgWallSec,
    lastOkTs: localH.lastOkTs,
    lastFailTs: localH.lastFailTs,
    lastError: localH.lastError,
    recent: localH.recent,
  }

  return {
    cachedAt: p.at,
    course,
    epochMs: proj.epochMs,
    sources: proj.sources,
    window: { key: w.key, label: w.label, startMs: w.startMs, endMs: w.endMs },
    nodes,
    local,
    selfStatus: p.selfStatus,
    localHash: p.hash,
  }
}

/** 真正探一次（不落缓存；落缓存/陈旧窗口由 `getPoolProbes` 的 SWR 负责）。 */
async function computePoolProbes(cfg: RlConfig): Promise<PoolProbes> {
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

  return {
    at: Date.now(),
    agg,
    pingById: new Map(probes.map(({ n, ping, ms }) => [n.id, { ping, ms }])),
    hash,
    selfStatus: await fetchSelfStatus(cfg),
  }
}
