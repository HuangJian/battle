/** spark.ts — sparkline 折线点计算、path 生成与趋势图 hover 命中。 */
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

/**
 * 趋势图 hover：捕获层（plot 区）屏幕坐标 → 数据下标。
 * `rectLeft/rectWidth` 是 **plot 捕获 rect** 的 getBoundingClientRect（已含 PAD_L 偏移、
 * 宽度 = plot 对应的屏幕宽），因此比例 t∈[0,1] 直接映射 i∈[0,n-1]。
 * 历史 bug：曾按全 viewBox 宽 VB_W 换算再减 PAD_L，导致中段选点系统性偏左。
 */
export function trendHoverIndex(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  n: number,
): number {
  if (n <= 1) return 0
  if (!(rectWidth > 0)) return 0
  const t = (clientX - rectLeft) / rectWidth
  const i = Math.round(t * (n - 1))
  return Math.max(0, Math.min(n - 1, i))
}

/**
 * 趋势图提示框的**水平中心**（px）：把「以准星为中心」的浮层钳制在图表宽度内。
 * 右端 hover ⇒ 中心左移（框的右缘贴住图表右内沿），左端对称；提示框比图表还宽时
 * **不折行**，贴着 hover 所在的一侧（右侧 hover ⇒ 整框向左伸，右缘仍在图内）。
 * 组件把结果交给 `left` + `translate(-50%, -100%)`，所以这里返回的是中心而不是左缘。
 * `chartW <= 0`（隐藏/首帧）原样返回，由调用方退回百分比定位。
 */
export function trendTipCenter(x: number, chartW: number, tipW: number): number {
  if (!(chartW > 0)) return x
  const half = tipW / 2
  // 太宽装不下：不折行，整框翻到准星另一侧（右端 hover ⇒ 框整体向左，右缘停在准星处）。
  if (chartW <= tipW + 2) return x <= chartW / 2 ? x + half + 1 : x - half - 1
  return Math.max(half + 1, Math.min(x, chartW - half - 1))
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
