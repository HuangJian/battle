/**
 * web-home-compare.test.ts — 首页走势图「多课同图」（用户 2026-11-20）。
 *
 * 分层：
 *   src/web/view/home-compare.ts          配置 / 图键→序列键 / 范围截取 / 合流 / y 下界（纯函数）
 *   src/web/app/panels/HomeCompareCell.tsx 首页多课格（当前课读数 + 多课小图）
 *   src/web/app/panels/TrendConfigModal.tsx 「走势配置」里的多课对比草稿
 *   src/web/app/panels/Hero.tsx            接线（全局一组对比课、颜色顺序、请求键）
 *
 * 钉子：
 *  ① 每门课颜色固定（跟课走）：切当前课/增减对比课都不重排已有课的颜色，跨图同课同色；
 *  ② 范围档位在**每门课自己的序列**上截取，稀疏 eval 保留 NaN 缺口（不把两段评估连成假趋势）；
 *  ③ 对比课缺账本/已删除 ⇒ 该课缺线，不补假点、不改变其余课的颜色；
 *  ④ 配置草稿制：多课对比和勾选/顺序/统计量一起在「完成」提交；
 *  ⑤ Hero 首帧不读 localStorage（SSR/hydrate 一致），对比关闭时保持原单课画面。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { HomeCompareCell } from '../src/web/app/panels/HomeCompareCell'
import { HomeCompareLegend } from '../src/web/app/panels/HomeCompareLegend'
import { TrendConfigModal } from '../src/web/app/panels/TrendConfigModal'
import { TrendZoomModal } from '../src/web/components/TrendZoomModal'
import {
  BEHAVIOR_METRICS,
  behaviorSeriesKey,
  behaviorTrendKey,
  buildHomeChartSeries,
  colorOf,
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_TREND_VISIBILITY,
  fmtPct,
  homeChartSpec,
  homeCompareRequestKey,
  homeSeriesFromCompare,
  homeYBounds,
  HOME_COMPARE_MAX_COURSES,
  METRIC_STAT_KEYS,
  parseHomeCompareConfig,
  preserveHomeCompareOnCourseSwitch,
  requestedHomeSeriesKeys,
  resolveHomeCompareColors,
  resolveTrendOrder,
  sliceHomeSeries,
  sourceSeriesKeys,
  toggleHomeCompareCourse,
  toggleHomeCompareCourseInConfig,
  TREND_SCALES_DEFAULT,
  type CompareSeriesData,
  type HomeChartLine,
  type Series,
  type TrendSeriesView,
} from '../src/web/view'

const scales = TREND_SCALES_DEFAULT
const stats = DEFAULT_BEHAVIOR_METRIC_STATS

function series(key: string, vals: number[], iters: number[]): Series {
  return { key, label: key, vals, iters }
}

function compareSeries(
  key: string,
  vals: Array<number | null>,
  iters: number[],
): CompareSeriesData {
  return { key, label: key, vals, iters }
}

describe('多课对比配置（纯函数）', () => {
  it('坏 JSON / 非法字段逐项退默认；课程去重、trim、截到上限', () => {
    expect(parseHomeCompareConfig(null)).toEqual({ on: false, courses: [] })
    expect(parseHomeCompareConfig('not json')).toEqual({ on: false, courses: [] })
    expect(
      parseHomeCompareConfig(JSON.stringify({ on: 'yes', courses: [1, ' a ', 'a', 'b'] })),
    ).toEqual({ on: false, courses: ['a', 'b'] })
    const capped = parseHomeCompareConfig(
      JSON.stringify({ on: true, courses: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] }),
    )
    expect(capped.on).toBe(true)
    expect(capped.courses).toEqual(['a', 'b', 'c', 'd', 'e', 'f'])
    expect(capped.courses.length).toBe(HOME_COMPARE_MAX_COURSES)
  })

  it('加/减对比课：已存在 ⇒ 移除；满了 ⇒ 原样返回（不静默挤掉别人）', () => {
    expect(toggleHomeCompareCourse([], 'a')).toEqual(['a'])
    expect(toggleHomeCompareCourse(['a', 'b'], 'a')).toEqual(['b'])
    const full = ['a', 'b', 'c', 'd', 'e', 'f']
    expect(toggleHomeCompareCourse(full, 'g')).toBe(full)
    expect(toggleHomeCompareCourse(['a'], '  ')).toEqual(['a'])
  })

  it('切到对比里的课：旧当前课接替进集合，所有课色位不变', () => {
    // a 在看、对比 b/c ⇒ 切到 b：b 变当前（仍是其固定色位），旧当前课 a 补进集合。
    expect(
      preserveHomeCompareOnCourseSwitch(
        { on: true, courses: ['b', 'c'], colors: { a: 0, b: 1, c: 2 } },
        'a',
        'b',
      ),
    ).toEqual({ on: true, courses: ['c', 'a'], colors: { b: 1, c: 2, a: 0 } })
    // 唯一额外课 = 新当前课 ⇒ 仍剩旧当前课，对比不会整块熄灭；色位都保住。
    expect(
      preserveHomeCompareOnCourseSwitch(
        { on: true, courses: ['b'], colors: { a: 0, b: 1 } },
        'a',
        'b',
      ),
    ).toEqual({ on: true, courses: ['a'], colors: { b: 1, a: 0 } })
    // 开关关着也维持契约（下次打开还是同一组课、同一组色）。
    expect(
      preserveHomeCompareOnCourseSwitch(
        { on: false, courses: ['b'], colors: { a: 0, b: 1 } },
        'a',
        'b',
      ),
    ).toEqual({ on: false, courses: ['a'], colors: { b: 1, a: 0 } })
  })

  it('切到对比外的课：对比集合不动、已有课色位不动；同一门/无旧当前课 ⇒ 原样', () => {
    const base = { on: true, courses: ['b'], colors: { a: 0, b: 1 } }
    // c 不在对比里 ⇒ courses 仍是 ['b']，b 仍是色位 1；旧当前课 a 的色位释放给 c。
    expect(preserveHomeCompareOnCourseSwitch(base, 'a', 'c')).toEqual({
      on: true,
      courses: ['b'],
      colors: { c: 0, b: 1 },
    })
    expect(preserveHomeCompareOnCourseSwitch(base, 'b', 'b')).toBe(base)
    expect(preserveHomeCompareOnCourseSwitch(base, '', 'b')).toBe(base)
    // 旧当前课已在名单里 ⇒ 去重，不重复占名额。
    expect(
      preserveHomeCompareOnCourseSwitch(
        { on: true, courses: ['b', 'a'], colors: { a: 0, b: 1 } },
        'a',
        'b',
      ),
    ).toEqual({ on: true, courses: ['a'], colors: { b: 1, a: 0 } })
  })

  it('切当前课不换色：b 从对比课升为当前课后，三门课的色位逐门不变', () => {
    const config = { on: true, courses: ['b', 'c'], colors: { a: 0, b: 1, c: 2 } }
    const before = resolveHomeCompareColors(config, 'a')
    const after = resolveHomeCompareColors(preserveHomeCompareOnCourseSwitch(config, 'a', 'b'), 'b')
    expect(after).toEqual(before)
    expect(colorOf(after['b']!)).toBe(colorOf(before['b']!))
    expect(colorOf(after['a']!)).toBe(colorOf(before['a']!))
  })

  it('增减对比课：已有课的色位不动，新课取最小空闲槽，移除释放色位', () => {
    const config = { on: true, courses: ['b'], colors: { a: 0, b: 1 } }
    const added = toggleHomeCompareCourseInConfig(config, 'a', 'c')
    expect(added.courses).toEqual(['b', 'c'])
    expect(added.colors).toEqual({ a: 0, b: 1, c: 2 })
    expect(toggleHomeCompareCourseInConfig(added, 'a', 'b')).toEqual({
      on: true,
      courses: ['c'],
      colors: { a: 0, c: 2 },
    })
  })

  it('请求身份 = 课程顺序 + 键序（两者任一变化都必须重拉）', () => {
    expect(homeCompareRequestKey(['a', 'b'], ['x', 'y'])).toBe('a,b|x,y')
    expect(homeCompareRequestKey(['b', 'a'], ['x', 'y'])).not.toBe(
      homeCompareRequestKey(['a', 'b'], ['x', 'y']),
    )
  })
})

describe('图键 → 序列键（六张基础图 + 逐局指标）', () => {
  it('六张基础图逐条钉死；三张纵坐标档位按 scales 选变体', () => {
    expect(homeChartSpec('winRate', scales, stats)).toEqual({
      mainKey: 'winRate',
      evalKey: 'eval',
      yRule: 'cap03',
    })
    expect(homeChartSpec('dmgPerKill', scales, stats)).toEqual({
      mainKey: 'dmgPerKill',
      evalKey: 'evalDmgPerKill',
      yRule: 'zero',
    })
    expect(homeChartSpec('winTicks', scales, stats)).toEqual({
      mainKey: 'winTicks',
      evalKey: 'evalWinTicks',
      yRule: 'scaled',
    })
    expect(homeChartSpec('kills', scales, stats)?.mainKey).toBe('killsPct')
    expect(homeChartSpec('kills', { ...scales, kills: 'abs' }, stats)?.mainKey).toBe('killsAbs')
    expect(homeChartSpec('winHp', scales, stats)?.mainKey).toBe('winHp')
    expect(homeChartSpec('winHp', { ...scales, winHp: 'abs' }, stats)?.mainKey).toBe('winHpAbs')
    expect(homeChartSpec('pu', scales, stats)?.mainKey).toBe('pu')
    expect(homeChartSpec('pu', { ...scales, pu: 'pct' }, stats)?.mainKey).toBe('puPct')
  })

  it('逐局指标按当前统计量拼两条口径；未知键 ⇒ null', () => {
    const metric = BEHAVIOR_METRICS[0]!
    const key = behaviorTrendKey(metric.key)
    const spec = homeChartSpec(key, scales, { ...stats, [metric.key]: 'median' })
    expect(spec).not.toBeNull()
    expect(spec!.mainKey).toBe(behaviorSeriesKey(metric.key, 'median', 'rollout'))
    expect(spec!.evalKey).toBe(behaviorSeriesKey(metric.key, 'median', 'eval'))
    expect(spec!.yRule).toBe('zero')
    expect(homeChartSpec('bm:not-a-metric' as never, scales, stats)).toBeNull()
    expect(homeChartSpec('not-a-chart' as never, scales, stats)).toBeNull()
  })

  it('source 档位决定取哪几条；请求键去重排序（图重排不触发重拉）', () => {
    const spec = homeChartSpec('winRate', scales, stats)!
    expect(sourceSeriesKeys(spec, 'all')).toEqual(['winRate', 'eval'])
    expect(sourceSeriesKeys(spec, 'rollout')).toEqual(['winRate'])
    expect(sourceSeriesKeys(spec, 'eval')).toEqual(['eval'])
    expect(requestedHomeSeriesKeys(['winRate', 'kills'], 'all', scales, stats)).toEqual([
      'eval',
      'evalKillsPct',
      'killsPct',
      'winRate',
    ])
    expect(requestedHomeSeriesKeys(['winRate', 'kills'], 'rollout', scales, stats)).toEqual([
      'killsPct',
      'winRate',
    ])
    // 同一张图重复出现不产生重复键
    expect(requestedHomeSeriesKeys(['winRate', 'winRate'], 'rollout', scales, stats)).toEqual([
      'winRate',
    ])
  })

  it('逐局指标一次取回六种统计量的两条口径（放大浮窗切统计量不空窗）', () => {
    const metric = BEHAVIOR_METRICS[0]!
    const chart = behaviorTrendKey(metric.key)
    expect(requestedHomeSeriesKeys([chart], 'all', scales, stats)).toEqual(
      METRIC_STAT_KEYS.flatMap((stat) => [
        behaviorSeriesKey(metric.key, stat, 'eval'),
        behaviorSeriesKey(metric.key, stat, 'rollout'),
      ]).sort(),
    )
    expect(requestedHomeSeriesKeys([chart], 'rollout', scales, stats)).toEqual(
      METRIC_STAT_KEYS.map((stat) => behaviorSeriesKey(metric.key, stat, 'rollout')).sort(),
    )
  })
})

describe('范围截取（保留稀疏缺口）', () => {
  it('普通序列按最近 N 轮截取；全量原样', () => {
    const s = series('winRate', [1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    expect(sliceHomeSeries(s, 'all')).toEqual(s)
    expect(sliceHomeSeries(s, '30pct').vals).toEqual([4, 5])
    expect(sliceHomeSeries(s, '30pct').iters).toEqual([4, 5])
    expect(sliceHomeSeries(s, '60pct').vals).toEqual([3, 4, 5])
  })

  it('稀疏序列按有效点截取，但**保留内部 NaN**（否则 eval 会把两段评估连成假线）', () => {
    const s: Series = {
      key: 'eval',
      label: 'eval',
      vals: [Number.NaN, 1, Number.NaN, 2, Number.NaN, 3],
      iters: [1, 2, 3, 4, 5, 6],
    }
    const all = sliceHomeSeries(s, 'all')
    expect(all.vals).toEqual([1, Number.NaN, 2, Number.NaN, 3])
    expect(all.iters).toEqual([2, 3, 4, 5, 6])
    const six = sliceHomeSeries(s, '60pct')
    expect(six.vals).toEqual([2, Number.NaN, 3])
    expect(six.iters).toEqual([4, 5, 6])
    expect(six.sparse).toBe(true)
    const three = sliceHomeSeries(s, '30pct')
    expect(three.vals).toEqual([3])
    expect(three.iters).toEqual([6])
  })

  it('全 NaN / 空序列 ⇒ 空，不抛', () => {
    const empty = sliceHomeSeries(series('eval', [], []), '30pct')
    expect(empty.vals).toEqual([])
    const nan = sliceHomeSeries(series('eval', [Number.NaN], [1]), '30pct')
    expect(nan.vals).toEqual([])
    expect(nan.iters).toEqual([])
  })

  it('过线 null → NaN 断笔；eval 键自动标 sparse', () => {
    const s = homeSeriesFromCompare(compareSeries('eval', [null, 0.2], [1, 2]))
    expect(s.vals[0]).toBeNaN()
    expect(s.vals[1]).toBe(0.2)
    expect(s.sparse).toBe(true)
    const rollout = homeSeriesFromCompare(compareSeries('winRate', [0.4], [1]))
    expect(rollout.sparse).toBeUndefined()
  })
})

describe('合流：当前课 + 对比课（颜色跟课固定）', () => {
  const curWin = series('winRate', [0.1, 0.2, 0.3], [1, 2, 3])
  const curEval: Series = {
    key: 'eval',
    label: 'eval',
    vals: [Number.NaN, 0.25, Number.NaN],
    iters: [1, 2, 3],
  }
  const extra: TrendSeriesView = {
    keys: ['winRate', 'eval'],
    from: null,
    to: null,
    courses: [
      {
        course: 'b',
        points: 4,
        series: [
          compareSeries('winRate', [0.4, 0.5, 0.6], [1, 2, 3]),
          compareSeries('eval', [null, null, 0.55], [1, 2, 3]),
        ],
      },
    ],
    unavailable: [],
    fingerprint: 'fp',
    time: 't',
  }
  const build = (
    source: 'all' | 'rollout' | 'eval',
    range: 'all' | '60pct' | '30pct' = 'all',
  ): HomeChartLine[] =>
    buildHomeChartSeries({
      spec: homeChartSpec('winRate', scales, stats)!,
      source,
      range,
      courses: ['cur', 'b', 'gone'],
      colors: { cur: 0, b: 1, gone: 2 },
      currentCourse: 'cur',
      currentSeries: (key) => (key === 'winRate' ? curWin : key === 'eval' ? curEval : undefined),
      extra,
    })

  it('颜色按显式色位取（不是数组下标）；不存在的课不补线', () => {
    const lines = build('all')
    expect(lines.map((l) => `${l.course}:${l.key}`)).toEqual([
      'cur:winRate',
      'cur:eval',
      'b:winRate',
      'b:eval',
    ])
    expect(lines.map((l) => l.color)).toEqual([colorOf(0), colorOf(0), colorOf(1), colorOf(1)])
    // 故意给非顺序槽位：证明颜色来自 colors 映射，而不是 courses 里的下标。
    const remapped = buildHomeChartSeries({
      spec: homeChartSpec('winRate', scales, stats)!,
      source: 'all',
      range: 'all',
      courses: ['cur', 'b'],
      colors: { cur: 3, b: 7 },
      currentCourse: 'cur',
      currentSeries: (key) => (key === 'winRate' ? curWin : key === 'eval' ? curEval : undefined),
      extra,
    })
    expect(remapped.map((l) => l.color)).toEqual([colorOf(3), colorOf(3), colorOf(7), colorOf(7)])
  })

  it('source 档位只留对应口径（eval 档不画 rollout 线）', () => {
    expect(build('rollout').map((l) => l.key)).toEqual(['winRate', 'winRate'])
    expect(build('eval').map((l) => l.key)).toEqual(['eval', 'eval'])
  })

  it('范围在每门课自己的序列上截取', () => {
    const lines = build('rollout', '30pct')
    expect(lines.map((l) => l.vals)).toEqual([[0.3], [0.6]])
  })

  it('对比课无该序列 / 全 NaN ⇒ 该课缺线，其余课照常', () => {
    const onlyEval: TrendSeriesView = {
      ...extra,
      courses: [
        {
          course: 'b',
          points: 1,
          series: [compareSeries('eval', [null, null, 0.55], [1, 2, 3])],
        },
      ],
    }
    const lines = buildHomeChartSeries({
      spec: homeChartSpec('winRate', scales, stats)!,
      source: 'rollout',
      range: 'all',
      courses: ['cur', 'b'],
      currentCourse: 'cur',
      currentSeries: (key) => (key === 'winRate' ? curWin : undefined),
      extra: onlyEval,
    })
    expect(lines.map((l) => l.course)).toEqual(['cur'])
  })
})

describe('y 轴下界（首页多课图的单一出处）', () => {
  it('cap03 / zero / scaled 三种规则', () => {
    expect(homeYBounds('cap03', [{ vals: [0.2, 0.5] }])).toEqual({ yFloor: 0.3 })
    expect(homeYBounds('zero', [{ vals: [-1, 2] }])).toEqual({ yFloor: 0 })
    expect(homeYBounds('scaled', [{ vals: [100, 200] }])).toEqual({ yMin: 100 })
  })

  it('无有效点 ⇒ 空对象（调用方画空态，不造一条假轴）', () => {
    expect(homeYBounds('zero', [{ vals: [Number.NaN] }, { vals: [] }])).toEqual({})
  })
})

describe('HomeCompareCell（SSR 形状）', () => {
  const lines: HomeChartLine[] = [
    { course: 'cur', key: 'winRate', color: colorOf(0), iters: [1, 2], vals: [0.2, 0.4] },
    { course: 'cur', key: 'eval', color: colorOf(0), iters: [1, 2], vals: [Number.NaN, 0.5] },
    { course: 'other', key: 'winRate', color: colorOf(1), iters: [1, 2], vals: [0.3, 0.6] },
  ]

  it('值列只报当前课最新读数（主口径 / eval 双口径），图是多课小图', () => {
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        title: '口径说明',
        course: 'cur',
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
      }),
    )
    expect(html).toContain('tc-tcell--multi')
    expect(html).toContain('胜率')
    expect(html).toContain('40.0%')
    expect(html).toContain('50.0%')
    expect(html).toContain('tc-mchart--compact')
    expect(html).toContain('aria-label="多课程胜率对比图"')
    // 首页小图不重复画图例（全局图例在网格上方）
    expect(html).not.toContain('tc-mchart__legend')
    // 两条课的线都在（颜色是数据，不是主题变量）
    expect(html).toContain(colorOf(0))
    expect(html).toContain(colorOf(1))
  })

  it('格头圆点用当前课自己的颜色（不恒为第一色）', () => {
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        currentColor: colorOf(3),
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
      }),
    )
    expect(html).toContain(`fill="${colorOf(3)}"`)
  })

  it('只看 eval ⇒ eval 就是主序列，值列不重复报「x / x」', () => {
    const evalOnly: HomeChartLine[] = [
      { course: 'cur', key: 'eval', color: colorOf(0), iters: [1, 2], vals: [0.2, 0.5] },
      { course: 'other', key: 'eval', color: colorOf(1), iters: [1, 2], vals: [0.3, 0.6] },
    ]
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines: evalOnly,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'eval',
      }),
    )
    expect(html).toContain('50.0%')
    expect(html).not.toContain('50.0% /')
  })

  it('没有可画的线 ⇒ 空态文案，不渲染空 SVG', () => {
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines: [],
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
      }),
    )
    expect(html).toContain('所选课程暂无该口径数据')
    expect(html).not.toContain('tc-mchart__svg')
  })

  it('放大入口：左下角独立按钮；没给 onExpand 就不渲染', () => {
    let opened = 0
    const withZoom = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
        onExpand: () => {
          opened++
        },
      }),
    )
    expect(withZoom).toContain('tc-tcell__zoom')
    expect(withZoom).toContain('aria-label="放大走势图：胜率 · cur"')
    const without = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
      }),
    )
    expect(without).not.toContain('tc-tcell__zoom')
    expect(opened).toBe(0)
  })

  it('「只看 eval」档把 hover 吸附交给 eval 点（档位一变就切换）', () => {
    const cellSrc = readFileSync(
      join(import.meta.dir, '..', 'src', 'web', 'app', 'panels', 'HomeCompareCell.tsx'),
      'utf8',
    )
    expect(cellSrc).toContain("snapToEval={source === 'eval'}")
  })

  it('图例隐藏某门课 ⇒ 该课线不画，格头当前课读数照报', () => {
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
        hiddenCourses: ['other'],
      }),
    )
    expect(html).toContain('40.0%')
    expect(html).toContain(colorOf(0))
    expect(html).not.toContain(colorOf(1))
  })

  it('全部隐藏 ⇒ 空态提示点图例恢复（不是「暂无数据」）；当前课读数仍在', () => {
    const html = renderToString(
      h(HomeCompareCell, {
        label: '胜率',
        course: 'cur',
        lines,
        fmt: fmtPct,
        yRule: 'cap03',
        source: 'all',
        hiddenCourses: ['cur', 'other'],
      }),
    )
    expect(html).toContain('全部课程已隐藏（点图例恢复）')
    expect(html).not.toContain('所选课程暂无该口径数据')
    expect(html).not.toContain('tc-mchart__svg')
    expect(html).toContain('40.0%')
  })
})

describe('HomeCompareLegend（图例点击显隐）', () => {
  const entries = [
    { course: 'cur', color: colorOf(0) },
    { course: 'other', color: colorOf(1) },
  ]

  it('每门课是带 aria-pressed 的按钮；隐藏项带 tc-chip--off 与「显示」文案', () => {
    const html = renderToString(
      h(HomeCompareLegend, { entries, hidden: ['other'], onToggle: () => {} }),
    )
    expect(html).toContain('role="group"')
    expect(html).toContain('aria-label="首页多课对比"')
    expect(html).toContain('aria-label="隐藏 cur 的曲线"')
    expect(html).toContain('aria-label="显示 other 的曲线"')
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain('aria-pressed="false"')
    expect(html).toContain('tc-chip--off')
    expect(html).toContain('title="悬停任一张图看各课读数 · 点课程名隐藏/显示"')
    expect(html).toContain('title="悬停任一张图看各课读数 · 点课程名恢复显示"')
  })

  it('提示语只在课程名 tooltip 里，不再常驻一行（全隐也不多一行文案）', () => {
    const html = renderToString(
      h(HomeCompareLegend, { entries, hidden: ['cur', 'other'], onToggle: () => {} }),
    )
    expect(html).not.toContain('>悬停任一张图看各课读数')
    expect(html).not.toContain('全部课程已隐藏')
    expect(html.match(/aria-pressed="true"/g)?.length).toBe(2)
  })
})

describe('TrendZoomModal 多课放大', () => {
  const lines: HomeChartLine[] = [
    { course: 'cur', key: 'winRate', color: colorOf(0), iters: [1, 2], vals: [0.2, 0.4] },
    { course: 'other', key: 'winRate', color: colorOf(1), iters: [1, 2], vals: [0.3, 0.6] },
  ]
  const target = {
    title: '胜率 · 多课对比',
    fmt: fmtPct,
    multi: { lines, yRule: 'cap03' as const, source: 'all' as const },
  }
  const base = { target, range: 'all' as const, onClose: () => {} }

  it('多课 target 画首页同一组线，课程名按钮与首页图例同语义', () => {
    const html = renderToString(h(TrendZoomModal, base))
    expect(html).toContain('tc-trendzoom')
    expect(html).toContain('多课对比 · 点击课程名显示/隐藏曲线')
    expect(html).toContain('aria-label="课程曲线显隐"')
    expect(html).toContain('tc-mchart__svg')
    expect(html).not.toContain('tc-mchart__legend')
    expect(html).toContain(`stroke="${colorOf(0)}"`)
    expect(html).toContain(`stroke="${colorOf(1)}"`)
    expect(html).toContain('aria-label="隐藏 cur 的曲线"')
    expect(html).toContain('aria-label="隐藏 other 的曲线"')
    expect((html.match(/aria-pressed="false"/g) ?? []).length).toBe(2)
  })

  it('隐藏只摘线不摘按钮：被隐藏的课仍在图例里可点回来', () => {
    const html = renderToString(h(TrendZoomModal, { ...base, hiddenCourses: ['other'] }))
    expect(html).toContain(`stroke="${colorOf(0)}"`)
    expect(html).not.toContain(`stroke="${colorOf(1)}"`)
    expect(html).toContain('aria-label="显示 other 的曲线"')
    expect(html).toContain(`fill="${colorOf(1)}"`) // 图例圆点还在
  })

  it('全部隐藏 ⇒ 空态提示点课程名恢复，不渲染空图', () => {
    const html = renderToString(h(TrendZoomModal, { ...base, hiddenCourses: ['cur', 'other'] }))
    expect(html).toContain('全部课程已隐藏（点课程名恢复）')
    expect(html).not.toContain('tc-mchart__svg')
    expect(html).toContain('aria-label="显示 cur 的曲线"')
  })

  it('逐局指标多课放大：六种统计量可切换，课程名显隐按钮同屏', () => {
    const metric = BEHAVIOR_METRICS[0]!
    const meanLines: HomeChartLine[] = [
      {
        course: 'cur',
        key: behaviorSeriesKey(metric.key, 'mean', 'rollout'),
        color: colorOf(0),
        iters: [1, 2],
        vals: [1, 2],
      },
      {
        course: 'other',
        key: behaviorSeriesKey(metric.key, 'mean', 'rollout'),
        color: colorOf(1),
        iters: [1, 2],
        vals: [2, 3],
      },
    ]
    const html = renderToString(
      h(TrendZoomModal, {
        target: {
          title: `${metric.label} · 多课对比`,
          fmt: (v) => String(v),
          multi: {
            lines: meanLines,
            yRule: 'zero',
            source: 'rollout',
            statLines: { mean: meanLines },
            stat: 'mean',
          },
        },
        range: 'all',
        onClose: () => {},
      }),
    )
    expect(html).toContain('多课对比 · 点击统计量切换口径，点击课程名显隐曲线')
    expect(html).toContain('aria-label="统计量切换"')
    expect(html).toContain('aria-label="课程曲线显隐"')
    // 6 个统计量按钮（当前口径选中）+ 2 个课程按钮（都可见）= 1 true / 7 false
    expect((html.match(/aria-pressed="true"/g) ?? []).length).toBe(1)
    expect((html.match(/aria-pressed="false"/g) ?? []).length).toBe(7)
    expect(html).toContain('title="只看多门课程的均值"')
    expect(html).toContain(`stroke="${colorOf(0)}"`)
    expect(html).toContain(`stroke="${colorOf(1)}"`)
  })

  it('切统计量 = 整组换线（每门课仍只一条该统计量的线）', () => {
    const src = readFileSync(
      join(import.meta.dir, '..', 'src', 'web', 'components', 'TrendZoomModal.tsx'),
      'utf8',
    )
    expect(src).toContain('multi?.statLines?.[multiStat] ?? multi?.lines ?? []')
    expect(src).toContain('onClick={() => setMultiStat(o.value)}')
  })
})

describe('走势配置弹窗：多课对比草稿', () => {
  const base = {
    open: true,
    order: resolveTrendOrder(null),
    visibility: DEFAULT_TREND_VISIBILITY,
    metricStats: DEFAULT_BEHAVIOR_METRIC_STATS,
    currentCourse: 'cur',
    courses: ['cur', 'other', 'third'],
    archived: ['old'],
    onApply: () => {},
    onClose: () => {},
  }

  it('开关、当前课、已选对比课、候选与封存课都在同一节', () => {
    const html = renderToString(
      h(TrendConfigModal, {
        ...base,
        compare: { on: true, courses: ['other'] },
      }),
    )
    expect(html).toContain('多课对比')
    expect(html).toContain('开启多课同图')
    expect(html).toContain('当前课：cur')
    expect(html).toContain('aria-label="移除对比课 other"')
    // 已选课在候选里打勾；未选课是可点的候选。
    expect(html).toContain('>✓ other</button>')
    expect(html).toContain('>third</button>')
    // 封存课是 span（不可点）且写明原因；不会以 button 出现。
    expect(html).toContain('old · 封存 · 无逐轮账本')
    expect(html).not.toContain('>old</button>')
  })

  it('没选对比课 ⇒ 提示可以先选好；满员 ⇒ 其余候选禁用并说明原因', () => {
    const empty = renderToString(
      h(TrendConfigModal, { ...base, compare: { on: false, courses: [] } }),
    )
    expect(empty).toContain('还没选对比课')

    const capped = renderToString(
      h(TrendConfigModal, {
        ...base,
        courses: ['cur', 'a', 'b', 'c', 'd', 'e', 'f', 'g'],
        compare: { on: true, courses: ['a', 'b', 'c', 'd', 'e', 'f'] },
      }),
    )
    expect(capped).toContain(`最多 ${HOME_COMPARE_MAX_COURSES} 门额外课`)
    const i = capped.indexOf('>g</button>')
    expect(i).toBeGreaterThan(0)
    const tag = capped.slice(capped.lastIndexOf('<button', i), i + 1)
    expect(tag).toContain('disabled')
  })
})

describe('Hero 接线（源码纪律）', () => {
  const heroSrc = readFileSync(
    join(import.meta.dir, '..', 'src', 'web', 'app', 'panels', 'Hero.tsx'),
    'utf8',
  )

  it('对比数据走 useHomeCompare + /api/trendSeries 的显式键；对比格走 HomeCompareCell', () => {
    expect(heroSrc).toContain('useHomeCompare')
    expect(heroSrc).toContain('<HomeCompareCell')
    expect(heroSrc).toContain('requestedHomeSeriesKeys')
    expect(heroSrc).toContain('buildHomeChartSeries')
    expect(heroSrc).toContain('resolveHomeCompareColors')
    expect(heroSrc).toContain('TC_TREND_COMPARE')
  })

  it('多课格保留放大入口：多课模式放大同组曲线，单课行为指标仍走统计曲线浮窗', () => {
    expect(heroSrc).toContain('onExpand={zoomTarget ? () => openZoom(zoomTarget) : undefined}')
    expect(heroSrc).toContain('behavior: { key: def.key, iters, stat: statKey, source }')
  })

  it('图例点击显隐：全局一组隐藏名单，传给每张多课格', () => {
    expect(heroSrc).toContain('<HomeCompareLegend')
    expect(heroSrc).toContain('hiddenCourses={hiddenCompare}')
    expect(heroSrc).toContain('toggleHiddenCourse')
    // 图例住在走势控制条右端（与「走势配置」同排），不再单独占一行。
    const endStart = heroSrc.indexOf('<div className="tc-trendctl__end">')
    expect(endStart).toBeGreaterThan(-1)
    const end = heroSrc.slice(endStart, heroSrc.indexOf('比较课程', endStart))
    expect(end).toContain('<HomeCompareLegend')
    expect(end.indexOf('<HomeCompareLegend')).toBeLessThan(end.indexOf('走势配置'))
  })

  it('切换当前课：新当前课原在对比里 ⇒ 旧当前课接替并落盘（对比不丢）', () => {
    expect(heroSrc).toContain('preserveHomeCompareOnCourseSwitch')
    expect(heroSrc).toContain('prevCourseRef')
    expect(heroSrc).toContain('localStorage.setItem(TC_TREND_COMPARE, JSON.stringify(next))')
  })

  it('放大浮窗沿用首页多课线条与同一份显隐名单', () => {
    expect(heroSrc).toContain('statLines, stat: currentStat')
    expect(heroSrc).toContain('onToggleCourse={toggleCompareCourse}')
    expect(heroSrc).toContain('hiddenCourses={hiddenCompare}')
  })

  it('六种统计量的当前课序列一次建齐，对比课六口径一次取回', () => {
    expect(heroSrc).toContain('behaviorSeriesKey(m.key, stat, src)')
    expect(heroSrc).toContain('for (const stat of METRIC_STAT_KEYS)')
  })

  it('首帧不读 localStorage（SSR/hydrate 一致）：恢复只住 mount 后的 effect', () => {
    const init = heroSrc.match(/useState<HomeCompareConfig>\(\(\) => \(\{[\s\S]*?\}\)\)/)
    expect(init).not.toBeNull()
    expect(init![0]).not.toContain('localStorage')
    expect(heroSrc).toContain('parseHomeCompareConfig(localStorage.getItem(TC_TREND_COMPARE))')
  })
})
