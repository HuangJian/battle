/** error-scan.ts — 错误采集的四条腿（纯采集，不持有登记表状态）+ 合并。
 *
 *  为什么单独一层：登记表（`error-registry.ts`）要管 SWR / 账本 / episode，而**采集**是可
 *  单测的纯 IO 逻辑（喂行 → 出事件）。四条腿（plan §4.2）：
 *    · A 本机组件日志尾（E1）—— 唯一的「本地无聚合」缺口；
 *    · B hub 落下的 `fail.json`（E2 + E3 改道后自动到家）；
 *    · C 云机上报（E4/E5，登记表从 hub ring 拉，见 error-registry）；
 *    · D 节点 `lastError`（E6，读**既有池视图缓存**，不重复扫）。
 *
 *  ★ 2026-10-19 评审后的两条硬约束（plan v2 D2/D4）：
 *    1. **全局组件只扫一次**：`selfNode`/`hubServer`/`localWorker`/`cloudflared` 的日志路径
 *       与课程无关（`component-meta.ts`），旧实现把它们放进 `for (course) { for (component) }`
 *       里 ⇒ 同一个文件被读 185 遍、事件数组以 185 个 `课程|组件` 键各存一份（2026-10-17 的
 *       400MB/958MB 事故）。现在缓存键 = `组件|绝对路径`，全局腿按组件扫一次。
 *    2. **开课闸不再当作采集前置**：错误与课程解耦（S3）。历史课的旧日志照常采，但按文件
 *       mtime 标 `activity:'historical'`，默认视图与告警坞不把它当「此刻活跃」。
 *
 *  ★ 三条闸（AGENTS §14：巡检是后台热路径）：文件 `size:mtime` 签名未变 ⇒ 零读盘 + 复用
 *  上一拍事件；`fail.json` 首写胜出 ⇒ 每 jid 只读一次；节点腿吃既有池视图缓存。
 */

import { readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { tmpLogsDir } from '../../core/paths'
import type { RlConfig } from '../../core/types'
import {
  ERROR_DETAIL_MAX,
  ERROR_MESSAGE_MAX,
  ERROR_SEVERITY_RANK,
  type ErrorActivity,
  type ErrorEvent,
  type ErrorSeverity,
} from '../../web/view'
import { parseLogLine } from '../../web/view'
import { classifyError } from './error-classify'
import { errorFingerprint } from './error-fingerprint'
import { ALL_COMPONENTS, COMPONENT_LOGS } from './component-meta'
import { discoverCourses } from './courses'
import { LOG_TAIL_BYTES, readLogTail, resolveComponentLog } from './logs'
import { buildPoolView } from './pool'

/** 巡检窗口的行数上限（256KB 字节窗内的行数；够覆盖任何正常日志的错误段）。 */
const LOG_SCAN_MAX_LINES = 400

/** 单文件最多产出多少个**不同** `fp`：超出后只保留前 N 类（同 fp 的重复行仍计次）。
 *  这是去开课闸后的内存上界（185 门 × 每文件 20 条 = 有界），也是 2026-10-17 事故的直接防线。 */
const MAX_FPS_PER_FILE = 20

/** 源文件静默超过这个时长 ⇒ 事件是历史（不是「此刻正在发生」）。 */
export const HISTORICAL_AFTER_MS = 24 * 60 * 60 * 1000

/** 全局组件（路径与课程无关，扫一次即可）。`trainingLoop` 是唯一按课解析的组件。 */
const GLOBAL_COMPONENTS = ALL_COMPONENTS.filter((k) => k !== 'trainingLoop')

const clip = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n)}…` : s)

// ────────────────────────── 腿 A：本机组件日志尾 ──────────────────────────

/** 每个 `组件|绝对路径` 的日志文件签名 + 上一拍的采集结果（未变 ⇒ 零读盘 + 复用事件）。 */
const logScanCache = new Map<string, { sig: string; events: ErrorEvent[] }>()

/** 单条错误行的**上下文整块**（plan §4.2 P0-2）。
 *
 *  块边界 = **带 `[HH:MM:SS]` 前缀的行**（`parseLogLine().ts !== null`）：向上扫到上一个带前缀的
 *  行、向下扫到下一个带前缀的行 —— 于是 `Traceback` 头与全部 `File` 行都在块里。
 *  截断：整块 > `ERROR_DETAIL_MAX` ⇒ 取块首 20 行 + `…（已截断）`（保头，因为原因在头部）。 */
export function errorDetailBlock(lines: string[], index: number): string {
  const hasTs = (l: string): boolean => parseLogLine(l).ts !== null
  let start = index
  while (start > 0 && !hasTs(lines[start - 1]!)) start--
  let end = index + 1
  while (end < lines.length && !hasTs(lines[end]!)) end++
  const block = lines.slice(start, end).join('\n')
  if (block.length <= ERROR_DETAIL_MAX) return block
  return `${lines.slice(start, start + 20).join('\n')}\n…（已截断）`
}

/** traceback 续行：**不单独成事件**（M3/M4），但仍落在命中行的 detail 块里。
 *
 *  ★ 只认确定的续行形状，不按「缩进行」一刀切（评审 F5）：多行 JSON / 包裹日志里的真实
 *  错误也可能带缩进。`File "…"` 今天本来就不是 error 级别，列在这里只是防御
 *  （路径里带 `error` 的源文件名会被关键词误判）。 */
const TRACEBACK_CONTINUATION_RE =
  /^\s*(?:Traceback \(most recent call last\):|File "|During handling of the above exception|The above exception was the direct cause|Exception occurred during processing of request)/

export function isTracebackContinuation(line: string): boolean {
  return parseLogLine(line).ts === null && TRACEBACK_CONTINUATION_RE.test(line)
}

export interface LogScope {
  /** 组件键（`Component`）。 */
  component: string
  /** 课程名（全局组件为空串；训练组件 = 该课）。**不进 `fp`**。 */
  scope: string
  /** 跳转锚点（日志页 = 组件键）。 */
  refTarget: string
  /** 活动度：源文件静默 > 24h ⇒ historical（由扫描腿按 mtime 判定）。 */
  activity?: ErrorActivity
  /** 事件时间（historical 用文件 mtime，active 用本拍 `now`）。 */
  at?: number
}

/** 一批日志行 → 错误事件（纯函数：用例直接喂行，不碰盘）。
 *
 *  - 访问日志的 2xx/3xx/4xx 由 `parseLogLine` 判为 info（不产事件），5xx 判为 error
 *    ⇒ 自然产出 `remote-failure` 事件（plan D4）；
 *  - traceback 续行跳过，detail 仍由 `errorDetailBlock` 并入命中行；
 *  - 每个文件最多 `MAX_FPS_PER_FILE` 个不同 `fp`（同 fp 的重复行继续计次）。 */
export function collectLocalLogEvents(lines: string[], ctx: LogScope, now: number): ErrorEvent[] {
  const out: ErrorEvent[] = []
  const seenFps = new Set<string>()
  const at = ctx.at ?? now
  const activity = ctx.activity ?? 'active'
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    if (isTracebackContinuation(line)) continue
    if (parseLogLine(line).level !== 'error') continue
    const message = clip(line.trim(), ERROR_MESSAGE_MAX)
    const detail = clip(errorDetailBlock(lines, i), ERROR_DETAIL_MAX)
    const fp = errorFingerprint('local-log', ctx.component, message)
    if (!seenFps.has(fp) && seenFps.size >= MAX_FPS_PER_FILE) break
    seenFps.add(fp)
    out.push({
      fp,
      source: 'local-log',
      component: ctx.component,
      scope: ctx.scope,
      severity: 'err',
      classification: classifyError({
        source: 'local-log',
        component: ctx.component,
        message,
        detail,
      }),
      activity,
      firstAt: at,
      lastAt: at,
      count: 1,
      message,
      detail,
      ref: { kind: 'log', target: ctx.refTarget },
    })
  }
  return out
}

/** 扫一份日志文件（签名未变 ⇒ 复用；变了 ⇒ 读尾窗）。 */
function scanLogFile(
  abs: string,
  component: string,
  scope: string,
  refTarget: string,
  now: number,
  out: ErrorEvent[],
  scanned: Set<string>,
): void {
  const st = statSync(abs)
  const key = `${component}|${abs}`
  scanned.add(key)
  const sig = `${st.size}:${st.mtimeMs}`
  const activity: ErrorActivity = now - st.mtimeMs > HISTORICAL_AFTER_MS ? 'historical' : 'active'
  const at = activity === 'historical' ? st.mtimeMs : now
  const prev = logScanCache.get(key)
  if (prev && prev.sig === sig) {
    // 零读盘：复用上一拍事件；active 的 `lastAt` 推到本拍，historical 保持源时刻。
    for (const e of prev.events) {
      out.push(e.activity === 'active' ? { ...e, lastAt: now } : e)
    }
    return
  }
  const { lines } = readLogTail(abs, LOG_SCAN_MAX_LINES, LOG_TAIL_BYTES, 'all')
  const events = collectLocalLogEvents(lines, { component, scope, refTarget, activity, at }, now)
  logScanCache.set(key, { sig, events })
  out.push(...events)
}

/** 腿 A：全局组件各扫一次 + 每门课的 trainer 日志（**无开课闸**，plan D2）。
 *
 *  `trainingLoop` 直接取 `COMPONENT_LOGS` 的静态路径（不调用 `resolveComponentLog` 的动态
 *  兜底——那会对每一门缺日志的课 `readdirSync(tmp)`，去闸后是 O(课数²)/拍）。 */
export function scanLocalLogEvents(cfg: RlConfig, now: number): ErrorEvent[] {
  const out: ErrorEvent[] = []
  const scanned = new Set<string>()
  for (const key of GLOBAL_COMPONENTS) {
    try {
      const abs = resolveComponentLog(key, cfg, '')
      if (abs) scanLogFile(abs, key, '', key, now, out, scanned)
    } catch {
      /* 单组件读失败不连坐 */
    }
  }
  for (const course of discoverCourses()) {
    try {
      const abs = COMPONENT_LOGS.trainingLoop?.(cfg, course)
      if (abs) scanLogFile(abs, 'trainingLoop', course, 'trainingLoop', now, out, scanned)
    } catch {
      /* 该课没有日志 */
    }
  }
  // 扫描面淘汰：不在本拍扫描集里的键直接删（课程被清理 / 文件消失 ⇒ 事件不常驻）。
  for (const key of logScanCache.keys()) {
    if (!scanned.has(key)) logScanCache.delete(key)
  }
  return out
}

// ────────────────────────── 腿 B：hub 落下的 fail.json ──────────────────────────

/** 已读过的 `<课>|<jid>`（`fail.json` 终局一次写定 ⇒ 只读一次，不重扫）。 */
const seenFailJobs = new Set<string>()

/** hub 的 `ts` 可能是 epoch 秒/毫秒，也可能是 ISO 串（`hub._now()` 两种历史形态都出现过）——
 *  解析不出来就退回本拍时刻（绝不编一个未来/过去的时间）。 */
function parseTs(v: unknown, fallback: number): number {
  if (typeof v === 'number' && Number.isFinite(v)) return v < 1e12 ? Math.round(v * 1000) : v
  if (typeof v === 'string' && v) {
    const t = Date.parse(v)
    if (Number.isFinite(t)) return t
  }
  return fallback
}

export interface FailRecord {
  reason: string
  kind?: unknown
  detail?: unknown
  ts?: unknown
}

/** 一条 `fail.json` → 错误事件（纯函数；`fail.json` 的元素集 = `hub/result.py:173-178` 那五键）。
 *
 *  `meta` 由扫描腿传入（按文件 mtime 判 historical）；纯函数调用缺省为 active/now。 */
export function failRecordEvent(
  course: string,
  jobId: string,
  rec: FailRecord,
  now: number,
  meta?: { activity: ErrorActivity; at: number },
): ErrorEvent | null {
  const reason = String(rec.reason ?? '').trim()
  if (!reason) return null
  const kind = String(rec.kind ?? '').trim()
  const message = clip(kind ? `[${kind}] ${reason}` : reason, ERROR_MESSAGE_MAX)
  const detail = clip(String(rec.detail ?? ''), ERROR_DETAIL_MAX)
  const parsed = parseTs(rec.ts, now)
  const at = meta?.at ?? parsed
  return {
    fp: errorFingerprint('hub-job', 'hub-job', message),
    source: 'hub-job',
    component: 'hub-job',
    scope: course,
    severity: 'err',
    classification: classifyError({ source: 'hub-job', component: 'hub-job', message, detail }),
    activity: meta?.activity ?? 'active',
    firstAt: at,
    lastAt: at,
    count: 1,
    message,
    detail,
    ref: { kind: 'job', target: jobId },
  }
}

/** 腿 B：扫 `<traj>/<课>/remote-jobs/<jid>/fail.json`（与 `ppo-queue.ts:82-90` 同一棵树）。
 *
 *  **无开课闸**：历史课的失败记录也可见（S3）；按 `fail.json` mtime 标 historical。 */
export function scanHubJobEvents(now: number): ErrorEvent[] {
  const out: ErrorEvent[] = []
  for (const course of discoverCourses()) {
    const root = path.join(tmpLogsDir(), course, 'remote-jobs')
    let names: string[]
    try {
      names = readdirSync(root, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => d.name)
    } catch {
      continue
    }
    for (const jid of names) {
      const key = `${course}|${jid}`
      if (seenFailJobs.has(key)) continue
      const p = path.join(root, jid, 'fail.json')
      let rec: FailRecord
      let mtime = now
      try {
        rec = JSON.parse(readFileSync(p, 'utf8')) as FailRecord
        mtime = statSync(p).mtimeMs
      } catch {
        continue // 没有 fail.json（或半截写入）⇒ 不是失败记录，下一拍再说
      }
      seenFailJobs.add(key) // 首写胜出 ⇒ 只读一次
      const activity: ErrorActivity = now - mtime > HISTORICAL_AFTER_MS ? 'historical' : 'active'
      const ev = failRecordEvent(course, jid, rec, now, {
        activity,
        at: activity === 'historical' ? mtime : now,
      })
      if (ev) out.push(ev)
    }
  }
  return out
}

// ────────────────────────── 腿 D：节点 lastError ──────────────────────────

/** 腿 D：节点行上的 `lastError`（`pool-history.ts:565-582` 的「近一小时最近错误」）。
 *
 *  `buildPoolView(false)` = **吃既有探测缓存**（R1 结构闸同向：没有第二个缓存层，也不在这里
 *  裸调聚合）。池视图不可用 ⇒ 空腿（不编事实）。 */
export async function scanNodeEvents(now: number): Promise<ErrorEvent[]> {
  const out: ErrorEvent[] = []
  let nodes
  try {
    const view = await buildPoolView(false, 'today')
    nodes = view.nodes
  } catch {
    return out
  }
  for (const row of nodes) {
    const err = (row.lastError ?? '').trim()
    if (!err) continue
    const message = clip(err, ERROR_MESSAGE_MAX)
    out.push({
      fp: errorFingerprint('node', 'node', message),
      source: 'node',
      component: 'node',
      scope: row.id,
      severity: 'err',
      classification: classifyError({ source: 'node', component: 'node', message }),
      activity: 'active',
      firstAt: now,
      lastAt: now,
      count: 1,
      message,
      detail: '',
      ref: { kind: 'node', target: row.id },
    })
  }
  return out
}

// ────────────────────────── 合并 ──────────────────────────

/** 四腿 → 按 `fp` 合并：`firstAt` 最小 · `lastAt` 最大 · `count` 求和 · `message/detail` 取**最新现场**。
 *
 *  `count` 的「拍内求和」与「episode 内取 max」是两件事：前者是这一拍各腿读数之和（同一 fp 可能
 *  同时来自日志与上报），后者在 `error-registry.ts` 里做（见那里的注释：尾巴窗口读的是**绝对读数**
 *  而不是增量，拍间累加会虚涨）。 */
export function foldErrorEvents(list: ErrorEvent[]): ErrorEvent[] {
  const byFp = new Map<string, ErrorEvent>()
  for (const e of list) {
    const prev = byFp.get(e.fp)
    if (!prev) {
      byFp.set(e.fp, { ...e })
      continue
    }
    const newest = e.lastAt >= prev.lastAt ? e : prev
    byFp.set(e.fp, {
      ...prev,
      scope: newest.scope,
      severity: prev.severity === 'err' || e.severity === 'err' ? 'err' : 'warn',
      classification: newest.classification,
      activity: prev.activity === 'active' || e.activity === 'active' ? 'active' : 'historical',
      firstAt: Math.min(prev.firstAt, e.firstAt),
      lastAt: Math.max(prev.lastAt, e.lastAt),
      count: prev.count + e.count,
      message: newest.message,
      detail: newest.detail || prev.detail,
      ref: newest.ref,
    })
  }
  return sortErrorEvents([...byFp.values()])
}

/** 排序：严重度（`err` 在前）→ `lastAt` 新在前。列表与汇总投影共用同一序（两处各排一次 = 迟早漂）。 */
export function sortErrorEvents(list: ErrorEvent[]): ErrorEvent[] {
  return list.sort(
    (a, b) =>
      ERROR_SEVERITY_RANK[a.severity] - ERROR_SEVERITY_RANK[b.severity] || b.lastAt - a.lastAt,
  )
}

/** 严重度归一（云机上报的 `severity` 只认 `warn`/`err` 两档；其余取值 ⇒ `warn`）。 */
export function normalizeSeverity(v: unknown): ErrorSeverity {
  return v === 'err' ? 'err' : 'warn'
}

/** 清空本模块的进程内记忆（腿 A 签名缓存 + 腿 B 已见 jid）——**仅供用例**隔离夹具用。 */
export function resetErrorScanCaches(): void {
  logScanCache.clear()
  seenFailJobs.clear()
}
