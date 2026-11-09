/**
 * web-trend-config-modal.test.ts — 首页「走势配置」弹窗（用户 2026-11-09）。
 *
 * 三个钉子：
 *  ① 弹窗扫描全部指标：中文名 + 账本英文键 + 统计量选择器都在，方便和课程 formula 对照；
 *  ② 分类不是 UI 里手写的：每个指标恰好落在一个 `BEHAVIOR_METRIC_GROUPS` 分组里，
 *     而且每一组都有大白话说明（新增指标忘了填 hint 会在这里红）；
 *  ③ 关着的时候不渲染任何东西（与其它弹窗同款早退）。
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
  TREND_BASE_CHARTS,
} from '../src/web/view'

function render(open: boolean): string {
  return renderToString(
    h(TrendConfigModal, {
      open,
      visibility: DEFAULT_TREND_VISIBILITY,
      metricStats: DEFAULT_BEHAVIOR_METRIC_STATS,
      onToggleChart: () => {},
      onMetricStat: () => {},
      onClose: () => {},
    }),
  )
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
