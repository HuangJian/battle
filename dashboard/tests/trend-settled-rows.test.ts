/**
 * trend-settled-rows.test.ts — 趋势图只画**已结算**的轮：eval 未结束的 it 不进走势图
 *
 * 分层：src/server/iters.ts（`evalInFlight` 事实）· src/web/view/series.ts（`metricSeries` 过滤）
 *
 * ★ 用户口径（2026-11-04）：一个 it 的 eval 未结束时，其指标不要在趋势图里绘制 ——
 *   半轮（`rollout_end` 刚收官、等 PPO）或迟到评估未结算时，rollout 读数虽然齐，
 *   与 eval 口径不可比，画上去会被当成趋势拐点。**指标表不过滤**（实时观察口，
 *   `PPO 中` 徽标已标明半轮）。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { readIterMetrics } from '../src/server/iters'
import { metricSeries } from '../src/web/view'
import type { IterRow } from '../src/web/view'

function withTmp<T>(fn: (dir: string) => T): T {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-trend-settled-'))
  try {
    return fn(dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

/** 最小合法 `IterRow`（趋势图只读 iter / winRate / actuals / evalData / 两个未结算标记）。 */
function row(iter: number, over: Partial<IterRow> = {}): IterRow {
  return {
    iter,
    time: `t${iter}`,
    winRate: iter / 10,
    scoreMean: 0,
    scoreStd: 0,
    samples: 100,
    rolloutSec: 10,
    ppoSec: 5,
    pureCollectSec: 8,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.01,
    entropy: 1,
    policyLoss: 0,
    valueLoss: 0,
    meanRet: 0,
    lr: 0.0001,
    expectedGames: 10,
    halted: false,
    topDims: '',
    avgTicks: 100,
    accuracy: 0,
    loot: 0,
    kills: 0,
    actuals: null,
    evalData: null,
    ...over,
  }
}

describe('趋势图只画已结算轮', () => {
  it('服务端：有 eval 逐局行、无 summary ⇒ evalInFlight；结算后同一轮为 false', () => {
    withTmp((dir) => {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        [
          JSON.stringify({ event: 'iteration', iter: 3, time: 't3', winRate: 0.3 }),
          JSON.stringify({ event: 'iteration', iter: 4, time: 't4', winRate: 0.4 }),
        ].join('\n'),
        'utf-8',
      )
      writeFileSync(
        path.join(dir, 'eval_log.jsonl'),
        [
          // it3：逐局行 + 结算 summary（已完成）
          JSON.stringify({ event: 'eval', iter: 3, wver: 'w3', stage: 1, seed: 11, win: true }),
          JSON.stringify({
            event: 'eval_summary',
            iter: 3,
            wver: 'w3',
            games: 1,
            wins: 1,
            winRate: 1,
            time: 't3',
          }),
          // it4：只有逐局行 —— 评估还在跑
          JSON.stringify({ event: 'eval', iter: 4, wver: 'w4', stage: 1, seed: 12, win: false }),
        ].join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      const it3 = rows.find((r) => r.iter === 3)!
      const it4 = rows.find((r) => r.iter === 4)!
      expect(it3.evalInFlight).toBe(false)
      expect(it4.evalInFlight).toBe(true)
      // 两行都只是在盘事实：指标表照旧吃它们，过滤只发生在走势图。
      expect(it4.evalData).toBeNull()
    })
  })

  it('服务端：只有 rollout_end 行 ⇒ pendingPpo（等 PPO 窗口，eval 必然未结束）', () => {
    withTmp((dir) => {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({ event: 'rollout_end', iter: 6, time: 't6', winRate: 0.6 }) + '\n',
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(1)
      expect(rows[0]!.pendingPpo).toBe(true)
      expect(rows[0]!.evalInFlight).toBe(false)
    })
  })

  it('metricSeries：pendingPpo / evalInFlight 的轮不画，其余轮逐位对齐', () => {
    const rows = [
      row(1, { winRate: 0.1 }),
      row(2, { winRate: 0.2, pendingPpo: true }),
      row(3, { winRate: 0.3, evalInFlight: true }),
      row(4, { winRate: 0.4 }),
    ]
    const win = metricSeries(rows).find((s) => s.key === 'winRate')!
    expect(win.iters).toEqual([1, 4])
    expect(win.vals).toEqual([0.1, 0.4])
  })

  it('eval 结算后（标记撤掉）同一轮重新入图 —— 过滤是状态，不是永久丢点', () => {
    const pending = row(5, { winRate: 0.5, evalInFlight: true })
    const settled = row(5, { winRate: 0.5 })
    expect(metricSeries([pending]).find((s) => s.key === 'winRate')!.iters).toEqual([])
    expect(metricSeries([settled]).find((s) => s.key === 'winRate')!.iters).toEqual([5])
  })
})
