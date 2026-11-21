/**
 * web-trend-ybounds.test.ts — 趋势曲线必须留在绘图区内。
 *
 * 2026-11-21 报障：g9-def 的 eval 胜局耗时曲线冲出图外，遮住下方指标表。
 * 根因：y 下界按**全量** rollout 算（≈16177），上界按**截取后** eval 算（≈16780），
 * 下界高于大多数点 ⇒ py() 算出远超图高的 y，而 SVG overflow:visible 让它画了出来。
 */
import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { MultiTrendChart } from '../src/web/components/MultiTrendChart'
import { TrendChart } from '../src/web/components/TrendChart'
import type { Series } from '../src/web/view'

const series = (vals: number[]): Series => ({
  key: 'evalWinTicks',
  label: 'eval 胜局耗时',
  vals,
  iters: vals.map((_, i) => i),
  sparse: true,
})

/** 抠出 SSR SVG 折线里所有 M/L 的 y 坐标（轴刻度是 line，不在其中）。 */
function pathYs(html: string): number[] {
  return [...html.matchAll(/[ML]\d+(?:\.\d+)?\s+(\d+(?:\.\d+)?)/g)].map((m) => Number(m[1]))
}

describe('趋势图 y 轴不得把曲线顶出绘图区', () => {
  const H = 56
  const TOP = 6 // PAD_T
  const BOTTOM = H - 16 // PAD_B

  it('yMin 高于全部可见点时，曲线仍在图内（g9-def eval 越界回归）', () => {
    const html = renderToString(
      h(TrendChart, {
        series: series([9457, 10000, 12000]),
        range: 'all',
        fmt: (v) => String(v),
        height: H,
        yMin: 16177, // 全量 rollout 推出来的下界，远高于本图可见的 eval 点
      }),
    )
    const ys = pathYs(html)
    expect(ys.length).toBeGreaterThan(0)
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(TOP - 0.2)
    expect(Math.max(...ys)).toBeLessThanOrEqual(BOTTOM + 0.2)
  })

  it('MultiTrendChart：yBounds.yMin 高于全部可见点时也在图内', () => {
    const h2 = 72
    const html = renderToString(
      h(MultiTrendChart, {
        seriesList: [
          {
            course: 'g9-def',
            key: 'winTicks',
            color: 'var(--accent)',
            iters: [5, 10, 15],
            vals: [9457, 10000, 12000],
          },
        ],
        yBounds: { yMin: 16177 },
        fmt: (v) => String(v),
        height: h2,
        compact: true,
      }),
    )
    const ys = pathYs(html)
    expect(ys.length).toBeGreaterThan(0)
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(10 - 0.2) // MultiTrendChart PAD_T
    expect(Math.max(...ys)).toBeLessThanOrEqual(h2 - 24 + 0.2) // PAD_B
  })

  it('yMin 正常时仍按量级缩放（不回退成从 0 起）', () => {
    const html = renderToString(
      h(TrendChart, {
        series: series([100, 200]),
        range: 'all',
        fmt: (v) => String(v),
        height: H,
        yMin: 80,
      }),
    )
    // y 轴刻度由低到高 = yMin(80) / 140 / 200 —— 量级缩放保留，没有退化成从 0 起。
    const ticks = [...html.matchAll(/<text x="34"[^>]*>(\d+(?:\.\d+)?)</g)].map((m) => Number(m[1]))
    expect(ticks).toContain(80)
    expect(Math.min(...ticks)).toBeGreaterThan(0)
  })
})
