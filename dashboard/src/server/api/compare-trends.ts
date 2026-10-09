/** compare-trends.ts — 「比较课程」跨课程指标端点（GET /api/compareTrends 的数据层）。
 *
 *  为什么要新端点（不是往 /api/state 里塞）：`/api/state` 的 metrics 只算**当前查看课程**
 *  一门课（`state-view.ts::buildStateView`），而比较图要 N 门课；塞进 state 会让每一次
 *  3s 轮询都为「可能没人在看的弹窗」读 N 份账本（plan/dashboard-compare-trends.plan.md §1.2-①）。
 *
 *  成本纪律（§3.10）：
 *    · 课程级缓存（TTL 10s + 账本 `mtimeMs:size` 指纹）——整表缓存 `metricSeries` 的 19 条序列，
 *      换指标/口径/起止都**不打盘**；
 *    · 请求级过滤永远现算（缓存只吃「读账本」这一层）；
 *    · 读账本走 `tmpLogsDir()`（惰性 env 重定向）——单测能造夹具，且这是 `paths.ts` 的路径纪律。
 */

import { existsSync, statSync } from 'fs'
import path from 'path'
import { tmpLogsDir } from '../../core/paths'
import {
  COMPARE_METRIC_SPECS,
  COMPARE_MAX_COURSES,
  isCompareMetric,
  isTrendSource,
  metricSeries,
  parseCompareCourses,
  parseIterRange,
  sliceIterRange,
  type CompareCourseSeries,
  type CompareMetric,
  type CompareSeriesData,
  type CompareTrendsView,
  type CompareUnavailable,
  type IterRow,
  type Series,
  type TrendSource,
} from '../../web/view'
import { readIterMetrics } from '../iters'
import { sanitizeViewCourse } from './courses'

export interface CompareQuery {
  /** 已过 `sanitizeViewCourse`（真实存在 + 形态合法）、已去重、已截断到上限。 */
  courses: string[]
  metric: CompareMetric
  source: TrendSource
  from: number | null
  to: number | null
}

export interface CompareQueryRaw {
  courses?: string | null
  metric?: string | null
  source?: string | null
  from?: string | null
  to?: string | null
}

/** 解析 + 校验（路由层用；单测可直接打它，不架 HTTP）。
 *
 *  语义（plan §3.9）：
 *    · `metric` / `source` 缺席 ⇒ 缺省 `winRate` / `all`（手拼 URL 友好）；**给了但非法 ⇒ 400**；
 *    · `from`/`to` 空串 = 全量；非负整数；`from > to` 非法；
 *    · `courses` 逐个 `sanitizeViewCourse`（形态 + 盘上存在性）：剔除非法/不存在的，
 *      剔完为空 ⇒ 400（**不静默回退**——静默回退会让人以为自己在比 A，实际在看 B）；
 *      超过 8 门取前 8（上限是 UI 约束，不是错误）。
 */
export function parseCompareQuery(
  raw: CompareQueryRaw,
): { ok: true; params: CompareQuery } | { ok: false; message: string } {
  const metricRaw = raw.metric == null || raw.metric === '' ? 'winRate' : raw.metric
  if (!isCompareMetric(metricRaw)) return { ok: false, message: 'metric 非法' }
  const sourceRaw = raw.source == null || raw.source === '' ? 'all' : raw.source
  if (!isTrendSource(sourceRaw)) return { ok: false, message: 'source 非法' }
  const range = parseIterRange(raw.from, raw.to)
  if (!range.ok) return { ok: false, message: range.message }
  const courses = parseCompareCourses(raw.courses)
    .map((c) => sanitizeViewCourse(c))
    .filter((c) => c !== '')
  if (courses.length === 0) return { ok: false, message: 'courses 参数无有效课程' }
  return {
    ok: true,
    params: {
      courses: courses.slice(0, COMPARE_MAX_COURSES),
      metric: metricRaw,
      source: sourceRaw,
      from: range.range.from,
      to: range.range.to,
    },
  }
}

// ────────────────────────── 课程级缓存（TTL 10s + 指纹） ──────────────────────────

interface CompareCacheEntry {
  fp: string
  at: number
  series: Map<string, Series>
}

const COURSE_CACHE_TTL_MS = 10_000
/** 账本缓存（键 = 课程名）。上限只是防洪：节奏是「弹窗打开时轮询」，正常个位数条目。 */
const courseCache = new Map<string, CompareCacheEntry>()
/** 命中/未命中计数（测试出口）：缓存是内部优化，断言要有把手（与 `poolCountersView` 同思路）。 */
let cacheHits = 0
let cacheMisses = 0

/** 测试出口：清缓存同时归零计数。 */
export function __clearCompareCache(): void {
  courseCache.clear()
  cacheHits = 0
  cacheMisses = 0
}

export function __compareCacheSize(): number {
  return courseCache.size
}

export function __compareCacheCounters(): { hits: number; misses: number } {
  return { hits: cacheHits, misses: cacheMisses }
}

/** 账本指纹 = `training_log.jsonl` + `eval_log.jsonl` 的 `mtimeMs:size`（缺失记 `-`）。
 *  两个都取：eval 点只由 eval_log 追加而 training_log 不动 ⇒ 只看一个会漏更新。 */
function ledgerFingerprint(trajDir: string): string {
  const parts: string[] = []
  for (const name of ['training_log.jsonl', 'eval_log.jsonl']) {
    try {
      const st = statSync(path.join(trajDir, name))
      parts.push(`${name}=${st.mtimeMs}:${st.size}`)
    } catch {
      parts.push(`${name}=-`)
    }
  }
  return parts.join('&')
}

/** 读一门课的 19 条序列（缓存命中 = 不读正文；未命中 = `readIterMetrics` → `metricSeries`）。
 *  返回 null = 账本为空/不可读（调用方记 unavailable，不假装零数据）。 */
function seriesOf(
  course: string,
  trajDir: string,
  fp: string,
  now: number,
): Map<string, Series> | null {
  const hit = courseCache.get(course)
  if (hit && hit.fp === fp && now - hit.at < COURSE_CACHE_TTL_MS) {
    cacheHits++
    return hit.series
  }
  cacheMisses++
  let rows: IterRow[]
  try {
    rows = readIterMetrics(trajDir).rows
  } catch {
    return null
  }
  if (rows.length === 0) return null
  const series = new Map(metricSeries(rows).map((s) => [s.key, s]))
  courseCache.set(course, { fp, at: now, series })
  // 洪泛闸：条目超过 64 时清掉过期项（正常节奏下永不触发；留着是防「切换几十门课」把内存顶高）。
  if (courseCache.size > 64) {
    for (const [k, v] of courseCache) {
      if (now - v.at >= COURSE_CACHE_TTL_MS) courseCache.delete(k)
    }
  }
  return series
}

/** 组装响应（纯读；`now` 可注入，便于单测谈 TTL 与指纹而不 sleeping）。 */
export function buildCompareTrendsView(q: CompareQuery, now = Date.now()): CompareTrendsView {
  const spec = COMPARE_METRIC_SPECS[q.metric]
  const wantedKeys =
    q.source === 'eval'
      ? [spec.evalKey]
      : q.source === 'rollout'
        ? [spec.mainKey]
        : [spec.mainKey, spec.evalKey]
  const courses: CompareCourseSeries[] = []
  const unavailable: CompareUnavailable[] = []
  const fps: string[] = []
  for (const course of q.courses) {
    const trajDir = path.join(tmpLogsDir(), course)
    const fp = ledgerFingerprint(trajDir)
    fps.push(`${course}:${fp}`)
    if (!existsSync(path.join(trajDir, 'training_log.jsonl'))) {
      unavailable.push({ course, reason: '无账本' })
      continue
    }
    const byKey = seriesOf(course, trajDir, fp, now)
    if (!byKey) {
      unavailable.push({ course, reason: '账本不可读' })
      continue
    }
    const series: CompareSeriesData[] = []
    let points = 0
    for (const key of wantedKeys) {
      const s = byKey.get(key)
      if (!s) continue
      const sliced = sliceIterRange(s.iters, s.vals, q.from, q.to)
      for (const v of sliced.vals) if (v != null) points += 1
      series.push({ key: s.key, label: s.label, iters: sliced.iters, vals: sliced.vals })
    }
    courses.push({ course, series, points })
  }
  return {
    metric: q.metric,
    source: q.source,
    from: q.from,
    to: q.to,
    courses,
    unavailable,
    fingerprint: fps.join('||'),
    time: new Date(now).toISOString(),
  }
}
