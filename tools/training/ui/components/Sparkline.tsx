/** Sparkline.tsx — 迷你趋势图（纯渲染；数据侧由 view.sparkPoints 归一化）。 */

import { sparkPoints } from '../view'

export interface SparklineProps {
  values: number[]
  width?: number
  height?: number
}

export function Sparkline({ values, width = 110, height = 26 }: SparklineProps) {
  const sp = sparkPoints(values, width, height)
  if (!sp) return <span className="tc-muted tc-small">—</span>
  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      <polyline
        points={sp.coords}
        fill="none"
        stroke={sp.color}
        stroke-width="1.5"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
      <circle cx={sp.lastX} cy={sp.lastY} r="2.2" fill={sp.color} />
    </svg>
  )
}
