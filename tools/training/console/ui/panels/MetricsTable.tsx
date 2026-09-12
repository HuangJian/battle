/** MetricsTable.tsx — 抽屉「指标」tab：完整表格 + eval 子行 + 行过滤。
 *  iter 列：无 eval 的主行可点 evalA（课程设计评估 → eval_log，与 Hero 最新 6 轮同路径）。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import {
  filterGroups,
  fmtPct,
  iterGroups,
  klTone,
  PAIRED_COL_TITLES,
  pairedBaselineOf,
  pairedTone,
  pairedVerdictText,
  retTone,
  TC_METRICS_FILTER,
  winTone,
  type EvalCkptFile,
  type EvalSummary,
  type IterFilter,
  type IterRow,
  type PairedCompare,
} from '../../../ui/view'
import type { ConsoleStateView } from '../../../ui/view'
import { Badge } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { ckptForIter, loadCourseCkpts, startEvalA } from '../lib/eval-a'

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

/** 击杀/道具每局平均展示。 */
function fmtPerGame(total: number, games: number, digits = 1): string {
  if (games <= 0) return String(total)
  return (total / games).toFixed(digits)
}

type EvalACols = {
  course: string
  ckpts: EvalCkptFile[]
  readOnly: boolean
  busyIters: Set<number>
  onEvalA: (iter: number) => void
}

/** eval 行配对单元格：null → 基线轮标“基线”，否则“—”（与基线无交集等）。 */
function pairedCell(
  p: PairedCompare | null | undefined,
  isBaseline: boolean,
  pick: (c: PairedCompare) => ComponentChildren,
): ComponentChildren {
  if (!p) {
    return isBaseline ? (
      <span className="tc-muted" title="配对基线本轮：vs自己不判">
        基线
      </span>
    ) : (
      <span className="tc-muted">-</span>
    )
  }
  return pick(p)
}

/** eval-only 模式的替换列：b01/b10/p/delta（PPO 诊断列在 eval 行恒为—，换成裁判）。 */
function pairedCols(baselineIter: number | null): Col<MetricRow>[] {
  const isBase = (r: MetricRow): boolean =>
    r.kind === 'eval' && baselineIter !== null && r.iter === baselineIter
  const get = (r: MetricRow): PairedCompare | null | undefined =>
    r.kind === 'eval' ? r.eval.pairedVsFirst : undefined
  return [
    {
      key: 'b01',
      label: 'b01',
      align: 'num',
      thTitle: PAIRED_COL_TITLES.b01,
      sortValue: (r) => get(r)?.b01 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b01}>{c.b01}</span>),
    },
    {
      key: 'b10',
      label: 'b10',
      align: 'num',
      thTitle: PAIRED_COL_TITLES.b10,
      sortValue: (r) => get(r)?.b10 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b10}>{c.b10}</span>),
    },
    {
      key: 'pairedP',
      label: 'p',
      align: 'num',
      thTitle: PAIRED_COL_TITLES.p,
      sortValue: (r) => get(r)?.p ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => (
          <Badge
            tone={pairedTone(c.verdict)}
            title={`${PAIRED_COL_TITLES.p}；${pairedVerdictText(c.verdict)}`}
          >
            {c.p.toFixed(2)}
          </Badge>
        )),
    },
    {
      key: 'delta',
      label: 'delta',
      align: 'num',
      thTitle: PAIRED_COL_TITLES.delta,
      sortValue: (r) => get(r)?.deltaPp ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => (
          <span title={`${PAIRED_COL_TITLES.delta}；${pairedVerdictText(c.verdict)}`}>
            {(c.deltaPp > 0 ? '+' : '') + c.deltaPp.toFixed(1)}pp
          </span>
        )),
    },
  ]
}

/** 列工厂：iter 列在无 eval 主行旁挂 evalA（课程 A 层，非 EvalBoard B）。 */
function buildMetricCols(
  ea: EvalACols,
  mode: IterFilter,
  baselineIter: number | null,
): Col<MetricRow>[] {
  return [
    {
      key: 'iter',
      label: 'iter',
      align: 'num',
      cell: (r) =>
        r.kind === 'main' ? (
          <span style={{ whiteSpace: 'nowrap' }}>
            <b>
              {r.main.iter}
              {r.main.halted ? <span className="tc-pill tc-pill--note">halted</span> : null}
            </b>
            {!ea.readOnly && ea.course && !r.main.evalData ? (
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                style={{ marginLeft: 6 }}
                disabled={ea.busyIters.has(r.main.iter) || !ckptForIter(ea.ckpts, r.main.iter)}
                title={
                  ckptForIter(ea.ckpts, r.main.iter)
                    ? `为 it${r.main.iter} 启动课程设计评估（evalA）`
                    : `未发现 it${r.main.iter} 权重归档`
                }
                onClick={() => ea.onEvalA(r.main.iter)}
              >
                {ea.busyIters.has(r.main.iter) ? '…' : 'evalA'}
              </button>
            ) : null}
          </span>
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
      cell: (r) => (
        <span className="tc-muted">{r.kind === 'main' ? r.main.time : r.eval.time}</span>
      ),
    },
    {
      key: 'winRate',
      label: '胜率',
      cell: (r) =>
        r.kind === 'main' ? (
          <Badge tone={winTone(r.main.winRate)}>{fmtPct(r.main.winRate)}</Badge>
        ) : r.eval.winRate !== null ? (
          <>
            <Badge
              tone={winTone(r.eval.winRate)}
              title={`干净评估（greedy 固定语料）· 评估权重 = 第 ${r.iter} 轮 PPO 更新前 · ${r.eval.games} 局 ${r.eval.wins} 胜 · 全歼 ${r.eval.clears} · outcomes: ${
                Object.entries(r.eval.outcomes)
                  .map(([k, v]) => `${k}×${v}`)
                  .join(' ') || '-'
              } · 用时 ${r.eval.sec}s · wver ${r.eval.wver.slice(0, 12)}…`}
            >
              {fmtPct(r.eval.winRate)}
            </Badge>{' '}
            <span className="tc-muted">
              {r.eval.wins}/{r.eval.games}
            </span>
          </>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      key: 'avgWinTicks',
      label: '胜局耗时',
      align: 'num',
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals?.avgWinTicks != null ? (
            r.main.actuals.avgWinTicks
          ) : r.main.actuals ? (
            <span title="无胜局耗时字段，回退全样本均值">{r.main.actuals.avgTicks}≈</span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {r.main.avgTicks}≈
            </span>
          )
        ) : r.eval.avgWinTicks != null ? (
          r.eval.avgWinTicks
        ) : r.eval.avgTicks !== null ? (
          <span title="无胜局耗时字段，回退全样本均值">{r.eval.avgTicks}≈</span>
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
            <span title="每局平均击杀">
              {fmtPerGame(r.main.actuals.totalKills, r.main.actuals.games)}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {r.main.kills.toFixed(1)}≈
            </span>
          )
        ) : r.eval.totalKills !== null ? (
          <span title="每局平均击杀">{fmtPerGame(r.eval.totalKills, r.eval.games)}</span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      key: 'dmgPerKill',
      label: '承伤/杀',
      align: 'num',
      cell: (r) => {
        const v = r.kind === 'main' ? r.main.actuals?.dmgPerKill : r.eval.dmgPerKill
        if (v == null) return <span className="tc-muted">-</span>
        return <span title="总承伤 / 总击杀（越小越会周旋）">{v.toFixed(1)}</span>
      },
    },
    {
      key: 'residualHp',
      label: '残血',
      align: 'num',
      cell: (r) => {
        const hp = r.kind === 'main' ? r.main.actuals?.avgResidualHp : r.eval.avgResidualHp
        if (hp == null) return <span className="tc-muted">-</span>
        return <span title="胜局平均剩余 hp；剩余多命时每命加满额 hp">{hp}</span>
      },
    },
    {
      key: 'loot',
      label: '道具',
      align: 'num',
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals ? (
            <span title="每局平均道具">
              {fmtPerGame(r.main.actuals.totalPU, r.main.actuals.games, 2)}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {(r.main.loot * 100).toFixed(0)}%≈
            </span>
          )
        ) : r.eval.totalPU !== null ? (
          <span title="每局平均道具">{fmtPerGame(r.eval.totalPU, r.eval.games, 2)}</span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      key: 'rolloutSec',
      label: 'rollout',
      align: 'num',
      cell: (r) =>
        r.kind === 'main' ? (
          `${r.main.rolloutSec.toFixed(0)}s`
        ) : (
          <span className="tc-muted">-</span>
        ),
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
      key: 'scoreMean',
      label: '得分',
      align: 'num',
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.scoreMean.toFixed(4)
        ) : r.eval.scoreMean !== null ? (
          r.eval.scoreMean.toFixed(4)
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    // eval-only 下 PPO 诊断四列恒为—，换成配对裁判列；其余模式保持原样。
    ...(mode === 'eval' ? pairedCols(baselineIter) : klEntropyCols()),
  ]
}

/** PPO 诊断列（rollout 行专属；eval-only 模式下整列是—，由配对列替换）。 */
function klEntropyCols(): Col<MetricRow>[] {
  return [
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
}

export function MetricsTable({
  stateView,
  onRefresh,
  readOnly = false,
}: {
  stateView: ConsoleStateView | null
  /** evalA 落盘后拉 /api/state 回填。 */
  onRefresh?: () => void
  readOnly?: boolean
}) {
  const course = stateView?.course ?? ''
  const [filter, setFilter] = useState<IterFilter>(() => {
    try {
      if (typeof localStorage !== 'undefined') {
        const v = localStorage.getItem(TC_METRICS_FILTER)
        if (v === 'all' || v === 'rollout' || v === 'eval') return v
      }
    } catch {
      /* ignore */
    }
    return 'all'
  })
  const [ckpts, setCkpts] = useState<EvalCkptFile[]>([])
  const [busyIters, setBusyIters] = useState<Set<number>>(() => new Set())
  const [flash, setFlash] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    if (!course || readOnly) {
      setCkpts([])
      return
    }
    let ok = true
    void loadCourseCkpts(course).then((files) => {
      if (ok) setCkpts(files)
    })
    return () => {
      ok = false
    }
  }, [course, readOnly])

  const iters = stateView?.metrics.iters ?? []
  useEffect(() => {
    setBusyIters((s) => {
      if (s.size === 0) return s
      const next = new Set(s)
      for (const r of iters) {
        if (r.evalData && next.has(r.iter)) next.delete(r.iter)
      }
      return next.size === s.size ? s : next
    })
  }, [iters])

  const onEvalA = (iter: number): void => {
    if (readOnly || !course) return
    const ckpt = ckptForIter(ckpts, iter)
    if (!ckpt) {
      setFlash(`未发现 it${iter} 权重归档`)
      return
    }
    setBusyIters((s) => new Set(s).add(iter))
    setFlash(`it${iter} evalA 启动中…`)
    void (async () => {
      try {
        const { ok, message } = await startEvalA(course, iter, ckpt)
        if (!alive.current) return
        setFlash(message)
        if (!ok) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
          return
        }
        for (let i = 0; i < 30 && alive.current; i++) {
          await new Promise((res) => setTimeout(res, 20_000))
          if (!alive.current) return
          onRefresh?.()
        }
        if (alive.current) {
          setBusyIters((s) => {
            const n = new Set(s)
            n.delete(iter)
            return n
          })
        }
      } catch (e) {
        if (!alive.current) return
        setFlash(String(e))
        setBusyIters((s) => {
          const n = new Set(s)
          n.delete(iter)
          return n
        })
      }
    })()
  }

  const metricCols = buildMetricCols(
    {
      course,
      ckpts,
      readOnly,
      busyIters,
      onEvalA,
    },
    filter,
    // 配对基线轮：任一非空 pairedVsFirst 的 baseIter（全空 → null，配对列标—）。
    pairedBaselineOf((stateView?.metrics.iters ?? []).map((r) => r.evalData?.pairedVsFirst)),
  )

  if (!stateView?.metrics.available) {
    return (
      <p className="tc-muted">
        该课程暂无 training_log.jsonl 数据
        {stateView?.metrics.error ? `（${stateView.metrics.error}）` : ''}。
      </p>
    )
  }
  const rows = stateView.metrics.iters
  const display = buildRows(rows, filter)
  const setFilterPersist = (f: IterFilter): void => {
    setFilter(f)
    try {
      if (typeof localStorage !== 'undefined') localStorage.setItem(TC_METRICS_FILTER, f)
    } catch {
      /* ignore */
    }
  }
  return (
    <div className="tc-drawer__panel">
      {flash ? (
        <div className="tc-muted tc-small" style={{ marginBottom: 6 }} role="status">
          {flash}
        </div>
      ) : null}
      <DataTable<MetricRow>
        rows={display}
        rowKey={(r) => (r.kind === 'main' ? `m${r.iter}` : `e${r.iter}`)}
        searchKeys={['iter', 'time']}
        columns={metricCols}
        initialSortKey="iter"
        initialSortDir="desc"
        storagePrefix="tc.metrics"
        emptyText="尚无完整迭代记录"
        ariaLabel="训练指标"
        toolbarLeft={
          <>
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
            <span className="tc-muted tc-small">{rows.length} 轮</span>
          </>
        }
      />
      <p className="tc-caption" style={{ border: 'none', padding: '8px 0 0' }}>
        胜局耗时/击杀/承伤·杀/道具 = <b>实际值</b>（it&#123;N&#125;/**/manifest.json 逐局聚合，
        stage+seed 去重后留底缓存）；击杀/道具为每局平均，承伤·杀 =
        总承伤/总击杀，残血为胜局平均剩余 hp （剩余多命每命加满额）；带 ≈ 为估算。 eval 行 ={' '}
        <b>干净评估</b>（greedy 固定语料），iter=N 评估的是第 N 轮 PPO 更新前的权重；缺N =
        窗口内未收官被清场。
      </p>
    </div>
  )
}
