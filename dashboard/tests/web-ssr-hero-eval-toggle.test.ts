/**
 * web-ssr-hero-eval-toggle.test.ts — 最新 6 轮完整指标 + hero 主行 / eval toggle 与 eval 视图数据源口径
 *
 * 分层：src/web/app/panels/Hero.tsx
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, render, view } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import type { IterRow } from '../src/web/view'

describe('console hero 最新 6 轮 eval toggle', () => {
  const mkIterRow = (iter: number, hasEval: boolean): IterRow => ({
    iter,
    time: `t${iter}`,
    winRate: 0.5,
    scoreMean: 0,
    scoreStd: 0,
    samples: 1,
    rolloutSec: 1,
    ppoSec: 1,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0,
    entropy: 1,
    policyLoss: 0,
    valueLoss: 0,
    meanRet: 0,
    lr: 1e-4,
    expectedGames: 4,
    halted: false,
    topDims: '',
    avgTicks: 100,
    accuracy: 0,
    loot: 0,
    kills: 0,
    actuals: null,
    evalData: hasEval
      ? {
          time: `e${iter}`,
          games: 10,
          wins: 1,
          winRate: 0.1,
          clears: 0,
          clearRate: 0,
          dropped: 0,
          sec: 30,
          wver: 'v1',
          outcomes: {},
          avgTicks: 100,
          avgWinTicks: null,
          totalKills: 1,
          totalPU: 0,
          avgResidualHp: null,
          avgLossTicks: null,
          dmgPerKill: null,
          scoreMean: 0,
          scoreStd: 0,
        }
      : null,
  })

  it('eval 视图数据源 = 有 evalData 的轮，iter 倒序（与抽屉 eval 过滤同口径）', () => {
    const rows = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => mkIterRow(i, i % 2 === 0))
    const ev = view.filterGroups(view.iterGroups(rows), 'eval')
    expect(ev.map((g) => g.iter)).toEqual([8, 6, 4, 2])
    for (const g of ev) expect(g.eval).not.toBeNull()
    // rollout 过滤 = 无 eval 的轮（奇数 iter：1,3,5,7）；eval 过滤 = 有 eval 的轮（偶数）
    const mains = view.filterGroups(view.iterGroups(rows), 'rollout')
    expect(mains.map((g) => g.iter)).toEqual([7, 5, 3, 1])
    expect(mains.length).toBe(4)
  })

  it('hero SSR 默认主行视图：渲染主行/eval toggle 与「最新 6 轮完整指标」', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('主行') // toggle 主行档
    expect(html).toContain('完整指标表')
    expect(html).toContain('最新 ')
  })
})
