/** CompareTrendsModal.tsx — 「比较课程」弹窗：一张大图叠 N 门课的同一指标（plan/dashboard-compare-trends.plan.md）。
 *
 *  为什么它自己拉数据（而不是蹭 /api/state）：`state.metrics` 只含**当前查看课程**，
 *  而本弹窗要在同一张图上比 N 门课；把 N 门课的账本塞进 3s 全局轮询 = 为「可能没人在看的弹窗」
 *  每次读 N 份日志（§1.2-①/③）。所以：**打开才轮询、关闭即停**，节奏复用顶栏那个旋钮
 *  （`refreshSec` prop），不新增第二个节奏开关（§8-③）。
 *
 *  四条实现纪律：
 *   ① 首帧不读 localStorage（hydrate 一致；与 Hero 的 `TC_TREND_RANGE` 同款）——本地偏好
 *      在 mount 后的 effect 里恢复，且写入以 `restored` 闸住（否则恢复前的那一拍会用默认值
 *      覆盖掉用户存的选择）；
 *   ② 选课与「盘上还剩哪些课」求交（选过的课可能已封存/删除）；`touched` 区分「默认选课」
 *      与「用户删空」——后者要显示空态，不能被默认值悄悄填回来；
 *   ③ 请求带自增序号 + 指纹/请求身份门闩（`shouldApplyCompareData`）：过期响应丢弃、
 *      账本没变不 setState（否则每拍重绘把 hover 准星清掉）；
 *   ④ 一切布局走 class（内联样式属性在全仓 `src/web/**` 只有 TrendChart 的 3 处计算值豁免）；
 *   ⑤ 弹窗吃窗口 80vw×80vh，**图要铺满**：图表槽是列里唯一 `flex: 1` 的兄弟，槽的实测宽高
 *      一路传给 `MultiTrendChart`（`viewBox` 与视口 1:1 才有正确字号/点径，见那支的 `width` 注释）；
 *      注释行必须住槽**外**，否则它们会被算进「可用给图的空间」。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  COMPARE_MAX_COURSES,
  COMPARE_METRIC_OPTIONS,
  TC_COMPARE_COURSES,
  TC_COMPARE_FROM,
  TC_COMPARE_METRIC,
  TC_COMPARE_SOURCE,
  TC_COMPARE_TO,
  TREND_SOURCE_OPTIONS,
  addCompareCourse,
  colorOf,
  compareChartRows,
  compareCourseCandidates,
  compareFmt,
  compareRequestKey,
  defaultCompareCourses,
  filterCourseCandidates,
  isCompareMetric,
  isTrendSource,
  parseCompareCourses,
  parseIterRange,
  removeCompareCourse,
  shouldApplyCompareData,
  toChartValues,
  toggleHiddenCourse,
  type CompareMetric,
  type CompareTrendsView,
  type ConsoleStateView,
  type TrendSource,
} from '../../view'
import { InlineNotice } from '../../components/InlineNotice'
import { MultiTrendChart, type MultiTrendSeries } from '../../components/MultiTrendChart'
import { SegmentedControl } from '../../components/SegmentedControl'
import { fetchCompareTrends } from '../lib/api-client'
import { usePolling } from '../lib/usePolling'

export interface CompareTrendsModalProps {
  open: boolean
  /** 整页快照（课程清单/在训课/封存课都在里面）——弹窗不额外拉课程表。 */
  stateView: ConsoleStateView | null
  /** 刷新节奏（秒）：顶栏那个旋钮的当前值（Hero 透传）。 */
  refreshSec: number
  onClose: () => void
}

/** 图表槽留给图例/间隙的高度预算（图例行 `nowrap` ⇒ 恒一行：fs-sm(12px) 行盒 ≈17px + `.tc-mchart` 的 6px gap）。
 *  槽高由 CSS 给（`flex: 1`，见 theme.css 的 `.tc-modal.tc-cmp`），SVG 高度必须由它算出来
 *  —— `viewBox` 是运行时数值，写不成类名也不是内联样式的活（样式纪律豁免只给 TrendChart 3 处）。 */
const LEGEND_RESERVE = 26
/** 视口极小/标签页不可见（`clientHeight === 0`）时的兜底画布高，别把图压成一条线。 */
const MIN_CHART_H = 200

function writeLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 隐私模式等不可写场景忽略（偏好不是功能） */
  }
}

/** 候选课选择器。
 *
 *  **导出理由**（与 Hero 的 `TrendCell` 同）：封存课的「不可点 + 写明原因」是一条独立渲染
 *  分支，而 picker 默认关着 ⇒ 只测整页弹窗永远看不到候选列表。把它拆出来，SSR 就能直接
 *  钉住「封存行是 span 不是 button」。
 *
 *  封存课不画是读面纪律（服务端 `api/archive.ts`：只读 manifest、不扫盘不解压）——不是懒。 */
export function CompareCoursePicker({
  candidates,
  archived,
  selected,
  query,
  onQuery,
  onAdd,
}: {
  candidates: string[]
  archived: Array<{ course: string }>
  selected: string[]
  query: string
  onQuery: (q: string) => void
  onAdd: (course: string) => void
}) {
  const pickable = filterCourseCandidates(candidates, query).filter((c) => !selected.includes(c))
  return (
    <div className="tc-cmp__picker">
      <input
        className="tc-cmp__srch"
        type="search"
        placeholder="搜索课程"
        aria-label="搜索课程"
        value={query}
        onInput={(e: JSX.TargetedInputEvent<HTMLInputElement>) => onQuery(e.currentTarget.value)}
      />
      <div className="tc-cmp__list">
        {pickable.map((c) => (
          <button type="button" className="tc-cmp__cand" key={c} onClick={() => onAdd(c)}>
            {c}
          </button>
        ))}
        {archived.map((a) => (
          <span
            className="tc-cmp__cand tc-cmp__cand--off"
            key={`arch:${a.course}`}
            title="封存课只留归档清单（不扫盘不解压）⇒ 没有逐轮账本可比"
          >
            {a.course} · 封存 · 无逐轮账本
          </span>
        ))}
        {pickable.length === 0 && archived.length === 0 ? (
          <span className="tc-muted tc-small">没有可加的课程</span>
        ) : null}
      </div>
    </div>
  )
}

export function CompareTrendsModal({
  open,
  stateView,
  refreshSec,
  onClose,
}: CompareTrendsModalProps) {
  const [restored, setRestored] = useState(false)
  const [courses, setCourses] = useState<string[]>([])
  const [touched, setTouched] = useState(false)
  /** 临时隐藏的课（**不进 localStorage**：临时就是临时，重开弹窗回到全显）。 */
  const [hidden, setHidden] = useState<string[]>([])
  const [metric, setMetric] = useState<CompareMetric>('winRate')
  const [source, setSource] = useState<TrendSource>('all')
  const [fromText, setFromText] = useState('')
  const [toText, setToText] = useState('')
  const [applied, setApplied] = useState<{ from: number | null; to: number | null }>({
    from: null,
    to: null,
  })
  const [data, setData] = useState<CompareTrendsView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [picker, setPicker] = useState(false)
  const [query, setQuery] = useState('')
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden)
  /** 图表槽（量它的可用宽高 → SVG 画布；见 `LEGEND_RESERVE`）。 */
  const slotRef = useRef<HTMLDivElement | null>(null)
  const [chartBox, setChartBox] = useState({ w: 720, h: 320 })
  /** 已应用数据的身份（请求身份 + 账本指纹）——门闩的另一半。 */
  const appliedMetaRef = useRef<{ requestKey: string; fingerprint: string } | null>(null)
  /** 请求序号：迟到的响应不许盖新的。 */
  const seqRef = useRef(0)

  const current = stateView?.course ?? ''
  const candidates = compareCourseCandidates(stateView?.courses ?? [], current)
  const known = new Set(candidates)
  const selected = courses.filter((c) => known.has(c))
  // 默认选课 = 在训 ∪ 当前课（≤6）；用户删空之后不再回填（要能看见「至少选一门」的空态）。
  const effective = touched
    ? selected
    : selected.length > 0
      ? selected
      : defaultCompareCourses(stateView?.courses ?? [], stateView?.trainingCourses ?? [], current)

  const rangeCheck = parseIterRange(fromText, toText)
  const requestKey = compareRequestKey({
    courses: effective,
    metric,
    source,
    from: applied.from,
    to: applied.to,
  })

  /** 有效隐藏名单：与当前选课求交（课程被移出再选回来 ⇒ 自动恢复显示，不留幽灵状态）。 */
  const hiddenKeys = hidden.filter((c) => effective.includes(c))
  const toggleHidden = (course: string): void => setHidden((h) => toggleHiddenCourse(h, course))

  /** 图表槽实测宽高 → SVG 画布（槽高 = 80vh 弹窗里扣完全部兄弟节点后的余量）。
   *  **必须是真实 px**：SVG 的 `viewBox` 与视口 1:1 时缩放才是 1，轴标签/圆点才是设计尺寸
   *  （见 `MultiTrendChart` 的 `width` prop 注释）。 */
  useEffect(() => {
    if (!open) return
    const el = slotRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const apply = (): void => {
      const w = Math.max(320, Math.round(el.clientWidth))
      const h = Math.max(MIN_CHART_H, Math.round(el.clientHeight - LEGEND_RESERVE))
      setChartBox((prev) => (prev.w === w && prev.h === h ? prev : { w, h }))
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [open])

  // ① 本地偏好恢复（mount 一次；此后才允许写）
  useEffect(() => {
    try {
      const m = localStorage.getItem(TC_COMPARE_METRIC)
      if (isCompareMetric(m)) setMetric(m)
      const s = localStorage.getItem(TC_COMPARE_SOURCE)
      if (isTrendSource(s)) setSource(s)
      const storedFrom = localStorage.getItem(TC_COMPARE_FROM)
      const storedTo = localStorage.getItem(TC_COMPARE_TO)
      const r = parseIterRange(storedFrom, storedTo)
      if (r.ok && (storedFrom !== null || storedTo !== null)) {
        setFromText(storedFrom ?? '')
        setToText(storedTo ?? '')
        setApplied(r.range)
      }
      const storedCourses = localStorage.getItem(TC_COMPARE_COURSES)
      if (storedCourses) setCourses(parseCompareCourses(storedCourses))
    } catch {
      /* 隐私模式等不可读场景忽略 */
    }
    setRestored(true)
  }, [])

  useEffect(() => {
    if (!restored) return
    writeLocal(TC_COMPARE_METRIC, metric)
    writeLocal(TC_COMPARE_SOURCE, source)
    writeLocal(TC_COMPARE_FROM, fromText)
    writeLocal(TC_COMPARE_TO, toText)
  }, [restored, metric, source, fromText, toText])

  useEffect(() => {
    if (!restored || !touched) return
    writeLocal(TC_COMPARE_COURSES, selected.join(','))
  }, [restored, touched, selected.join(',')])

  // 起止 it：非法 ⇒ 沿用上一次合法值（不重拉、不动图）；合法 ⇒ 400ms 防抖后落为「已应用」。
  useEffect(() => {
    const r = parseIterRange(fromText, toText)
    if (!r.ok) return
    if (r.range.from === applied.from && r.range.to === applied.to) return
    const t = setTimeout(() => setApplied(r.range), 400)
    return () => clearTimeout(t)
  }, [fromText, toText, applied.from, applied.to])

  const load = async (): Promise<void> => {
    if (effective.length === 0) {
      appliedMetaRef.current = null
      setData(null)
      setErr(null)
      return
    }
    const my = ++seqRef.current
    try {
      const next = await fetchCompareTrends({
        courses: effective,
        metric,
        source,
        from: applied.from,
        to: applied.to,
      })
      if (my !== seqRef.current) return // 过期响应：丢（快切选项 + 轮询重叠时的必守纪律）
      const key = compareRequestKey({
        courses: effective,
        metric,
        source,
        from: applied.from,
        to: applied.to,
      })
      const meta = { requestKey: key, fingerprint: next.fingerprint }
      if (!shouldApplyCompareData(appliedMetaRef.current, meta)) return
      appliedMetaRef.current = meta
      setData(next)
      setErr(null)
    } catch (e) {
      if (my !== seqRef.current) return
      setErr(e instanceof Error ? e.message : String(e)) // 保留上一帧数据（不清图）
    }
  }
  const loadRef = useRef(load)
  loadRef.current = load

  // 打开/可见/选项变化 ⇒ 立即重拉（不等下一拍）；轮询只负责「节奏」
  useEffect(() => {
    if (!open || !visible) return
    void loadRef.current()
  }, [open, visible, requestKey])

  usePolling({
    enabled: open && visible,
    intervalSec: refreshSec,
    fetch: () => loadRef.current(),
  })

  // 可见性：与 app.tsx 同款（Hero 不发这个 prop；它是本弹窗自己的运行事实）
  useEffect(() => {
    if (!open) return
    setVisible(!document.hidden)
    const onVis = (): void => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [open])

  // Esc：先关候选列表，再关弹窗（一层一层退，别一跳关到底）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      if (picker) setPicker(false)
      else onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, picker, onClose])

  if (!open) return null

  // 图上序列：口径过滤 + 临时隐藏都住 view 层纯函数（`compareChartRows`）。
  // 色按**选课顺序**（`effective`）取 —— 与 chip 圆点同源；隐藏/无账本都不让别人换色。
  const { rows: chartRows, noPoints } = compareChartRows(data?.courses ?? [], source, hiddenKeys)
  const chartSeries: MultiTrendSeries[] = chartRows.flatMap((r) =>
    r.series.map((s) => ({
      course: r.course,
      key: s.key,
      color: colorOf(Math.max(0, effective.indexOf(r.course))),
      iters: s.iters,
      vals: toChartValues(s.vals),
    })),
  )
  const fmt = compareFmt(metric)
  const loading = effective.length > 0 && data == null && err == null
  const atCap = effective.length >= COMPARE_MAX_COURSES

  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal tc-modal--wide tc-cmp"
        role="dialog"
        aria-label="比较课程"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tc-cmp__hd">
          <h3>比较课程</h3>
          <span className="tc-muted tc-small">
            同图只负责「同框看趋势」——语料/起始权重/iter 起点不同的课不给结论
          </span>
          <button type="button" className="tc-link" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="tc-cmp__ctl">
          <SegmentedControl<CompareMetric>
            value={metric}
            ariaLabel="比较指标"
            options={COMPARE_METRIC_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            onChange={setMetric}
          />
          <SegmentedControl<TrendSource>
            value={source}
            ariaLabel="比较口径"
            options={TREND_SOURCE_OPTIONS}
            onChange={setSource}
          />
          <label className="tc-cmp__range">
            <span className="tc-muted tc-small">起</span>
            <input
              className="tc-cmp__inp"
              type="number"
              min="0"
              step="1"
              value={fromText}
              placeholder="全量"
              aria-label="起始 iter"
              onInput={(e: JSX.TargetedInputEvent<HTMLInputElement>) =>
                setFromText(e.currentTarget.value)
              }
            />
            <span className="tc-muted tc-small">止</span>
            <input
              className="tc-cmp__inp"
              type="number"
              min="0"
              step="1"
              value={toText}
              placeholder="全量"
              aria-label="结束 iter"
              onInput={(e: JSX.TargetedInputEvent<HTMLInputElement>) =>
                setToText(e.currentTarget.value)
              }
            />
          </label>
          {rangeCheck.ok ? null : <span className="tc-cmp__bad">{rangeCheck.message}</span>}
        </div>

        <div className="tc-cmp__courses">
          {effective.length === 0 ? (
            <span className="tc-muted tc-small">至少选一门课程</span>
          ) : null}
          {effective.map((c, i) => {
            const off = hiddenKeys.includes(c)
            return (
              <span className={`tc-chip${off ? ' tc-chip--off' : ''}`} key={c}>
                <svg
                  className="tc-cmp__dot"
                  viewBox="0 0 8 8"
                  width="8"
                  height="8"
                  aria-hidden="true"
                >
                  <circle cx="4" cy="4" r="4" fill={colorOf(i)} />
                </svg>
                {c}
                {/* 临时隐藏（不进 localStorage）：只摘图上的线，选课与请求都不动。 */}
                <button
                  type="button"
                  className="tc-chip__eye"
                  aria-pressed={off}
                  aria-label={off ? `显示 ${c}` : `暂时隐藏 ${c}`}
                  title={off ? `恢复显示 ${c}` : `暂时隐藏 ${c}（只摘这张图上的线，不改选课）`}
                  onClick={() => toggleHidden(c)}
                >
                  {off ? '隐' : '显'}
                </button>
                <button
                  type="button"
                  className="tc-chip__x"
                  aria-label={`移除 ${c}`}
                  title={`移除 ${c}`}
                  onClick={() => {
                    setTouched(true)
                    setCourses(removeCompareCourse(effective, c))
                  }}
                >
                  ×
                </button>
              </span>
            )
          })}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={atCap}
            title={
              atCap ? `最多 ${COMPARE_MAX_COURSES} 门（读账本成本随门数线性上涨）` : '加一门课'
            }
            onClick={() => setPicker(!picker)}
          >
            + 课程
          </button>
          {atCap ? (
            <span className="tc-muted tc-small">
              最多 {COMPARE_MAX_COURSES} 门（读账本成本随门数线性上涨）
            </span>
          ) : null}
        </div>

        {picker ? (
          <CompareCoursePicker
            candidates={candidates}
            archived={stateView?.archived ?? []}
            selected={effective}
            query={query}
            onQuery={setQuery}
            onAdd={(c) => {
              setTouched(true)
              setCourses(addCompareCourse(effective, c))
            }}
          />
        ) : null}

        {/* 图表槽 = 弹窗里唯一 `flex: 1` 的兄弟 ⇒ 它的高度就是 80vh 扣完控制条后的余量；
            SVG 高度由 `slotRef` 量出来后算（见 LEGEND_RESERVE）。注释行**必须住槽外**，
            否则它们的高度也会被算进「可用空间」，把图挤掉一行。 */}
        <div className="tc-cmp__chart" ref={slotRef}>
          {err ? <InlineNotice>比较数据拉取失败：{err}（图保留上一帧）</InlineNotice> : null}
          {effective.length === 0 ? (
            <InlineNotice>至少选一门课程</InlineNotice>
          ) : loading ? (
            <span className="tc-muted tc-small">正在读账本…</span>
          ) : (
            <MultiTrendChart
              seriesList={chartSeries}
              metric={metric}
              fmt={fmt}
              width={chartBox.w}
              height={chartBox.h}
              bridgeGaps={source === 'eval'}
            />
          )}
        </div>

        <div className="tc-cmp__notes">
          {effective.length > 0 && hiddenKeys.length === effective.length ? (
            <p className="tc-muted tc-small tc-cmp__note">
              全部选课都被临时隐藏了（点课程名旁的「隐」恢复）
            </p>
          ) : null}
          {data && data.unavailable.length > 0 ? (
            <p className="tc-muted tc-small tc-cmp__note">
              无数据：
              {data.unavailable.map((u) => `${u.course}（${u.reason}）`).join(' · ')}
            </p>
          ) : null}
          {noPoints.length > 0 ? (
            <p className="tc-muted tc-small tc-cmp__note">
              无有效点：{noPoints.join(' · ')}（该指标在这些课上还没有可用读数）
            </p>
          ) : null}
          {data && data.courses.length > 0 && data.from == null && data.to == null ? (
            <p className="tc-muted tc-small tc-cmp__note">
              读账本最多取最新 500 轮（超长腿的早期轮不在此图）
            </p>
          ) : null}
        </div>

        <div className="tc-modal__foot tc-cmp__foot">
          <span className="tc-muted tc-small">
            自动刷新跟随顶栏节奏（每 {refreshSec}s）；打开弹窗与切换任何选项都会立即重拉
          </span>
          <span className="sp" />
          <button type="button" className="tc-btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
