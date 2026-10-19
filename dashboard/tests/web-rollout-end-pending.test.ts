/**
 * web-rollout-end-pending.test.ts — 「PPO 中」标记在**两张指标表**的 iter 列都出得来。
 *
 * 分层：src/web/app/panels（Hero 主表 / MetricsTable）
 *
 * 背景（用户 2026-10-19 口径）：指标行现在可以来自 `rollout_end`（采集收官即刻），
 *  此时 PPO 侧那几列是 NaN 缺口。若 UI 不标注，操作员会把「空」读成「没动」。
 *  ⇒ iter 列给一盏灯；它**只说明来源**，不动任何读数。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { type ConsoleStateView, type IterRow } from '../src/web/view'

function baseRow(iter: number): IterRow {
  return {
    iter,
    time: '2026-10-19 07:00:00',
    winRate: 0.42,
    scoreMean: 0.75,
    scoreStd: 0.11,
    samples: 12345,
    rolloutSec: 31.5,
    ppoSec: 120,
    pureCollectSec: 28,
    ppoCloudSec: 90,
    distPhaseSec: 2.5,
    kl: 0.012,
    entropy: 0.8,
    policyLoss: -0.01,
    valueLoss: 0.02,
    meanRet: -0.4,
    lr: 0.0001,
    expectedGames: 200,
    halted: false,
    topDims: '',
    avgTicks: 2000,
    accuracy: 0.5,
    loot: 0.1,
    kills: 6,
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

describe('rollout_end 行的「PPO 中」标记', () => {
  it('Hero 主表：pendingPpo 行出标记，普通行不出', async () => {
    const { Hero } = await import('../src/web/app/panels/Hero')
    const pending = { ...baseRow(6), pendingPpo: true }
    const html = renderToString(
      h(Hero, { stateView: mkView([pending, baseRow(5)]), onMore: () => {} }),
    )
    expect(html).toContain('PPO 中')
    // 只给 pending 那一行：整页只有一处标记
    expect(html.split('PPO 中').length - 1).toBe(1)

    const plain = renderToString(
      h(Hero, { stateView: mkView([baseRow(6), baseRow(5)]), onMore: () => {} }),
    )
    expect(plain).not.toContain('PPO 中')
  })

  it('指标页表：pendingPpo 行出标记，普通行不出', async () => {
    const { MetricsTable } = await import('../src/web/app/panels/MetricsTable')
    const pending = { ...baseRow(6), pendingPpo: true }
    const html = renderToString(
      h(MetricsTable, { stateView: mkView([pending, baseRow(5)]), onRefresh: () => {} }),
    )
    expect(html).toContain('PPO 中')
    expect(html.split('PPO 中').length - 1).toBe(1)

    const plain = renderToString(
      h(MetricsTable, { stateView: mkView([baseRow(6), baseRow(5)]), onRefresh: () => {} }),
    )
    expect(plain).not.toContain('PPO 中')
  })
})
