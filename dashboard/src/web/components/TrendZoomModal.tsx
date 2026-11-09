/** TrendZoomModal.tsx — 趋势图全屏浮窗。
 *
 *  · 现有六项：放大同一张单指标图（rollout 实线 / eval 橙线按当前数据源）。
 *  · 行为指标：把六种统计量（均值/中位数/min/max/p90/p99）画成多条曲线，
 *    点击图例逐条显示/隐藏——统计量是同一轮逐局读数的不同切面，不是六条独立指标。
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
  sliceSeries,
  type BehaviorMetricKey,
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
}

const MIN_CHART_H = 280

export function TrendZoomModal({ target, range, onClose }: TrendZoomModalProps) {
  const initialStat = target?.behavior?.stat ?? 'mean'
  const [visibleStats, setVisibleStats] = useState<Set<MetricStatKey>>(() => new Set([initialStat]))
  const [chartBox, setChartBox] = useState({ w: 1200, h: 560 })
  const slotRef = useRef<HTMLDivElement | null>(null)

  const behaviorKey = target?.behavior?.key
  const behaviorStat = target?.behavior?.stat ?? 'mean'
  useEffect(() => {
    setVisibleStats(new Set([behaviorStat]))
  }, [behaviorKey, behaviorStat])

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
            {def
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

        <div className="tc-trendzoom__chart" ref={slotRef}>
          {behavior ? (
            statSeries.length > 0 ? (
              <MultiTrendChart
                seriesList={statSeries}
                fmt={fmt}
                width={chartBox.w}
                height={chartBox.h}
                showLegend={false}
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
