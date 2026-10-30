/**
 * schedule.ts — 定时任务的纯函数（W4）：不做计时，只回答「下一次/欠不欠」。
 *
 * 时间口径 = 注入的 `LocalParts`（默认本地时区）。DST 用真实扫描分钟处理：
 * 跳过的钟点当天不存在 ⇒ 自然顺延到下一天；回拨的钟点出现两次 ⇒ 同一个
 * occurrenceKey 只欠一次（lastFiredKey 去重）。不在代码里写死时区偏移。
 */
export interface DailyJob {
  id: string
  /** `HH:MM`（24h）。 */
  at: string
  /** 0=周日 … 6=周六；null = 每天。 */
  days: number[] | null
  /** 错过的点是否补跑（默认 false：只在 ±2 分钟内自动跑）。 */
  catchUp: boolean
  enabled: boolean
}

export interface LocalParts {
  /** YYYY-MM-DD。 */
  date: string
  /** HH:MM。 */
  time: string
  /** 0=周日 … 6=周六。 */
  weekday: number
}

export type PartsFn = (ms: number) => LocalParts

const pad2 = (n: number): string => String(n).padStart(2, '0')

/** 默认实现：本地时区（浏览器里就是操作员看到的钟）。 */
export function defaultParts(ms: number): LocalParts {
  const d = new Date(ms)
  return {
    date: `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`,
    time: `${pad2(d.getHours())}:${pad2(d.getMinutes())}`,
    weekday: d.getDay(),
  }
}

export function parseAt(at: string): { hh: number; mm: number } | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(at.trim())
  if (!m) return null
  const hh = Number(m[1])
  const mm = Number(m[2])
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null
  return { hh, mm }
}

export function normalizeAt(at: string): string | null {
  const p = parseAt(at)
  return p ? `${pad2(p.hh)}:${pad2(p.mm)}` : null
}

export function occurrenceKey(parts: LocalParts): string {
  return `${parts.date}@${parts.time}`
}

function dayAllowed(job: DailyJob, weekday: number): boolean {
  return job.days === null || job.days.includes(weekday)
}

const MINUTE = 60_000
const LOOKAHEAD_MIN = 8 * 24 * 60
const LOOKBACK_MIN = 36 * 60

/**
 * 下一次触发（毫秒）。找不到（禁用/非法/8 天内都不匹配）⇒ null。
 * 扫描从「下一分钟」开始：`now` 恰好是该分钟也不算下一次。
 */
export function nextFireAt(
  job: DailyJob,
  nowMs: number,
  parts: PartsFn = defaultParts,
): number | null {
  const at = normalizeAt(job.at)
  if (!at || !job.enabled) return null
  const start = Math.floor(nowMs / MINUTE) * MINUTE
  for (let i = 1; i <= LOOKAHEAD_MIN; i++) {
    const ms = start + i * MINUTE
    const p = parts(ms)
    if (p.time === at && dayAllowed(job, p.weekday)) return ms
  }
  return null
}

/**
 * 当前「欠不欠」：返回今天的触发点与去重键；已跑过 ⇒ null。
 * `catchUp=false` 时，错过超过 2 分钟不再补（避免半夜开机一次性狂跑）。
 */
export function dueOccurrence(
  job: DailyJob,
  nowMs: number,
  lastFiredKey: string | null,
  parts: PartsFn = defaultParts,
): { key: string; at: number } | null {
  const at = normalizeAt(job.at)
  if (!at || !job.enabled) return null
  const start = Math.floor(nowMs / MINUTE) * MINUTE
  for (let i = 0; i <= LOOKBACK_MIN; i++) {
    const ms = start - i * MINUTE
    const p = parts(ms)
    if (p.time !== at || !dayAllowed(job, p.weekday)) continue
    const key = occurrenceKey(p)
    if (key === lastFiredKey) return null
    if (!job.catchUp && nowMs - ms > 2 * MINUTE) return null
    return { key, at: ms }
  }
  return null
}
