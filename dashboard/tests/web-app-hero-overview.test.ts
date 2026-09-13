/**
 * web-app-hero-overview.test.ts — Hero 最新 6 轮完整指标（§367）+ 同屏多课总览（P5-W2）
 *
 * 分层：src/web/app/panels/Hero.tsx + MultiCourseOverview.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { type ConsoleStateView, type CourseOverview, type IterRow } from '../src/web/view'

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
      modes: { trainerPpo: 'pull', stream: 0, doubleBuffer: 0, precollectEarly: 0 },
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

// ────────────────────────── 多课程总览（P5-W2） ──────────────────────────
function covOf(course: string, over: Partial<CourseOverview> = {}): CourseOverview {
  return {
    course,
    components: [
      { key: 'hubServer', status: 'running', pid: 11 },
      { key: 'cloudflared', status: 'stopped', pid: null },
      { key: 'workerServe', status: 'exited', pid: 22 },
      { key: 'trainingLoop', status: 'running', pid: 33 },
    ],
    phase: { phase: 'rollout', sinceMs: null, iter: 12 },
    last: { iter: 12, winRate: 0.42, rolloutSec: 82, ppoSec: 240, halted: false },
    iters: 12,
    cloudHalt: null,
    ppoQueueStall: null,
    ...over,
  }
}

describe('MultiCourseOverview 同屏多课总览（P5-W2）', () => {
  it('单课不渲染；多课每课一行（组件状态点 + 最近指标 + 停机徽标）', async () => {
    const { MultiCourseOverview } = await import('../src/web/app/panels/MultiCourseOverview')
    const base: ConsoleStateView = {
      time: 't',
      course: 'a',
      courses: ['a', 'b'],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull', stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle', sinceMs: null, iter: null },
    }
    // 单课（只有 1 行）→ 不制造与组件卡重复的面板
    const single = renderToString(
      h(MultiCourseOverview, {
        stateView: { ...base, courseOverviews: [covOf('a')] },
        onSelectCourse: () => {},
      }),
    )
    expect(single).not.toContain('多课程总览')

    const html = renderToString(
      h(MultiCourseOverview, {
        stateView: {
          ...base,
          courseOverviews: [
            covOf('a'),
            covOf('b', {
              phase: { phase: 'ppo', sinceMs: null, iter: 3 },
              last: { iter: 3, winRate: 0.1, rolloutSec: 30, ppoSec: 0, halted: true },
              iters: 3,
              cloudHalt: { status: 'halted', reason: 'TrainingLoop 停车' },
              ppoQueueStall: { jobId: 'j1', waitedSec: 400, it: 3 },
            }),
          ],
        },
        onSelectCourse: () => {},
      }),
    )
    expect(html).toContain('多课程总览（2 课')
    expect(html).toContain('查看课程 a')
    expect(html).toContain('查看课程 b')
    // 指标：a 课 it12 / 42% / 采集耗时
    expect(html).toContain('it12')
    expect(html).toContain('胜 42.0%')
    expect(html).toContain('采 1m22s')
    // 组件短名 + 状态点（running=on / exited=dead / stopped=empty）
    expect(html).toContain('trainer')
    expect(html).toContain('tc-dot tc-dot--on')
    expect(html).toContain('tc-dot tc-dot--dead')
    // b 课：阶段 PPO、本轮停车、停机红徽标、排队超时黄徽标
    expect(html).toContain('PPO')
    expect(html).toContain('本轮停车')
    expect(html).toContain('停机中')
    expect(html).toContain('排队超时')
  })

  it('getCourseOverviews：空课程清单 → 空数组（不碰账本/日志）', async () => {
    const { getCourseOverviews } = await import('../src/server/api')
    expect(getCourseOverviews({} as never, [], {})).toEqual([])
  })
})
