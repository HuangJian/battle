/** error-registry.ts — 组件错误**登记表**（G1/G5 + 腿 C）：SWR 状态 + 账本 + episode 去重。
 *
 *  三件事各有唯一主人（plan §4.4）：
 *    · **episode 身份**（`firstAt`）住**内存**、可重置 —— 某拍没扫到该 `fp` ⇒ episode 结束；
 *      下次重现 ⇒ 新的 `firstAt` ⇒ **必重弹**（一次 ack 不会把复发吃掉，§4.7）；
 *    · **账本**（`errorLedgerPath()`）只记「这个 `fp` 我见过」（`firstSeenAt` **不可变**）——
 *      重启后重放它当**种子**，不重放 `firstAt`（代价：重启后未消失的错误会重弹一次，显式接受）；
 *    · **`count`**：episode 内取 `max`（腿 A 每拍读到的是**窗口内绝对读数**而不是增量 ——
 *      累加会在「文件没变」时虚涨；窗口轮转/截断只会低估）。
 *
 *  ★ 别的模块**只读** `getErrorRegistry()` / `getErrorSummary()` 的缓存值：`/api/state` 不许
 *  在这里触发探测（N9 / R1 结构闸，`state-view.ts:132-135` 同规）。唯一的探测点是
 *  `refreshErrorRegistry()`，由 `snapshot-refresher` 的**独立 10s 节拍**调用。
 */

import { appendFileSync, mkdirSync, readFileSync } from 'fs'
import path from 'path'
import { errorLedgerPath } from '../../core/paths'
import {
  ERROR_DETAIL_MAX,
  ERROR_MESSAGE_MAX,
  ERROR_SUMMARY_MAX,
  type ErrorEvent,
  type ErrorListItem,
  type ErrorSummaryView,
  type WorkerErrorReport,
} from '../../web/view'
import { loadConsoleState } from '../actions'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { errorFingerprint } from './error-fingerprint'
import {
  foldErrorEvents,
  normalizeSeverity,
  resetErrorScanCaches,
  scanHubJobEvents,
  scanLocalLogEvents,
  scanNodeEvents,
} from './error-scan'
import { hubWorkerErrors } from '../../stack/hub-admin'

/** 巡检节拍：比慢快照 5s 慢、比逐轮抄录 15s 快（plan §4.2）。 */
export const ERROR_SCAN_INTERVAL_MS = 10_000

/** hub 基址/凭据的记忆时长：腿 C 每拍问一次 hub，但**不必**每拍重解候选（4 个候选各 1s 超时）。
 *  与 `getHubAdmin` 的 5s 节拍同一个 hub，不需要更短。 */
const HUB_BASE_TTL_MS = 60_000

interface Episode {
  /** **本 episode** 首次出现（可重置；ack 身份的一部分）。 */
  firstAt: number
  /** episode 内累计次数（取 max，不跨拍累加）。 */
  count: number
}

export interface RegistryState {
  events: ErrorEvent[]
  episodes: Map<string, Episode>
  /** 见过的 `fp`（账本重放 + 本进程新见）——**只用于「账本要不要 append」**。 */
  known: Set<string>
}

const EMPTY_STATE: RegistryState = { events: [], episodes: new Map(), known: new Set() }

// ────────────────────────── 账本（G5） ──────────────────────────

/** 追加一行（**新 `fp` 首次出现时**唯一调用点）。失败即丢：账本是观测面，写不进去不该拖垮巡检
 *  （磁盘满/权限问题时 `tmp/` 里别的东西更该先报）。 */
export function appendErrorLedger(ev: ErrorEvent, firstSeenAt: number): void {
  try {
    const p = errorLedgerPath()
    mkdirSync(path.dirname(p), { recursive: true })
    const row = {
      fp: ev.fp,
      source: ev.source,
      component: ev.component,
      scope: ev.scope,
      severity: ev.severity,
      message: ev.message,
      detail: ev.detail,
      ref: ev.ref,
      firstSeenAt,
    }
    appendFileSync(p, `${JSON.stringify(row)}\n`, 'utf8')
  } catch {
    /* 观测面写失败不抛 */
  }
}

/** 重放账本 → 已知 `fp` 集合（**只是种子**：`firstAt`/`count` 一概不继承）。 */
export function replayErrorLedger(): Set<string> {
  const out = new Set<string>()
  try {
    const lines = readFileSync(errorLedgerPath(), 'utf8').split('\n')
    // 有界读：账本按**错误种类数**增长（每次重复不写行），这里再兜一层防御（只取末尾 2000 行）。
    for (const line of lines.slice(-2000)) {
      if (!line.trim()) continue
      try {
        const rec = JSON.parse(line) as { fp?: unknown }
        if (typeof rec.fp === 'string' && rec.fp) out.add(rec.fp)
      } catch {
        /* 半截行跳过 */
      }
    }
  } catch {
    /* 没有账本 = 全部都没见过 */
  }
  return out
}

// ────────────────────────── episode（G8） ──────────────────────────

/** 一拍 → 下一份状态（**纯函数**：账本副作用在 `commitSweep` 里，用例可以只测这一层）。
 *
 *  消失即结束：`sweep` 里没有的 `fp` **不进**新状态（episode 清掉）；下次再来 ⇒ `firstAt` = 当时的
 *  `now` ⇒ ack 键变 ⇒ 重弹。这正是「持续错误不重复打断、复发必重弹」的判据。 */
export function applySweep(
  prev: RegistryState,
  sweep: ErrorEvent[],
  now: number,
): { state: RegistryState; fresh: ErrorEvent[] } {
  const episodes = new Map<string, Episode>()
  const known = new Set(prev.known)
  const fresh: ErrorEvent[] = []
  const events: ErrorEvent[] = []
  for (const e of sweep) {
    const prevEp = prev.episodes.get(e.fp)
    const firstAt = prevEp?.firstAt ?? now
    const count = Math.max(prevEp?.count ?? 0, e.count)
    episodes.set(e.fp, { firstAt, count })
    const item: ErrorEvent = { ...e, firstAt, count }
    events.push(item)
    if (!known.has(e.fp)) {
      known.add(e.fp)
      fresh.push(item)
    }
  }
  return { state: { events, episodes, known }, fresh }
}

let state: RegistryState = EMPTY_STATE
let ledgerSeeded = false
let lastRefreshAt = 0

/** 本进程的第一拍要把账本重放当种子（否则重启后每个仍存在的错误都会再 append 一行）。 */
function ensureSeeded(): void {
  if (ledgerSeeded) return
  state = { ...state, known: replayErrorLedger() }
  ledgerSeeded = true
}

/** 应用一拍并把**新 `fp`** 落进账本（登记表的唯一写点）。 */
export function commitSweep(sweep: ErrorEvent[], now: number): ErrorEvent[] {
  ensureSeeded()
  const { state: next, fresh } = applySweep(state, sweep, now)
  for (const ev of fresh) appendErrorLedger(ev, now)
  state = next
  return state.events
}

// ────────────────────────── 腿 C：云机上报（E4/E5） ──────────────────────────

/** hub 基址/凭据记忆（见 `HUB_BASE_TTL_MS`）。 */
let hubBase: { url: string; token: string; at: number } | null = null
/** `?since=` 游标（秒）——只往前走；hub 侧按 TTL 过滤（`WORKER_ERROR_TTL_SEC`）。 */
let hubCursor = 0

/** 一条上报体 → 错误事件（**纯函数**；缺 `reason`/`worker` ⇒ null，不编事实）。
 *
 *  `scope` = 课程名（有则用），`ref` = job 锚点（有 job_id 用它，否则退回 worker 名）。
 *  `severity` 只认两档（云端可能塞别的词）⇒ `normalizeSeverity`。 */
export function workerReportEvent(rec: WorkerErrorReport, now: number): ErrorEvent | null {
  const reason = String(rec.reason ?? '').trim()
  const worker = String(rec.worker ?? '').trim()
  if (!reason || !worker) return null
  const component = String(rec.component ?? '').trim() || 'ppo-worker'
  const scope = String(rec.course ?? '').trim()
  const jobId = String(rec.job_id ?? '').trim()
  const at = typeof rec.ts === 'number' && Number.isFinite(rec.ts) ? rec.ts * 1000 : now
  const message =
    reason.length > ERROR_MESSAGE_MAX ? `${reason.slice(0, ERROR_MESSAGE_MAX)}…` : reason
  return {
    fp: errorFingerprint('worker-report', component, message),
    source: 'worker-report',
    component,
    scope,
    severity: normalizeSeverity(rec.severity),
    firstAt: at,
    lastAt: at,
    count: 1,
    message,
    detail: String(rec.detail ?? '').slice(0, ERROR_DETAIL_MAX),
    ref: { kind: 'job', target: jobId || worker },
  }
}

/** 腿 C：从 hub 拉增量上报（hub 的 ring 是中继，权威账本在控制台 —— §4.4）。 */
async function fetchWorkerReports(now: number): Promise<ErrorEvent[]> {
  const cfg = loadConfigSafe()
  if (now - (hubBase?.at ?? 0) > HUB_BASE_TTL_MS) {
    hubBase = null
    try {
      const url = await liveHubUrl(cfg)
      if (url) hubBase = { url, token: String(cfg.rl?.remote_token ?? ''), at: now }
    } catch {
      hubBase = null
    }
  }
  if (!hubBase) return []
  const reports = await hubWorkerErrors(hubBase.url, hubBase.token, hubCursor)
  if (!reports) return []
  const out: ErrorEvent[] = []
  for (const rec of reports) {
    const ev = workerReportEvent(rec, now)
    if (ev) out.push(ev)
    const ts = typeof rec.ts === 'number' && Number.isFinite(rec.ts) ? rec.ts : 0
    if (ts > hubCursor) hubCursor = ts
  }
  return out
}

/** 惰性 import `liveHub`：把「哪台 hub」的解析集中到一处（候选清单来自账本 + 槽位算术，
 *  见 `stack/hub-admin.ts` 头注）。惰性是为了**只为这一条腿**把那份候选链拉进来（登记表在
 *  服务端启动路径上，早 import 会让「没有 hub 的机器」也付这份解析）。 */
async function liveHubUrl(cfg: ReturnType<typeof loadConfigSafe>): Promise<string | null> {
  const { liveHub } = await import('../../stack/hub-admin')
  const course = effectiveCourse(loadConsoleState(), discoverCourses())
  const live = await liveHub(cfg, course)
  return live?.url ?? null
}

// ────────────────────────── 巡检（唯一探测点） ──────────────────────────

let inflight: Promise<void> | null = null

/** 巡检一拍（单飞：重入直接复用同一次；单腿失败不连坐）。 */
export function refreshErrorRegistry(now: number = Date.now()): Promise<void> {
  if (inflight) return inflight
  inflight = (async () => {
    const cfg = loadConfigSafe()
    const legs: ErrorEvent[] = []
    try {
      legs.push(...scanLocalLogEvents(cfg, now))
    } catch {
      /* 腿 A 失败 */
    }
    try {
      legs.push(...scanHubJobEvents(now))
    } catch {
      /* 腿 B 失败 */
    }
    try {
      legs.push(...(await fetchWorkerReports(now)))
    } catch {
      /* 腿 C 失败（hub 不可达 = 常态） */
    }
    try {
      legs.push(...(await scanNodeEvents(now)))
    } catch {
      /* 腿 D 失败 */
    }
    commitSweep(foldErrorEvents(legs), now)
    lastRefreshAt = now
  })().finally(() => {
    inflight = null
  })
  return inflight
}

// ────────────────────────── 只读面 ──────────────────────────

/** 登记表当前值（**只读缓存**；不触发任何探测 —— N9/R1）。 */
export function getErrorRegistry(): ErrorEvent[] {
  if (!ledgerSeeded) ensureSeeded()
  return state.events
}

/** `GET /api/errors?fp=` 的唯一实现（detail 懒取）：只读缓存，绝不触发探测。
 *
 *  找不到 ⇒ `null`（**不编事实**）：登记表每拍按「本拍扫到了什么」重建，被清掉/换了 episode 的
 *  `fp` 会真的消失 —— 那时页面该说「已经被清理」而不是拿一份旧 traceback 冒充当下。 */
export function findErrorEvent(fp: string): ErrorEvent | null {
  return getErrorRegistry().find((e) => e.fp === fp) ?? null
}

/** 汇总投影（`/api/state` 与 `/api/errors` 共用同一份；两处各排一次 = 迟早漂）。 */
export function buildErrorSummary(
  events: ErrorEvent[],
  max: number = ERROR_SUMMARY_MAX,
): ErrorSummaryView {
  const items: ErrorListItem[] = events.slice(0, max).map((e) => ({
    fp: e.fp,
    source: e.source,
    component: e.component,
    scope: e.scope,
    severity: e.severity,
    firstAt: e.firstAt,
    lastAt: e.lastAt,
    count: e.count,
    message: e.message,
    ref: e.ref,
  }))
  return { items, truncated: events.length > items.length }
}

export function getErrorSummary(): ErrorSummaryView {
  return buildErrorSummary(getErrorRegistry())
}

/** 上一次巡拍的落定时刻（0 = 还没跑过 ⇒ UI 不谎报「已检查」）。 */
export function errorRegistryScannedAt(): number {
  return lastRefreshAt
}

/** 用例隔离：清空登记表 + 采集缓存 + hub 记忆（**不**动账本文件）。 */
export function resetErrorRegistry(): void {
  state = EMPTY_STATE
  ledgerSeeded = false
  lastRefreshAt = 0
  hubBase = null
  hubCursor = 0
  resetErrorScanCaches()
}
