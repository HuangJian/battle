/** bc-trend.test.ts —— BC 面板趋势序列（plan §13.2 #3 / #11）。 */

import { describe, expect, it } from 'bun:test'
import { buildBcTrend, evalWinByEpoch, missingEpochs } from '../src/web/view'
import type { BcEpochRow, BcEvalRow, BcEvalLevelRow } from '../src/web/view'

function ep(epoch: number, over: Partial<BcEpochRow> = {}): BcEpochRow {
  return {
    it: 1,
    epoch,
    trainLoss: 0.1 + epoch / 100,
    valLoss: 0.2 + epoch / 100,
    moveAcc: 0.5,
    fireAcc: 0.4,
    holdAcc: null,
    lr: null,
    ts: epoch,
    ...over,
  }
}

function lvl(level: string, wins: number, n: number): BcEvalLevelRow {
  return {
    level,
    n,
    wins,
    winRate: n > 0 ? wins / n : 0,
    killsMean: 0,
    phitsMean: 0,
    pickupMean: 0,
    timeoutFrac: 0,
    scoreMean: 0,
    meanTicks: 0,
    failed: 0,
  }
}

function ev(epoch: number, levels: BcEvalLevelRow[]): BcEvalRow {
  return { it: 1, epoch, wver: 'w', levels, ts: epoch }
}

describe('buildBcTrend', () => {
  it('空账本 ⇒ 无图（不渲染误导空轴）', () => {
    expect(buildBcTrend([], [])).toEqual([])
  })

  it('x 轴 = 全局 epoch：续训 it 重置后曲线不断段', () => {
    const charts = buildBcTrend([ep(40, { it: 1 }), ep(41, { it: 1 }), ep(42, { it: 2 })], [])
    const train = charts.find((c) => c.key === 'train_loss')!
    expect(train.series.iters).toEqual([40, 41, 42])
    expect(train.series.vals).toEqual([0.5, 0.51, 0.52])
  })

  it('同一 epoch 重放 ⇒ 以最后一行为准且只画一次', () => {
    const charts = buildBcTrend(
      [ep(3, { valLoss: 9 }), ep(3, { valLoss: 1 }), ep(1, { valLoss: 2 })],
      [],
    )
    const val = charts.find((c) => c.key === 'val_loss')!
    expect(val.series.iters).toEqual([1, 3])
    expect(val.series.vals[1]).toBe(1)
  })

  it('hold acc 只在 v6 账本有读数时出图；legacy 行不画成 0', () => {
    expect(buildBcTrend([ep(1), ep(2)], []).map((c) => c.key)).not.toContain('hold_acc')
    const charts = buildBcTrend([ep(1, { holdAcc: 0.3 }), ep(2, { holdAcc: 0.6 })], [])
    const hold = charts.find((c) => c.key === 'hold_acc')!
    expect(hold.series.vals).toEqual([0.3, 0.6])
  })

  it('缺值 = NaN 断笔（不当作 0）', () => {
    const charts = buildBcTrend([ep(1, { valLoss: null }), ep(2)], [])
    const val = charts.find((c) => c.key === 'val_loss')!
    expect(Number.isNaN(val.series.vals[0]!)).toBe(true)
    expect(Number.isFinite(val.series.vals[1]!)).toBe(true)
  })

  it('eval win_rate 独立橙线：跨地图按局数加权，缺评估 epoch = NaN', () => {
    const charts = buildBcTrend([ep(1), ep(2), ep(3)], [ev(2, [lvl('a', 3, 4), lvl('b', 1, 2)])])
    const evalChart = charts.find((c) => c.key === 'eval_win')!
    expect(evalChart.evalSeries).toBe(true)
    expect(evalChart.series.iters).toEqual([1, 2, 3])
    expect(Number.isNaN(evalChart.series.vals[0]!)).toBe(true)
    expect(evalChart.series.vals[1]).toBeCloseTo(4 / 6, 10)
    expect(Number.isNaN(evalChart.series.vals[2]!)).toBe(true)
  })

  it('无 eval ⇒ 不出 eval 图（不与 win 0 混淆）', () => {
    expect(buildBcTrend([ep(1)], []).map((c) => c.key)).not.toContain('eval_win')
  })
})

describe('missingEpochs（B8 数据不全）', () => {
  it('空账本 ⇒ 不报缺号', () => {
    expect(missingEpochs([])).toEqual([])
  })

  it('连续账本 ⇒ 无缺口', () => {
    expect(missingEpochs([ep(1), ep(2), ep(3)])).toEqual([])
  })

  it('缺 5/25 ⇒ 精确点名（不把缺口当连续）', () => {
    const rows = Array.from({ length: 27 }, (_, i) => ep(i + 1)).filter(
      (r) => r.epoch !== 5 && r.epoch !== 25,
    )
    expect(missingEpochs(rows)).toEqual([5, 25])
  })

  it('expected 右边界：末轮缺失也算（ep80 必达）', () => {
    expect(missingEpochs([ep(1), ep(2)], 4)).toEqual([3, 4])
  })
})

describe('evalWinByEpoch', () => {
  it('零局数不进表；同 epoch 多次评估求和', () => {
    const win = evalWinByEpoch([
      ev(1, [lvl('a', 0, 0)]),
      ev(2, [lvl('a', 1, 2)]),
      ev(2, [lvl('b', 1, 2)]),
    ])
    expect(win.has(1)).toBe(false)
    expect(win.get(2)).toBeCloseTo(0.5, 10)
  })
})
