/** TrendChart.tsx — 轻量 SVG 走势图：轴 + 网格 + 折线 + 悬停十字准星 + 坐标提示。
 *  支持双序列叠加（rollout 实线 + eval 虚线）：hover 同时提示两口径数值。
 *  数据侧由 sliceSeries 按范围档位截取后绘制；hover 状态纯客户端。 */

import { useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { sliceSeries, type Series, type TrendRange } from '../view'

export interface TrendChartProps {
  /** 主序列（rollout）：全量时序（组件内部按 range 截取）。 */
  series: Series
  /** 叠加序列（eval）：与主序列共用 iter 对齐；缺值 = NaN 不画点。 */
  series2?: Series
  range: TrendRange
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
  height?: number
  /** y 轴下界上限：实际下界 = min(dataMin, yFloor)。缺省贴 dataMin。
   *  击杀/道具传 0（基底锁 0）；胜率传 0.3（基底不得高于 30%）。 */
  yFloor?: number
  /** 主序列图例色（默认 accent）；叠加序列固定琥珀。 */
  color2?: string
}

const VB_W = 260
const PAD_L = 38
const PAD_R = 6
const PAD_T = 6
const PAD_B = 16
/** 轴刻度统一字体（横/纵一致）。 */
const AXIS_FONT = { fontSize: '8', fill: 'var(--muted)' } as const
/** eval 叠加线：高饱和橙，与 accent 蓝 rollout 强对比；白描边保证压在面积上仍可读。 */
const COLOR2_DEFAULT = 'var(--eval-line, #ea580c)'

function pathFrom(
  vals: number[],
  n: number,
  px: (i: number) => number,
  py: (v: number) => number,
): string {
  let d = ''
  let started = false
  for (let i = 0; i < n; i++) {
    const v = vals[i]
    if (!Number.isFinite(v)) {
      started = false
      continue
    }
    d += `${started ? 'L' : 'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)} `
    started = true
  }
  return d
}

export function TrendChart({
  series,
  series2,
  range,
  fmt,
  tone,
  height = 56,
  yFloor,
  color2 = COLOR2_DEFAULT,
}: TrendChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const s = sliceSeries(series, range)
  const vals = s.vals
  const iters = s.iters
  const n = vals.length

  // 叠加序列按主序列 iters 对齐（eval 稀疏：缺轮 = NaN）。
  let vals2: number[] | null = null
  if (series2 && n > 0) {
    vals2 = iters.map((it) => {
      const i = series2.iters.indexOf(it)
      return i >= 0 ? series2.vals[i] : Number.NaN
    })
  }

  const valid = vals.filter(Number.isFinite)
  const valid2 = vals2 ? vals2.filter(Number.isFinite) : []
  if (valid.length === 0 && valid2.length === 0) {
    return (
      <div className="tc-trend" style={{ height }}>
        <span className="tc-muted tc-small">暂无数据</span>
      </div>
    )
  }

  const plotW = VB_W - PAD_L - PAD_R
  const plotH = height - PAD_T - PAD_B
  const all = [...valid, ...valid2]
  const dataMin = all.length ? Math.min(...all) : 0
  const max = all.length ? Math.max(...all) : 1
  const min = yFloor !== undefined ? Math.min(dataMin, yFloor) : dataMin
  const span = max - min

  const px = (i: number): number => PAD_L + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW)
  const py = (v: number): number =>
    PAD_T + (span === 0 ? plotH / 2 : (1 - (v - min) / span) * plotH)

  const line = pathFrom(vals, n, px, py)
  const line2 = vals2 ? pathFrom(vals2, n, px, py) : ''

  // 面积只铺主序列。
  let area = line
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
  const hv2 = hover != null && vals2 ? vals2[hover] : null
  const hy2 = hv2 != null && Number.isFinite(hv2) ? py(hv2) : null

  const onMove = (e: JSX.TargetedMouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const relX = ((e.clientX - rect.left) / rect.width) * VB_W - PAD_L
    let i = Math.round((relX / plotW) * (n - 1))
    i = Math.max(0, Math.min(n - 1, i))
    setHover(i)
  }

  const tipParts: string[] = []
  if (hv != null && Number.isFinite(hv)) tipParts.push(`${series.label} ${fmt(hv)}`)
  if (vals2 && hv2 != null && Number.isFinite(hv2)) {
    tipParts.push(`${series2!.label} ${fmt(hv2)}`)
  }

  return (
    <div className="tc-trend" style={{ height }}>
      <svg
        className="tc-trend__svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${VB_W} ${height}`}
        preserveAspectRatio="none"
        aria-label={`${series.label}${series2 ? ` / ${series2.label}` : ''} 走势`}
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
                fontSize={AXIS_FONT.fontSize}
                fill={AXIS_FONT.fill}
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
              fontSize={AXIS_FONT.fontSize}
              fill={AXIS_FONT.fill}
            >
              {iters[i]}
            </text>
          )
        })}

        {/* 面积 + 主折线 + 叠加线（eval：白描边 + 紫实线 + 点，压在最上层） */}
        {area ? <path d={area} fill={`url(#tg-${series.key})`} /> : null}
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {line2 ? (
          <>
            {/* 白描边：与 rollout 线/面积分离，eval 不再被淹没 */}
            <path
              d={line2}
              fill="none"
              stroke="#fff"
              strokeWidth="3.6"
              strokeLinejoin="round"
              strokeLinecap="round"
              opacity="0.9"
            />
            <path
              d={line2}
              fill="none"
              stroke={color2}
              strokeWidth="2.2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {/* 有效评估点打点：小圆点标位置，不遮 rollout 线 */}
            {vals2
              ? vals2.map((v, i) =>
                  Number.isFinite(v) ? (
                    <circle
                      key={i}
                      cx={px(i).toFixed(1)}
                      cy={py(v).toFixed(1)}
                      r="1.6"
                      fill={color2}
                    />
                  ) : null,
                )
              : null}
          </>
        ) : null}

        {/* 悬停十字准星 + 两点 */}
        {hx != null ? (
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
            {hy != null ? (
              <>
                <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="3" fill={color} />
                <circle
                  cx={hx.toFixed(1)}
                  cy={hy.toFixed(1)}
                  r="5.5"
                  fill={color}
                  fillOpacity="0.18"
                />
              </>
            ) : null}
            {hy2 != null ? (
              <>
                <circle cx={hx.toFixed(1)} cy={hy2.toFixed(1)} r="3" fill={color2} />
                <circle
                  cx={hx.toFixed(1)}
                  cy={hy2.toFixed(1)}
                  r="5.5"
                  fill={color2}
                  fillOpacity="0.18"
                />
              </>
            ) : null}
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

      {/* 坐标提示（双口径并排；固定在图表顶部） */}
      {hx != null && tipParts.length > 0 ? (
        <div className="tc-trend__tip" style={{ left: `${(hx / VB_W) * 100}%` }}>
          <span className="tc-trend__tip-it">it{hover != null ? iters[hover] : ''}</span>
          <span className="tc-trend__tip-val">{tipParts.join(' · ')}</span>
        </div>
      ) : null}
    </div>
  )
}
