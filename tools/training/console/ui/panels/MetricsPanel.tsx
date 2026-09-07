/** MetricsPanel.tsx — 训练指标卡（DS-U1：source=state，3s 实时）：
 *  迷你趋势卡（点选联动列高亮，GLM-U5 切换式）+ 13 列表格 + eval 子行 + 行过滤
 *  （全部/rollout/eval，localStorage 记忆，旧 pool.iterFilter 键迁移自 view.ts）。 */

import { useState } from 'preact/hooks'
import {
  filterGroups,
  fmtPct,
  fmtValue,
  iterGroups,
  lastFinite,
  metricSeries,
  TC_METRICS_FILTER,
  type EvalSummary,
  type IterFilter,
  type IterRow,
} from '../../../ui/view'
import type { PanelProps } from '../../../ui/view'
import { Badge } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { Sparkline } from '../../../ui/components/Sparkline'

const FILTER_KEY = TC_METRICS_FILTER

function winTone(v: number): 'g' | 'y' | 'r' {
  if (v >= 0.3) return 'g'
  if (v >= 0.1) return 'y'
  return 'r'
}

function klTone(v: number): 'g' | 'y' | 'r' {
  if (v > 0.05) return 'r'
  if (v > 0.02) return 'y'
  return 'g'
}

function retTone(v: number): 'g' | 'y' | 'r' {
  if (v > -0.5) return 'g'
  if (v > -1.0) return 'y'
  return 'r'
}

/** 显示行 = 主行 | eval 子行 的联合（eval only 时只保留子行，与旧 /pool 语义一致）。 */
type MetricRow =
  | { kind: 'main'; iter: number; time: string; main: IterRow }
  | { kind: 'eval'; iter: number; time: string; eval: EvalSummary }

function buildRows(rows: IterRow[], mode: IterFilter): MetricRow[] {
  const out: MetricRow[] = []
  for (const g of filterGroups(iterGroups(rows), mode)) {
    if (mode !== 'eval') out.push({ kind: 'main', iter: g.iter, time: g.main.time, main: g.main })
    if (mode !== 'rollout' && g.eval)
      out.push({ kind: 'eval', iter: g.iter, time: g.eval.time, eval: g.eval })
  }
  return out
}

const metricCols: Col<MetricRow>[] = [
  {
    key: 'iter',
    label: 'iter',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        <b>
          {r.main.iter}
          {r.main.halted ? <span className="tc-pill tc-pill--note">halted</span> : null}
        </b>
      ) : (
        <span className="tc-muted" style={{ whiteSpace: 'nowrap' }}>
          eval it{r.iter}
          {r.eval.dropped > 0 ? (
            <span
              className="tc-pill tc-pill--note"
              title="评估窗口内未收官、被下轮权重分发清场的评估局数"
            >
              缺{r.eval.dropped}
            </span>
          ) : null}
        </span>
      ),
  },
  {
    key: 'time',
    label: '时间',
    cell: (r) => <span className="tc-muted">{r.kind === 'main' ? r.main.time : r.eval.time}</span>,
  },
  {
    key: 'winRate',
    label: '胜率',
    cell: (r) =>
      r.kind === 'main' ? (
        <Badge tone={winTone(r.main.winRate)}>{fmtPct(r.main.winRate)}</Badge>
      ) : r.eval.winRate !== null ? (
        <>
          <Badge tone={winTone(r.eval.winRate)}>{fmtPct(r.eval.winRate)}</Badge>{' '}
          <span className="tc-muted">
            {r.eval.wins}/{r.eval.games}
          </span>
        </>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'avgTicks',
    label: '存活',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        r.main.actuals ? (
          r.main.actuals.avgTicks
        ) : (
          <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
            {r.main.avgTicks}≈
          </span>
        )
      ) : r.eval.avgTicks !== null ? (
        r.eval.avgTicks
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'kills',
    label: '击杀',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        r.main.actuals ? (
          <>
            {r.main.actuals.totalKills}
            <span className="tc-muted"> /{r.main.actuals.games}局</span>
          </>
        ) : (
          <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
            {r.main.kills.toFixed(1)}≈
          </span>
        )
      ) : r.eval.totalKills !== null ? (
        <>
          {r.eval.totalKills}
          <span className="tc-muted"> /{r.eval.games}局</span>
        </>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'loot',
    label: '道具',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        r.main.actuals ? (
          <>
            {r.main.actuals.totalPU}
            <span className="tc-muted"> /{r.main.actuals.games}局</span>
          </>
        ) : (
          <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
            {(r.main.loot * 100).toFixed(0)}%≈
          </span>
        )
      ) : r.eval.totalPU !== null ? (
        <>
          {r.eval.totalPU}
          <span className="tc-muted"> /{r.eval.games}局</span>
        </>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'scoreMean',
    label: '得分',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        <>
          {r.main.scoreMean.toFixed(4)}
          <span className="tc-muted">±{r.main.scoreStd.toFixed(4)}</span>
        </>
      ) : r.eval.scoreMean !== null ? (
        <>
          {r.eval.scoreMean.toFixed(4)}
          <span className="tc-muted">±{(r.eval.scoreStd ?? 0).toFixed(4)}</span>
        </>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'rolloutSec',
    label: 'rollout',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? `${r.main.rolloutSec.toFixed(0)}s` : <span className="tc-muted">-</span>,
  },
  {
    key: 'ppoSec',
    label: 'PPO/eval',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? (
        `${r.main.ppoSec.toFixed(0)}s`
      ) : (
        <span title="eval 窗口用时" className="tc-num">
          {r.eval.sec.toFixed(0)}s
        </span>
      ),
  },
  {
    key: 'kl',
    label: 'KL',
    cell: (r) =>
      r.kind === 'main' ? (
        <Badge tone={klTone(r.main.kl)}>{r.main.kl.toFixed(4)}</Badge>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'entropy',
    label: 'entropy',
    align: 'num',
    cell: (r) =>
      r.kind === 'main' ? r.main.entropy.toFixed(3) : <span className="tc-muted">-</span>,
  },
  {
    key: 'meanRet',
    label: 'mean_ret',
    cell: (r) =>
      r.kind === 'main' ? (
        <Badge tone={retTone(r.main.meanRet)}>{r.main.meanRet.toFixed(3)}</Badge>
      ) : (
        <span className="tc-muted">-</span>
      ),
  },
  {
    key: 'lr',
    label: 'lr',
    align: 'num',
    cell: (r) => (r.kind === 'main' ? r.main.lr.toFixed(6) : <span className="tc-muted">-</span>),
  },
]

export function MetricsPanel({ stateView }: PanelProps) {
  const [filter, setFilter] = useState<IterFilter>(() => {
    try {
      if (typeof localStorage !== 'undefined') {
        const v = localStorage.getItem(FILTER_KEY)
        if (v === 'all' || v === 'rollout' || v === 'eval') return v
      }
    } catch {
      /* ignore */
    }
    return 'all'
  })
  const [highlightCol, setHighlightCol] = useState<string | null>(null)

  if (!stateView?.metrics.available) {
    const err = stateView?.metrics.error
    return <p className="tc-muted">该课程暂无 training_log.jsonl 数据{err ? `（${err}）` : ''}。</p>
  }

  const rows = stateView.metrics.iters
  const series = metricSeries(rows)
  const display = buildRows(rows, filter)
  const setFilterPersist = (f: IterFilter): void => {
    setFilter(f)
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(FILTER_KEY, f)
    } catch {
      /* ignore */
    }
  }

  return (
    <div>
      <div className="tc-spark-strip">
        {series.map((m) => {
          const last = lastFinite(m.vals)
          const on = highlightCol === m.key
          return (
            <button
              key={m.key}
              type="button"
              className={`tc-spark-cell${on ? ' tc-spark-cell--on' : ''}`}
              aria-pressed={on}
              aria-label={`高亮 ${m.label} 列`}
              title="点选联动表格列高亮（再点取消）"
              onClick={() => setHighlightCol(on ? null : m.key)}
            >
              <div className="tc-spark-head">
                <span>{m.label}</span>
                <b>{fmtValue(last)}</b>
              </div>
              <Sparkline values={m.vals} />
            </button>
          )
        })}
      </div>
      <div className="tc-toolbar">
        <SegmentedControl<IterFilter>
          value={filter}
          ariaLabel="行过滤"
          options={[
            { value: 'all', label: '全部' },
            { value: 'rollout', label: 'rollout only' },
            { value: 'eval', label: 'eval only' },
          ]}
          onChange={setFilterPersist}
        />
        <span className="tc-caption" style={{ border: 'none', padding: 0 }}>
          存活/击杀/道具 = <b>实际值</b>（it&#123;N&#125;/**/manifest.json 逐局聚合，stage+seed
          去重后留底缓存）；带 ≈ 为估算。 eval 行 = <b>干净评估</b>（greedy 固定语料），iter=N
          评估的是第 N 轮 PPO 更新前的权重；缺N = 窗口内未收官被清场。
        </span>
      </div>
      <DataTable<MetricRow>
        rows={display}
        rowKey={(r) => (r.kind === 'main' ? `m${r.iter}` : `e${r.iter}`)}
        searchKeys={['iter', 'time']}
        columns={metricCols}
        initialSortKey="iter"
        initialSortDir="desc"
        highlightCol={highlightCol}
        emptyText="尚无完整迭代记录"
        ariaLabel="训练指标"
      />
    </div>
  )
}
