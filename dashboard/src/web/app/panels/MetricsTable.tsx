/** MetricsTable.tsx — 「指标」页：完整表格 + eval 子行 + 行过滤。
 *  iter 列：无 eval 的主行可点 evalA（课程设计评估 → eval_log，与 Hero 最新 6 轮同路径）。
 *
 *  列模型（表头文字 / hover 口径 / 数字对齐）来自 `view/metric-columns.ts` 的**唯一一份**——
 *  本文件只提供每列的**格子渲染 + 排序键**（`cell`/`sortValue`），不再自己写 label/align/thTitle。
 *  背景与验收：`DECISIONS.md §2026-09-20-metric-table-single-home`。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import {
  filterGroups,
  fmtLootPickDrop,
  fmtOverfitGap,
  fmtPct,
  fmtPhaseSecs,
  iterGroups,
  klTone,
  METRIC_COLS,
  overfitCellTitle,
  overfitTone,
  PAIRED_COL_TITLES,
  pairedBaselineOf,
  pairedTone,
  pairedVerdictText,
  phaseSecs,
  phaseSecsTitle,
  retTone,
  TC_METRICS_FILTER,
  winTone,
  type EvalCkptFile,
  type EvalSummary,
  type IterFilter,
  type IterRow,
  type MetricColKey,
  type PairedCompare,
} from '../../view'
import type { ConsoleStateView } from '../../view'
import { Badge } from '../../components/Pill'
import { DataTable, type Col } from '../../components/DataTable'
import { InlineNotice } from '../../components/InlineNotice'
import { SegmentedControl } from '../../components/SegmentedControl'
import { ckptForIter, loadCourseCkpts, startEvalA } from '../lib/eval-a'

/** 列模型 → `DataTable` 的列壳（键/表头/对齐/hover 口径）。
 *
 *  为什么用展开而不是逐个赋值：`Col` 的这四个字段本来就该由模型独占，展开能保证「模型加一个字段、
 *  这里自动跟上」，不必两处同步；`key` 也从模型取，顺手保证列 id 与模型键一致。 */
function colDef(key: MetricColKey): Pick<Col<MetricRow>, 'key' | 'label' | 'align' | 'thTitle'> {
  const m = METRIC_COLS[key]
  return { key: m.key, label: m.label, align: m.num ? 'num' : undefined, thTitle: m.title }
}

/** 显示行 = 主行 | eval 子行 的联合（eval only 时只保留子行，与旧 /pool 语义一致）。 */
type MetricRow =
  | { kind: 'main'; iter: number; time: string; main: IterRow }
  | { kind: 'eval'; iter: number; time: string; eval: EvalSummary }

function buildRows(rows: IterRow[], mode: IterFilter): MetricRow[] {
  const out: MetricRow[] = []
  for (const g of filterGroups(iterGroups(rows), mode)) {
    // it0 = bc 权重基线（训练前）：只有干净评估、没有 rollout 采样，不渲染主行
    // （合成行在 console/iters.ts；rollout 派生字段是 NaN 缺口，不是 0）。
    if (mode !== 'eval' && g.iter > 0)
      out.push({ kind: 'main', iter: g.iter, time: g.main.time, main: g.main })
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
      ...colDef('b01'),
      sortValue: (r) => get(r)?.b01 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b01}>{c.b01}</span>),
    },
    {
      ...colDef('b10'),
      sortValue: (r) => get(r)?.b10 ?? null,
      cell: (r) =>
        pairedCell(get(r), isBase(r), (c) => <span title={PAIRED_COL_TITLES.b10}>{c.b10}</span>),
    },
    {
      ...colDef('pairedP'),
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
      ...colDef('delta'),
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

/** 共享列：iter（eval 行旁无 evalA——eval-only 只有 eval 子行）。 */
function iterCol(ea: EvalACols): Col<MetricRow> {
  return {
    ...colDef('iter'),
    cell: (r) =>
      r.kind === 'main' ? (
        <span className="tc-nowrap">
          <b>
            {r.main.iter}
            {r.main.halted ? <span className="tc-pill tc-pill--note">halted</span> : null}
          </b>
          {!ea.readOnly && ea.course && !r.main.evalData ? (
            <button
              type="button"
              className="tc-btn tc-btn--sm tc-ml-2"
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
        <span className="tc-muted tc-nowrap">
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
  }
}

/** 共享列：技能读数（主行走 rollout 实际值；eval 行走干净评估实际值）。 */
function skillCols(): Col<MetricRow>[] {
  return [
    {
      ...colDef('avgWinTicks'),
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
      ...colDef('kills'),
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals?.killRate != null ? (
            <span title="歼灭率 = Σ击杀 / Σ关卡敌数">{fmtPct(r.main.actuals.killRate)}</span>
          ) : r.main.actuals ? (
            <span className="tc-muted" title="缺关卡敌数，显示每局平均击杀（非百分比）">
              {fmtPerGame(r.main.actuals.totalKills, r.main.actuals.games)}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {r.main.kills.toFixed(1)}≈
            </span>
          )
        ) : r.eval.killRate != null ? (
          <span title="歼灭率 = Σ击杀 / Σ关卡敌数">{fmtPct(r.eval.killRate)}</span>
        ) : r.eval.totalKills != null && r.eval.games > 0 ? (
          <span className="tc-muted" title="缺关卡敌数，显示每局平均击杀">
            {fmtPerGame(r.eval.totalKills, r.eval.games)}
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('dmgPerKill'),
      cell: (r) => {
        const pct = r.kind === 'main' ? r.main.actuals?.dmgPerKillPct : r.eval.dmgPerKillPct
        const abs = r.kind === 'main' ? r.main.actuals?.dmgPerKill : r.eval.dmgPerKill
        if (pct != null)
          return <span title="每杀承伤 / (命数×满血)；越小越会周旋">{fmtPct(pct)}</span>
        if (abs != null)
          return (
            <span className="tc-muted" title="缺容量分母，显示每杀承伤绝对 HP">
              {abs.toFixed(1)}
            </span>
          )
        return <span className="tc-muted">-</span>
      },
    },
    {
      ...colDef('residualHp'),
      cell: (r) => {
        const pct = r.kind === 'main' ? r.main.actuals?.avgResidualHpPct : r.eval.avgResidualHpPct
        const abs = r.kind === 'main' ? r.main.actuals?.avgResidualHp : r.eval.avgResidualHp
        if (pct != null) return <span title="胜局残血 / 该局可支配生命容量">{fmtPct(pct)}</span>
        if (abs != null)
          return (
            <span className="tc-muted" title="缺容量分母，显示胜局平均残血 HP">
              {abs}
            </span>
          )
        return <span className="tc-muted">-</span>
      },
    },
    {
      ...colDef('loot'),
      cell: (r) =>
        r.kind === 'main' ? (
          r.main.actuals ? (
            <span title="每局平均拾取数/掉落数">
              {fmtLootPickDrop(
                r.main.actuals.totalPU,
                r.main.actuals.totalPUSpawn,
                r.main.actuals.games,
              )}
            </span>
          ) : (
            <span className="tc-muted" title="该轮磁盘数据已清理，估算值">
              {(r.main.loot * 100).toFixed(0)}%≈
            </span>
          )
        ) : r.eval.totalPU !== null ? (
          <span title="每局平均拾取数/掉落数">
            {fmtLootPickDrop(r.eval.totalPU, r.eval.totalPUSpawn, r.eval.games)}
          </span>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
  ]
}

function overfitCol(): Col<MetricRow> {
  return {
    ...colDef('overfit'),
    sortValue: (r) => (r.kind === 'eval' ? (r.eval.overfitGapPp ?? null) : null),
    cell: (r) => {
      if (r.kind !== 'eval') return <span className="tc-muted">-</span>
      const gap = r.eval.overfitGapPp
      if (gap == null) {
        return (
          <span className="tc-muted" title={overfitCellTitle(r.eval)}>
            -
          </span>
        )
      }
      return (
        <Badge tone={overfitTone(gap)} title={overfitCellTitle(r.eval)}>
          {fmtOverfitGap(gap)}
        </Badge>
      )
    },
  }
}

/**
 * 列工厂。
 * - eval-only：列结构与顺序与首页 Hero EvalTable 逐列一致
 *   （iter / 时间 / eval 胜率 / b01 / b10 / p / delta / 过拟合 / 技能列 / 得分 / 用时 / wver）。
 * - all / rollout：主行诊断列（rollout / PPO / KL…）；eval 子行同技能读数。
 */
function buildMetricCols(
  ea: EvalACols,
  mode: IterFilter,
  baselineIter: number | null,
): Col<MetricRow>[] {
  const timeCol: Col<MetricRow> = {
    ...colDef('time'),
    cell: (r) => <span className="tc-muted">{r.kind === 'main' ? r.main.time : r.eval.time}</span>,
  }
  /** 胜率列：两处调用分别是主行「胜率」与 eval 视图「eval 胜率」——两个列键共用一份格子渲染。 */
  const winRateCol = (key: 'winRate' | 'evalRate'): Col<MetricRow> => ({
    ...colDef(key),
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
  })
  const scoreCol: Col<MetricRow> = {
    ...colDef('scoreMean'),
    cell: (r) =>
      r.kind === 'main' ? (
        r.main.scoreMean.toFixed(4)
      ) : r.eval.scoreMean !== null ? (
        r.eval.scoreMean.toFixed(4)
      ) : (
        <span className="tc-muted">-</span>
      ),
  }

  if (mode === 'eval') {
    return [
      iterCol(ea),
      timeCol,
      winRateCol('evalRate'),
      ...pairedCols(baselineIter),
      overfitCol(),
      ...skillCols(),
      scoreCol,
      {
        ...colDef('evalSec'),
        cell: (r) =>
          r.kind === 'eval' ? `${r.eval.sec.toFixed(0)}s` : <span className="tc-muted">-</span>,
      },
      {
        ...colDef('wver'),
        cell: (r) =>
          r.kind === 'eval' ? (
            <span className="tc-mono tc-muted tc-small" title={r.eval.wver}>
              {r.eval.wver.slice(0, 7)}
            </span>
          ) : (
            <span className="tc-muted">-</span>
          ),
      },
    ]
  }

  return [
    iterCol(ea),
    timeCol,
    winRateCol('winRate'),
    overfitCol(),
    ...skillCols(),
    {
      ...colDef('phaseSecs'),
      cell: (r) =>
        r.kind === 'main' ? (
          <span title={phaseSecsTitle(phaseSecs(r.main))}>{fmtPhaseSecs(phaseSecs(r.main))}</span>
        ) : (
          <span title="eval 窗口用时" className="tc-num">
            {r.eval.sec.toFixed(0)}s
          </span>
        ),
    },
    scoreCol,
    ...klEntropyCols(),
  ]
}

/** PPO 诊断列（rollout 行专属；eval-only 模式下整列是—，由配对列替换）。 */
function klEntropyCols(): Col<MetricRow>[] {
  return [
    {
      ...colDef('kl'),
      cell: (r) =>
        r.kind === 'main' ? (
          <Badge tone={klTone(r.main.kl)}>{r.main.kl.toFixed(4)}</Badge>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('entropy'),
      cell: (r) =>
        r.kind === 'main' ? r.main.entropy.toFixed(3) : <span className="tc-muted">-</span>,
    },
    {
      ...colDef('meanRet'),
      cell: (r) =>
        r.kind === 'main' ? (
          <Badge tone={retTone(r.main.meanRet)}>{r.main.meanRet.toFixed(3)}</Badge>
        ) : (
          <span className="tc-muted">-</span>
        ),
    },
    {
      ...colDef('lr'),
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
    <div className="tc-panelbody">
      {flash ? <InlineNotice>{flash}</InlineNotice> : null}
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
      <p className="tc-caption tc-caption--flush">
        胜局耗时/击杀/承伤·杀/道具 = <b>实际值</b>（it&#123;N&#125;/**/manifest.json 逐局聚合，
        道具列 = 每局平均拾取/掉落； stage+seed
        去重后留底缓存）；击杀=歼灭率（Σkills/Σ敌数），承伤·杀 =
        每杀承伤/(命数×满血)，残血=胜局残血/可支配生命容量（均百分比）；带 ≈ 为估算。 eval 行 ={' '}
        <b>干净评估</b>（greedy 固定语料），iter=N 评估的是第 N 轮 PPO 更新前的权重；缺N =
        窗口内未收官被清场。
      </p>
    </div>
  )
}
