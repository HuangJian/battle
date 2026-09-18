/**
 * web-view-trend-hover.test.ts — 首页趋势图 hover 命中下标（竖线/数值与鼠标对齐）
 *
 * 分层：src/web/view/spark.ts（trendHoverIndex）↔ src/web/components/TrendChart.tsx
 *
 * 复现：88 点序列，鼠标落在 plot 中央时应命中中位 iter（~it43/44），
 * 旧公式（按全 viewBox 换算再减 PAD_L）会偏左到 ~it37–41。
 */

import { describe, expect, it } from 'bun:test'
import { trendHoverIndex } from '../src/web/view'

/** 旧 onMove 公式（TrendChart 修复前）：plot rect 上误用 VB_W + PAD_L。 */
function legacyBuggyIndex(
  clientX: number,
  rectLeft: number,
  rectWidth: number,
  n: number,
  VB_W = 260,
  PAD_L = 38,
  plotW = 216,
): number {
  const relX = ((clientX - rectLeft) / rectWidth) * VB_W - PAD_L
  const i = Math.round((relX / plotW) * (n - 1))
  return Math.max(0, Math.min(n - 1, i))
}

describe('trendHoverIndex：plot 捕获层坐标 → 数据下标', () => {
  const n = 88
  const rectLeft = 100
  const rectWidth = 400

  it('左缘 → 0；右缘 → n-1', () => {
    expect(trendHoverIndex(rectLeft, rectLeft, rectWidth, n)).toBe(0)
    expect(trendHoverIndex(rectLeft + rectWidth, rectLeft, rectWidth, n)).toBe(n - 1)
  })

  it('plot 中央 → 中位下标（n=88 → 43；iters[43]≈it43/44）', () => {
    const midX = rectLeft + rectWidth / 2
    const i = trendHoverIndex(midX, rectLeft, rectWidth, n)
    expect(i).toBe(Math.round(0.5 * (n - 1))) // 43
    // 不得再出现旧 bug 的 ~37（用户报的 it41 量级偏左）
    expect(Math.abs(i - 0.5 * (n - 1))).toBeLessThanOrEqual(1)
  })

  it('1/4 与 3/4 位置线性对应', () => {
    expect(trendHoverIndex(rectLeft + rectWidth * 0.25, rectLeft, rectWidth, n)).toBe(
      Math.round(0.25 * (n - 1)),
    )
    expect(trendHoverIndex(rectLeft + rectWidth * 0.75, rectLeft, rectWidth, n)).toBe(
      Math.round(0.75 * (n - 1)),
    )
  })

  it('越界钳制；n≤1 或 rectWidth≤0 安全回落 0', () => {
    expect(trendHoverIndex(rectLeft - 50, rectLeft, rectWidth, n)).toBe(0)
    expect(trendHoverIndex(rectLeft + rectWidth + 50, rectLeft, rectWidth, n)).toBe(n - 1)
    expect(trendHoverIndex(10, 0, 100, 1)).toBe(0)
    expect(trendHoverIndex(10, 0, 0, 88)).toBe(0)
  })

  it('回归：旧公式在 plot 中心偏左，与正确下标不一致（证明 bug 存在过）', () => {
    const midX = rectLeft + rectWidth / 2
    const correct = trendHoverIndex(midX, rectLeft, rectWidth, n)
    const buggy = legacyBuggyIndex(midX, rectLeft, rectWidth, n)
    // JS Math.round(43.5)=44（半值向上）；旧公式 ~37，系统性偏左
    expect(correct).toBe(Math.round(0.5 * (n - 1)))
    expect(buggy).toBeLessThan(correct - 3)
  })
})
