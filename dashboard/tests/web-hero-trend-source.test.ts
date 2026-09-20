/**
 * web-hero-trend-source.test.ts — hero 走势图数据源档位（全部 / rollout / eval）。用户指令 2026-09-20。
 *
 * 分层：src/web/view/series.ts（TrendSource / TREND_SOURCE_OPTIONS / isTrendSource）
 *      src/web/app/panels/Hero.tsx（TrendCell 三档的渲染形状）
 *
 * 为什么三档都要单独渲染断言：整页 SSR 首帧**恒为「全部」**（偏好 hydrate 后才生效），
 * 只测整页等于只测到一条分支；而三档的区别恰好是「画几条线、画的是哪个口径、值列给谁」——
 * 全是形状，只能从渲染里看。
 *
 * 三条钉子：
 *  ① 全部 = 两条（主序列 + eval 橙线 + `a / b` 双值）；
 *  ② rollout = 一条（**没有** eval 橙线、没有双值）；
 *  ③ eval = 一条，但**口径换了**：标签是 eval 自己的词、线色是 eval 橙、值列是 eval 的数
 *     （`toneOf` 必须跟着显示的那条走，否则 rollout 的阈值会去染 eval 的数——一个过期判断）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { TrendCell } from '../src/web/app/panels/Hero'
import {
  isTrendSource,
  TREND_SOURCE_OPTIONS,
  winTone,
  type Series,
  type TrendSource,
} from '../src/web/view'

/** eval 线的口径色（`TrendChart` 导出常量）：断言用**渲染后的 attribute 形状**，
 *  不用裸 `#ea580c`——那个字符串也出现在内联进 SSR 的 theme.css 里，断言它等于断言样式表。 */
const EVAL_STROKE = 'stroke="var(--eval-line, #ea580c)"'

const win: Series = { key: 'winRate', label: '胜率', vals: [0.4, 0.5], iters: [1, 2] }
const ev: Series = { key: 'eval', label: 'eval 胜率', vals: [Number.NaN, 0.3], iters: [1, 2] }

/** 渲染一格。**不用默认参数**（`cell('eval', win, undefined)` 会静默落到默认值——
 *  「没有 eval 数据」这条分支就永远测不到，而它看起来像在测）。缺省 = 真的缺省。 */
function cell(o: {
  source: TrendSource
  series?: Series | undefined
  seriesEval?: Series | undefined
}): string {
  return renderToString(
    h(TrendCell, {
      series: 'series' in o ? o.series : win,
      seriesEval: 'seriesEval' in o ? o.seriesEval : ev,
      source: o.source,
      fmt: (v) => (v == null ? '—' : String(v)),
      toneOf: winTone,
      range: 'all',
    }),
  )
}

describe('TrendSource（纯函数）', () => {
  it('选项 = 全部 / rollout / eval（顺序即上屏顺序）', () => {
    expect(TREND_SOURCE_OPTIONS.map((o) => o.value)).toEqual(['all', 'rollout', 'eval'])
    expect(TREND_SOURCE_OPTIONS.map((o) => o.label)).toEqual(['全部', 'rollout', 'eval'])
  })

  it('isTrendSource：三个合法值通过，其余（含空串、大小写变体、null）一律不收', () => {
    for (const v of ['all', 'rollout', 'eval']) expect(isTrendSource(v)).toBe(true)
    for (const v of ['', 'ALL', 'Rollout', 'eval ', 'both', null, undefined])
      expect(isTrendSource(v as string | null | undefined)).toBe(false)
  })
})

describe('TrendCell · 三档渲染形状', () => {
  it('全部：两条线（主序列 + eval 橙）+ 值列 `主 / eval`', () => {
    const html = cell({ source: 'all' })
    expect(html).toContain('tc-tcell__lbl')
    expect(html).toContain('>胜率</span>')
    expect(html).toContain('id="tg-winRate"') // 主序列 = rollout
    expect(html).toContain(EVAL_STROKE) // eval 叠加线在
    expect(html).toContain('tc-tcell__pair') // 双口径值列
    expect(html).toContain('/ 0.3')
  })

  it('rollout：只一条线 —— 没有 eval 橙线、没有双口径值列', () => {
    const html = cell({ source: 'rollout' })
    expect(html).toContain('>胜率</span>')
    expect(html).toContain('id="tg-winRate"')
    expect(html).not.toContain(EVAL_STROKE)
    expect(html).not.toContain('tc-tcell__pair')
    expect(html).toContain('>0.5</b>')
  })

  it('eval：唯一那条线是 eval —— 换标签、换色、换值列（口径整套跟着换）', () => {
    const html = cell({ source: 'eval' })
    // 标签是 eval 自己的词（不是把 rollout 的标签留在原处）
    expect(html).toContain('>eval 胜率</span>')
    expect(html).not.toContain('>胜率</span>')
    // 线色 = eval 橙（一条线也要能看出它是什么口径），且**只有一条**
    expect((html.match(/stroke="var\(--eval-line, #ea580c\)"/g) ?? []).length).toBe(1)
    // 主序列的渐变 id 也是 eval（不再是 tg-winRate）
    expect(html).toContain('id="tg-eval"')
    expect(html).not.toContain('id="tg-winRate"')
    // 值列 = eval 的数，没有双值
    expect(html).toContain('>0.3</b>')
    expect(html).not.toContain('tc-tcell__pair')
  })

  it('eval 档的 toneOf 用的是「正在显示的那条」：rollout 0.5（绿）不得去染 eval 0.1（红）', () => {
    // rollout 0.5 → winTone('g')；eval 0.1 → winTone('y')。eval 档必须显示黄，不是绿。
    const e: Series = { key: 'eval', label: 'eval 胜率', vals: [0.1], iters: [1] }
    const w: Series = { key: 'winRate', label: '胜率', vals: [0.5], iters: [1] }
    expect(winTone(0.5)).toBe('g')
    expect(winTone(0.1)).toBe('y')
    expect(cell({ source: 'eval', series: w, seriesEval: e })).toContain('tc-mtrend__val--y')
    expect(cell({ source: 'eval', series: w, seriesEval: e })).not.toContain('tc-mtrend__val--g')
    expect(cell({ source: 'rollout', series: w, seriesEval: e })).toContain('tc-mtrend__val--g')
  })

  it('eval 档但该课没有评估数据：说出原因（不是空白格）', () => {
    const html = cell({ source: 'eval', series: win, seriesEval: undefined })
    expect(html).toContain('该课程暂无干净评估')
    expect(html).not.toContain('tc-trend__svg')
    expect(html).toContain('>—</span>')
  })

  it('rollout 档缺主序列：保持旧行为（不出图，也不冒充有数据）', () => {
    const html = cell({ source: 'rollout', series: undefined, seriesEval: ev })
    expect(html).not.toContain('tc-trend__svg')
    expect(html).not.toContain('该课程暂无干净评估') // 那句只属于 eval 档
  })
})
