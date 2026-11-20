/** HomeCompareCell.tsx — 首页走势格的多课版本：一张小图叠当前课 + 对比课。
 *
 *  为什么不是给 `TrendCell` 加一个 `multi` 分支：`TrendCell` 的三种数据源档位
 *  （全部/rollout/eval）与单课主序列 + eval 叠加是绑死的；多课图的 x 轴是 iter 数值、
 *  每条线各自一门课，形状完全不同。拆成独立组件，`TrendCell` 保持原样（SSR 首帧也不变）。
 *
 *  两条实现纪律：
 *   · 图宽必须量容器（`viewBox` 与视口 1:1 才有正确字号/点径，见 `MultiTrendChart` 的 `width` 注释）；
 *   · 值列只报**当前课**的最新读数（对比课的数在 hover 提示框里看）——格头空间只够一个数。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import {
  colorOf,
  homeYBounds,
  isEvalSeriesKey,
  type HomeChartLine,
  type HomeChartYRule,
} from '../../view'
import { MultiTrendChart } from '../../components/MultiTrendChart'
import { TrendZoomButton } from '../../components/TrendZoomButton'

/** 首页小图高度：提示框画在图上方（HTML 浮层），不占画布；72px 留给折线与轴标签。 */
const CHART_H = 72
/** 量不到容器宽时的兜底（SSR / 隐藏 tab）。 */
const FALLBACK_W = 260

function lastFinite(line: HomeChartLine | undefined): number | null {
  if (!line) return null
  for (let i = line.vals.length - 1; i >= 0; i--) {
    if (Number.isFinite(line.vals[i])) return line.vals[i]
  }
  return null
}

export interface HomeCompareCellProps {
  /** 图名（格头左侧；与单课模式同一个词）。 */
  label: string
  /** 格头悬停说明（口径/统计量）。 */
  title?: string
  /** 当前课名（值列只报它）。 */
  course: string
  /** 当前课在图上的线色（格头圆点与线同色；颜色跟课走，不恒为第一色）。 */
  currentColor?: string
  /** 已按范围截取、按课程顺序排好的线条（`buildHomeChartSeries` 的输出）。 */
  lines: HomeChartLine[]
  fmt: (v: number | null) => string
  yRule: HomeChartYRule
  /** 数据源档位：只看 eval 时跨缺口连线（与比较弹窗同款）。 */
  source: 'all' | 'rollout' | 'eval'
  /** 同组共享的悬停迭代号（首页多张图联动）。 */
  hoverIter?: number | null
  onHoverIter?: (it: number | null) => void
  /** 临时隐藏的课名（点首页图例切换；只摘图上的线，格头当前课读数照报）。 */
  hiddenCourses?: readonly string[]
  /** 放大这张多课图（浮窗由调用方渲染，复用同一组线条；没有可放大的线 ⇒ 不传）。 */
  onExpand?: () => void
}

export function HomeCompareCell({
  label,
  title,
  course,
  currentColor,
  lines,
  fmt,
  yRule,
  source,
  hoverIter,
  onHoverIter,
  hiddenCourses = [],
  onExpand,
}: HomeCompareCellProps) {
  const chartRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(FALLBACK_W)
  useEffect(() => {
    const el = chartRef.current
    if (!el) return
    const apply = (): void => {
      const w = Math.max(220, Math.round(el.clientWidth))
      setWidth((prev) => (prev === w ? prev : w))
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const currentLines = lines.filter((l) => l.course === course)
  const mainLine = currentLines.find((l) => !isEvalSeriesKey(l.key)) ?? currentLines[0]
  // 只看 eval 时主序列**就是** eval（单课 `TrendCell` 同款）：不要再把它当叠加层报第二遍。
  const evalLine = source === 'all' ? currentLines.find((l) => isEvalSeriesKey(l.key)) : undefined
  const last = lastFinite(mainLine)
  const lastEval = lastFinite(evalLine)
  // 图例的隐藏只作用于**曲线**：格头仍报当前课最新读数（隐藏 ≠ 没数据）。
  const chartLines =
    hiddenCourses.length === 0 ? lines : lines.filter((l) => !hiddenCourses.includes(l.course))
  const bounds = homeYBounds(yRule, chartLines)

  return (
    <div className="tc-tcell tc-tcell--multi">
      <span className="tc-tcell__hd">
        <span className="tc-tcell__hdmid">
          <svg className="tc-tcell__dot" viewBox="0 0 8 8" width="8" height="8" aria-hidden="true">
            <circle cx="4" cy="4" r="4" fill={currentColor ?? colorOf(0)} />
          </svg>
          <span className="tc-tcell__lbl" title={title}>
            {label}
          </span>
        </span>
        <b title={`当前课 ${course} 的最新读数（对比课看 hover）`}>
          {fmt(last)}
          {evalLine && lastEval != null ? (
            <span className="tc-tcell__pair">
              {' / '}
              {fmt(lastEval)}
            </span>
          ) : null}
        </b>
      </span>
      <div className="tc-tcell__chart" ref={chartRef}>
        {chartLines.length === 0 ? (
          <span className="tc-muted tc-small">
            {lines.length === 0 ? '所选课程暂无该口径数据' : '全部课程已隐藏（点图例恢复）'}
          </span>
        ) : (
          <MultiTrendChart
            seriesList={chartLines}
            fmt={fmt}
            yBounds={bounds}
            label={label}
            width={width}
            height={CHART_H}
            showLegend={false}
            bridgeGaps={source === 'eval'}
            compact
            snapToEval={source === 'eval'}
            hoverIter={hoverIter}
            onHoverIter={onHoverIter}
          />
        )}
        {onExpand ? (
          <TrendZoomButton
            label={`${label} · ${course}`}
            title="放大走势图（浮窗内可点击课程名显隐曲线）"
            onClick={onExpand}
          />
        ) : null}
      </div>
    </div>
  )
}
