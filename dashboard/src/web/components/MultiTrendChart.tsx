/** MultiTrendChart.tsx — 多课程大图：iter **数值** x 轴 + 共享 y 轴 + N 条课色折线 + hover 十字准星。
 *
 *  为什么不能复用 `TrendChart`：它是「主序列 + 一条 eval 叠加」且 x 轴按**下标**映射
 *  （`px(i) = PAD_L + i/(n-1)*plotW`）。比较图里各课 iter 网格不同（resume 的课从 it50 起），
 *  按下标画会让两门课的 it50 落在同一列——那是画错的图，不是「不精确的图」。
 *  于是本组件按 iter **数值**铺 x：`x = (iter - xMin)/(xMax - xMin)`，`xMin/xMax` = 全部可见课
 *  的 iter 全域（跨课共享）。起点错开是**事实**（plan §3.4），不是缺陷。
 *
 *  三条纪律：
 *   ① SVG 属性一律短横线拼法（Preact 客户端 diff 原样 `setAttribute`，见 `TrendChart.tsx` 头注）；
 *   ② 断笔复用 `TrendChart` 导出的 `pathFrom`（缺值不连线，同一份语义只有一个实现）；
 *   ③ 布局一律走 class（内联样式属性在 `src/web/**` 只有 TrendChart 的 3 处计算值豁免）——所以
 *      hover 提示框画在 **SVG 内**（`<rect>`+`<text>`，坐标是属性），不是 HTML 浮层。
 */

import { useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { compareYBounds, nearestIter, type CompareMetric } from '../view'
import { pathFrom } from './TrendChart'

export interface MultiTrendSeries {
  /** 课程名（hover 提示与图例的键）。 */
  course: string
  /** 序列 key（`metricSeries` 的 key；以 `eval` 开头 = eval 口径 ⇒ 虚线）。 */
  key: string
  color: string
  iters: number[]
  /** 与 `iters` 等长；NaN = 缺值（断笔，不是 0）。 */
  vals: number[]
}

export interface MultiTrendChartProps {
  seriesList: MultiTrendSeries[]
  metric: CompareMetric
  fmt: (v: number | null) => string
  height?: number
}

const VB_W = 720
const PAD_L = 46
const PAD_R = 12
const PAD_T = 10
const PAD_B = 24
/** 轴刻度统一字体（与 `TrendChart` 同款：小、灰、短横线属性）。 */
const AXIS_FONT = { fontSize: '9', fill: 'var(--muted)' } as const
/** 提示框宽度（给「课程名 eval: 0.123」留够；超出会被裁成省略号般的错觉，故宁宽勿窄）。 */
const TIP_W = 168

/** eval 口径 = 虚线（多课共图时不能再靠颜色区分口径——颜色已经被课程占用）。 */
const isEvalKey = (key: string): boolean => key.startsWith('eval')

export function MultiTrendChart({ seriesList, metric, fmt, height = 240 }: MultiTrendChartProps) {
  const [hover, setHover] = useState<number | null>(null)

  const allIters: number[] = []
  const nums: number[] = []
  for (const s of seriesList) {
    for (const it of s.iters) allIters.push(it)
    for (const v of s.vals) if (Number.isFinite(v)) nums.push(v)
  }
  if (nums.length === 0 || allIters.length === 0) {
    return (
      <div className="tc-mchart__empty tc-muted tc-small">
        该指标在所选课程上暂无有效点
        <span className="tc-mchart__empty-hint">
          （胜率/击杀等主口径要先有迭代行；eval 口径要先跑过评估）
        </span>
      </div>
    )
  }

  // y 轴下界规则住在 view 层（`compareYBounds` 单一出处）：本组件只负责把它翻译成像素。
  const bounds = compareYBounds(metric, seriesList)
  const dataMin = Math.min(...nums)
  const dataMax = Math.max(...nums)
  let lo = bounds.yMin ?? (bounds.yFloor != null ? Math.min(dataMin, bounds.yFloor) : dataMin)
  let hi = Math.max(dataMax, lo)
  if (!(hi > lo)) {
    // 单点 / 全等值：给一个对称窗口，否则整条线会贴在轴上（看起来像空图）。
    lo -= 0.5
    hi += 0.5
  }

  const itersSorted = [...new Set(allIters)].sort((a, b) => a - b)
  const xMin = itersSorted[0] ?? 0
  const xMax = itersSorted[itersSorted.length - 1] ?? xMin
  const plotW = VB_W - PAD_L - PAD_R
  const plotH = height - PAD_T - PAD_B
  const xOf = (it: number): number =>
    xMin === xMax ? PAD_L + plotW / 2 : PAD_L + (plotW * (it - xMin)) / (xMax - xMin)
  const yOf = (v: number): number => PAD_T + plotH * (1 - (v - lo) / (hi - lo))

  const onMove = (e: JSX.TargetedMouseEvent<SVGSVGElement>): void => {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    if (box.width <= 0) return
    // 视口坐标 → viewBox 坐标（viewBox 宽 720，实际宽度随容器伸缩）。
    const vx = ((e.clientX - box.left) / box.width) * VB_W
    const target = xMin === xMax ? xMin : xMin + ((vx - PAD_L) / plotW) * (xMax - xMin)
    setHover(nearestIter(itersSorted, target))
  }

  const valueAt = (s: MultiTrendSeries, it: number): number | null => {
    const i = s.iters.indexOf(it)
    if (i < 0) return null
    const v = s.vals[i]
    return typeof v === 'number' && Number.isFinite(v) ? v : null
  }

  const tipLines =
    hover == null
      ? []
      : seriesList.map((s) => ({
          color: s.color,
          text: `${s.course}${isEvalKey(s.key) ? ' · eval' : ''}: ${fmt(valueAt(s, hover))}`,
        }))
  const tipH = 14 + tipLines.length * 11
  const tipX = hover == null ? 0 : Math.min(Math.max(xOf(hover) + 8, PAD_L), VB_W - PAD_R - TIP_W)
  const midIter = Math.round((xMin + xMax) / 2)

  return (
    <div className="tc-mchart">
      <svg
        className="tc-mchart__svg"
        viewBox={`0 0 ${VB_W} ${height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`多课程${metric}对比图`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* y 网格 + 刻度（下/中/上） */}
        {[0, 0.5, 1].map((t) => {
          const v = lo + (hi - lo) * t
          const y = yOf(v)
          return (
            <g key={`y${t}`}>
              <line
                x1={PAD_L}
                y1={y.toFixed(1)}
                x2={VB_W - PAD_R}
                y2={y.toFixed(1)}
                stroke="var(--border)"
                stroke-width="1"
              />
              <text
                x={PAD_L - 4}
                y={(y + 3).toFixed(1)}
                text-anchor="end"
                font-size={AXIS_FONT.fontSize}
                fill={AXIS_FONT.fill}
              >
                {fmt(v)}
              </text>
            </g>
          )
        })}

        {/* x 刻度：it 全域的两端 + 中点（数值，不是下标） */}
        {[xMin, midIter, xMax].map((it, i) => (
          <text
            key={`x${i}`}
            x={xOf(it).toFixed(1)}
            y={height - 6}
            text-anchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
            font-size={AXIS_FONT.fontSize}
            fill={AXIS_FONT.fill}
          >
            it{it}
          </text>
        ))}

        {/* N 条折线：课色；eval 口径虚线；缺值断笔（pathFrom 只吃有限值） */}
        {seriesList.map((s) => (
          <path
            key={`${s.course}:${s.key}`}
            d={pathFrom(s.vals, s.vals.length, (i) => xOf(s.iters[i] ?? xMin), yOf)}
            fill="none"
            stroke={s.color}
            stroke-width={isEvalKey(s.key) ? '1.6' : '1.8'}
            stroke-dasharray={isEvalKey(s.key) ? '4 3' : undefined}
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        ))}

        {/* hover：竖线 + 各课点 + SVG 内提示框（对齐到最近的真实 iter，不是鼠标 x） */}
        {hover != null ? (
          <g>
            <line
              x1={xOf(hover).toFixed(1)}
              y1={PAD_T}
              x2={xOf(hover).toFixed(1)}
              y2={PAD_T + plotH}
              stroke="var(--muted)"
              stroke-width="1"
              stroke-dasharray="2 2"
            />
            {seriesList.map((s) => {
              const v = valueAt(s, hover)
              return v == null ? null : (
                <circle
                  key={`h:${s.course}:${s.key}`}
                  cx={xOf(hover).toFixed(1)}
                  cy={yOf(v).toFixed(1)}
                  r="2.4"
                  fill={s.color}
                />
              )
            })}
            <g>
              <rect
                x={tipX}
                y={PAD_T + 2}
                width={TIP_W}
                height={tipH}
                rx="4"
                fill="var(--card)"
                fill-opacity="0.96"
                stroke="var(--border)"
              />
              <text x={tipX + 6} y={PAD_T + 14} font-size="9" fill="var(--muted)">
                it{hover}
              </text>
              {tipLines.map((l, i) => (
                <g key={`t${i}`}>
                  <rect x={tipX + 6} y={PAD_T + 20 + i * 11} width="6" height="6" fill={l.color} />
                  <text x={tipX + 16} y={PAD_T + 26 + i * 11} font-size="9" fill="var(--text)">
                    {l.text}
                  </text>
                </g>
              ))}
            </g>
          </g>
        ) : null}
      </svg>
      {/* 图例：课色 + 口径（eval 虚线）。SVG 小条取色，避免内联样式（样式纪律）。 */}
      <div className="tc-mchart__legend">
        {seriesList.map((s) => (
          <span className="tc-mchart__lg" key={`lg:${s.course}:${s.key}`}>
            <svg
              className="tc-mchart__sw"
              viewBox="0 0 12 4"
              width="12"
              height="4"
              aria-hidden="true"
            >
              <line
                x1="0"
                y1="2"
                x2="12"
                y2="2"
                stroke={s.color}
                stroke-width="2"
                stroke-dasharray={isEvalKey(s.key) ? '3 2' : undefined}
              />
            </svg>
            {s.course}
            {isEvalKey(s.key) ? ' · eval' : ''}
          </span>
        ))}
      </div>
    </div>
  )
}
