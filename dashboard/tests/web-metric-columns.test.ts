/**
 * web-metric-columns.test.ts — 逐轮指标列模型（src/web/view/metric-columns.ts）
 *
 * 分层：src/web/view/metric-columns.ts（纯数据 + 纯函数）
 *
 * 为什么要这个文件（DECISIONS §2026-09-20-metric-table-single-home）：同一套逐轮指标被渲染在
 * 首页「最新 6 轮」速览表与 `/metrics` 完整表两处。**结构**本来就该不同（速览 vs 分析），但
 * **列模型**（表头文字 / hover 口径 / 数字对齐）只该有一份——此前是手写两遍，能一致纯靠人抄得
 * 仔细（实测已分叉：`dmgPerKill` 的 hover 文案两处不一样）。
 *
 * 三层：
 *   ① 模型自身完整性（键 ↔ 定义、顺序数组、数字列清单）；
 *   ② **源文件级闸**：两个渲染器里不许再有手写的列定义；
 *   ③ **渲染级闸**：两处渲染出的表头文字 / hover 口径 / 数字对齐必须逐一等于模型。
 *
 * ⚠ `theme.css` 被整体内联进 SSR 的 `<style>`，所以断言页面必须切 `#root`（见 slice()）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import {
  colsOf,
  EVAL_ROW_KEYS,
  MAIN_ROW_KEYS,
  METRIC_COLS,
  TC_METRICS_FILTER,
  type ConsoleStateView,
  type IterRow,
  type MetricColKey,
} from '../src/web/view'

const ALL_KEYS = Object.keys(METRIC_COLS) as MetricColKey[]

/** 只取 `#root` 内的渲染体（整份 theme.css 内联在 head 的 <style> 里，不切就断言到样式表）。 */
function slice(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

/** 抽出 HTML 里所有 `<th …>文字</th>`（含可选 title 与数字对齐类）。
 *
 *  ⚠ 不能用 `<th([^>]*)>`——它会先匹配到 `<thead>`（实测：第一条“表头”解析成了 `thead`，
 *  于是断言全错位）。所以要求 `<th` 后面紧跟空白或 `>`。 */
function heads(html: string): Array<{ label: string; title: string | null; num: boolean }> {
  const out: Array<{ label: string; title: string | null; num: boolean }> = []
  for (const m of html.matchAll(/<th(?=[\s>])([^>]*)>([\s\S]*?)<\/th>/g)) {
    const t = m[1].match(/title="([^"]*)"/)
    out.push({
      label: m[2].replace(/<[^>]*>/g, '').trim(),
      title: t ? t[1] : null,
      num: /\btc-num\b/.test(m[1]),
    })
  }
  return out
}

/** 一行最小可用指标行（与 web-app-hero-overview.test.ts 的同名夹具同形）。 */
function fakeRow(iter: number, evalData: IterRow['evalData'] = null): IterRow {
  return {
    iter,
    time: '2026-09-20 09:00:00',
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
    evalData,
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

const ROWS = Array.from({ length: 3 }, (_, i) => fakeRow(i + 1))

describe('列模型：完整性（键 ↔ 定义）', () => {
  it('每个定义都自带与记录键相同的 key，label 非空', () => {
    for (const k of ALL_KEYS) {
      expect(METRIC_COLS[k].key).toBe(k)
      expect(METRIC_COLS[k].label.length).toBeGreaterThan(0)
    }
  })

  it('label 不重复（重复 = 两列在表上分不出来）', () => {
    const labels = ALL_KEYS.map((k) => METRIC_COLS[k].label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('两个顺序数组都指向已定义的键、且各自无重复', () => {
    for (const k of MAIN_ROW_KEYS) expect(METRIC_COLS[k]).toBeDefined()
    for (const k of EVAL_ROW_KEYS) expect(METRIC_COLS[k]).toBeDefined()
    expect(new Set(MAIN_ROW_KEYS).size).toBe(MAIN_ROW_KEYS.length)
    expect(new Set(EVAL_ROW_KEYS).size).toBe(EVAL_ROW_KEYS.length)
  })

  it('colsOf 保序（表头顺序不由渲染器决定）', () => {
    expect(colsOf(MAIN_ROW_KEYS).map((c) => c.key)).toEqual([...MAIN_ROW_KEYS])
    expect(colsOf(EVAL_ROW_KEYS).map((c) => c.key)).toEqual([...EVAL_ROW_KEYS])
    expect(colsOf([]).length).toBe(0)
  })

  it('数字列清单（右对齐 + tabular-nums 的两位一体）', () => {
    for (const k of [
      'avgWinTicks',
      'kills',
      'dmgPerKill',
      'residualHp',
      'loot',
      'scoreMean',
      'lr',
    ]) {
      expect(METRIC_COLS[k as MetricColKey].num).toBe(true)
    }
    for (const k of ['time', 'winRate', 'evalRate', 'wver']) {
      expect(METRIC_COLS[k as MetricColKey].num).toBeFalsy()
    }
  })

  it('配对列的 hover 口径与 view/format.ts 同源（不是第二份文案）', () => {
    expect(METRIC_COLS.b01.title).toBe('基线输、新权重赢的局数——新学会的本事，涨没涨看它')
    expect(METRIC_COLS.delta.title?.startsWith('净涨幅=b01−b10')).toBe(true)
    expect(METRIC_COLS.overfit.title?.length).toBeGreaterThan(10)
  })
})

describe('源文件级闸：渲染器不再手写列定义', () => {
  const read = (p: string): string => readFileSync(p, 'utf8')

  it('Hero：表头只有 colHead 一处模板（没有第二个 `<th>` 字面量）', () => {
    const src = read('src/web/app/panels/Hero.tsx')
    // 注释里提到 `<th>` 不算（说明文字本来就该提）；只看代码本体。
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
    // 唯一允许的 `<th>` 是 colHead 里的模板；表头**列表**必须来自模型。
    expect(code.match(/<th\b/g)?.length).toBe(1)
    expect(code).toContain('function colHead')
    expect(src).toContain('colsOf(MAIN_ROW_KEYS).map(colHead)')
    expect(src).toContain('colsOf(EVAL_ROW_KEYS).map(colHead)')
    // 空态 colSpan 也由模型算（此前手写 15，而 eval 表实为 16 列）。
    expect(src).toContain('colSpan={EVAL_ROW_KEYS.length}')
  })

  it('MetricsTable：列壳全部由 colDef 取模型（零内联 label/align/thTitle）', () => {
    const src = read('src/web/app/panels/MetricsTable.tsx')
    const lines = src.split('\n').filter((l) => !l.includes('{ value:'))
    const bad = lines.filter((l) => /^\s*(label|align|thTitle): '.*',$/.test(l))
    expect(bad).toEqual([])
    expect(src).toContain("...colDef('")
  })
})

describe('渲染级闸：两处表头 = 同一份模型', () => {
  it('首页主行表：表头逐列等于 MAIN_ROW_KEYS（文字 + hover 口径 + 数字对齐）', async () => {
    const { Hero } = await import('../src/web/app/panels/Hero')
    const html = renderToString(h(Hero, { stateView: mkView(ROWS), onMore: () => {} }))
    const got = heads(slice(html))
    const want = colsOf(MAIN_ROW_KEYS)
    expect(got.map((x) => x.label)).toEqual(want.map((c) => c.label))
    for (const [i, col] of want.entries()) {
      expect(got[i].title).toBe(col.title ?? null)
      expect(got[i].num).toBe(Boolean(col.num))
    }
  })

  it('指标页表（all 模式）：每个表头都能在模型里找到，且口径一字不差', async () => {
    const { MetricsTable } = await import('../src/web/app/panels/MetricsTable')
    const html = renderToString(h(MetricsTable, { stateView: mkView(ROWS) }))
    const hs = heads(slice(html))
    expect(hs.length).toBeGreaterThan(8)
    const byLabel = new Map(ALL_KEYS.map((k) => [METRIC_COLS[k].label, METRIC_COLS[k]]))
    for (const x of hs) {
      const col = byLabel.get(x.label)
      expect(col, `表头「${x.label}」不在列模型里`).toBeDefined()
      expect(x.title).toBe(col!.title ?? null)
      expect(x.num).toBe(Boolean(col!.num))
    }
  })

  it('指标页表（eval 模式）：列序逐列等于 EVAL_ROW_KEYS（与首页 eval 表同一份）', async () => {
    const { MetricsTable } = await import('../src/web/app/panels/MetricsTable')
    const store: Record<string, string> = { [TC_METRICS_FILTER]: 'eval' }
    const g = globalThis as { localStorage?: unknown }
    const saved = g.localStorage
    g.localStorage = {
      getItem: (k: string): string | null => store[k] ?? null,
      setItem: (k: string, v: string): void => {
        store[k] = v
      },
      removeItem: (k: string): void => {
        delete store[k]
      },
    }
    try {
      const html = renderToString(h(MetricsTable, { stateView: mkView(ROWS) }))
      const got = heads(slice(html)).map((x) => x.label)
      expect(got).toEqual(colsOf(EVAL_ROW_KEYS).map((c) => c.label))
    } finally {
      g.localStorage = saved
    }
  })
})
