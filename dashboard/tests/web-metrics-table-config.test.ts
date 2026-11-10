/**
 * web-metrics-table-config.test.ts — 指标表配置（用户 2026-11-10：像走势图一样可配置）。
 *
 * 分层：src/web/view/metric-columns.ts + app/panels/MetricsConfigModal.tsx + MetricsTable.tsx
 *
 * 四个钉子：
 *  ① 显隐偏好的解析：新键为准；旧键（DataTable 时代的 `tc.metrics.hidden`）迁移时补默认隐藏集合，
 *     免得 90+ 逐局指标列一起上屏；
 *  ② 统计量 → 表内读数/表头文字的映射（整页 SSR 恒为默认均值，改不了配置，所以单独锁纯函数）；
 *  ③ 弹窗扫描全部列：基础列一节 + 七类逐局指标，复选框与统计量下拉的数量对得上；
 *  ④ 指标页只留一个显隐入口：壳里的「列▾」收起、「指标配置」在场，逐局指标列默认不上屏。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { MetricsConfigModal } from '../src/web/app/panels/MetricsConfigModal'
import {
  BEHAVIOR_METRICS,
  BEHAVIOR_METRIC_GROUPS,
  behaviorColKey,
  colsOf,
  computeMetricStats,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_HIDDEN_COL_KEYS,
  EVAL_ROW_KEYS,
  METRIC_COLS,
  ROLLOUT_ROW_KEYS,
  metricColLabelWithStat,
  metricStatValue,
  parseHiddenCols,
  type ConsoleStateView,
  type IterRow,
  type MetricCol,
} from '../src/web/view'

/** 当前模式下的全部列（基础列 + 逐局指标列），顺序与表格模型一致。 */
function configCols(): MetricCol[] {
  const bm = Object.keys(METRIC_COLS).filter((k) => k.startsWith('bm:')) as Array<
    keyof typeof METRIC_COLS
  >
  return [...colsOf(ROLLOUT_ROW_KEYS), ...bm.map((k) => METRIC_COLS[k] as MetricCol)]
}

describe('parseHiddenCols：显隐偏好（含旧键迁移）', () => {
  it('什么都没有 ⇒ 默认隐藏集合（只有逐局指标列）', () => {
    const s = parseHiddenCols(null)
    expect([...s].sort()).toEqual([...DEFAULT_HIDDEN_COL_KEYS].sort())
    expect(s.has(behaviorColKey('ticks'))).toBe(true)
    expect(s.has('winRate')).toBe(false)
  })

  it('新键有值 ⇒ 只认它（手点的结果就是全部真相，不再补默认）', () => {
    const s = parseHiddenCols(JSON.stringify(['winRate', 'kl']))
    expect([...s].sort()).toEqual(['kl', 'winRate'])
    expect(s.has(behaviorColKey('ticks'))).toBe(false)
  })

  it('旧键迁移：旧集合 ∪ 默认隐藏集合（旧键存的时候还没有逐局指标列）', () => {
    const s = parseHiddenCols(null, JSON.stringify(['winRate']))
    expect(s.has('winRate')).toBe(true)
    for (const k of DEFAULT_HIDDEN_COL_KEYS) expect(s.has(k)).toBe(true)
  })

  it('坏 JSON / 非字符串数组 ⇒ 退默认（绝不把半张表弄丢）', () => {
    for (const bad of ['{', '{"a":1}', '[1,2]', 'null', '']) {
      expect([...parseHiddenCols(bad)].sort()).toEqual([...DEFAULT_HIDDEN_COL_KEYS].sort())
    }
  })
})

describe('统计量 → 表内读数 / 表头文字', () => {
  const stats = computeMetricStats([1, 2, 3, 10, 100])!

  it('读数取配置的那一种，缺读数 = null（渲染显 —，不冒充 0）', () => {
    expect(metricStatValue(stats, 'mean')).toBe(stats.mean)
    expect(metricStatValue(stats, 'median')).toBe(3)
    expect(metricStatValue(stats, 'max')).toBe(100)
    expect(metricStatValue(null, 'mean')).toBeNull()
    expect(metricStatValue({ ...stats, p90: Number.NaN }, 'p90')).toBeNull()
  })

  it('表头：均值不带后缀，其它统计量带后缀（否则读表的人以为还是均值）', () => {
    expect(metricColLabelWithStat('总时长', 'mean')).toBe('总时长')
    expect(metricColLabelWithStat('总时长', 'median')).toBe('总时长·中位数')
    expect(metricColLabelWithStat('总时长', 'p90')).toBe('总时长·p90')
  })
})

function renderModal(open: boolean, hidden = new Set<string>()): string {
  const cols = configCols().map((c) => ({ key: c.key, label: c.label, hint: c.title }))
  const stats = Object.fromEntries(BEHAVIOR_METRICS.map((m) => [m.key, 'median' as const]))
  return renderToString(
    h(MetricsConfigModal, {
      open,
      cols,
      hidden,
      stats: stats as never,
      onToggle: () => {},
      onStat: () => {},
      onClose: () => {},
    }),
  )
}

describe('MetricsConfigModal · 分组 + 显隐 + 统计量', () => {
  it('关着 ⇒ 不渲染遮罩', () => {
    expect(renderModal(false)).toBe('')
  })

  it('打开 ⇒ 基础列一节 + 七类逐局指标，每组都有大白话说明', () => {
    const html = renderModal(true)
    expect(html).toContain('指标表配置')
    expect(html).toContain('基础列')
    for (const g of BEHAVIOR_METRIC_GROUPS) {
      expect(html).toContain(g.label)
      expect(html).toContain(g.desc)
    }
  })

  it('每个逐局指标都有中文名、账本英文键与统计量下拉；复选框数 = 全部列', () => {
    const html = renderModal(true)
    for (const m of BEHAVIOR_METRICS) {
      expect(html).toContain(`<span class="tc-trendcfg__cn">${m.label}</span>`)
      expect(html).toContain(`<code class="tc-trendcfg__key">${m.key}</code>`)
      expect(html).toContain(`aria-label="${m.label}统计量"`)
    }
    expect((html.match(/type="checkbox"/g) ?? []).length).toBe(configCols().length)
    // 统计量下拉只属于逐局指标（基础列不是分布，没有统计量可选）。
    expect((html.match(/<select/g) ?? []).length).toBe(BEHAVIOR_METRICS.length)
  })

  it('勾选态跟着 hidden 走：藏起来的列不打勾', () => {
    const html = renderModal(true, new Set(['winRate', behaviorColKey('ticks')]))
    expect(html).toContain(' checked/>') // 大部分列照旧打勾
    // 藏起来的列：它那个复选框没 checked（Preact SSR 只输出裸属性 ` checked`）
    for (const label of ['胜率', '总时长']) {
      const at = html.indexOf(`<span class="tc-trendcfg__cn">${label}</span>`)
      expect(at).toBeGreaterThan(0)
      expect(html.slice(Math.max(0, at - 90), at)).not.toContain('checked')
    }
  })
})

// ────────────────────────── 指标页：唯一入口 + 默认不上屏 ──────────────────────────

function fakeRow(iter: number): IterRow {
  return {
    iter,
    time: '2026-11-10 09:00:00',
    winRate: 0.4,
    scoreMean: 1,
    scoreStd: 0.1,
    samples: 100,
    rolloutSec: 60,
    ppoSec: 120,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.02,
    entropy: 0.3,
    policyLoss: 0.1,
    valueLoss: 0.2,
    meanRet: 0.1,
    lr: 0.0001,
    expectedGames: 100,
    halted: false,
    topDims: '',
    avgTicks: 900,
    accuracy: 0.5,
    loot: 0.1,
    kills: 5,
    actuals: null,
    evalData: null,
  }
}

function mkView(iters: IterRow[]): ConsoleStateView {
  return {
    time: 't',
    course: 'kb1',
    courses: [],
    components: [],
    nodes: [],
    modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
    metrics: { available: true, iters },
    phase: { phase: 'idle', sinceMs: null, iter: null },
  }
}

const EA = {
  course: 'kb1',
  ckpts: [],
  readOnly: false,
  busyIters: new Set<number>(),
  onEvalA: () => {},
}

describe('首页速览表：列配置（备选列 = 完整指标表的列，用户 2026-11-10）', () => {
  it('首帧默认只藏逐局指标块（与指标页同一份默认），基础列全在', async () => {
    const hidden = parseHiddenCols(null)
    expect([...hidden].sort()).toEqual([...DEFAULT_HIDDEN_COL_KEYS].sort())
    const { buildMetricCols } = await import('../src/web/app/panels/metric-table-cols')
    const shown = buildMetricCols(EA, 'all', null, { ...DEFAULT_BEHAVIOR_METRIC_STATS }).filter(
      (c) => !hidden.has(c.key),
    )
    expect(shown.map((c) => c.key)).toEqual([...ROLLOUT_ROW_KEYS])
  })

  it('逐局指标 90+ 列全在备选里，打开就能上屏并跟着统计量走', async () => {
    const { buildMetricCols } = await import('../src/web/app/panels/metric-table-cols')
    const all = buildMetricCols(EA, 'all', null, { ...DEFAULT_BEHAVIOR_METRIC_STATS })
    const bm = all.filter((c) => c.key.startsWith('bm:'))
    expect(bm.length).toBe(BEHAVIOR_METRICS.length)
    // 一个都不藏 ⇒ 逐局指标全部上屏（用户：「所有可见指标都应该可以配置在首页显示」）
    const shown = all.filter((c) => !new Set<string>().has(c.key))
    expect(shown.length).toBe(all.length)
    // 统计量非均值 ⇒ 表头带后缀（两页同一套字号口径）
    const k = BEHAVIOR_METRICS[0].key
    const med = buildMetricCols(EA, 'all', null, {
      ...DEFAULT_BEHAVIOR_METRIC_STATS,
      [k]: 'median',
    }).find((c) => c.key === behaviorColKey(k))
    expect(med!.label).toBe(metricColLabelWithStat(METRIC_COLS[behaviorColKey(k)].label, 'median'))
    expect(med!.label).toContain('中位数')
  })

  it('Hero 速览表头部有「列配置」入口，弹窗关着时不渲染遮罩', async () => {
    const { Hero } = await import('../src/web/app/panels/Hero')
    const html = renderToString(h(Hero, { stateView: mkView([fakeRow(2)]), onMore: () => {} }))
    expect(html).toContain('列配置')
    expect(html).not.toContain('速览表列配置')
    // 格子由共享工厂铺：数字列右对齐，时间列仍是灰字
    expect(html).toMatch(/<td class="tc-num">/)
    expect(html).toContain('<span class="tc-muted">')
  })
})

describe('指标页：配置是显隐的唯一入口', () => {
  it('「指标配置」在场、「列▾」收起、逐局指标列默认不上屏', async () => {
    const { MetricsTable } = await import('../src/web/app/panels/MetricsTable')
    const html = renderToString(h(MetricsTable, { stateView: mkView([fakeRow(2), fakeRow(1)]) }))
    expect(html).toContain('指标配置')
    expect(html).not.toContain('列▾')
    for (const k of ['ticks', 'playerShots', 'dangerTicks']) {
      const label = METRIC_COLS[behaviorColKey(k as never)].label
      expect(html).not.toContain(`>${label}</th>`)
    }
    // 基础列照旧上屏（iter / 时间 / 胜率 …）
    expect(html).toContain('>胜率</th>')
    expect(colsOf(EVAL_ROW_KEYS).length).toBeGreaterThan(0) // 模型仍在（另一分支）
  })
})
