/** eval-matrix.tsx — 共享的 EvalBoard iter×rung×指标矩阵（首页摘要 + /eval 独立页共用）。
 *
 * 口径（plan/evalboard-console-ux.md §4-R2/R5/R6）：扁平列 key = `${rung}.${metric}`，
 * 指标级显隐走 DataTable 的 toolbarLeft 插槽（不用 per-column 列▾）。
 * 本模块只做展示；数据由 console/evalboard.ts 的 EvalIterRow 提供。
 */

import type { ComponentChildren } from 'preact'
import type { EvalIterRow, EvalLadderRow, EvalMetricKey } from '../../../ui/view'
import { EVAL_METRIC_KEYS, EVAL_METRIC_LABELS, rungLabel } from '../../../ui/view'
import { DataTable, type Col } from '../../../ui/components/DataTable'

const pct = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

/** 指标值格式化（列渲染 + CSV 共用口径）。 */
export function fmtMetric(metric: EvalMetricKey, v: number | null): string {
  if (v === null || v === undefined) return '—'
  switch (metric) {
    case 'winRate':
    case 'clearRate':
    case 'killCompletion':
      return pct(v)
    case 'meanKills':
    case 'meanPowerUps':
      return v.toFixed(2)
    case 'winTickMean':
      return `${Math.round(v)}t`
    case 'winHpLeftMean':
      return String(Math.round(v))
  }
}

/** 矩阵列（扁平 key = `${rung}.${metric}`；thTitle = rungLabel + 指标）。 */
export function matrixColumns(
  ladder: EvalLadderRow[],
  metricKeys: EvalMetricKey[],
): Col<EvalIterRow>[] {
  const cols: Col<EvalIterRow>[] = [
    {
      key: 'iter',
      label: 'iter',
      cell: (r) => (r.kind === 'god' ? <b>God</b> : <b>it{r.iter}</b>),
      sortValue: (r) => (r.kind === 'god' ? Number.POSITIVE_INFINITY : r.iter),
    },
    {
      key: 'n',
      label: 'n',
      align: 'num',
      cell: (r) => (r.n > 0 ? String(r.n) : '—'),
      sortValue: (r) => r.n,
    },
  ]
  for (const rung of ladder) {
    for (const metric of metricKeys) {
      const key = `${rung.rung}.${metric}`
      cols.push({
        key,
        label: `${rung.rung} ${EVAL_METRIC_LABELS[metric]}`,
        align: 'num',
        thTitle: `${rungLabel(rung)} · 指标：${EVAL_METRIC_LABELS[metric]}`,
        sortValue: (r) => r.cells[key] ?? Number.NEGATIVE_INFINITY,
        cell: (r) =>
          r.cells[key] === undefined ? (
            <span className="tc-muted">·</span>
          ) : (
            fmtMetric(metric, r.cells[key]!)
          ),
      })
    }
  }
  return cols
}

/** 指标显隐 checkbox（勾掉 = 该指标全部 rung 列消失）。 */
export function MetricToggles({
  metricKeys,
  onToggle,
}: {
  metricKeys: EvalMetricKey[]
  onToggle: (k: EvalMetricKey) => void
}) {
  return (
    <div className="tc-metric-toggles" role="group" aria-label="指标显隐">
      {EVAL_METRIC_KEYS.map((k) => (
        <label key={k} className="tc-small">
          <input type="checkbox" checked={metricKeys.includes(k)} onChange={() => onToggle(k)} />
          <span>{EVAL_METRIC_LABELS[k]}</span>
        </label>
      ))}
    </div>
  )
}

/** 矩阵表（不含空态；空态由调用方决定文案与引导）。 */
export function EvalMatrix({
  ladder,
  rows,
  metricKeys,
  onToggleMetric,
  storagePrefix,
  ariaLabel,
  emptyText,
  toolbarExtra,
}: {
  ladder: EvalLadderRow[]
  rows: EvalIterRow[]
  metricKeys: EvalMetricKey[]
  onToggleMetric: (k: EvalMetricKey) => void
  storagePrefix: string
  ariaLabel: string
  emptyText: string
  toolbarExtra?: ComponentChildren
}) {
  return (
    <DataTable<EvalIterRow>
      columns={matrixColumns(ladder, metricKeys)}
      rows={rows}
      rowKey={(r) => `${r.course}:${r.kind}:${r.iter}`}
      searchKeys={['iter']}
      storagePrefix={storagePrefix}
      emptyText={emptyText}
      ariaLabel={ariaLabel}
      toolbarLeft={
        <>
          <MetricToggles metricKeys={metricKeys} onToggle={onToggleMetric} />
          {toolbarExtra}
        </>
      }
    />
  )
}
