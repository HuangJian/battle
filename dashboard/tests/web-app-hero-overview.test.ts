/**
 * web-app-hero-overview.test.ts — Hero 最新 6 轮完整指标（§367）
 *
 * 分层：src/web/app/panels/Hero.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 * 2026-09-14：同屏多课总览（P5-W2）面板随用户指令整体下线，相关用例删除。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { type ConsoleStateView, type IterRow } from '../src/web/view'

// ────────────────────────── 纯函数：指标行 / 排序 / 过滤 ──────────────────────────
function fakeRow(iter: number, evalData: IterRow['evalData'] = null): IterRow {
  return {
    iter,
    time: '2026-09-07 09:00:00',
    winRate: 0.1,
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

describe('Hero 训练状态区（§367：最新 6 轮完整指标）', () => {
  it('渲染最新 6 轮（iter 倒序截断）；超过 6 轮不溢出；无数据不出表', async () => {
    const { Hero } = await import('../src/web/app/panels/Hero')
    const mkView = (iters: IterRow[]): ConsoleStateView => ({
      time: 't',
      course: 'kb1',
      courses: [],
      components: [],
      nodes: [],
      modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: true, iters },
      phase: { phase: 'idle', sinceMs: null, iter: null },
    })
    // 8 轮 → 只出 3..8（倒序前 6）
    const html = renderToString(
      h(Hero, {
        stateView: mkView(Array.from({ length: 8 }, (_, i) => fakeRow(i + 1))),
        onMore: () => {},
      }),
    )
    expect(html).toContain('最新 6 轮完整指标')
    for (const it of [8, 7, 6, 5, 4, 3]) expect(html).toContain(`<b>${it}</b>`)
    for (const it of [2, 1]) expect(html).not.toContain(`<b>${it}</b>`)
    // 不足 6 轮：全出，标题带实际行数
    const html2 = renderToString(
      h(Hero, { stateView: mkView([fakeRow(1), fakeRow(2)]), onMore: () => {} }),
    )
    expect(html2).toContain('最新 2 轮完整指标')
    expect(html2).toContain('<b>2</b>')
    expect(html2).toContain('<b>1</b>')
    // 无数据：hero 空态无表
    const html3 = renderToString(h(Hero, { stateView: mkView([]), onMore: () => {} }))
    expect(html3).not.toContain('tc-hero__iters')
  })
})
