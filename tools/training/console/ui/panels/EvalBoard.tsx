/** EvalBoard.tsx — 抽屉「评估」tab：EvalBench 看板（plan/rl-eval-system.md §8）。
 *
 * 区块：① 异常清单（首屏）② 阶梯表（无 best 列，只报 latest + 窗均值）
 * ③ 批次台账 ④ 翻转矩阵 ⑤ 触发区（选课程 + ckpt + 确认 → 入队）。
 * phase 仅染色，不进派发关键路径。写权限沿用 LAN 只读：POST 仅回环可执行。
 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import type { EvalAlert, EvalBoardView, EvalLadderRow } from '../../../ui/view'
import { Badge, Pill } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { usePolling } from '../lib/usePolling'
import { fetchEvalBoard, postAction } from '../lib/api-client'

export interface EvalBoardProps {
  enabled: boolean
  course?: string
  readOnly?: boolean
}

const pct = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

function alertBadge(a: EvalAlert) {
  const tone =
    a.severity === 'red'
      ? 'r'
      : a.severity === 'yellow'
        ? 'y'
        : a.severity === 'star'
          ? 'a'
          : 'gray'
  return (
    <Badge tone={tone as 'r' | 'y' | 'a' | 'gray'} title={a.id}>
      {a.id}
    </Badge>
  )
}

function gateCell(r: EvalLadderRow) {
  if (r.god.provisional)
    return (
      <Pill tone="gray" title="老师自身过不去，穿过不判">
        provisional 穿过
      </Pill>
    )
  if (!r.gate)
    return (
      <span className="tc-muted">
        {r.partial ? '数据不足（partial）' : r.god.winRate === null ? '待 God 基线' : '—'}
      </span>
    )
  const g = r.gate
  return (
    <span title={`主 ${g.main} · 代价 ${g.cost} · 风格 ${g.style} · 可信 ${g.credible}`}>
      {g.pass ? <Pill tone="g">过门</Pill> : <Pill tone="y">未过</Pill>}{' '}
      <span className="tc-muted tc-small">
        主{g.main ? '✓' : '✗'} 价{g.cost ? '✓' : '✗'} 风{g.style ? '✓' : '✗'} 信
        {g.credible ? '✓' : '✗'}
      </span>
    </span>
  )
}

const ladderColumns: Col<EvalLadderRow>[] = [
  { key: 'rung', label: 'rung', cell: (r) => <b>{r.rung}</b> },
  {
    key: 'god',
    label: 'God',
    sortValue: (r) => r.god.winRate ?? -1,
    cell: (r) =>
      r.god.winRate === null ? <span className="tc-muted">TBD</span> : pct(r.god.winRate),
  },
  {
    key: 'n',
    label: '累积局数',
    align: 'num',
    sortValue: (r) => r.n,
    cell: (r) => <span>{r.n}</span>,
  },
  {
    // §2.1：A 层只做趋势，不判能力 —— 列名与 tooltip 都标注清楚，避免误读为判定读数。
    key: 'aTrend',
    label: 'A 趋势',
    align: 'num',
    sortValue: (r) => r.aTrend?.winRate ?? -1,
    cell: (r) =>
      r.aTrend === null ? (
        <span className="tc-muted">—</span>
      ) : (
        <span title={`A 层自动入账：it${r.aTrend.iter} / ${r.aTrend.n} 局（不判能力）`}>
          {pct(r.aTrend.winRate)}
          <span className="tc-muted"> it{r.aTrend.iter}</span>
        </span>
      ),
  },
  {
    key: 'latest',
    label: 'latest',
    sortValue: (r) => r.latestWin ?? -1,
    cell: (r) => pct(r.latestWin),
  },
  {
    key: 'window',
    label: '窗均值',
    sortValue: (r) => r.windowWin ?? -1,
    cell: (r) => pct(r.windowWin),
  },
  {
    key: 'delta',
    label: 'Δ',
    sortValue: (r) => r.deltaVsGod ?? -999,
    cell: (r) =>
      r.deltaVsGod === null
        ? '—'
        : `${r.deltaVsGod >= 0 ? '+' : ''}${(r.deltaVsGod * 100).toFixed(1)}pp`,
  },
  { key: 'gate', label: '过门', cell: gateCell },
]

export function EvalBoard({ enabled, course = '', readOnly = false }: EvalBoardProps) {
  const [view, setView] = useState<EvalBoardView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ckpt, setCkpt] = useState('')
  const [rung, setRung] = useState('c4l1')
  const [flash, setFlash] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setView(await fetchEvalBoard(false, course))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [course])

  useEffect(() => {
    if (enabled) void refresh()
  }, [enabled, refresh])
  usePolling({ enabled, intervalSec: 300, fetch: refresh })

  const trigger = useCallback(async () => {
    if (!ckpt.trim()) {
      setFlash('先填 ckpt（权重文件路径，如 tmp/<course>/weights.json）')
      return
    }
    const r = await postAction('evalProbeRun', { course, ckpt: ckpt.trim(), rung_from: rung })
    setFlash(r.message)
    void refresh()
  }, [ckpt, course, rung, refresh])

  if (error) return <div className="tc-banner tc-banner--err">{error}</div>
  if (!view) return <div className="tc-muted">加载评估看板…</div>

  return (
    <div className="tc-evalboard">
      {view.abWarn ? (
        <div className="tc-banner tc-banner--err" role="alert">
          {view.abWarn}
        </div>
      ) : null}
      <h4>
        异常清单{' '}
        <span className="tc-muted tc-small">
          {view.rows} 局入账 ·{' '}
          {view.spaceCalibrated ? 'Δ_space 已标定' : 'Δ_space 未出数（S11 只提示）'}
        </span>
      </h4>
      {view.alerts.length === 0 ? (
        <p className="tc-muted">无异常（或数据不足未武装）。</p>
      ) : (
        <ul className="tc-eval-alerts">
          {view.alerts.map((a, i) => (
            <li key={i}>
              {alertBadge(a)} <span>{a.message}</span>
            </li>
          ))}
        </ul>
      )}
      <h4>阶梯表（latest + 窗均值，无 best 列）</h4>
      <DataTable<EvalLadderRow>
        columns={ladderColumns}
        rows={view.ladder}
        rowKey={(r) => r.rung}
        searchKeys={['rung']}
        storagePrefix="tc.eval.ladder"
        emptyText="阶梯无数据"
        ariaLabel="评估阶梯"
      />
      <h4>批次台账</h4>
      <DataTable
        columns={[
          {
            key: 'batch_id',
            label: 'batch',
            cell: (r: (typeof view.batches)[number]) => (
              <span className="tc-small">{r.batch_id}</span>
            ),
          },
          { key: 'rung_from', label: '范围', cell: (r) => r.rung_from },
          {
            key: 'status',
            label: '状态',
            cell: (r) => (
              <Pill
                tone={
                  r.status === 'done'
                    ? 'g'
                    : r.status === 'running'
                      ? 'a'
                      : r.status === 'aborted'
                        ? 'r'
                        : 'gray'
                }
              >
                {r.status} {r.units.done.length}/{r.units.of}
              </Pill>
            ),
          },
          { key: 'iter', label: 'iter', align: 'num', cell: (r) => <span>{r.iter}</span> },
          {
            key: 'trigger',
            label: '触发',
            cell: (r) => <span className="tc-small">{r.trigger}</span>,
          },
        ]}
        rows={view.batches}
        rowKey={(r) => r.batch_id}
        searchKeys={['batch_id']}
        storagePrefix="tc.eval.batches"
        emptyText="暂无批次——用下方触发区入队"
        ariaLabel="批次台账"
      />
      <h4>翻转矩阵（行=对比对，格=Δ/翻转率）</h4>
      {view.flips.length === 0 ? (
        <p className="tc-muted">暂无相邻批次对比。</p>
      ) : (
        <DataTable
          columns={[
            { key: 'rung', label: 'rung', cell: (r: (typeof view.flips)[number]) => r.rung },
            {
              key: 'delta',
              label: 'Δ',
              sortValue: (r) => r.delta,
              cell: (r) =>
                `${r.delta >= 0 ? '+' : ''}${(r.delta * 100).toFixed(1)}pp${r.paired ? '（配对）' : ''}`,
            },
            {
              key: 'rate',
              label: '翻转率',
              sortValue: (r) => r.rate ?? -1,
              cell: (r) => (r.rate === null ? '—' : `${(r.rate * 100).toFixed(0)}% (${r.flips}局)`),
            },
          ]}
          rows={view.flips}
          rowKey={(r) => `${r.rung}${r.from}${r.to}`}
          searchKeys={['rung']}
          storagePrefix="tc.eval.flips"
          emptyText="暂无对比"
          ariaLabel="翻转矩阵"
        />
      )}
      <h4>触发区（人工触发 → 分布式集群派发）</h4>
      <div className="tc-row tc-small">
        <label>
          rung{' '}
          <select value={rung} onChange={(e) => setRung((e.target as HTMLSelectElement).value)}>
            {view.ladder.map((l) => (
              <option key={l.rung} value={l.rung}>
                {l.rung}
              </option>
            ))}
          </select>
        </label>{' '}
        <label>
          ckpt{' '}
          <input
            type="text"
            size={36}
            placeholder="tmp/<course>/weights.json"
            value={ckpt}
            onInput={(e) => setCkpt((e.target as HTMLInputElement).value)}
          />
        </label>{' '}
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          onClick={() => void trigger()}
          disabled={readOnly}
        >
          入队评估批
        </button>
        {readOnly ? <span className="tc-muted">（只读视图：请在本机 localhost 触发）</span> : null}
      </div>
      {flash ? <p className="tc-muted">{flash}</p> : null}
    </div>
  )
}
