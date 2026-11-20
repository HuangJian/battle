/** home-compare.ts — 首页走势图「多课同图」的纯函数与配置契约（client-safe：零 IO）。
 *
 *  用户 2026-11-20：原来只有「比较课程」浮窗能多课同图，操作太绕。现在首页的走势图
 *  直接按一份**全局比较课**配置叠线：当前课自动加入，另可选最多 6 门比较课；颜色跟课走，
 *  切换当前课也不换色。
 *
 *  为什么是全局一组而不是每图各配一组：
 *   · 色序跨图必须一致，否则「红=kb2」在两张图上会变成两门不同的课；
 *   · 首页小图本来就窄，每图再配一次课只会把「看趋势」变成「配表」。
 *
 *  数据侧分工：
 *   · 当前课：Hero 已有 `/api/state.metrics.iters`，客户端现算（`metricSeries` /
 *     `behaviorMetricSeries`）；
 *   · 对比课：`GET /api/trendSeries` 按显式序列键批量取（服务端复用比较课程的账本缓存）。
 *   本文件只做「图键 → 序列键」的映射、范围截取与合流，不碰 IO。
 */

import {
  behaviorMetricDef,
  METRIC_STAT_KEYS,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  type TrendChartKey,
} from './behavior-metrics'
import {
  COMPARE_COLORS,
  colorOf,
  type CompareSeriesData,
  type TrendSeriesView,
} from './compare-trends'
import {
  behaviorSeriesKey,
  isSparseSeriesKey,
  type Series,
  type TrendRange,
  type TrendScales,
  type TrendSource,
} from './series'

/** 首页同图最多再叠 6 门比较课（当前课 + 6 = 7 条线，仍在 8 色色板内；再多小图与提示框都读不动）。 */
export const HOME_COMPARE_MAX_COURSES = 6

export interface HomeCompareConfig {
  /** 开关：关 = 首页保持单课（配置的课仍记住，下次打开不用重选）。 */
  on: boolean
  /** **当前课之外的比较课**（当前课自动加入对比，不占这里的名额）。顺序 = 图例/叠线顺序。 */
  courses: string[]
  /** 课名 → 色板槽位（0 起）。颜色跟课走：切换当前课、改选课都不重排已有课的颜色。
   *  旧配置没有该字段 ⇒ 按「当前课 0、比较课按 courses 顺序 1..」迁移（与旧行为一致）。 */
  colors?: Record<string, number>
}

export const HOME_COMPARE_DEFAULT: HomeCompareConfig = { on: false, courses: [] }

/** localStorage JSON → 配置。坏 JSON / 非法值逐项退默认，课程去重并截到上限。 */
export function parseHomeCompareConfig(raw: string | null | undefined): HomeCompareConfig {
  if (!raw) return { on: false, courses: [] }
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    if (!o || typeof o !== 'object') return { on: false, courses: [] }
    const seen = new Set<string>()
    const courses: string[] = []
    if (Array.isArray(o.courses)) {
      for (const v of o.courses) {
        if (typeof v !== 'string') continue
        const c = v.trim()
        if (!c || seen.has(c)) continue
        seen.add(c)
        courses.push(c)
        if (courses.length >= HOME_COMPARE_MAX_COURSES) break
      }
    }
    // colors 可以存当前课的槽位（它不在 courses 里），所以不能按 courses 修剪。
    const colors: Record<string, number> = {}
    let colorCount = 0
    if (o.colors && typeof o.colors === 'object' && !Array.isArray(o.colors)) {
      for (const [rawName, rawSlot] of Object.entries(o.colors as Record<string, unknown>)) {
        if (typeof rawSlot !== 'number' || !Number.isInteger(rawSlot)) continue
        if (rawSlot < 0 || rawSlot >= COMPARE_COLORS.length) continue
        const name = rawName.trim()
        if (!name || name === '__proto__' || Object.hasOwn(colors, name)) continue
        colors[name] = rawSlot
        colorCount++
        if (colorCount >= COMPARE_COLORS.length) break
      }
    }
    return colorCount > 0 ? { on: o.on === true, courses, colors } : { on: o.on === true, courses }
  } catch {
    return { on: false, courses: [] }
  }
}

/** 加/减一门对比课（纯数组；去重 + 上限；已存在 ⇒ 移除，满了 ⇒ 原样返回）。 */
export function toggleHomeCompareCourse(courses: string[], course: string): string[] {
  const c = course.trim()
  if (!c) return courses
  if (courses.includes(c)) return courses.filter((x) => x !== c)
  if (courses.length >= HOME_COMPARE_MAX_COURSES) return courses
  return [...courses, c]
}

/** 当前课 + 比较课的颜色表（课名 → 色板槽位）。
 *
 *  显式槽位优先；旧配置/缺失槽位按「当前课先取 0，其余按 courses 顺序取最小空闲槽」补。
 *  先保留所有显式槽位再补缺，避免补色抢走别的课已经固定的颜色。 */
export function resolveHomeCompareColors(
  config: HomeCompareConfig,
  currentCourse: string,
): Record<string, number> {
  const ordered: string[] = []
  if (currentCourse) ordered.push(currentCourse)
  for (const c of config.courses) {
    if (c && c !== currentCourse && !ordered.includes(c)) ordered.push(c)
  }
  const configured = config.colors ?? {}
  const slots = new Map<string, number>()
  const used = new Set<number>()
  for (const c of ordered) {
    const slot = configured[c]
    if (slot == null || !Number.isInteger(slot)) continue
    if (slot < 0 || slot >= COMPARE_COLORS.length || used.has(slot)) continue
    slots.set(c, slot)
    used.add(slot)
  }
  const out: Record<string, number> = {}
  for (const c of ordered) {
    let slot = slots.get(c)
    if (slot == null) {
      slot = 0
      while (used.has(slot)) slot++
      used.add(slot)
    }
    out[c] = slot
  }
  return out
}

/** 只保留当前可见课（当前课 + courses）的颜色，避免 localStorage 积累已离开的课。 */
function withVisibleColors(
  config: HomeCompareConfig,
  colors: Record<string, number>,
  currentCourse: string,
): HomeCompareConfig {
  const out: Record<string, number> = {}
  if (currentCourse && colors[currentCourse] != null) out[currentCourse] = colors[currentCourse]
  for (const c of config.courses) {
    if (c !== currentCourse && colors[c] != null) out[c] = colors[c]
  }
  const next: HomeCompareConfig = { on: config.on, courses: config.courses }
  if (Object.keys(out).length > 0) next.colors = out
  return next
}

/** 在配置层加/减一门比较课：课程顺序与每门课的固定色位一起更新。
 *  候选课不占当前课名额；移除课时释放它的色位，新加课取最小空闲色位。 */
export function toggleHomeCompareCourseInConfig(
  config: HomeCompareConfig,
  currentCourse: string,
  course: string,
): HomeCompareConfig {
  const c = course.trim()
  if (!c || c === currentCourse) return config
  const courses = toggleHomeCompareCourse(config.courses, c)
  if (courses === config.courses) return config
  const colors = resolveHomeCompareColors(config, currentCourse)
  if (courses.length < config.courses.length) {
    delete colors[c]
  } else {
    const used = new Set<number>()
    for (const slot of Object.values(colors)) used.add(slot)
    let slot = 0
    while (used.has(slot)) slot++
    colors[c] = slot
  }
  return withVisibleColors({ ...config, courses }, colors, currentCourse)
}

/** 切换「当前课」时保持多课对比集合与每门课的颜色（用户 2026-11-20）。
 *
 *  新当前课原本是比较课 ⇒ 把它从 courses 里摘掉，再把**旧当前课补进集合**：
 *  对比一门不少；若它是唯一比较课，`compareActive` 也不会整个熄灭——这正是用户报的
 *  「切到对比里的课，对比没了」。两门课都沿用切换前的色位 ⇒ 同一门课不会因为换当前课而变色。
 *
 *  新当前课不在比较课里 / 同一门课 / 旧当前课为空 ⇒ 原样返回（不替用户改选课）；
 *  旧当前课已在名单里 ⇒ 去重，不重复占名额。与开关无关：关着时也维持契约
 *  （`courses` 不含当前课），下次打开仍是对比同一组课。 */
export function preserveHomeCompareOnCourseSwitch(
  config: HomeCompareConfig,
  prevCourse: string,
  nextCourse: string,
): HomeCompareConfig {
  if (!prevCourse || prevCourse === nextCourse) return config
  const colors = resolveHomeCompareColors(config, prevCourse)
  let courses: string[]
  if (config.courses.includes(nextCourse)) {
    courses = config.courses.filter((c) => c !== nextCourse && c !== prevCourse)
    courses.push(prevCourse)
  } else {
    // 旧当前课离开对比；新当前课取它在 colors 里原本的色位（若有且空闲），否则取最小空闲槽。
    courses = config.courses.filter((c) => c !== nextCourse && c !== prevCourse)
    delete colors[prevCourse]
    const used = new Set<number>()
    for (const c of courses) {
      const slot = colors[c]
      if (slot != null) used.add(slot)
    }
    const stored = config.colors?.[nextCourse]
    let slot = 0
    if (
      stored != null &&
      Number.isInteger(stored) &&
      stored >= 0 &&
      stored < COMPARE_COLORS.length &&
      !used.has(stored)
    ) {
      slot = stored
    } else {
      while (used.has(slot)) slot++
    }
    colors[nextCourse] = slot
  }
  return withVisibleColors({ ...config, courses }, colors, nextCourse)
}

/** 请求身份（门闩用）：课程顺序与键序都算身份；服务端返回的 fingerprint 只描述账本。 */
export function homeCompareRequestKey(courses: string[], keys: string[]): string {
  return `${courses.join(',')}|${keys.join(',')}`
}

// ────────────────────────── 图键 → 序列键 ──────────────────────────

/** y 轴下界规则（与 `compareYBounds` 同语义；首页多课图在 view 层算，组件只翻译成像素）。 */
export type HomeChartYRule = 'cap03' | 'zero' | 'scaled'

export interface HomeChartSpec {
  /** rollout 口径的序列键（`metricSeries` / `behaviorSeriesKey`）。 */
  mainKey: string
  /** eval 口径的序列键。 */
  evalKey: string
  yRule: HomeChartYRule
}

/** 首页走势图键 → 两条口径的序列键 + y 轴规则。
 *
 *  三张带纵坐标档位的图（击杀 / 胜局残血 / 道具）按 `scales` 选 `*Pct` / `*Abs` 变体；
 *  逐局指标按当前统计量选键。未知键（旧存储残留）⇒ null，调用方跳过。 */
export function homeChartSpec(
  chart: TrendChartKey,
  scales: TrendScales,
  stats: BehaviorMetricStatMap,
): HomeChartSpec | null {
  switch (chart) {
    case 'winRate':
      return { mainKey: 'winRate', evalKey: 'eval', yRule: 'cap03' }
    case 'dmgPerKill':
      return { mainKey: 'dmgPerKill', evalKey: 'evalDmgPerKill', yRule: 'zero' }
    case 'winTicks':
      return { mainKey: 'winTicks', evalKey: 'evalWinTicks', yRule: 'scaled' }
    case 'kills':
      return scales.kills === 'pct'
        ? { mainKey: 'killsPct', evalKey: 'evalKillsPct', yRule: 'zero' }
        : { mainKey: 'killsAbs', evalKey: 'evalKillsAbs', yRule: 'zero' }
    case 'winHp':
      return scales.winHp === 'pct'
        ? { mainKey: 'winHp', evalKey: 'evalWinHp', yRule: 'zero' }
        : { mainKey: 'winHpAbs', evalKey: 'evalWinHpAbs', yRule: 'zero' }
    case 'pu':
      return scales.pu === 'pct'
        ? { mainKey: 'puPct', evalKey: 'evalPuPct', yRule: 'zero' }
        : { mainKey: 'pu', evalKey: 'evalPu', yRule: 'zero' }
  }
  if (!chart.startsWith('bm:')) return null
  const key = chart.slice(3) as BehaviorMetricKey
  if (!behaviorMetricDef(key)) return null
  const stat = stats[key] ?? 'mean'
  return {
    mainKey: behaviorSeriesKey(key, stat, 'rollout'),
    evalKey: behaviorSeriesKey(key, stat, 'eval'),
    yRule: 'zero',
  }
}

/** 当前数据源档位下，这张图要哪几条序列（全部 = 主 + eval；单边 = 只一条）。 */
export function sourceSeriesKeys(spec: HomeChartSpec, source: TrendSource): string[] {
  if (source === 'rollout') return [spec.mainKey]
  if (source === 'eval') return [spec.evalKey]
  return [spec.mainKey, spec.evalKey]
}

/** 首页可见图 → 需要向 `/api/trendSeries` 请求的序列键（去重 + 排序：图重排不会改请求身份）。
 *
 *  逐局指标一次取回**六种统计量**（用户 2026-11-20）：放大浮窗要能点统计量切换，而对比课
 *  的数据只能从 `/api/trendSeries` 拿，等切换时再拉会有空窗。默认逐局指标全不显示 ⇒ 只有
 *  用户主动勾选的图才付这份成本；当前课的统计序列客户端现算（`behaviorMetricSeries`），不在请求里。 */
export function requestedHomeSeriesKeys(
  charts: readonly TrendChartKey[],
  source: TrendSource,
  scales: TrendScales,
  stats: BehaviorMetricStatMap,
): string[] {
  const set = new Set<string>()
  for (const chart of charts) {
    if (chart.startsWith('bm:')) {
      const metric = chart.slice(3) as BehaviorMetricKey
      if (!behaviorMetricDef(metric)) continue
      for (const stat of METRIC_STAT_KEYS) {
        if (source === 'all') {
          set.add(behaviorSeriesKey(metric, stat, 'rollout'))
          set.add(behaviorSeriesKey(metric, stat, 'eval'))
        } else {
          set.add(behaviorSeriesKey(metric, stat, source))
        }
      }
      continue
    }
    const spec = homeChartSpec(chart, scales, stats)
    if (!spec) continue
    for (const key of sourceSeriesKeys(spec, source)) set.add(key)
  }
  return [...set].sort()
}

// ────────────────────────── 范围截取 / 合流 ──────────────────────────

/** 首页多课图的范围截取。
 *
 *  与 `sliceSeries` 的差别只有一个：**保留稀疏序列的 NaN 缺口**。
 *  `sliceSeries` 给单课图用（`TrendChart` 自己按主序列对齐 eval），会把稀疏序列过滤成
 *  只含有效点；多课图的 x 轴是 iter 数值，缺口必须留着——否则 eval 的虚线会把两段
 *  不连续的评估连成一条假趋势。窗口语义仍是「最近 N 个有效点」：从第 N 个有效点起切到末尾。
 */
export function sliceHomeSeries(series: Series, range: TrendRange): Series {
  const { key, vals, iters } = series
  if (vals.length === 0) return series
  const tail = (n: number): number =>
    range === 'all' ? n : Math.max(1, Math.ceil(n * (range === '60pct' ? 0.6 : 0.3)))
  if (!(series.sparse === true || isSparseSeriesKey(key))) {
    const n = tail(vals.length)
    return {
      ...series,
      vals: range === 'all' ? vals : vals.slice(-n),
      iters: range === 'all' ? iters : iters.slice(-n),
    }
  }
  const finiteIdx: number[] = []
  for (let i = 0; i < vals.length; i++) if (Number.isFinite(vals[i])) finiteIdx.push(i)
  if (finiteIdx.length === 0) return { ...series, vals: [], iters: [] }
  const start = finiteIdx[finiteIdx.length - tail(finiteIdx.length)]
  return { ...series, vals: vals.slice(start), iters: iters.slice(start), sparse: true }
}

/** 过线数据（null = 缺值）→ 图上序列（NaN = 断笔）。 */
export function homeSeriesFromCompare(data: CompareSeriesData): Series {
  return {
    key: data.key,
    label: data.label,
    iters: data.iters,
    vals: data.vals.map((v) => (v == null ? Number.NaN : v)),
    sparse: isSparseSeriesKey(data.key) || undefined,
  }
}

/** 图上一条线（字段与 `MultiTrendSeries` 同形；view 层不 import 组件）。 */
export interface HomeChartLine {
  course: string
  key: string
  color: string
  iters: number[]
  vals: number[]
}

export interface BuildHomeChartSeriesArgs {
  spec: HomeChartSpec
  source: TrendSource
  range: TrendRange
  /** 课程顺序 = 图例/叠线顺序：当前课在第一位，其后是比较课。 */
  courses: string[]
  /** 课名 → 色板槽位（`resolveHomeCompareColors` 的结果）；缺省按 courses 顺序取色（旧行为）。 */
  colors?: Record<string, number>
  currentCourse: string
  /** 当前课的序列查找（键 → 全量序列；未截取）。 */
  currentSeries: (key: string) => Series | undefined
  /** 比较课的批量响应（还没拉到 / 某课无账本 ⇒ 该课缺线，不补假点）。 */
  extra: TrendSeriesView | null
}

function findExtraSeries(
  view: TrendSeriesView | null,
  course: string,
  key: string,
): CompareSeriesData | undefined {
  const row = view?.courses.find((c) => c.course === course)
  return row?.series.find((s) => s.key === key)
}

/** 一张首页走势图的多课线条（当前课 + 对比课；范围在各自序列上截取）。 */
export function buildHomeChartSeries(args: BuildHomeChartSeriesArgs): HomeChartLine[] {
  const keys = sourceSeriesKeys(args.spec, args.source)
  const out: HomeChartLine[] = []
  for (let ci = 0; ci < args.courses.length; ci++) {
    const course = args.courses[ci]
    // 颜色跟课走（用户 2026-11-20）：显式槽位优先，切当前课/增减对比课都不重排已有课。
    // 没传 colors 的旧调用方才退到位置色（ci），保证纯函数单测与渐进接线兼容。
    const slot = args.colors?.[course] ?? ci
    for (const key of keys) {
      const raw =
        course === args.currentCourse
          ? args.currentSeries(key)
          : (() => {
              const d = findExtraSeries(args.extra, course, key)
              return d ? homeSeriesFromCompare(d) : undefined
            })()
      if (!raw) continue
      const sliced = sliceHomeSeries(raw, args.range)
      let has = false
      for (const v of sliced.vals) {
        if (Number.isFinite(v)) {
          has = true
          break
        }
      }
      if (!has) continue
      out.push({ course, key, color: colorOf(slot), iters: sliced.iters, vals: sliced.vals })
    }
  }
  return out
}

/** y 轴下界（首页多课图的单一出处）：语义与 `TrendChart` 的两个 prop 一一对应。
 *  · `cap03`：基底不高于 0.3（胜率）；· `zero`：基底锁 0；· `scaled`：胜局耗时按量级缩放。
 *  无有效点 ⇒ `{}`（调用方画空态）。 */
export function homeYBounds(
  rule: HomeChartYRule,
  seriesList: Array<{ vals: number[] }>,
): { yFloor?: number; yMin?: number } {
  const vals: number[] = []
  for (const s of seriesList) {
    for (const v of s.vals) if (Number.isFinite(v)) vals.push(v)
  }
  if (vals.length === 0) return {}
  if (rule === 'cap03') return { yFloor: 0.3 }
  if (rule === 'zero') return { yFloor: 0 }
  const mn = Math.min(...vals)
  const mx = Math.max(...vals)
  return { yMin: Math.max(0, mn * 0.8, mx * 0.5) }
}
