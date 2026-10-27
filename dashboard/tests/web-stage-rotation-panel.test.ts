/**
 * web-stage-rotation-panel.test.ts — 「采集窗口」面板与两列（窗口 / 各关进度）
 * （2026-10-28，plan/it-stage-ratation §9.3）
 *
 * 分层：src/web/view/rotation.ts（展示纯函数）· src/web/app/panels/StageRotationPanel.tsx ·
 *       src/web/view/metric-columns.ts + src/web/app/panels/MetricsTable.tsx（两列）·
 *       src/web/app/app.tsx（挂载点）
 *
 * 守什么：
 *   ① 无轮换/分关读数 ⇒ 面板整块不渲染（老课程页零变化）；
 *   ② 有读数的课：窗口（`12/35` + 关号）、逐关表（收到/达标线/缺口）、最近轮次一览都上屏；
 *   ③ 缺哪一格显 `—`，绝不编 0（0 在配额语义里是「真的没采到」）；
 *   ④ 两列的表头/口径住在列模型里（`METRIC_COLS`），且渲染出来确实等于模型。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import {
  metStages,
  METRIC_COLS,
  stageRangeText,
  volumeText,
  volumeTitle,
  windowText,
  windowTitle,
  type ConsoleStateView,
  type IterRow,
  type IterVolume,
} from '../src/web/view'
import { StageRotationPanel } from '../src/web/app/panels/StageRotationPanel'
import { MetricsTable } from '../src/web/app/panels/MetricsTable'

const ROTATION = { mode: 'even', perIt: 12, pinned: [2033], all: 35, window: [2033, 2034, 2035] }

const VOLUME: IterVolume = {
  rule: 'per-stage-v3',
  quota: 17,
  estSrc: 'trailing',
  halted: false,
  stages: [
    { stage: 2033, collected: 17, quota: 17, games: 2, capGames: 8, estS: 9, shortfall: 0 },
    { stage: 2034, collected: 9, quota: 17, games: 1, capGames: 8, estS: 10, shortfall: 8 },
    { stage: 2035, collected: 0, quota: 17, games: 0, capGames: 8, estS: 0, shortfall: 17 },
  ],
}

function row(iter: number, over: Partial<IterRow> = {}): IterRow {
  return {
    iter,
    time: `2026-10-28 0${iter}:00:00`,
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
    ...over,
  }
}

function mkView(iters: IterRow[]): ConsoleStateView {
  return {
    time: 't',
    course: 'x34',
    courses: [],
    components: [],
    nodes: [],
    modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
    metrics: { available: true, iters },
    phase: { phase: 'idle', sinceMs: null, iter: null },
  }
}

/** 只取 `#root` 内的渲染体（整份 theme.css 内联在 head 的 <style> 里，不切就断言到样式表）。 */
function slice(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

describe('展示纯函数（view/rotation.ts）', () => {
  it('关号压缩：连续段合并、非连续照旧', () => {
    expect(stageRangeText([])).toBe('')
    expect(stageRangeText([0, 1, 2, 5])).toBe('0-2,5')
    expect(stageRangeText([2005, 2000, 2001, 2002, 2030, 2031])).toBe('2000-2002,2005,2030-2031')
  })

  it('窗口文字：窗口关数/全关数；无读数/坏读数都是 —（不编 0/0）', () => {
    expect(windowText(ROTATION)).toBe('3/35')
    expect(windowText(null)).toBe('—')
    expect(windowTitle(ROTATION)).toContain('even')
    expect(windowTitle(ROTATION)).toContain('2033')
    expect(windowTitle(null)).toContain('没有窗口读数')
  })

  it('各关进度：达标数/总关数；缺 quota ⇒ —；hover 列逐关与缺口', () => {
    expect(metStages(VOLUME)).toBe(1)
    expect(volumeText(VOLUME)).toBe('达标 1/3')
    expect(volumeText(null)).toBe('—')
    expect(volumeText({ ...VOLUME, quota: null })).toBe('—')
    const t = volumeTitle(VOLUME)
    expect(t).toContain('2034: 9/17 · 缺 8')
    expect(t).toContain('2033: 17/17')
    expect(volumeTitle(null)).toContain('没有分关采集账')
  })
})

describe('StageRotationPanel 渲染', () => {
  it('无任何轮换/分关读数 ⇒ 整块不渲染（老课程零变化）', () => {
    const html = renderToString(h(StageRotationPanel, { stateView: mkView([row(1), row(2)]) }))
    expect(html).toBe('')
  })

  it('有读数 ⇒ 窗口读数 + 逐关表 + 最近轮次一览', () => {
    const html = slice(
      renderToString(
        h(StageRotationPanel, {
          stateView: mkView([
            row(12, { rotation: ROTATION, volume: VOLUME }),
            row(11, {
              rotation: { ...ROTATION, window: [2036] },
              volume: { ...VOLUME, halted: true },
            }),
            row(10),
          ]),
        }),
      ),
    )
    expect(html).toContain('采集窗口')
    expect(html).toContain('轮换 even')
    expect(html).toContain('窗口 3/35 关')
    expect(html).toContain('it12')
    // 逐关表：三关都列，缺口与达标线可见
    expect(html).toContain('2035')
    expect(html).toContain('达标 1/3')
    expect(html).toContain('17') // 达标线
    // 最近轮次一览：两轮有读数（it12/it11）⇒ 表头说 2；it11 的满额备注可见
    expect(html).toContain('最近 2 轮')
    expect(html).toContain('满额')
  })

  it('触硬顶标注：更该被看见的是「缺口是顶出来的」', () => {
    const html = renderToString(
      h(StageRotationPanel, {
        stateView: mkView([row(7, { rotation: ROTATION, volume: VOLUME, capped: true })]),
      }),
    )
    expect(html).toContain('触硬顶')
  })

  it('非轮换的动态采集课（只有分关账）⇒ 面板照旧出，标「全关采集」', () => {
    const html = renderToString(
      h(StageRotationPanel, {
        stateView: mkView([
          row(3, {
            rotation: null,
            volume: { ...VOLUME, quota: 10, stages: VOLUME.stages.slice(0, 2) },
          }),
        ]),
      }),
    )
    expect(html).toContain('采集窗口')
    expect(html).toContain('全关采集')
    expect(html).toContain('达标 1/2')
  })
})

describe('两列（窗口 / 各关进度）', () => {
  it('列模型里有它们（表头文字唯一一份），数字列清单不受影响', () => {
    expect(METRIC_COLS.window.label).toBe('窗口')
    expect(METRIC_COLS.stageProgress.label).toBe('各关进度')
    expect(METRIC_COLS.window.num).toBeFalsy()
    expect(METRIC_COLS.stageProgress.num).toBeFalsy()
  })

  it('指标页表渲染出这两列，格子读的就是账本读数', () => {
    const html = slice(
      renderToString(
        h(MetricsTable, {
          stateView: mkView([row(9, { rotation: ROTATION, volume: VOLUME })]),
        }),
      ),
    )
    expect(html).toContain('>窗口<')
    expect(html).toContain('>各关进度<')
    expect(html).toContain('3/35')
    expect(html).toContain('达标 1/3')
  })
})

describe('挂载点（源码级闸）', () => {
  it('「指标」页同时渲染采集窗口面板与指标表', () => {
    const src = readFileSync('src/web/app/app.tsx', 'utf8')
    expect(src).toContain("import { StageRotationPanel } from './panels/StageRotationPanel'")
    expect(src).toContain('<StageRotationPanel stateView={stateView} />')
  })
})
