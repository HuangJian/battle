/**
 * web-view-trend-range.test.ts — §382 走势范围（全量 / 最近30 / 最近10）：metricSeries 全量正序对齐、eval「最近 N」= 最近 N 个有效点、hero 6 条走势图与档位开关
 *
 * 分层：src/web/view/series.ts（metricSeries / sliceSeries）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { describe, expect, it } from 'bun:test'
import { render, view } from './helpers/console-fixture'
import type { ConsoleStateView } from '../src/web/view'

describe('console sparkline (ui/view)', () => {
  describe('console trend chart / 走势范围 (§382: 全量/最近30/最近10)', () => {
    const mkView = (n: number, withEval: boolean): ConsoleStateView => ({
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      metrics: {
        available: true,
        iters: Array.from({ length: n }, (_, i) => ({
          iter: 100 - i,
          time: '',
          winRate: i / 100,
          scoreMean: i,
          scoreStd: 1,
          samples: 1,
          rolloutSec: 1,
          ppoSec: 1,
          kl: 0.01,
          entropy: 1,
          policyLoss: 0,
          valueLoss: 0,
          meanRet: 0,
          lr: 0.00005,
          expectedGames: 4,
          halted: false,
          topDims: '',
          avgTicks: 100,
          accuracy: 0,
          loot: 0,
          kills: 0,
          actuals: {
            games: 4,
            totalKills: i,
            totalPU: i % 3,
            avgTicks: 100,
            avgResidualHp: 150,
            avgWinTicks: 800,
            avgLossTicks: 900,
            dmgPerKill: 3,
          },
          evalData: withEval
            ? {
                time: '',
                games: 10,
                wins: i % 10,
                winRate: (i % 10) / 10,
                clears: 0,
                clearRate: 0,
                dropped: 0,
                sec: 30,
                wver: 'v1',
                outcomes: {},
                avgTicks: 100,
                avgWinTicks: 100,
                totalKills: i,
                totalPU: 0,
                avgResidualHp: 150,
                avgLossTicks: null,
                dmgPerKill: null,
                scoreMean: 0,
                scoreStd: 0,
              }
            : null,
        })),
      },
    })

    it('metricSeries 返回全量时间正序 + 逐位对齐的 iters', () => {
      const series = view.metricSeries(mkView(30, true).metrics.iters)
      const win = series.find((s) => s.key === 'winRate')!
      expect(win.vals.length).toBe(30)
      expect(win.iters.length).toBe(30)
      // 时间正序：iters 升序（输入 iter=100-i 是降序，排序后应升序）
      expect(win.iters[0]).toBe(71)
      expect(win.iters[29]).toBe(100)
    })

    it('sliceSeries：all 全量 / 30 / 10 截取点数正确', () => {
      const series = view.metricSeries(mkView(50, true).metrics.iters)
      const win = series.find((s) => s.key === 'winRate')!
      expect(view.sliceSeries(win, 'all').vals.length).toBe(50)
      expect(view.sliceSeries(win, '30').vals.length).toBe(30)
      expect(view.sliceSeries(win, '10').vals.length).toBe(10)
    })

    it('sliceSeries eval 特殊语义：「最近 N」= 最近 N 个有效点（跳过 NaN 缺口）', () => {
      // 仅偶数 iter 有 eval 数据 → 50 轮中约 25 个有效点
      const iters = Array.from({ length: 50 }, (_, i) => ({
        iter: i + 1,
        time: '',
        winRate: 0.5,
        scoreMean: 0,
        scoreStd: 0,
        samples: 1,
        rolloutSec: 1,
        ppoSec: 1,
        kl: 0,
        entropy: 1,
        policyLoss: 0,
        valueLoss: 0,
        meanRet: 0,
        lr: 0.0001,
        expectedGames: 4,
        halted: false,
        topDims: '',
        avgTicks: 100,
        accuracy: 0,
        loot: 0,
        kills: 0,
        actuals: null,
        evalData:
          (i + 1) % 2 === 0
            ? {
                time: '',
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
                avgWinTicks: 100,
                totalKills: 0,
                totalPU: 0,
                avgResidualHp: null,
                avgLossTicks: null,
                dmgPerKill: null,
                scoreMean: 0,
                scoreStd: 0,
              }
            : null,
      }))
      const series = view.metricSeries(iters)
      const evalS = series.find((s) => s.key === 'eval')!
      // eval 胜率：只保留非 NaN（25 个有效点），iters 逐位对齐
      const all = view.sliceSeries(evalS, 'all')
      expect(all.vals.length).toBe(25)
      expect(all.iters.length).toBe(25)
      // 最近 10 个有效 eval 点
      const last10 = view.sliceSeries(evalS, '10')
      expect(last10.vals.length).toBe(10)
      // 应为最后 10 个偶数 iter：32,34,...,50
      expect(last10.iters[0]).toBe(32)
      expect(last10.iters[9]).toBe(50)
    })

    it('metricSeries：胜局耗时/胜局残血/承伤·杀/败局耗时 = rollout「所有 iter」平均，非 eval 口径', () => {
      // actuals 提供 rollout 实际值；evalData 仅偶数 iter 有、且给了不同数值（1300/180）——
      // 四张图取 actuals（800/150/3/900），证明不再用 eval 100 局平均。
      const iters = Array.from({ length: 50 }, (_, i) => ({
        iter: i + 1,
        time: '',
        winRate: 0.5,
        scoreMean: 0,
        scoreStd: 0,
        samples: 1,
        rolloutSec: 1,
        ppoSec: 1,
        kl: 0,
        entropy: 1,
        policyLoss: 0,
        valueLoss: 0,
        meanRet: 0,
        lr: 0.0001,
        expectedGames: 4,
        halted: false,
        topDims: '',
        avgTicks: 100,
        accuracy: 0,
        loot: 0,
        kills: 0,
        actuals: {
          games: 4,
          totalKills: i,
          totalPU: i % 3,
          avgTicks: 100,
          avgResidualHp: 150,
          avgWinTicks: 800,
          avgLossTicks: 900,
          dmgPerKill: 3,
        },
        evalData:
          (i + 1) % 2 === 0
            ? {
                time: '',
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
                avgWinTicks: 1300,
                totalKills: 0,
                totalPU: 0,
                avgResidualHp: 180,
                avgLossTicks: null,
                dmgPerKill: null,
                scoreMean: 0,
                scoreStd: 0,
              }
            : null,
      }))
      const series = view.metricSeries(iters)
      const ticks = series.find((s) => s.key === 'winTicks')!
      expect(ticks.label).toBe('胜局耗时')
      const hp = series.find((s) => s.key === 'winHp')!
      expect(hp.label).toBe('胜局残血')
      const dmg = series.find((s) => s.key === 'dmgPerKill')!
      expect(dmg.label).toBe('承伤/杀')
      const loss = series.find((s) => s.key === 'lossTicks')!
      expect(loss.label).toBe('败局耗时')
      // rollout 口径：取 actuals（800/150/3/900），evalData 同轮给出 1300/180 亦被忽略；
      // 奇数 iter 无 evalData 仍是有效 rollout 点（非 NaN）。
      expect(ticks.vals[0]).toBeCloseTo(800)
      expect(hp.vals[0]).toBeCloseTo(150)
      expect(dmg.vals[0]).toBeCloseTo(3)
      expect(loss.vals[0]).toBeCloseTo(900)
      expect(Number.isFinite(ticks.vals[1])).toBe(true)
      // 所有 iter 均为 rollout 有效点（不再随 eval 缺口稀疏）→ 全量 = 50 点，最近 10 = 10 点
      expect(view.sliceSeries(hp, 'all').vals.length).toBe(50)
      expect(view.sliceSeries(ticks, '10').vals.length).toBe(10)
    })

    it('hero 渲染 6 条走势图（rollout+eval 叠加，无耗时/败局耗时）+ 范围档位开关', () => {
      const html = render.renderConsolePage(mkView(30, true))
      const charts = (html.match(/class="tc-trend__svg"/g) ?? []).length
      expect(charts).toBe(6)
      // 6 格：胜率 / 承伤·杀 / 击杀 / 胜局耗时 / 胜局残血 / 道具
      const labels = [...html.matchAll(/tc-tcell__lbl[^>]*>([^<]+)<\/span>/g)].map((m) => m[1])
      expect(labels).toEqual(['胜率', '承伤/杀', '击杀', '胜局耗时', '胜局残血', '道具'])
      // 双序列叠加：eval 橙线（#ea580c）在有 eval 数据时出现
      expect(html).toContain('#ea580c')
      // 范围档位渲染且默认最近 30
      expect(html).toContain('tc-trend-range__btn')
      expect(html).toContain('全量')
      expect(html).toContain('最近30')
      expect(html).toContain('最近10')
    })
  })
})
