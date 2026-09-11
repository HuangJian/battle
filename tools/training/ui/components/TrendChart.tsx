/** TrendChart.tsx — 轻量 SVG 走势图：轴 + 网格 + 折线 + 悬停十字准星 + 坐标提示。
 *  数据侧由 sliceSeries 按范围档位截取后绘制；eval 的「最近 N」= 最近 N 个有效点。
 *  hover 状态纯客户端（SSR 渲染静态折线，hydrate 后交互）。 */

import { useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { sliceSeries, type Series, type TrendRange } from '../view'

export interface TrendChartProps {
  /** 全量时序（组件内部按 range 截取）。 */
  series: Series
  range: TrendRange
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
  height?: number
  /** y 轴下界上限：实际下界 = min(dataMin, yFloor)。缺省贴 dataMin。
   *  击杀/道具传 0（基底锁 0）；胜率传 0.3（基底不得高于 30%）。 */
  yFloor?: number
}

const VB_W = 260
const PAD_L = 38
const PAD_R = 6
const PAD_T = 6
const PAD_B = 16

export function TrendChart({ series, range, fmt, tone, height = 56, yFloor }: TrendChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const s = sliceSeries(series, range)
  const vals = s.vals
  const iters = s.iters
  const n = vals.length
  const valid = vals.filter(Number.isFinite)

  if (valid.length === 0) {
    return (
      <div className="tc-trend" style={{ height }}>
        <span className="tc-muted tc-small">暂无数据</span>
      </div>
    )
  }

  const plotW = VB_W - PAD_L - PAD_R
  const plotH = height - PAD_T - PAD_B
  const dataMin = Math.min(...valid)
  const max = Math.max(...valid)
  // yFloor = 下界上限：min(数据最小, yFloor)——击杀/道具 0；胜率 ≤0.3
  const min = yFloor !== undefined ? Math.min(dataMin, yFloor) : dataMin
  const span = max - min

  const px = (i: number): number => PAD_L + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW)
  const py = (v: number): number =>
    PAD_T + (span === 0 ? plotH / 2 : (1 - (v - min) / span) * plotH)

  // 折线 / 面积路径（NaN 缺口处断开）。
  let line = ''
  let area = ''
  let started = false
  for (let i = 0; i < n; i++) {
    const v = vals[i]
    if (!Number.isFinite(v)) {
      started = false
      continue
    }
    const x = px(i)
    const y = py(v)
    line += `${started ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)} `
    area += `${started ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)} `
    started = true
  }
  const firstIdx = vals.findIndex(Number.isFinite)
  const lastIdx = vals.length - 1 - [...vals].reverse().findIndex(Number.isFinite)
  const baseY = PAD_T + plotH
  if (firstIdx >= 0 && lastIdx >= 0 && lastIdx > firstIdx) {
    area += `L${px(lastIdx).toFixed(1)} ${baseY.toFixed(1)} L${px(firstIdx).toFixed(1)} ${baseY.toFixed(1)} Z`
  }

  const color =
    tone === 'g'
      ? 'var(--green)'
      : tone === 'y'
        ? 'var(--yellow)'
        : tone === 'r'
          ? 'var(--red)'
          : 'var(--accent)'

  const hx = hover != null ? px(hover) : null
  const hv = hover != null ? vals[hover] : null
  const hy = hv != null && Number.isFinite(hv) ? py(hv) : null

  const onMove = (e: JSX.TargetedMouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const relX = ((e.clientX - rect.left) / rect.width) * VB_W - PAD_L
    let i = Math.round((relX / plotW) * (n - 1))
    i = Math.max(0, Math.min(n - 1, i))
    setHover(i)
  }

  return (
    <div className="tc-trend" style={{ height }}>
      <svg
        className="tc-trend__svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${VB_W} ${height}`}
        preserveAspectRatio="none"
        aria-label={`${series.label} 走势`}
      >
        <defs>
          <linearGradient id={`tg-${series.key}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.18" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* 网格 + Y 轴刻度 */}
        {[0, 0.5, 1].map((t) => {
          const y = PAD_T + plotH * (1 - t)
          const v = min + span * t
          return (
            <g key={t}>
              <line
                x1={PAD_L}
                y1={y.toFixed(1)}
                x2={VB_W - PAD_R}
                y2={y.toFixed(1)}
                stroke="var(--border)"
                strokeWidth="1"
              />
              <text
                x={PAD_L - 4}
                y={(y + 3).toFixed(1)}
                textAnchor="end"
                fontSize="8"
                fill="var(--muted)"
              >
                {fmt(v)}
              </text>
            </g>
          )
        })}

        {/* X 轴刻度（首/中/末迭代） */}
        {[0, 0.5, 1].map((t) => {
          const i = Math.round(t * (n - 1))
          const x = px(i)
          return (
            <text
              key={i}
              x={x.toFixed(1)}
              y={height - 2}
              textAnchor="middle"
              fontSize="8"
              fill="var(--muted)"
            >
              {iters[i]}
            </text>
          )
        })}

        {/* 面积 + 折线 */}
        {area ? <path d={area} fill={`url(#tg-${series.key})`} /> : null}
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth="1.6"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* 悬停十字准星 + 提示点 */}
        {hx != null && hy != null ? (
          <g>
            <line
              x1={hx.toFixed(1)}
              y1={PAD_T}
              x2={hx.toFixed(1)}
              y2={baseY}
              stroke={color}
              strokeWidth="1"
              strokeDasharray="2 2"
              opacity="0.5"
            />
            <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="3" fill={color} />
            <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="5.5" fill={color} fillOpacity="0.18" />
          </g>
        ) : null}

        {/* 悬停捕获层 */}
        <rect
          x={PAD_L}
          y={PAD_T}
          width={plotW}
          height={plotH}
          fill="transparent"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        />
      </svg>

      {/* 坐标提示（固定在图表顶部，不遮挡曲线） */}
      {hx != null && hy != null && hv != null ? (
        <div className="tc-trend__tip" style={{ left: `${(hx / VB_W) * 100}%` }}>
          <span className="tc-trend__tip-it">it{hover != null ? iters[hover] : ''}</span>
          <span className="tc-trend__tip-val">{fmt(hv)}</span>
        </div>
      ) : null}
    </div>
  )
}
