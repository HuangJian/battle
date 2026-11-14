/**
 * web-trend-config-modal.test.ts — 首页「走势配置」弹窗（用户 2026-11-09）。
 *
 * 三个钉子：
 *  ① 弹窗扫描全部指标：中文名 + 账本英文键 + 统计量选择器都在，方便和课程 formula 对照；
 *  ② 分类不是 UI 里手写的：每个指标恰好落在一个 `BEHAVIOR_METRIC_GROUPS` 分组里，
 *     而且每一组都有大白话说明（新增指标忘了填 hint 会在这里红）；
 *  ③ 关着的时候不渲染任何东西（与其它弹窗同款早退）；
 *  ④ 草稿制（2026-11-14）：改勾选 / 顺序 / 统计量不直接落盘，只有「完成」会调 `onApply`。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { TrendConfigModal } from '../src/web/app/panels/TrendConfigModal'
import {
  BEHAVIOR_METRICS,
  BEHAVIOR_METRIC_GROUPS,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_TREND_VISIBILITY,
  resolveTrendOrder,
  TREND_BASE_CHARTS,
  type TrendChartKey,
} from '../src/web/view'

function render(
  open: boolean,
  order: TrendChartKey[] = resolveTrendOrder(null),
  visibility: Record<TrendChartKey, boolean> = DEFAULT_TREND_VISIBILITY,
): string {
  return renderToString(
    h(TrendConfigModal, {
      open,
      order,
      visibility,
      metricStats: DEFAULT_BEHAVIOR_METRIC_STATS,
      onApply: () => {},
      onClose: () => {},
    }),
  )
}

/** 顶部「已选走势」列表的 HTML 片段（空态 = ''）。 */
function pickedList(html: string): string {
  const start = html.indexOf('<ol class="tc-trendcfg__picked"')
  if (start < 0) return ''
  return html.slice(start, html.indexOf('</ol>', start))
}

describe('TrendConfigModal · 分组与英文键', () => {
  it('关着 ⇒ 不渲染遮罩', () => {
    expect(render(false)).toBe('')
  })

  it('打开 ⇒ 七类逐局指标 + 基础走势都上屏，且每类写明用途', () => {
    const html = render(true)
    expect(html).toContain('走势配置')
    expect(html).toContain('基础走势')
    for (const g of BEHAVIOR_METRIC_GROUPS) {
      expect(html).toContain(g.label)
      expect(html).toContain(g.desc)
    }
  })

  it('每个指标都给出中文名、账本英文键与统计量选择器', () => {
    const html = render(true)
    for (const m of BEHAVIOR_METRICS) {
      expect(html).toContain(`<span class="tc-trendcfg__cn">${m.label}</span>`)
      expect(html).toContain(`<code class="tc-trendcfg__key">${m.key}</code>`)
      expect(html).toContain(`aria-label="${m.label}统计量"`)
    }
    for (const c of TREND_BASE_CHARTS) {
      expect(html).toContain(`<span class="tc-trendcfg__cn">${c.label}</span>`)
      expect(html).toContain(`<code class="tc-trendcfg__key">${c.key}</code>`)
    }
    // 复选框数量 = 六张基础图 + 全部逐局指标；统计量下拉只属于逐局指标。
    expect((html.match(/type="checkbox"/g) ?? []).length).toBe(
      TREND_BASE_CHARTS.length + BEHAVIOR_METRICS.length,
    )
    expect((html.match(/<select/g) ?? []).length).toBe(BEHAVIOR_METRICS.length)
  })

  it('顶部「已选走势」= 勾选中的图，且按传进来的键序排', () => {
    const order = resolveTrendOrder(null)
    const html = render(true, order)
    const list = pickedList(html)
    expect(list.length).toBeGreaterThan(0)
    // 默认勾选 = 基础六图；逐局指标一个都不在顶部列表里。
    const checked = order.filter((k) => DEFAULT_TREND_VISIBILITY[k])
    expect(checked.length).toBe(TREND_BASE_CHARTS.length)
    for (const c of TREND_BASE_CHARTS)
      expect(list).toContain(`<span class="tc-trendcfg__cn">${c.label}</span>`)
    for (const m of BEHAVIOR_METRICS) {
      expect(list).not.toContain(`<span class="tc-trendcfg__cn">${m.label}</span>`)
    }
    // 顺序 = order 序（不是模型序）：把「胜率」排到最后，列表里就得最后出现。
    const moved = [...order.filter((k) => k !== 'winRate'), 'winRate'] as TrendChartKey[]
    const list2 = pickedList(render(true, moved))
    const rateAt = list2.indexOf('胜率')
    const ticksAt = list2.indexOf('胜局耗时')
    expect(rateAt).toBeGreaterThan(0)
    expect(ticksAt).toBeGreaterThan(0)
    expect(rateAt).toBeGreaterThan(ticksAt)
  })

  it('顶部列表的 ↑↓ 在首尾禁用，中间项两个键都在', () => {
    const list = pickedList(render(true))
    /** 取某个按钮的整个开始标签（属性顺序不参与断言）。 */
    const tag = (ariaLabel: string): string => {
      const i = list.indexOf(`aria-label="${ariaLabel}"`)
      expect(i).toBeGreaterThan(0)
      return list.slice(list.lastIndexOf('<button', i), list.indexOf('>', i))
    }
    const first = TREND_BASE_CHARTS[0]
    expect(tag(`${first.label}上移`)).toContain('disabled')
    expect(tag(`${first.label}下移`)).not.toContain('disabled')
    // 最后一项（默认序的最后 = 道具）↓ 禁用。
    const last = TREND_BASE_CHARTS[TREND_BASE_CHARTS.length - 1]
    expect(tag(`${last.label}下移`)).toContain('disabled')
    expect(tag(`${last.label}上移`)).not.toContain('disabled')
  })

  it('顶部每一行都有「取消显示」键（不用滚到下面去找同一项）', () => {
    const list = pickedList(render(true))
    for (const c of TREND_BASE_CHARTS) {
      expect(list).toContain(`aria-label="${c.label}取消显示"`)
    }
    // 取消键永不禁用（首尾也一样）；它不属于「换位」那对。
    const i = list.indexOf('aria-label="胜率取消显示"')
    expect(i).toBeGreaterThan(0)
    const tag = list.slice(list.lastIndexOf('<button', i), list.indexOf('>', i))
    expect(tag).not.toContain('disabled')
    expect(tag).toContain('tc-trendcfg__mv--off')
    // 每行三个键：↑ / ↓ / ×；勾着的行没有 ✓（恢复键要置灰之后才出现）。
    expect((list.match(/<button/g) ?? []).length).toBe(TREND_BASE_CHARTS.length * 3)
    expect(list).not.toContain('恢复显示')
    expect(list).not.toContain('tc-trendcfg__picked-row--off')
  })

  it('草稿制：渲染时不调 onApply（只有「完成」才提交）', () => {
    let calls = 0
    const html = renderToString(
      h(TrendConfigModal, {
        open: true,
        order: resolveTrendOrder(null),
        visibility: DEFAULT_TREND_VISIBILITY,
        metricStats: DEFAULT_BEHAVIOR_METRIC_STATS,
        onApply: () => {
          calls++
        },
        onClose: () => {},
      }),
    )
    expect(calls).toBe(0)
    expect(html).toContain('完成')
    // 正文说清「点完成才生效」，不让用户以为改了就算数。
    expect(html).toContain('点「完成」才生效')
  })

  it('一项都没勾 ⇒ 顶部显示空态文案，而不是空列表', () => {
    const allOff = Object.fromEntries(
      Object.keys(DEFAULT_TREND_VISIBILITY).map((k) => [k, false]),
    ) as Record<TrendChartKey, boolean>
    const html = render(true, resolveTrendOrder(null), allOff)
    expect(pickedList(html)).toBe('')
    expect(html).toContain('还没勾选任何走势图')
    // 逐局指标仍然可勾（列表没被藏掉）
    expect(html).toContain('aria-label="危险时长统计量"')
  })

  it('分类数据自洽：每个指标恰好落在一组，且每组都有大白话说明', () => {
    const seen = new Set<string>()
    let total = 0
    for (const g of BEHAVIOR_METRIC_GROUPS) {
      expect(g.label.length).toBeGreaterThan(0)
      expect(g.desc.length).toBeGreaterThan(10)
      const items = BEHAVIOR_METRICS.filter((m) => m.group === g.key)
      expect(items.length).toBeGreaterThan(0)
      total += items.length
      for (const m of items) seen.add(m.key)
    }
    expect(total).toBe(BEHAVIOR_METRICS.length)
    expect(seen.size).toBe(BEHAVIOR_METRICS.length)
    for (const m of BEHAVIOR_METRICS) {
      expect(m.hint.length).toBeGreaterThan(10)
      expect(m.hint).not.toBe(m.title)
      expect(BEHAVIOR_METRIC_GROUPS.some((g) => g.key === m.group)).toBe(true)
    }
  })
})
