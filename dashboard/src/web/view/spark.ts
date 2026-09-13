/** spark.ts — sparkline 折线点计算与 path 生成。 */
// ────────────────────────── 纯函数：sparkline ──────────────────────────

export const SPARK_W = 110
export const SPARK_H = 26

export interface SparkPoints {
  coords: string
  color: string
  lastX: number
  lastY: number
}

/** 归一化数据点（非有限值跳过；恒定序列满幅平线灰色）。 */
export function sparkPoints(
  values: number[],
  width = SPARK_W,
  height = SPARK_H,
): SparkPoints | null {
  const pts = values.filter((v) => Number.isFinite(v))
  if (pts.length === 0) return null
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min
  const px = (i: number): number => +((i / (pts.length - 1)) * (width - 4) + 2).toFixed(1)
  const py = (v: number): number =>
    span === 0
      ? +(height / 2).toFixed(1)
      : +(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)
  const coords = pts.map((v, i) => `${px(i)},${py(v)}`).join(' ')
  return {
    coords,
    color: span === 0 ? '#94a3b8' : '#2f5fe0',
    lastX: px(pts.length - 1),
    lastY: py(pts[pts.length - 1]!),
  }
}

/** SSR/首屏用 SVG 字符串（客户端 <Sparkline> 组件与它同源，逐字节一致）。 */
export function sparkline(values: number[], width = SPARK_W, height = SPARK_H): string {
  const sp = sparkPoints(values, width, height)
  if (!sp) return '<span class="muted small">—</span>'
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
<polyline points="${sp.coords}" fill="none" stroke="${sp.color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
<circle cx="${sp.lastX}" cy="${sp.lastY}" r="2.2" fill="${sp.color}"/>
</svg>`
}
