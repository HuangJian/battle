/** TrendZoomModal.tsx — 趋势图全屏浮窗。
 *
 *  · 现有六项：放大同一张单指标图（rollout 实线 / eval 橙线按当前数据源）。
 *  · 行为指标：把六种统计量（均值/中位数/min/max/p90/p99）画成多条曲线，
 *    点击图例逐条显示/隐藏——统计量是同一轮逐局读数的不同切面，不是六条独立指标。
 *  · 多课对比（用户 2026-11-20）：首页正在多课同图时，放大**仍是那张多课图**——直接复用
 *    首页的线条/色位/口径；点课程名与首页图例共用同一份临时隐藏名单。
 *
 *  统计曲线按 Hero 当前的范围档位（全量/最近60%/最近30%）截取；颜色只走 SVG 属性，
 *  布局一律走 class（与 TrendChart / MultiTrendChart 同规）。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import {
  behaviorMetricDef,
  behaviorMetricSeries,
  fmtMetricStat,
  METRIC_STAT_COLORS,
  METRIC_STAT_KEYS,
  METRIC_STAT_LABEL,
  METRIC_STAT_OPTIONS,
  homeYBounds,
  sliceSeries,
  type BehaviorMetricKey,
  type HomeChartLine,
  type HomeChartYRule,
  type IterRow,
  type MetricStatKey,
  type Series,
  type TrendRange,
  type TrendSource,
} from '../view'
import { MultiTrendChart, type MultiTrendSeries } from './MultiTrendChart'
import { TrendChart } from './TrendChart'

export interface ZoomTarget {
  title: string
  fmt: (v: number | null) => string
  series?: Series
  series2?: Series
  color?: string
  yFloor?: number
  yMin?: number
  /** 首页多课对比的放大：线条原样复用（含被临时隐藏的课），浮窗自己按隐藏名单过滤。 */
  multi?: {
    lines: HomeChartLine[]
    yRule: HomeChartYRule
    source: TrendSource
    /** 逐局指标：六种统计量各自的多课线条（切换后每门课只画该统计量的线）。 */
    statLines?: Partial<Record<MetricStatKey, HomeChartLine[]>>
    /** 首页当前统计量（浮窗初始选中）。 */
    stat?: MetricStatKey
  }
  behavior?: {
    key: BehaviorMetricKey
    iters: IterRow[]
    stat: MetricStatKey
    /** 与首页数据源档位一致；`all` 时每条统计曲线同时画 rollout 实线和 eval 虚线。 */
    source?: TrendSource
  }
}

export interface TrendZoomModalProps {
  target: ZoomTarget | null
  range: TrendRange
  onClose: () => void
  /** 首页的临时隐藏名单（放大浮窗与首页图例共用一份；不进 localStorage）。 */
  hiddenCourses?: readonly string[]
  /** 点课程名切换显隐（与首页图例同一个回调）。 */
  onToggleCourse?: (course: string) => void
}

const MIN_CHART_H = 280

export function TrendZoomModal({
  target,
  range,
  onClose,
  hiddenCourses = [],
  onToggleCourse,
}: TrendZoomModalProps) {
  const initialStat = target?.multi?.stat ?? target?.behavior?.stat ?? 'mean'
  const [visibleStats, setVisibleStats] = useState<Set<MetricStatKey>>(() => new Set([initialStat]))
  const [multiStat, setMultiStat] = useState<MetricStatKey>(initialStat)
  const [chartBox, setChartBox] = useState({ w: 1200, h: 560 })
  const slotRef = useRef<HTMLDivElement | null>(null)

  const behaviorKey = target?.behavior?.key
  const behaviorStat = target?.behavior?.stat ?? 'mean'
  useEffect(() => {
    setVisibleStats(new Set([behaviorStat]))
    setMultiStat(target?.multi?.stat ?? behaviorStat)
  }, [behaviorKey, behaviorStat, target?.multi?.stat])

  useEffect(() => {
    if (!target) return
    const apply = (): void => {
      const el = slotRef.current
      if (!el) return
      setChartBox({
        w: Math.max(320, Math.round(el.clientWidth)),
        h: Math.max(MIN_CHART_H, Math.round(el.clientHeight)),
      })
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const el = slotRef.current
    if (!el) return
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [target])

  useEffect(() => {
    if (!target) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [target, onClose])

  if (!target) return null

  const multi = target.multi
  /** 逐局指标切统计量 = 整组换线（每门课仍只一条该统计量的线）；其它图没有 statLines。 */
  const drawnMultiLines = multi?.statLines?.[multiStat] ?? multi?.lines ?? []
  /** 隐藏只摘线、不摘图例按钮（否则隐藏后就没入口点回来了）；y 轴按可见线重算。 */
  const visibleMultiLines = drawnMultiLines.filter((l) => !hiddenCourses.includes(l.course))
  const multiEntries: Array<{ course: string; color: string }> = []
  if (multi) {
    for (const l of drawnMultiLines) {
      if (!multiEntries.some((e) => e.course === l.course)) {
        multiEntries.push({ course: l.course, color: l.color })
      }
    }
  }
  const multiBounds = multi ? homeYBounds(multi.yRule, visibleMultiLines) : {}

  const behavior = target.behavior
  const def = behavior ? behaviorMetricDef(behavior.key) : undefined
  const fmt = def ? (v: number | null) => fmtMetricStat(v, def) : target.fmt
  const behaviorSource = behavior?.source ?? 'eval'
  const sources: Array<'rollout' | 'eval'> =
    behaviorSource === 'all' ? ['rollout', 'eval'] : [behaviorSource]
  const statSeries: MultiTrendSeries[] = behavior
    ? METRIC_STAT_KEYS.filter((k) => visibleStats.has(k)).flatMap((k) =>
        sources.map((src) => {
          const s = sliceSeries(behaviorMetricSeries(behavior.iters, behavior.key, k, src), range)
          return {
            course: `${METRIC_STAT_LABEL[k]}${src === 'eval' ? ' · eval' : ''}`,
            key: `${src === 'eval' ? 'eval-' : ''}behavior-${behavior.key}-${k}-${src}`,
            color: METRIC_STAT_COLORS[k],
            iters: s.iters,
            vals: s.vals,
          }
        }),
      )
    : []

  const toggleStat = (k: MetricStatKey): void => {
    setVisibleStats((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal tc-trendzoom"
        role="dialog"
        aria-modal="true"
        aria-label={`${target.title} 放大走势`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tc-trendzoom__hd">
          <h3>{target.title}</h3>
          <span className="tc-muted tc-small">
            {multi
              ? multi.statLines
                ? '多课对比 · 点击统计量切换口径，点击课程名显隐曲线'
                : '多课对比 · 点击课程名显示/隐藏曲线'
              : def
                ? `${def.title} · 逐局统计；点击图例显示/隐藏统计曲线`
                : '当前范围档位下的放大走势'}
          </span>
          <button type="button" className="tc-link" onClick={onClose}>
            关闭
          </button>
        </div>

        {behavior ? (
          <div className="tc-trendzoom__legend" role="group" aria-label="统计曲线显隐">
            {METRIC_STAT_OPTIONS.map((o) => (
              <button
                type="button"
                key={o.value}
                className={`tc-trendzoom__lg${visibleStats.has(o.value) ? '' : ' tc-trendzoom__lg--off'}`}
                aria-pressed={visibleStats.has(o.value)}
                onClick={() => toggleStat(o.value)}
              >
                <svg viewBox="0 0 12 4" width="12" height="4" aria-hidden="true">
                  <line
                    x1="0"
                    y1="2"
                    x2="12"
                    y2="2"
                    stroke={METRIC_STAT_COLORS[o.value]}
                    stroke-width="2"
                  />
                </svg>
                {o.label}
              </button>
            ))}
          </div>
        ) : null}
        {multi?.statLines ? (
          <div className="tc-trendzoom__legend" role="group" aria-label="统计量切换">
            {METRIC_STAT_OPTIONS.map((o) => (
              <button
                type="button"
                key={o.value}
                className={`tc-trendzoom__lg${multiStat === o.value ? '' : ' tc-trendzoom__lg--off'}`}
                aria-pressed={multiStat === o.value}
                title={`只看多门课程的${o.label}`}
                onClick={() => setMultiStat(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        ) : null}
        {multi ? (
          <div className="tc-trendzoom__legend" role="group" aria-label="课程曲线显隐">
            {multiEntries.map((e) => {
              const off = hiddenCourses.includes(e.course)
              return (
                <button
                  type="button"
                  key={e.course}
                  className={`tc-trendzoom__lg${off ? ' tc-trendzoom__lg--off' : ''}`}
                  aria-pressed={off}
                  aria-label={off ? `显示 ${e.course} 的曲线` : `隐藏 ${e.course} 的曲线`}
                  onClick={() => onToggleCourse?.(e.course)}
                >
                  <svg viewBox="0 0 8 8" width="8" height="8" aria-hidden="true">
                    <circle cx="4" cy="4" r="4" fill={e.color} />
                  </svg>
                  {e.course}
                </button>
              )
            })}
          </div>
        ) : null}

        <div className="tc-trendzoom__chart" ref={slotRef}>
          {multi ? (
            visibleMultiLines.length > 0 ? (
              <MultiTrendChart
                seriesList={visibleMultiLines}
                fmt={fmt}
                yBounds={multiBounds}
                label={target.title}
                width={chartBox.w}
                height={chartBox.h}
                showLegend={false}
                bridgeGaps={multi.source === 'eval'}
                snapToEval={multi.source === 'eval'}
              />
            ) : (
              <span className="tc-muted tc-small">全部课程已隐藏（点课程名恢复）</span>
            )
          ) : behavior ? (
            statSeries.length > 0 ? (
              <MultiTrendChart
                seriesList={statSeries}
                fmt={fmt}
                width={chartBox.w}
                height={chartBox.h}
                showLegend={false}
                snapToEval={behaviorSource === 'eval'}
              />
            ) : (
              <span className="tc-muted tc-small">至少显示一条统计曲线</span>
            )
          ) : target.series ? (
            <TrendChart
              series={target.series}
              series2={target.series2}
              range={range}
              fmt={target.fmt}
              color={target.color}
              yFloor={target.yFloor}
              yMin={target.yMin}
              width={chartBox.w}
              height={chartBox.h}
            />
          ) : null}
        </div>

        <div className="tc-trendzoom__foot">
          <span className="tc-muted tc-small">
            范围档位：{range === 'all' ? '全量' : range === '60pct' ? '最近60%' : '最近30%'}
            （统计曲线与卡片趋势图同一档位）
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
