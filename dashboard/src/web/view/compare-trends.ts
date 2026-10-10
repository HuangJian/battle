/** compare-trends.ts — 「比较课程」弹窗的纯函数与数据契约（client-safe：零 IO、零 server import）。
 *
 *  谁在用：`components/MultiTrendChart.tsx`（画图几何/hover）、`app/panels/CompareTrendsModal.tsx`
 *  （档位/选课/起止/门闩）、服务端 `api/compare-trends.ts`（同一份档位表与解析——**不许各写一套**）。
 *
 *  三条契约（plan/dashboard-compare-trends.plan.md §3.8–§3.11，评审据此定死）：
 *   ① 过线的数值一律 `number | null`（JSON 不能带 NaN）；进图前 `toChartValues` 还原成 NaN 断笔；
 *   ② 档位 → series key 的映射只此一处（`COMPARE_METRIC_SPECS`），6 档顺序 = 用户给的顺序；
 *   ③ y 轴下界规则只此一处（`compareYBounds`）——"胜率基底 0.3 / 耗时按量级缩放"这类口径
 *      不该在组件里散落第二份。
 */

import { fmtPct } from './format'
import { type TrendSource } from './series'

/** 同图课程数上限（读账本成本随门数线性上涨，plan §6-1）。 */
export const COMPARE_MAX_COURSES = 8

export type CompareMetric = 'winRate' | 'kills' | 'dmgPerKill' | 'winTicks' | 'winHp' | 'pu'

export interface CompareMetricOption {
  value: CompareMetric
  label: string
}

/** 6 档（**顺序即上屏顺序**，用户 2026-10-08 给的顺序）。 */
export const COMPARE_METRIC_OPTIONS: readonly CompareMetricOption[] = [
  { value: 'winRate', label: '胜率' },
  { value: 'kills', label: '击杀' },
  { value: 'dmgPerKill', label: '承伤' },
  { value: 'winTicks', label: '胜局耗时' },
  { value: 'winHp', label: '胜局残血' },
  { value: 'pu', label: '道具' },
]

const METRIC_SET: ReadonlySet<string> = new Set(COMPARE_METRIC_OPTIONS.map((o) => o.value))

/** 非法/缺省一律拒（`localStorage` 与查询参数两个入口都要过它）。 */
export function isCompareMetric(raw: string | null | undefined): raw is CompareMetric {
  return typeof raw === 'string' && METRIC_SET.has(raw)
}

/** 档位 → `metricSeries` 的两个 key + 展示格式 + y 轴规则。 */
export interface CompareMetricSpec {
  metric: CompareMetric
  label: string
  /** rollout 口径的序列 key（`metricSeries` 里恒存在）。 */
  mainKey: string
  /** eval 口径的序列 key；本弹窗 6 档都有 eval 对应（缺值 = NaN/null 断笔）。 */
  evalKey: string
  fmtKind: 'pct' | 'int' | 'dec2'
  /** y 轴下界规则（`compareYBounds` 唯一解释）：cap03=基底不高于 0.3；zero=基底锁 0；scaled=按量级缩放。 */
  yRule: 'cap03' | 'zero' | 'scaled'
}

export const COMPARE_METRIC_SPECS: Record<CompareMetric, CompareMetricSpec> = {
  winRate: {
    metric: 'winRate',
    label: '胜率',
    mainKey: 'winRate',
    evalKey: 'eval',
    fmtKind: 'pct',
    yRule: 'cap03',
  },
  kills: {
    metric: 'kills',
    label: '击杀',
    mainKey: 'kills',
    evalKey: 'evalKills',
    fmtKind: 'pct',
    yRule: 'zero',
  },
  dmgPerKill: {
    metric: 'dmgPerKill',
    label: '承伤',
    mainKey: 'dmgPerKill',
    evalKey: 'evalDmgPerKill',
    fmtKind: 'pct',
    yRule: 'zero',
  },
  winTicks: {
    metric: 'winTicks',
    label: '胜局耗时',
    mainKey: 'winTicks',
    evalKey: 'evalWinTicks',
    fmtKind: 'int',
    yRule: 'scaled',
  },
  winHp: {
    metric: 'winHp',
    label: '胜局残血',
    mainKey: 'winHp',
    evalKey: 'evalWinHp',
    fmtKind: 'pct',
    yRule: 'zero',
  },
  pu: {
    metric: 'pu',
    label: '道具',
    mainKey: 'pu',
    evalKey: 'evalPu',
    fmtKind: 'dec2',
    yRule: 'zero',
  },
}

/** 数值格式化（档位 → 展示形状；与 Hero 同款：百分比 1 位、耗时整数、道具 2 位）。 */
export function compareFmt(metric: CompareMetric): (v: number | null) => string {
  const kind = COMPARE_METRIC_SPECS[metric].fmtKind
  if (kind === 'pct') return (v) => (v == null ? '—' : fmtPct(v))
  if (kind === 'int') return (v) => (v == null ? '—' : String(Math.round(v)))
  return (v) => (v == null ? '—' : v.toFixed(2))
}

/** 序列形态（过线后：NaN 已变 null；`iters` 与 `vals` 等长同序）。 */
export interface CompareSeriesData {
  key: string
  label: string
  vals: Array<number | null>
  iters: number[]
}

/** 一门课在该指标下的结果：`series` 按 source 过滤后 1–2 条；`points` = 有限值个数。 */
export interface CompareCourseSeries {
  course: string
  series: CompareSeriesData[]
  points: number
}

export interface CompareUnavailable {
  course: string
  reason: string
}

/** `GET /api/compareTrends` 响应（端点契约；客户端 `fetchCompareTrends` 的返回类型）。 */
export interface CompareTrendsView {
  metric: CompareMetric
  source: TrendSource
  from: number | null
  to: number | null
  courses: CompareCourseSeries[]
  unavailable: CompareUnavailable[]
  /** 各课账本 `mtimeMs:size` 的聚合串（客户端指纹门闩，见 `shouldApplyCompareData`）。 */
  fingerprint: string
  time: string
}

// ────────────────────────── 纯函数：数值 / 切片 / 解析 ──────────────────────────

/** 有限值收集（NaN / null 都不算——断笔语义的唯一判据）。 */
function finites(seriesList: Array<{ vals: Array<number | null> }>): number[] {
  const out: number[] = []
  for (const s of seriesList) {
    for (const v of s.vals) if (typeof v === 'number' && Number.isFinite(v)) out.push(v)
  }
  return out
}

/** y 轴下界（**单一出处**）：返回语义与 `TrendChart` 的两个 prop 一一对应。
 *
 *  · `cap03`：`min(dataMin, 0.3)` —— 胜率基底不得高于 30%（沿用 Hero 的 `yFloor=0.3`）；
 *  · `zero`：`min(dataMin, 0)` —— 击杀/承伤/残血/道具基底锁 0，不裁正文；
 *  · `scaled`：`max(0, dataMin×0.8, dataMax×0.5)` —— 胜局耗时量级大，按数据缩放（沿用 `winTicksYMin`）。
 *
 *  无有效点 ⇒ `{}`（调用方画空态，不造一条假轴）。 */
export function compareYBounds(
  metric: CompareMetric,
  seriesList: Array<{ vals: Array<number | null> }>,
): { yFloor?: number; yMin?: number } {
  const vals = finites(seriesList)
  if (vals.length === 0) return {}
  const mn = Math.min(...vals)
  const mx = Math.max(...vals)
  switch (COMPARE_METRIC_SPECS[metric].yRule) {
    case 'cap03':
      return { yFloor: Math.min(mn, 0.3) }
    case 'zero':
      return { yFloor: Math.min(mn, 0) }
    case 'scaled':
      return { yMin: Math.max(0, mn * 0.8, mx * 0.5) }
  }
}

/** 按 iter 数值过滤（含端点；`from`/`to` = null 表示该侧不限）。
 *  `iters` 允许任意序（`readIterMetrics` 是倒序、`metricSeries` 是正序，两边都不假设）。
 *  非有限值（NaN）归一成 null —— 过线契约（§3.8）。 */
export function sliceIterRange(
  iters: number[],
  vals: Array<number | null>,
  from: number | null,
  to: number | null,
): { iters: number[]; vals: Array<number | null> } {
  const oi: number[] = []
  const ov: Array<number | null> = []
  for (let i = 0; i < iters.length; i++) {
    const it = iters[i]
    if (it == null) continue
    if (from != null && it < from) continue
    if (to != null && it > to) continue
    const v = vals[i]
    oi.push(it)
    ov.push(typeof v === 'number' && Number.isFinite(v) ? v : null)
  }
  return { iters: oi, vals: ov }
}

/** 过线值 → 图上值（null 还原成 NaN 断笔；`TrendChart.pathFrom` 同款语义）。 */
export function toChartValues(vals: Array<number | null>): number[] {
  return vals.map((v) => (v == null ? Number.NaN : v))
}

/** 单个起止边界：空串 ⇒ null（全量）；非负整数 ⇒ 值；其余 ⇒ 'invalid'。 */
export function parseIterBound(raw: string | null | undefined): number | null | 'invalid' {
  const s = (raw ?? '').trim()
  if (s === '') return null
  if (!/^\d+$/.test(s)) return 'invalid'
  const n = Number(s)
  return Number.isSafeInteger(n) ? n : 'invalid'
}

export interface CompareIterRange {
  from: number | null
  to: number | null
}

/** 起止对：校验 + 归一（`from > to` 是非法，不是「空区间」——静默空图会让人以为课坏了）。 */
export function parseIterRange(
  fromRaw: string | null | undefined,
  toRaw: string | null | undefined,
): { ok: true; range: CompareIterRange } | { ok: false; message: string } {
  const f = parseIterBound(fromRaw)
  const t = parseIterBound(toRaw)
  if (f === 'invalid' || t === 'invalid') return { ok: false, message: '起止 it 须为非负整数' }
  if (f != null && t != null && f > t) return { ok: false, message: '起始 it 大于结束 it' }
  return { ok: true, range: { from: f, to: t } }
}

/** `courses=a,b,c` → 去重后的课程名（只做形态与去重；**存在性校验在服务端** —— 那要读盘）。 */
export function parseCompareCourses(raw: string | null | undefined): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of (raw ?? '').split(',')) {
    const c = part.trim()
    if (!c || seen.has(c)) continue
    seen.add(c)
    out.push(c)
  }
  return out
}

// ────────────────────────── 纯函数：选课 / hover / 门闩 ──────────────────────────

/** 默认选课（用户裁定 2026-10-08）：在训 ∪ 当前课，去重取前 6；一门都没有 ⇒ `courses` 前 2。
 *  调用方注意：`trainingCourses` 是「开课标记」不是「进程在跑」（两件事，见 state-view 注释）。 */
export function defaultCompareCourses(
  courses: string[],
  trainingCourses: string[],
  current: string,
): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  const push = (c: string): void => {
    if (!c || seen.has(c)) return
    seen.add(c)
    out.push(c)
  }
  for (const c of trainingCourses) push(c)
  push(current)
  if (out.length === 0) {
    for (const c of courses) {
      push(c)
      if (out.length >= 2) break
    }
  }
  return out.slice(0, 6)
}

/** 候选课（当前课置顶 + `courses` 原序去重——同一门课在盐盘上只该占一个候选位）。 */
export function compareCourseCandidates(courses: string[], current: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  if (current) {
    seen.add(current)
    out.push(current)
  }
  for (const c of courses) {
    if (!c || seen.has(c)) continue
    seen.add(c)
    out.push(c)
  }
  return out
}

/** 子串搜索（大小写不敏感；空查询 = 原样返回）。 */
export function filterCourseCandidates(candidates: string[], query: string): string[] {
  const q = query.trim().toLowerCase()
  if (!q) return candidates
  return candidates.filter((c) => c.toLowerCase().includes(q))
}

/** 加课：去重 + 上限。已存在/超限 ⇒ 原数组原样返回（调用方据此禁用按钮，纯函数不抛）。 */
export function addCompareCourse(selected: string[], course: string): string[] {
  if (!course || selected.includes(course) || selected.length >= COMPARE_MAX_COURSES) {
    return selected
  }
  return [...selected, course]
}

/** 移课（保留顺序；允许删到 0 —— 空态文案由 UI 给）。 */
export function removeCompareCourse(selected: string[], course: string): string[] {
  return selected.filter((c) => c !== course)
}

/** 临时隐藏开关（用户 2026-10-10）：把该课从「隐藏名单」里去掉 / 加进来。
 *  纯函数、无状态：名单本身住组件（**不进 localStorage** —— 临时就是临时，重开弹窗全显）。 */
export function toggleHiddenCourse(hidden: readonly string[], course: string): string[] {
  return hidden.includes(course) ? hidden.filter((c) => c !== course) : [...hidden, course]
}

/** 8 色色板（**顺序 = 选择顺序 = 色序**；与 `--accent` 蓝、eval 橙都拉得开）。
 *  颜色是数据不是主题 ⇒ 不进 theme.css 变量表（单一出处就在这）。 */
export const COMPARE_COLORS: readonly string[] = [
  '#2563eb',
  '#dc2626',
  '#16a34a',
  '#9333ea',
  '#0891b2',
  '#ca8a04',
  '#db2777',
  '#4d7c0f',
]

/** 第 i 门课的色（取模循环；负数也稳）。 */
export function colorOf(i: number): string {
  const n = COMPARE_COLORS.length
  return COMPARE_COLORS[((i % n) + n) % n] ?? COMPARE_COLORS[0] ?? '#2563eb'
}

/** 口径过滤：`all` 全收；`rollout`/`eval` 按 key 前缀（eval 序列 key 恒以 `eval` 开头）。 */
export function visibleCompareSeries(
  series: CompareSeriesData[],
  source: TrendSource,
): CompareSeriesData[] {
  if (source === 'all') return series
  return series.filter((s) => (source === 'eval') === s.key.startsWith('eval'))
}

/** 图上的一门课：`source` 过滤后、且**不在临时隐藏名单**里。
 *  **不带色**：色是「选课顺序」的身份（chip 圆点与图上折线必须同色），而选课顺序只有弹窗知道
 *  ——服务端响应里少了「无账本」的课，用它的下标取色会让 chip 与线错位。 */
export interface CompareChartRow {
  course: string
  series: CompareSeriesData[]
}

/** 组装图上序列（弹窗唯一的「哪几门进图」判据）。
 *
 *  · **临时隐藏**（用户 2026-10-10）：被隐藏的课整条不进图，但**仍在选课与请求里**
 *    —— 隐藏是纯渲染态，不发请求、不改选课，所以切换是瞬时的、也不需要第二次读盘；
 *  · `noPoints` = 过滤后仍无任何有效点的课（提示文案用；隐藏的课不进这个名单，
 *    否则「无有效点」会去解释一门用户刚刚亲手藏起来的课）。 */
export function compareChartRows(
  courses: CompareCourseSeries[],
  source: TrendSource,
  hidden: readonly string[],
): { rows: CompareChartRow[]; noPoints: string[] } {
  const skip = new Set(hidden)
  const rows: CompareChartRow[] = []
  const noPoints: string[] = []
  for (const row of courses) {
    if (skip.has(row.course)) continue
    const series = visibleCompareSeries(row.series, source)
    let has = false
    for (const s of series) {
      for (const v of s.vals) {
        if (v != null) has = true
      }
    }
    rows.push({ course: row.course, series })
    if (!has) noPoints.push(row.course)
  }
  return { rows, noPoints }
}

/** 跨课 hover 对齐：合并全部 iter，取离 `target` 最近的一个（并列取小）。无 iter ⇒ null。 */
export function nearestIter(iters: number[], target: number): number | null {
  let best: number | null = null
  let bestD = Number.POSITIVE_INFINITY
  for (const it of iters) {
    if (!Number.isFinite(it)) continue
    const d = Math.abs(it - target)
    if (d < bestD || (d === bestD && best != null && it < best)) {
      best = it
      bestD = d
    }
  }
  return best
}

/** 请求身份（门闩的左半边：选项一变就必须覆盖，哪怕账本没动）。 */
export function compareRequestKey(q: {
  courses: string[]
  metric: CompareMetric
  source: TrendSource
  from: number | null
  to: number | null
}): string {
  return `${q.courses.join(',')}|${q.metric}|${q.source}|${q.from ?? ''}|${q.to ?? ''}`
}

/** fingerprint 门闩（plan §3.6）：请求身份与账本指纹都没变 ⇒ 不 setState
 *  （否则每拍重绘把 hover 准星清掉）。任一变 ⇒ 覆盖。 */
export function shouldApplyCompareData(
  prev: { requestKey: string; fingerprint: string } | null,
  next: { requestKey: string; fingerprint: string },
): boolean {
  if (!prev) return true
  return prev.requestKey !== next.requestKey || prev.fingerprint !== next.fingerprint
}
