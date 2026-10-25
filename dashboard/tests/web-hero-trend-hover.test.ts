/**
 * web-hero-trend-hover.test.ts — 首页六张趋势图的共享 hover（同一 it 全组亮读数）。
 *
 * 分层：src/web/app/panels/Hero.tsx（TrendCell 透传 hoverIter/onHoverIter）
 *      src/web/components/TrendChart.tsx（受控模式按 **iter** 对齐，不是按下标）
 *
 * SSR 首帧没有鼠标，但受控 prop 已经能把「共享 it」渲染形状钉住：
 *  ① 两个不同指标收到同一个 hoverIter ⇒ 各自显示自己在该轮的读数；
 *  ② 该 it 不在本图可见点里 ⇒ 不亮准星、不拿邻点冒充（eval 稀疏档的诚实留空）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { TrendCell } from '../src/web/app/panels/Hero'
import type { Series } from '../src/web/view'

const win: Series = { key: 'winRate', label: '胜率', vals: [0.4, 0.5, 0.6], iters: [1, 2, 3] }
const hp: Series = { key: 'winHp', label: '胜局残血', vals: [0.2, 0.8, 0.9], iters: [1, 2, 3] }

/** 渲染一格（rollout 单线档：共享 hover 与 eval 叠加无关，测形状更干净）。 */
function cell(series: Series, hoverIter: number | null): string {
  return renderToString(
    h(TrendCell, {
      series,
      seriesEval: undefined,
      source: 'rollout',
      fmt: (v) => (v == null ? '—' : String(v)),
      range: 'all',
      hoverIter,
      onHoverIter: () => {},
    }),
  )
}

describe('Hero 趋势图 · 共享 hover', () => {
  it('同一个 it 落到两张不同的图：各自显示自己在该轮的读数', () => {
    const a = cell(win, 2)
    const b = cell(hp, 2)
    expect(a).toContain('tc-trend__tip')
    expect(a).toContain('>it2</span>')
    expect(a).toContain('胜率 0.5')
    expect(a).toContain('stroke-dasharray="2 2"') // 准星也一起亮
    expect(b).toContain('tc-trend__tip')
    expect(b).toContain('>it2</span>')
    expect(b).toContain('胜局残血 0.8')
    expect(b).toContain('stroke-dasharray="2 2"')
  })

  it('按 iter 对齐而不是按下标：稀疏序列 iters=[10,30] 收到 30 时读的是第二个点', () => {
    const sparse: Series = {
      key: 'eval',
      label: 'eval 胜率',
      vals: [0.5, 0.7],
      iters: [10, 30],
    }
    const html = cell(sparse, 30)
    expect(html).toContain('>it30</span>')
    expect(html).toContain('eval 胜率 0.7')
  })

  it('共享 it 不在本图可见点里：不亮准星、不冒充读数', () => {
    const html = cell(win, 99)
    expect(html).toContain('tc-trend__svg')
    expect(html).not.toContain('tc-trend__tip')
    expect(html).not.toContain('stroke-dasharray="2 2"')
  })
})
