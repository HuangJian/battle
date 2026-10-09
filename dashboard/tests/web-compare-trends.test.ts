/**
 * web-compare-trends.test.ts — 「比较课程」弹窗：纯函数契约 + 组件 SSR 形状 + hydrate 纪律。
 *
 * 分层：src/web/view/compare-trends.ts（档位/切片/解析/选课/门闩）
 *      src/web/components/MultiTrendChart.tsx（数值 x 轴、N 条线、断笔、图例）
 *      src/web/app/panels/CompareTrendsModal.tsx（弹窗骨架 + 候选选择器）
 *
 * 为什么 SSR 也要测：整页 SSR 首帧弹窗**是关的**（`open=false` ⇒ null），只测弹窗等于测空气；
 * 而图形形状（几条线、x 按数值还是下标、虚线口径、空态文案）只能从渲染里看——与
 * `web-hero-trend-source.test.ts` 同款理由（那里的三档也是一条分支一个断言）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { CompareCoursePicker, CompareTrendsModal } from '../src/web/app/panels/CompareTrendsModal'
import { Hero } from '../src/web/app/panels/Hero'
import { MultiTrendSeries, MultiTrendChart } from '../src/web/components/MultiTrendChart'
import {
  COMPARE_COLORS,
  COMPARE_MAX_COURSES,
  COMPARE_METRIC_OPTIONS,
  COMPARE_METRIC_SPECS,
  addCompareCourse,
  colorOf,
  compareCourseCandidates,
  compareFmt,
  compareRequestKey,
  compareYBounds,
  defaultCompareCourses,
  filterCourseCandidates,
  isCompareMetric,
  nearestIter,
  parseCompareCourses,
  parseIterBound,
  parseIterRange,
  removeCompareCourse,
  shouldApplyCompareData,
  sliceIterRange,
  toChartValues,
  visibleCompareSeries,
  type CompareSeriesData,
} from '../src/web/view'
import type { ConsoleStateView } from '../src/web/view'

const countOccurrences = (s: string, needle: string): number => s.split(needle).length - 1

function chartHtml(
  seriesList: MultiTrendSeries[],
  metric: 'winRate' | 'winTicks' | 'kills' = 'winRate',
): string {
  return renderToString(h(MultiTrendChart, { seriesList, metric, fmt: compareFmt(metric) }))
}

const series = (
  course: string,
  key: string,
  iters: number[],
  vals: number[],
  color = '#111111',
): MultiTrendSeries => ({ course, key, color, iters, vals })

describe('比较档位表（纯函数）', () => {
  it('6 档顺序即上屏顺序（用户给的顺序），且值得是数据 key', () => {
    expect(COMPARE_METRIC_OPTIONS.map((o) => o.value)).toEqual([
      'winRate',
      'kills',
      'dmgPerKill',
      'winTicks',
      'winHp',
      'pu',
    ])
    expect(COMPARE_METRIC_OPTIONS.map((o) => o.label)).toEqual([
      '胜率',
      '击杀',
      '承伤',
      '胜局耗时',
      '胜局残血',
      '道具',
    ])
  })

  it('档位 → metricSeries key 映射逐条钉死（错一个就是把 A 画成 B）', () => {
    const got = Object.fromEntries(
      Object.values(COMPARE_METRIC_SPECS).map((s) => [s.metric, `${s.mainKey}|${s.evalKey}`]),
    )
    expect(got).toEqual({
      winRate: 'winRate|eval',
      kills: 'kills|evalKills',
      dmgPerKill: 'dmgPerKill|evalDmgPerKill',
      winTicks: 'winTicks|evalWinTicks',
      winHp: 'winHp|evalWinHp',
      pu: 'pu|evalPu',
    })
  })

  it('isCompareMetric：6 个合法值通过，大小写变体/空串/null 一律不收', () => {
    for (const v of ['winRate', 'kills', 'dmgPerKill', 'winTicks', 'winHp', 'pu']) {
      expect(isCompareMetric(v)).toBe(true)
    }
    for (const v of ['', 'WinRate', 'winrate', 'eval', 'dmg', null, undefined]) {
      expect(isCompareMetric(v as string | null | undefined)).toBe(false)
    }
  })

  it('格式化：百分比 1 位 / 耗时整数 / 道具 2 位；null 一律 —（不冒充 0）', () => {
    expect(compareFmt('winRate')(0.4237)).toBe('42.4%')
    expect(compareFmt('winTicks')(123.6)).toBe('124')
    expect(compareFmt('pu')(1.5)).toBe('1.50')
    for (const m of ['winRate', 'kills', 'dmgPerKill', 'winTicks', 'winHp', 'pu'] as const) {
      expect(compareFmt(m)(null)).toBe('—')
    }
  })
})

describe('compareYBounds（y 轴下界单一出处）', () => {
  it('胜率：基底 min(dataMin, 0.3)——数据更低时贴数据、更高时锁 30%', () => {
    expect(compareYBounds('winRate', [{ vals: [0.4, 0.6] }])).toEqual({ yFloor: 0.3 })
    expect(compareYBounds('winRate', [{ vals: [0.1, 0.2] }])).toEqual({ yFloor: 0.1 })
  })

  it('击杀/承伤/残血/道具：基底 min(dataMin, 0)（锁 0）', () => {
    expect(compareYBounds('kills', [{ vals: [0.2, 0.9] }])).toEqual({ yFloor: 0 })
    expect(compareYBounds('dmgPerKill', [{ vals: [0.3, 0.4] }])).toEqual({ yFloor: 0 })
    expect(compareYBounds('winHp', [{ vals: [0.5] }])).toEqual({ yFloor: 0 })
    expect(compareYBounds('pu', [{ vals: [1.2, 2.0] }])).toEqual({ yFloor: 0 })
  })

  it('胜局耗时：max(0, min×0.8, max×0.5)（沿用 winTicksYMin 公式）', () => {
    expect(compareYBounds('winTicks', [{ vals: [100, 200] }])).toEqual({ yMin: 100 })
    expect(compareYBounds('winTicks', [{ vals: [1000, 1200] }])).toEqual({ yMin: 800 })
    expect(compareYBounds('winTicks', [{ vals: [1, 400] }])).toEqual({ yMin: 200 })
  })

  it('跨课合并计算（不是逐课），NaN / null 都不算有效值；全空 ⇒ {}', () => {
    expect(compareYBounds('winRate', [{ vals: [0.5, Number.NaN] }, { vals: [null, 0.2] }])).toEqual(
      { yFloor: 0.2 },
    )
    expect(compareYBounds('winRate', [{ vals: [Number.NaN, null] }])).toEqual({})
  })
})

describe('sliceIterRange / toChartValues（切片与断笔契约）', () => {
  it('按 iter 数值过滤（含端点）；单侧 null = 该侧不限', () => {
    const iters = [1, 2, 3, 4, 5]
    const vals = [0.1, 0.2, 0.3, 0.4, 0.5]
    expect(sliceIterRange(iters, vals, 2, 4)).toEqual({ iters: [2, 3, 4], vals: [0.2, 0.3, 0.4] })
    expect(sliceIterRange(iters, vals, null, 2).iters).toEqual([1, 2])
    expect(sliceIterRange(iters, vals, 4, null).iters).toEqual([4, 5])
    expect(sliceIterRange(iters, vals, null, null).iters).toEqual(iters)
    expect(sliceIterRange(iters, vals, 3, 2).iters).toEqual([])
  })

  it('NaN 归一成 null（过线契约：JSON 不能带 NaN），null 保持 null', () => {
    const out = sliceIterRange([1, 2, 3], [0.1, Number.NaN, null], null, null)
    expect(out.vals).toEqual([0.1, null, null])
  })

  it('toChartValues：null 还原成 NaN（断笔，不是 0）', () => {
    expect(toChartValues([0.1, null, 0.3]).map((v) => (Number.isFinite(v) ? v : 'NaN'))).toEqual([
      0.1,
      'NaN',
      0.3,
    ])
  })
})

describe('起止 it 解析（非法 ⇒ 不发请求）', () => {
  it('parseIterBound：空=全量 / 非负整数通过 / 负数小数文本非法', () => {
    expect(parseIterBound('')).toBe(null)
    expect(parseIterBound(null)).toBe(null)
    expect(parseIterBound('0')).toBe(0)
    expect(parseIterBound(' 30 ')).toBe(30)
    for (const bad of ['-1', '3.5', 'x', '1e3', '01x']) expect(parseIterBound(bad)).toBe('invalid')
  })

  it('parseIterRange：from > to 非法（静默空图会被误读成「课坏了」）', () => {
    expect(parseIterRange('5', '2')).toEqual({ ok: false, message: '起始 it 大于结束 it' })
    expect(parseIterRange('x', '')).toEqual({ ok: false, message: '起止 it 须为非负整数' })
    expect(parseIterRange('', '')).toEqual({ ok: true, range: { from: null, to: null } })
    expect(parseIterRange('10', '')).toEqual({ ok: true, range: { from: 10, to: null } })
    expect(parseIterRange('10', '10')).toEqual({ ok: true, range: { from: 10, to: 10 } })
  })

  it('parseCompareCourses：trim + 去重 + 丢空段', () => {
    expect(parseCompareCourses(' a, b ,a,,c ')).toEqual(['a', 'b', 'c'])
    expect(parseCompareCourses(null)).toEqual([])
  })
})

describe('选课助手（默认/增删/候选/搜索）', () => {
  it('默认选课 = 在训 ∪ 当前课（去重保序，取前 6）；全空退 courses 前 2', () => {
    expect(defaultCompareCourses(['a', 'b'], ['b'], 'cur')).toEqual(['b', 'cur'])
    expect(defaultCompareCourses(['a', 'b'], ['b', 'a'], 'cur')).toEqual(['b', 'a', 'cur'])
    expect(defaultCompareCourses(['a', 'b'], [], '')).toEqual(['a', 'b'])
    expect(defaultCompareCourses([], [], 'cur')).toEqual(['cur'])
    // 全空 ⇒ 退 `courses` 前 2；训练/当前存在 ⇒ 只取它们（∩ courses）并截前 6
    expect(defaultCompareCourses(['a', 'b', 'c', 'd', 'e', 'f', 'g'], [], '')).toEqual(['a', 'b'])
    expect(defaultCompareCourses([], ['b', 'c', 'd', 'e', 'f', 'g', 'h'], '')).toHaveLength(6)
    expect(defaultCompareCourses([], [], '')).toEqual([])
  })

  it('加课：去重 + 上限 8（超限原样返回——调用方据此禁用按钮）', () => {
    expect(addCompareCourse(['a'], 'b')).toEqual(['a', 'b'])
    expect(addCompareCourse(['a'], 'a')).toEqual(['a'])
    const full = Array.from({ length: COMPARE_MAX_COURSES }, (_, i) => `c${i}`)
    expect(addCompareCourse(full, 'x')).toBe(full)
  })

  it('移课：保序；删到 0 也不抛（空态文案由 UI 给）', () => {
    expect(removeCompareCourse(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
    expect(removeCompareCourse(['a'], 'a')).toEqual([])
  })

  it('候选：当前课置顶 + 去重；搜索大小写不敏感、空查询原样', () => {
    expect(compareCourseCandidates(['b', 'a', 'b'], 'cur')).toEqual(['cur', 'b', 'a'])
    expect(filterCourseCandidates(['ladder-x', 'bc-c4'], 'C4')).toEqual(['bc-c4'])
    expect(filterCourseCandidates(['a'], '  ')).toEqual(['a'])
  })
})

describe('色板 / hover / 门闩（纯函数）', () => {
  it('色板 ≥ 上限且不重复；colorOf 取模循环、负数稳', () => {
    expect(COMPARE_COLORS.length).toBeGreaterThanOrEqual(COMPARE_MAX_COURSES)
    expect(new Set(COMPARE_COLORS).size).toBe(COMPARE_COLORS.length)
    expect(colorOf(0)).not.toBe(colorOf(1))
    expect(colorOf(COMPARE_COLORS.length)).toBe(colorOf(0))
    expect(colorOf(-1)).toBe(COMPARE_COLORS[COMPARE_COLORS.length - 1]!)
  })

  it('nearestIter：取最近真实 iter；并列取小', () => {
    expect(nearestIter([1, 5, 10], 6)).toBe(5)
    expect(nearestIter([1, 5, 10], 8)).toBe(10)
    expect(nearestIter([3, 7], 5)).toBe(3)
    expect(nearestIter([], 5)).toBe(null)
  })

  it('visibleCompareSeries：all 全收；rollout/eval 按 key 前缀过滤', () => {
    const s: CompareSeriesData[] = [
      { key: 'winRate', label: '胜率', vals: [], iters: [] },
      { key: 'eval', label: 'eval 胜率', vals: [], iters: [] },
    ]
    expect(visibleCompareSeries(s, 'all')).toHaveLength(2)
    expect(visibleCompareSeries(s, 'rollout').map((x) => x.key)).toEqual(['winRate'])
    expect(visibleCompareSeries(s, 'eval').map((x) => x.key)).toEqual(['eval'])
  })

  it('门闩：请求身份或账本指纹任一变化都要覆盖；两者都不变才拦下', () => {
    const q = {
      courses: ['a', 'b'],
      metric: 'winRate' as const,
      source: 'all' as const,
      from: null,
      to: null,
    }
    const key = compareRequestKey(q)
    expect(shouldApplyCompareData(null, { requestKey: key, fingerprint: 'f1' })).toBe(true)
    expect(
      shouldApplyCompareData(
        { requestKey: key, fingerprint: 'f1' },
        { requestKey: key, fingerprint: 'f1' },
      ),
    ).toBe(false)
    expect(
      shouldApplyCompareData(
        { requestKey: key, fingerprint: 'f1' },
        { requestKey: key, fingerprint: 'f2' },
      ),
    ).toBe(true)
    expect(
      shouldApplyCompareData(
        { requestKey: key, fingerprint: 'f1' },
        { requestKey: `${key}x`, fingerprint: 'f1' },
      ),
    ).toBe(true)
    // 切口径必须覆盖：账本没动，但请求身份变了
    expect(compareRequestKey({ ...q, source: 'eval' })).not.toBe(key)
  })
})

describe('MultiTrendChart（SSR 形状）', () => {
  it('3 门课 ⇒ 3 条 path + 3 个图例项；零 hover 时不画提示框', () => {
    const html = chartHtml([
      series('a', 'winRate', [1, 2, 3], [0.4, 0.5, 0.6]),
      series('b', 'winRate', [50, 52], [0.3, 0.35], '#222222'),
      series('c', 'winRate', [10], [0.7], '#333333'),
    ])
    expect(countOccurrences(html, '<path')).toBe(3)
    expect(countOccurrences(html, 'class="tc-mchart__lg"')).toBe(3)
    expect(html).toContain('</svg>a</span>') // 图例名跟在色条后（主口径无后缀）
    expect(html).toContain('</svg>c</span>')
  })

  it('x 轴按 iter **数值**铺开：it50 的点在 it10 右侧（按下标画会重叠）', () => {
    const html = chartHtml([
      series('a', 'winRate', [10], [0.5]),
      series('b', 'winRate', [50], [0.5], '#222222'),
    ])
    const ds = [...html.matchAll(/d="([^"]+)"/g)].map((m) => m[1] ?? '')
    expect(ds).toHaveLength(2)
    const xOf = (d: string): number => Number(d.match(/M([\d.]+)/)?.[1] ?? 'NaN')
    expect(xOf(ds[0]!)).toBeLessThan(xOf(ds[1]!))
  })

  it('缺值断笔：NaN 处两段 M、无 L（不连线）', () => {
    const html = chartHtml([series('a', 'winRate', [1, 2, 3], [0.4, Number.NaN, 0.6])])
    const d = html.match(/d="([^"]+)"/)?.[1] ?? ''
    expect(countOccurrences(d, 'M')).toBe(2)
    expect(countOccurrences(d, 'L')).toBe(0)
  })

  it('eval 口径 = 虚线（颜色已归课程，口径只能靠线型）', () => {
    const html = chartHtml([series('a', 'evalKills', [1, 2], [0.1, 0.2])], 'kills')
    expect(html).toContain('stroke-dasharray="4 3"')
    expect(html).toContain('a · eval</span>')
  })

  it('全 NaN ⇒ 空态文案（不画轴、不造假数据）', () => {
    const html = chartHtml([series('a', 'winRate', [1, 2], [Number.NaN, Number.NaN])])
    expect(html).toContain('该指标在所选课程上暂无有效点')
    expect(html).not.toContain('<svg')
  })

  it('胜局耗时：y 下界按量级缩放（最低点 100 时不从 0 起）', () => {
    const html = chartHtml([series('a', 'winTicks', [1, 2], [100, 200])], 'winTicks')
    expect(html).toContain('>100<')
    expect(html).not.toContain('>0<')
  })
})

describe('CompareTrendsModal（SSR 骨架）', () => {
  const stateView = {
    course: 'cur',
    courses: ['a', 'b', 'cur'],
    trainingCourses: ['a'],
    archived: [],
  } as unknown as ConsoleStateView

  it('关闭 ⇒ 渲染 null（与 OpenCourseModal 同款早退）', () => {
    expect(
      renderToString(
        h(CompareTrendsModal, { open: false, stateView: null, refreshSec: 300, onClose: () => {} }),
      ),
    ).toBe('')
  })

  it('打开：默认选课 = 在训 ∪ 当前课；给出移除键与节奏说明', () => {
    const html = renderToString(
      h(CompareTrendsModal, { open: true, stateView, refreshSec: 180, onClose: () => {} }),
    )
    expect(html).toContain('比较课程')
    expect(html).toContain('aria-label="移除 a"')
    expect(html).toContain('aria-label="移除 cur"')
    expect(html).not.toContain('aria-label="移除 b"')
    expect(html).toContain('每 180s')
    expect(html).toContain('正在读账本…') // 没有 fetch 的首帧：loading 态，不是假数据
  })

  it('没有任何课程 ⇒ 空态「至少选一门课程」', () => {
    const empty = {
      course: '',
      courses: [],
      trainingCourses: [],
      archived: [],
    } as unknown as ConsoleStateView
    const html = renderToString(
      h(CompareTrendsModal, { open: true, stateView: empty, refreshSec: 300, onClose: () => {} }),
    )
    expect(html).toContain('至少选一门课程')
  })

  it('首帧不读 localStorage（hydrate 一致；偏好只在 mount 后恢复）', () => {
    const src = readFileSync(
      join(import.meta.dir, '..', 'src', 'web', 'app', 'panels', 'CompareTrendsModal.tsx'),
      'utf8',
    ).replace(/\s+/g, ' ')
    const inits = src.match(/useState(?:<[^>]*>)?\((?:[^()]|\([^()]*\))*\)/g) ?? []
    expect(inits.length).toBeGreaterThan(3) // 守卫：正则真扫到了初始化
    for (const init of inits) expect(init).not.toMatch(/localStorage|readLocal/)
  })
})

describe('接线：Hero 入口 + app 透传节奏', () => {
  it('Hero 空态分支（当前课无迭代）也渲染「比较课程」按钮（能比别的课）', () => {
    const html = renderToString(
      h(Hero, {
        stateView: {
          course: 'cur',
          courses: ['cur'],
          trainingCourses: [],
          archived: [],
          metrics: { available: false, iters: [] },
        } as unknown as ConsoleStateView,
        onMore: () => {},
      }),
    )
    expect(html).toContain('比较课程')
    expect(html).toContain('该课程暂无迭代记录')
  })

  it('主分支：入口挂在趋势控制条右端（`tc-trendctl__end`）；两个分支各挂一个弹窗', () => {
    const heroSrc = readFileSync(
      join(import.meta.dir, '..', 'src', 'web', 'app', 'panels', 'Hero.tsx'),
      'utf8',
    )
    expect(heroSrc).toContain('tc-trendctl__end')
    expect((heroSrc.match(/<CompareTrendsModal/g) ?? []).length).toBe(2)
    expect((heroSrc.match(/refreshSec=\{refreshSec\}/g) ?? []).length).toBe(2)
    // app.tsx 只透传、不搬状态（顶栏旋钮是唯一节奏源）
    const appSrc = readFileSync(join(import.meta.dir, '..', 'src', 'web', 'app', 'app.tsx'), 'utf8')
    expect(appSrc).toContain('refreshSec={refreshInterval}')
  })
})

describe('CompareCoursePicker（封存课分支）', () => {
  it('封存课是**不可点的 span** 且写明原因；可加课是 button；已选课不进候选', () => {
    const html = renderToString(
      h(CompareCoursePicker, {
        candidates: ['a', 'b'],
        archived: [{ course: 'old' }],
        selected: ['a'],
        query: '',
        onQuery: () => {},
        onAdd: () => {},
      }),
    )
    expect(html).toContain('class="tc-cmp__cand"')
    expect(html).toContain('>b</button>')
    expect(html).not.toContain('>a</button>')
    expect(html).toContain('tc-cmp__cand--off')
    expect(html).toContain('old · 封存 · 无逐轮账本')
    expect(html).not.toContain('>old</button>')
    expect(html).toContain('title="封存课只留归档清单（不扫盘不解压）⇒ 没有逐轮账本可比"')
  })
})
