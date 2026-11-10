/**
 * web-formula-metrics.test.ts — hover 里的 formula 名（用户 2026-11-10）。
 *
 * 分层：src/web/view/formula-metrics.ts（映射）+ series.ts（`Series.formula`）
 *       + TrendChart（tooltip 多一行）+ metric-table-cols（表头 / 单元格 hover）。
 *
 * 五个钉子：
 *  ① 名单 = 90 个 metrics 列、无重复、去掉两个 eval 专属读数（公式里写不到）；
 *  ② 四种键形状（账本键 / `bm:` 列键 / `behavior-…` 走势键 / 聚合键）都能认出来；
 *  ③ 非公式指标（胜率、胜局耗时、歼灭率…）一律 null（宁可不说，不许冒充）；
 *  ④ 走势序列带 formula，指标表的表头与单元格 hover 都写同一句；
 *  ⑤ 两处渲染共用一个文案函数（`formulaLabel`），不各写一种标点。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { buildMetricCols } from '../src/web/app/panels/metric-table-cols'
import {
  BEHAVIOR_METRICS,
  behaviorMetricSeries,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  FORMULA_METRIC_NAMES,
  formulaLabel,
  formulaMetricName,
  metricSeries,
  NON_FORMULA_METRIC_KEYS,
  type IterRow,
  type MetricStats,
} from '../src/web/view'

/** 六种统计量同值（这些用例只关心「哪一列、叫什么」，不关心分布形状）。 */
const st = (v: number): MetricStats => ({
  mean: v,
  median: v,
  min: v,
  max: v,
  p90: v,
  p99: v,
})

/** 最小可用指标行（只填这些用例真正读到的字段）。 */
function row(iter: number, bm: Record<string, MetricStats> | null): IterRow {
  return {
    iter,
    time: '2026-11-10 09:00:00',
    winRate: 0.5,
    scoreMean: 1,
    scoreStd: 0,
    samples: 10,
    rolloutSec: 1,
    ppoSec: 1,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0,
    entropy: 0,
    policyLoss: 0,
    valueLoss: 0,
    meanRet: 0,
    lr: 0,
    expectedGames: 10,
    halted: false,
    topDims: '',
    avgTicks: 900,
    accuracy: 0.5,
    loot: 0,
    kills: 0,
    actuals: {
      games: 2,
      totalKills: 3,
      totalPU: 4,
      avgTicks: 900,
      avgResidualHp: null,
      behaviorMetrics: bm,
    },
    evalData: null,
  }
}

describe('FORMULA_METRIC_NAMES：公式可用的指标名', () => {
  it('恰好 90 个（= reward_library.METRICS）、无重复、都能在逐局指标注册表里找到', () => {
    expect(FORMULA_METRIC_NAMES.length).toBe(90)
    expect(new Set(FORMULA_METRIC_NAMES).size).toBe(90)
    const known = new Set<string>(BEHAVIOR_METRICS.map((m) => m.key))
    for (const n of FORMULA_METRIC_NAMES) expect(known.has(n)).toBe(true)
  })

  it('两个 eval 专属读数不在名单里（rollout 侧没有该列 ⇒ 公式写不到）', () => {
    expect([...NON_FORMULA_METRIC_KEYS]).toEqual(['aliveTicks', 'postThawDamageRate'])
    for (const k of NON_FORMULA_METRIC_KEYS) expect(FORMULA_METRIC_NAMES).not.toContain(k)
  })
})

describe('formulaMetricName：四种键形状都认', () => {
  it('账本键 / 列键 / 走势键 → 同名', () => {
    expect(formulaMetricName('ticks')).toBe('ticks')
    expect(formulaMetricName('bm:threatTicks')).toBe('threatTicks')
    expect(formulaMetricName('behavior-playerHpRatio-median-eval')).toBe('playerHpRatio')
  })

  it('聚合图：每局平均击杀 / 拾取 → 对应的 metrics 列', () => {
    expect(formulaMetricName('killsAbs')).toBe('kills')
    expect(formulaMetricName('pu')).toBe('powerUpsCollected')
  })

  it('非公式指标一律 null（不冒充）', () => {
    for (const k of [
      'winRate',
      'eval',
      'avgWinTicks',
      'killsPct',
      'loot',
      'bm:aliveTicks',
      'bm:nope',
      'behavior-aliveTicks-mean-eval',
    ]) {
      expect(formulaMetricName(k)).toBeNull()
    }
  })

  it('文案只有一种写法', () => {
    expect(formulaLabel('kills')).toBe('formula: kills')
  })
})

describe('走势序列：带上 formula 名', () => {
  const rows = [row(1, { ticks: st(100), aliveTicks: st(300) })]

  it('基础图：只有真对应的那几张带名字', () => {
    const s = metricSeries(rows)
    expect(s.find((x) => x.key === 'killsAbs')!.formula).toBe('kills')
    expect(s.find((x) => x.key === 'pu')!.formula).toBe('powerUpsCollected')
    for (const k of ['winRate', 'killsPct', 'winTicks', 'winHp']) {
      expect(s.find((x) => x.key === k)!.formula).toBeUndefined()
    }
  })

  it('逐局指标图：带 formula；eval 专属列没有', () => {
    expect(behaviorMetricSeries(rows, 'ticks', 'mean', 'rollout').formula).toBe('ticks')
    expect(behaviorMetricSeries(rows, 'aliveTicks', 'mean', 'eval').formula).toBeUndefined()
  })
})

describe('指标表：表头与单元格 hover 都写 formula 名', () => {
  const ea = {
    course: 'c',
    ckpts: [],
    readOnly: false,
    busyIters: new Set<number>(),
    onEvalA: () => {},
  }
  const cols = buildMetricCols(ea, 'all', null, DEFAULT_BEHAVIOR_METRIC_STATS)
  const main = row(7, { ticks: st(120), aliveTicks: st(300) })
  const mainRow = { kind: 'main' as const, iter: 7, time: 't', main }
  const cellHtml = (key: string): string => {
    const col = cols.find((c) => c.key === key)
    expect(col, `找不到列 ${key}`).toBeDefined()
    return renderToString(h('div', null, col!.cell(mainRow)))
  }

  it('表头 hover（= 配置弹窗的释义）带 formula 名', () => {
    expect(cols.find((c) => c.key === 'bm:ticks')!.thTitle).toContain('formula: ticks')
  })

  it('单元格 hover 带 formula 名，且六种统计量照旧', () => {
    const html = cellHtml('bm:ticks')
    expect(html).toContain('formula: ticks')
    expect(html).toContain('中位数')
    expect(html).toContain('p99')
  })

  it('eval 专属列（公式里没有）不写', () => {
    expect(cols.find((c) => c.key === 'bm:aliveTicks')!.thTitle).not.toContain('formula:')
    expect(cellHtml('bm:aliveTicks')).not.toContain('formula:')
  })

  it('基础列（胜率等跨局聚合）不写', () => {
    expect(cols.find((c) => c.key === 'winRate')!.thTitle ?? '').not.toContain('formula:')
  })
})

describe('接线：两处渲染共用同一份 hover 文案', () => {
  const read = (p: string): string => readFileSync(p, 'utf8')

  it('TrendChart 渲染 formula 行（hover 才有 ⇒ 这里只做源文件闸）', () => {
    const src = read('src/web/components/TrendChart.tsx')
    expect(src).toContain('tc-trend__tip-formula')
    expect(src).toContain('formulaLabel')
    expect(read('src/web/theme.css')).toContain('.tc-trend__tip-formula')
  })
})
