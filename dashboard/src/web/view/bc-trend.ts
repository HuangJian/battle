/** bc-trend.ts — BC 趋势序列（纯函数；BcPanel 与测试共用）。
 *
 * **x 轴 = 全局 epoch，不是 `it`**（plan §13.2 #3）：续训（改 epochs 重开）或 A→B 接替后
 * `it` 会重置/变化，而 `bc.py` 的 `epoch_base + epoch` 继续递增；按 `it` 归零会把曲线断成
 * 两段。同一 epoch 的重放行以最后一行为准（同 x 不画两遍）。
 */

import type { BcEpochRow, BcEvalRow } from './bc-types'
import type { Series } from './series'

export interface BcTrendChart {
  key: string
  label: string
  series: Series
  /** eval 曲线：组件注入口径色（view 层不持有色值，避免第二处字面量）。 */
  evalSeries?: boolean
  tone?: 'g' | 'y' | 'r'
}

/** 每个 epoch 的 eval 总胜率（跨地图按局数加权）；无有效局数 ⇒ 不进表。 */
export function evalWinByEpoch(evals: BcEvalRow[]): Map<number, number> {
  const acc = new Map<number, { wins: number; n: number }>()
  for (const ev of evals) {
    if (!(ev.epoch > 0)) continue
    const cur = acc.get(ev.epoch) ?? { wins: 0, n: 0 }
    for (const l of ev.levels) {
      cur.wins += l.wins
      cur.n += l.n
    }
    acc.set(ev.epoch, cur)
  }
  const out = new Map<number, number>()
  for (const [epoch, { wins, n }] of acc) {
    if (n > 0) out.set(epoch, wins / n)
  }
  return out
}

function seriesOf(
  key: string,
  label: string,
  iters: number[],
  vals: Array<number | null | undefined>,
): Series {
  return {
    key,
    label,
    iters,
    // 缺值必须是 NaN（TrendChart 的断笔语义）：0 会被画成真实的下降点。
    vals: vals.map((v) => (v == null || !Number.isFinite(v) ? Number.NaN : v)),
  }
}

/** 缺失的全局 epoch（B8）：面板据此标「数据不全」，而不是把缺口当连续曲线画。
 *  `expected` = 课程声明的总 epoch（0 = 用已观察到的最大 epoch 当右边界）。 */
export function missingEpochs(epochs: BcEpochRow[], expected = 0): number[] {
  const have = new Set(epochs.map((r) => r.epoch).filter((e) => e > 0))
  if (have.size === 0) return []
  const last = expected > 0 ? expected : Math.max(...have)
  const out: number[] = []
  for (let e = 1; e <= last; e++) {
    if (!have.has(e)) out.push(e)
  }
  return out
}

export function buildBcTrend(epochs: BcEpochRow[], evals: BcEvalRow[]): BcTrendChart[] {
  const byEpoch = new Map<number, BcEpochRow>()
  for (const r of epochs) {
    if (r.epoch > 0) byEpoch.set(r.epoch, r)
  }
  const rows = [...byEpoch.values()].sort((a, b) => a.epoch - b.epoch)
  if (rows.length === 0) return []

  const iters = rows.map((r) => r.epoch)
  const charts: BcTrendChart[] = [
    {
      key: 'train_loss',
      label: 'train loss',
      series: seriesOf(
        'train_loss',
        'train loss',
        iters,
        rows.map((r) => r.trainLoss),
      ),
      tone: 'r',
    },
    {
      key: 'val_loss',
      label: 'val loss',
      series: seriesOf(
        'val_loss',
        'val loss',
        iters,
        rows.map((r) => r.valLoss),
      ),
      tone: 'r',
    },
    {
      key: 'move_acc',
      label: 'move acc',
      series: seriesOf(
        'move_acc',
        'move acc',
        iters,
        rows.map((r) => r.moveAcc),
      ),
      tone: 'g',
    },
    {
      key: 'fire_acc',
      label: 'fire acc',
      series: seriesOf(
        'fire_acc',
        'fire acc',
        iters,
        rows.map((r) => r.fireAcc),
      ),
      tone: 'g',
    },
  ]
  if (rows.some((r) => r.holdAcc != null)) {
    charts.push({
      key: 'hold_acc',
      label: 'hold acc',
      series: seriesOf(
        'hold_acc',
        'hold acc',
        iters,
        rows.map((r) => r.holdAcc),
      ),
      tone: 'g',
    })
  }
  if (rows.some((r) => r.lr != null)) {
    charts.push({
      key: 'lr',
      label: 'lr',
      series: seriesOf(
        'lr',
        'lr',
        iters,
        rows.map((r) => r.lr),
      ),
    })
  }

  const win = evalWinByEpoch(evals)
  if (win.size > 0) {
    charts.push({
      key: 'eval_win',
      label: 'eval win_rate',
      series: seriesOf(
        'eval_win',
        'eval win_rate',
        iters,
        iters.map((ep) => win.get(ep)),
      ),
      evalSeries: true,
      tone: 'g',
    })
  }
  return charts
}
