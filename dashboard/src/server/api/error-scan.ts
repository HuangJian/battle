/** error-scan.ts — 错误采集的四条腿（纯采集，不持有登记表状态）+ 合并。
 *
 *  为什么单独一层：登记表（`error-registry.ts`）要管 SWR / 账本 / episode，而**采集**是可
 *  单测的纯 IO 逻辑（喂行 → 出事件）。四条腿（plan §4.2）：
 *    · A 本机组件日志尾（E1）—— 唯一的「本地无聚合」缺口；
 *    · B hub 落下的 `fail.json`（E2 + E3 改道后自动到家）；
 *    · C 云机上报（E4/E5，登记表从 hub ring 拉，见 error-registry）；
 *    · D 节点 `lastError`（E6，读**既有池视图缓存**，不重复扫）。
 *
 *  ★ 三条闸（AGENTS §14：巡检是后台热路径，不能每拍全扫）：
 *    A 每组件日志文件 `size:mtime` 签名（照 `snapshot-refresher.ts:119 harvestLastStat`）——
 *      签名未变 ⇒ **零读盘**，且**复用上一拍的事件**（不是丢弃：丢弃会把「日志没动但有错」
 *      读成「错误消失了」⇒ episode 边界乱跳、ack 反复重弹）；
 *    B 已见 jid 集合——`fail.json` 是首写胜出的一次性文件（`hub/store_results.py:58-79`
 *      两条首写规则），每个 jid **只读一次**；
 *    D 走 `buildPoolView(false)`——`false` = 吃既有缓存，不触发重算。
 */

import { readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { tmpLogsDir } from '../../core/paths'
import type { RlConfig } from '../../core/types'
import {
  ERROR_DETAIL_MAX,
  ERROR_MESSAGE_MAX,
  ERROR_SEVERITY_RANK,
  type ErrorEvent,
  type ErrorSeverity,
} from '../../web/view'
import { parseLogLine } from '../../web/view'
import { errorFingerprint } from './error-fingerprint'
import { ALL_COMPONENTS } from './component-meta'
import { discoverCourses } from './courses'
import { LOG_TAIL_BYTES, readLogTail, resolveComponentLog } from './logs'
import { buildPoolView } from './pool'

/** 巡检窗口的行数上限（256KB 字节窗内的行数；够覆盖任何正常日志的错误段）。 */
const LOG_SCAN_MAX_LINES = 400

const clip = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n)}…` : s)

// ────────────────────────── 腿 A：本机组件日志尾 ──────────────────────────

/** 每个 `课程|组件` 的日志文件签名 + 上一拍的采集结果（见文件头：未变 ⇒ 零读盘 + 复用事件）。 */
const logScanCache = new Map<string, { sig: string; events: ErrorEvent[] }>()

/** 单条错误行的**上下文整块**（plan §4.2 P0-2）。
 *
 *  为什么必须是整块而不是那一行：plan 的触发现场（TrainingLoop 缺 BC 参考 ⇒ boot 崩溃）证据是
 *  **多行 traceback**（`Traceback (most recent call last):` + N 行 `File ...` + 末行异常）——
 *  而那行异常才是 `LOG_ERR_RE` 唯一命中的一行（它含 `Error`）。只搬那一行 = 「只有翻日志才知道
 *  原因」原样保留。
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

export interface LogScope {
  /** 组件键（`Component`）。 */
  component: string
  /** 课程名（该组件日志的归属；共享组件也带上当前扫描课程——`fp` 不含它，见 §4.1）。 */
  scope: string
  /** 跳转锚点（日志页 = 组件键）。 */
  refTarget: string
}

/** 一批日志行 → 错误事件（纯函数：用例直接喂行，不碰盘）。
 *
 *  每个命中行产出一条事件，`fp` 由归一化后的行正文决定 ⇒ 同一错误（行号/iter/耗时不同）落同 fp；
 *  同 fp 在**本窗口内**出现 N 次 ⇒ `count` = N（登记表在 episode 内取 max，拍间不累加）。 */
export function collectLocalLogEvents(lines: string[], ctx: LogScope, now: number): ErrorEvent[] {
  const out: ErrorEvent[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    if (parseLogLine(line).level !== 'error') continue
    const message = clip(line.trim(), ERROR_MESSAGE_MAX)
    out.push({
      fp: errorFingerprint('local-log', ctx.component, message),
      source: 'local-log',
      component: ctx.component,
      scope: ctx.scope,
      severity: 'err',
      firstAt: now,
      lastAt: now,
      count: 1,
      message,
      detail: clip(errorDetailBlock(lines, i), ERROR_DETAIL_MAX),
      ref: { kind: 'log', target: ctx.refTarget },
    })
  }
  return out
}

/** 腿 A：逐课程 × 逐组件扫日志尾（扫描面写死：`discoverCourses() × ALL_COMPONENTS`）。
 *
 *  `resolveComponentLog(key, cfg, course)` 要 **course**（trainer 有每课镜像日志），而
 *  `COMPONENT_LOGS` 虽在 `component-meta.ts` 导出，取路径仍应走 resolver（它会兜底动态文件名
 *  ——cloudflared 每次 spawn 换名、trainer 日志按课落在 `tmp/<课>/`）。 */
export function scanLocalLogEvents(cfg: RlConfig, now: number): ErrorEvent[] {
  const out: ErrorEvent[] = []
  for (const course of discoverCourses()) {
    for (const key of ALL_COMPONENTS) {
      try {
        const abs = resolveComponentLog(key, cfg, course)
        if (!abs) continue
        const sig = `${statSync(abs).size}:${statSync(abs).mtimeMs}`
        const cacheKey = `${course}|${key}`
        const prev = logScanCache.get(cacheKey)
        if (prev && prev.sig === sig) {
          // 零读盘：复用上一拍的事件，只把 `lastAt` 推到本拍（否则会被读成「错误消失了」）。
          for (const e of prev.events) out.push({ ...e, lastAt: now })
          continue
        }
        const { lines } = readLogTail(abs, LOG_SCAN_MAX_LINES, LOG_TAIL_BYTES)
        const events = collectLocalLogEvents(
          lines,
          { component: key, scope: course, refTarget: key },
          now,
        )
        logScanCache.set(cacheKey, { sig, events })
        out.push(...events)
      } catch {
        /* 单组件读失败不连坐（观测面坏掉不该拖垮巡检） */
      }
    }
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

/** 一条 `fail.json` → 错误事件（纯函数；`fail.json` 的元素集 = `hub/result.py:173-178` 那五键）。 */
export function failRecordEvent(
  course: string,
  jobId: string,
  rec: FailRecord,
  now: number,
): ErrorEvent | null {
  const reason = String(rec.reason ?? '').trim()
  if (!reason) return null
  const kind = String(rec.kind ?? '').trim()
  const message = clip(kind ? `[${kind}] ${reason}` : reason, ERROR_MESSAGE_MAX)
  const at = parseTs(rec.ts, now)
  return {
    fp: errorFingerprint('hub-job', 'hub-job', message),
    source: 'hub-job',
    component: 'hub-job',
    scope: course,
    severity: 'err',
    firstAt: at,
    lastAt: at,
    count: 1,
    message,
    detail: clip(String(rec.detail ?? ''), ERROR_DETAIL_MAX),
    ref: { kind: 'job', target: jobId },
  }
}

/** 腿 B：扫 `<traj>/<课>/remote-jobs/<jid>/fail.json`（与 `ppo-queue.ts:82-90` 同一棵树）。 */
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
      let rec: FailRecord
      try {
        rec = JSON.parse(readFileSync(path.join(root, jid, 'fail.json'), 'utf8')) as FailRecord
      } catch {
        continue // 没有 fail.json（或半截写入）⇒ 不是失败记录，下一拍再说
      }
      seenFailJobs.add(key) // 首写胜出 ⇒ 只读一次
      const ev = failRecordEvent(course, jid, rec, now)
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
