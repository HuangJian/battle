/** web-behavior-metrics.test.ts — 逐局行为指标统计 / 序列 / 显示配置 / 放大浮窗。
 *
 * 分层：src/web/view/behavior-metrics.ts + series.ts + components/TrendZoomModal.tsx
 *
 * 这些指标来自 `eval_log.jsonl` 的 event=eval 行；服务端按 (iter,wver) 算六种统计量后
 * 挂在 `EvalSummary.behaviorMetrics`。本文件钉客户端取数与浮窗展示，不碰 IO。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { TrendZoomModal } from '../src/web/components/TrendZoomModal'
import {
  behaviorMetricSeries,
  computeMetricStats,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_TREND_VISIBILITY,
  BEHAVIOR_METRICS,
  behaviorTrendKey,
  parseBehaviorMetricStats,
  parseTrendVisibility,
  sliceSeries,
  TREND_RANGE_OPTIONS,
  type IterRow,
  type MetricStats,
} from '../src/web/view'

function stats(mean: number): MetricStats {
  return { mean, median: mean, min: mean, max: mean, p90: mean, p99: mean }
}

function row(iter: number, danger?: MetricStats): IterRow {
  return {
    iter,
    actuals: danger ? { behaviorMetrics: { dangerTicks: danger } } : null,
    evalData: danger
      ? {
          behaviorMetrics: { dangerTicks: danger },
        }
      : null,
  } as unknown as IterRow
}

describe('行为指标统计量', () => {
  it('线性插值分位与 min/max/median/mean', () => {
    expect(computeMetricStats([1, 2, 3, 4, 5])).toEqual({
      mean: 3,
      median: 3,
      min: 1,
      max: 5,
      p90: 4.6,
      p99: 4.96,
    })
  })

  it('空数组 / 全非有限值 = null（缺读数，不补 0）', () => {
    expect(computeMetricStats([])).toBeNull()
    expect(computeMetricStats([Number.NaN, null, undefined, Number.POSITIVE_INFINITY])).toBeNull()
  })

  it('跳过非有限值后统计', () => {
    const s = computeMetricStats([null, 2, Number.NaN, 4])!
    expect(s.mean).toBe(3)
    expect(s.min).toBe(2)
    expect(s.max).toBe(4)
  })
})

describe('行为指标序列', () => {
  it('按 iter 正序、缺 eval 轮 = NaN 缺口', () => {
    const s = behaviorMetricSeries(
      [row(4, stats(8)), row(2, stats(2)), row(3)],
      'dangerTicks',
      'mean',
    )
    expect(s.sparse).toBe(true)
    expect(s.iters).toEqual([2, 3, 4])
    expect(s.vals).toEqual([2, Number.NaN, 8])
    expect(s.key).toBe('behavior-dangerTicks-mean-eval')
  })

  it('rollout 口径读 actuals.behaviorMetrics，与 eval 分开', () => {
    const s = behaviorMetricSeries(
      [row(2, stats(2)), row(4, stats(8))],
      'dangerTicks',
      'mean',
      'rollout',
    )
    expect(s.key).toBe('behavior-dangerTicks-mean-rollout')
    expect(s.vals).toEqual([2, 8])
    const e = behaviorMetricSeries(
      [row(2, stats(2)), row(4, stats(8))],
      'dangerTicks',
      'mean',
      'eval',
    )
    expect(e.vals).toEqual([2, 8])
  })

  it('60pct 按有效点数截取，保留最后一段', () => {
    const rows = [1, 2, 3, 4, 5].map((i) => row(i, stats(i)))
    const s = behaviorMetricSeries(rows, 'dangerTicks', 'mean')
    const sliced = sliceSeries(s, '60pct')
    expect(sliced.vals).toEqual([3, 4, 5])
    expect(sliced.iters).toEqual([3, 4, 5])
  })
})

describe('走势图显示配置', () => {
  it('注册表覆盖完整逐局指标集（不再只有 15 个）', () => {
    expect(BEHAVIOR_METRICS.length).toBeGreaterThanOrEqual(90)
    expect(BEHAVIOR_METRICS.some((m) => m.key === 'ticks')).toBe(true)
    expect(BEHAVIOR_METRICS.some((m) => m.key === 'playerShots')).toBe(true)
    expect(new Set(BEHAVIOR_METRICS.map((m) => m.key)).size).toBe(BEHAVIOR_METRICS.length)
  })

  it('默认六项开、逐局指标全关', () => {
    for (const m of BEHAVIOR_METRICS)
      expect(DEFAULT_TREND_VISIBILITY[behaviorTrendKey(m.key)]).toBe(false)
    for (const k of ['winRate', 'dmgPerKill', 'winTicks', 'kills', 'winHp', 'pu']) {
      expect(DEFAULT_TREND_VISIBILITY[k as keyof typeof DEFAULT_TREND_VISIBILITY]).toBe(true)
    }
  })

  it('坏 JSON / 未知键退默认，已知布尔值生效', () => {
    expect(parseTrendVisibility('{oops')).toEqual(DEFAULT_TREND_VISIBILITY)
    const v = parseTrendVisibility('{"bm:dangerTicks":true,"winRate":false,"nope":true}')
    expect(v[behaviorTrendKey('dangerTicks')]).toBe(true)
    expect(v.winRate).toBe(false)
    expect(v.pu).toBe(true)
    expect('nope' in v).toBe(false)
  })

  it('逐指标统计量默认均值；JSON 覆盖；旧单值铺到全部指标', () => {
    for (const m of BEHAVIOR_METRICS) {
      expect(DEFAULT_BEHAVIOR_METRIC_STATS[m.key]).toBe('mean')
    }
    const custom = parseBehaviorMetricStats(
      JSON.stringify({ dangerTicks: 'median', ticks: 'p99', nope: 'max' }),
    )
    expect(custom.dangerTicks).toBe('median')
    expect(custom.ticks).toBe('p99')
    expect(custom.playerShots).toBe('mean')
    expect(parseBehaviorMetricStats('{oops')).toEqual(DEFAULT_BEHAVIOR_METRIC_STATS)
    const legacy = parseBehaviorMetricStats('p90')
    expect(legacy.dangerTicks).toBe('p90')
    expect(legacy.ticks).toBe('p90')
  })

  it('范围档位文案为 全量 / 最近60% / 最近30%', () => {
    expect(TREND_RANGE_OPTIONS.map((o) => o.label)).toEqual(['全量', '最近60%', '最近30%'])
  })
})

describe('TrendZoomModal', () => {
  it('行为指标浮窗初始只按下配置的统计量，其它统计量可点击打开', () => {
    const html = renderToString(
      h(TrendZoomModal, {
        target: {
          title: '危险时长·中位数',
          fmt: (v) => String(v),
          behavior: {
            key: 'dangerTicks',
            iters: [row(2, stats(2)), row(4, stats(8))],
            stat: 'median',
            source: 'eval',
          },
        },
        range: 'all',
        onClose: () => {},
      }),
    )
    expect(html).toContain('tc-trendzoom')
    for (const label of ['均值', '中位数', '最小', '最大', 'p90', 'p99']) {
      expect(html).toContain(`>${label}</button>`)
    }
    // 六个按钮都在，只有配置的那一个没带 --off；其余五个可点开
    expect((html.match(/class="tc-trendzoom__lg"/g) ?? []).length).toBe(1)
    expect((html.match(/tc-trendzoom__lg--off/g) ?? []).length).toBe(5)
    expect((html.match(/aria-pressed="true"/g) ?? []).length).toBe(1)
    // 统计曲线用更粗的多序列图，不是单序列 TrendChart
    expect(html).toContain('tc-mchart__svg')
    expect(html).not.toContain('tc-mchart__legend')
  })

  it('target 为 null 时不渲染遮罩', () => {
    expect(
      renderToString(h(TrendZoomModal, { target: null, range: '30pct', onClose: () => {} })),
    ).toBe('')
  })
})
