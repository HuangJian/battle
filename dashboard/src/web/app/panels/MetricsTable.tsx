/** MetricsTable.tsx — 「指标」页：完整表格 + eval 子行 + 行过滤。
 *  iter 列：无 eval 的主行可点 evalA（课程设计评估 → eval_log，与 Hero 最新 6 轮同路径）。
 *
 *  列模型（表头文字 / hover 口径 / 数字对齐）来自 `view/metric-columns.ts` 的**唯一一份**；
 *  列工厂（格子渲染）在 `metric-table-cols.tsx`——首页「最新 6 轮」速览表也用它，
 *  否则两处格子迟早分叉（红心——用户 2026-11-10：首页备选列必须与完整表完全一样）。
 *  本文件只管：行筛选 / 表格壳 / 列显隐与统计量持久化 / evalA 入队。
 *  背景与验收：`DECISIONS.md §2026-09-20-metric-table-single-home`。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import {
  DEFAULT_BEHAVIOR_METRIC_STATS,
  DEFAULT_HIDDEN_COL_KEYS,
  parseBehaviorMetricStats,
  parseHiddenCols,
  TC_METRICS_COLS,
  TC_METRICS_HIDDEN_LEGACY,
  TC_METRICS_STAT,
  type BehaviorMetricKey,
  type BehaviorMetricStatMap,
  evalBannerDone,
  pairedBaselineOf,
  TC_METRICS_FILTER,
  type ConsoleStateView,
  type EvalABanner,
  type EvalCkptFile,
  type IterFilter,
  type MetricStatKey,
} from '../../view'
import { DataTable } from '../../components/DataTable'
import { EvalBanner } from '../../components/EvalBanner'
import { MetricsConfigModal } from './MetricsConfigModal'
import { SegmentedControl } from '../../components/SegmentedControl'
import { ckptForIter, loadCourseCkpts, startEvalA } from '../lib/eval-a'
import { buildMetricCols, buildRows, type MetricRow } from './metric-table-cols'

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
  // ── 指标表配置（用户 2026-11-10）：哪几列上屏 + 逐局指标用哪种统计量 ──
  // 持久化与 Hero 的走势配置同一套路：SSR 首帧 = 默认（逐局指标列隐藏、全均值），
  // hydrate 后由 effect 从 localStorage 恢复偏好。
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(() => new Set(DEFAULT_HIDDEN_COL_KEYS))
  useEffect(() => {
    try {
      setHiddenCols(
        parseHiddenCols(
          localStorage.getItem(TC_METRICS_COLS),
          localStorage.getItem(TC_METRICS_HIDDEN_LEGACY),
        ),
      )
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const [metricStats, setMetricStats] = useState<BehaviorMetricStatMap>(() => ({
    ...DEFAULT_BEHAVIOR_METRIC_STATS,
  }))
  useEffect(() => {
    try {
      setMetricStats(parseBehaviorMetricStats(localStorage.getItem(TC_METRICS_STAT)))
    } catch {
      /* 隐私模式等不可写场景忽略 */
    }
  }, [])
  const [colsOpen, setColsOpen] = useState(false)
  const onToggleCol = (key: string, on: boolean): void => {
    const next = new Set(hiddenCols)
    if (on) next.delete(key)
    else next.add(key)
    setHiddenCols(next)
    try {
      localStorage.setItem(TC_METRICS_COLS, JSON.stringify([...next]))
    } catch {
      /* ignore */
    }
  }
  const onMetricStat = (key: BehaviorMetricKey, stat: MetricStatKey): void => {
    const next = { ...metricStats, [key]: stat }
    setMetricStats(next)
    try {
      localStorage.setItem(TC_METRICS_STAT, JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }

  const [ckpts, setCkpts] = useState<EvalCkptFile[]>([])
  const [busyIters, setBusyIters] = useState<Set<number>>(() => new Set())
  // evalA 横幅（用户 2026-10-18：与首页同一形状 + 读数回填后自动隐藏；原来是 InlineNotice 一行
  // 灰字且永不消失）。形状在 `components/EvalBanner.tsx`，该不该消失的判据在 `view/eval-a-banner.ts`。
  const [evalBanner, setEvalBanner] = useState<EvalABanner | null>(null)
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
  // 读数回填 ⇒ 横幅自动隐藏（与首页同一条判据）：只认**点的那一轮**出现 evalData；切课作废。
  useEffect(() => {
    setEvalBanner((b) => (b && evalBannerDone(b, iters, course) ? null : b))
  }, [iters, course])
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
      // 点不动的档：没有读数会来 ⇒ 红横幅留给用户看完手动关（fail loud，不静默）。
      setEvalBanner({ course, iter, message: `未发现 it${iter} 权重归档`, ok: false })
      return
    }
    setBusyIters((s) => new Set(s).add(iter))
    setEvalBanner({ course, iter, message: `it${iter} evalA 启动中…`, ok: true })
    void (async () => {
      try {
        const { ok, message } = await startEvalA(course, iter, ckpt)
        if (!alive.current) return
        setEvalBanner({ course, iter, message, ok })
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
        setEvalBanner({ course, iter, message: String(e), ok: false })
        setBusyIters((s) => {
          const n = new Set(s)
          n.delete(iter)
          return n
        })
      }
    })()
  }

  const allCols = buildMetricCols(
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
    metricStats,
  )
  // 显隐由本页持有（「指标表配置」弹窗是唯一入口）⇒ 只把要显示的列交给表格。
  const metricCols = allCols.filter((c) => !hiddenCols.has(c.key))

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
      {evalBanner ? <EvalBanner banner={evalBanner} onDismiss={() => setEvalBanner(null)} /> : null}
      <DataTable<MetricRow>
        rows={display}
        rowKey={(r) => (r.kind === 'main' ? `m${r.iter}` : `e${r.iter}`)}
        searchKeys={['iter', 'time']}
        columns={metricCols}
        initialSortKey="iter"
        initialSortDir="desc"
        storagePrefix="tc.metrics"
        // 显隐已由本页接管（弹窗 + `tc.metrics.cols`）⇒ 收起表格自带的「列▾」。
        columnsMenu={false}
        // 表头可拖拽重排（用户 2026-11-10）：顺序存 `tc.metrics.order`，与显隐/统计量各存一份。
        reorderable
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
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              title="选哪些列上屏，以及逐局指标用哪个统计量（均值/中位数/p90…）"
              onClick={() => setColsOpen(true)}
            >
              指标配置
            </button>
            {stateView.metrics.source === 'mirror' ? (
              <span
                className="tc-badge tc-badge--y"
                title="tmp 已无本课账本；以下读数来自备份库的热备份"
              >
                备份数据
              </span>
            ) : stateView.metrics.source === 'archive' ? (
              <span
                className="tc-badge tc-badge--y"
                title="tmp 已无本课账本；以下读数来自冷存包（内存解压，未落盘）"
              >
                备份数据（冷存包）
              </span>
            ) : null}
          </>
        }
      />
      <MetricsConfigModal
        open={colsOpen}
        cols={allCols.map((c) => ({ key: c.key, label: c.label, hint: c.thTitle }))}
        hidden={hiddenCols}
        stats={metricStats}
        onToggle={onToggleCol}
        onStat={onMetricStat}
        onClose={() => setColsOpen(false)}
      />
      <p className="tc-caption tc-caption--flush">
        胜局耗时/击杀/承伤·杀/道具 = <b>实际值</b>（it&#123;N&#125;/**/manifest.json 逐局聚合，
        道具列 = 每局平均拾取/掉落； stage+seed
        去重后留底缓存）；击杀=歼灭率（Σkills/Σ敌数），承伤·杀 =
        每杀承伤/(命数×满血)，残血=胜局残血/可支配生命容量（均百分比）；带 ≈ 为估算。 eval 行 ={' '}
        <b>干净评估</b>（greedy 固定语料），iter=N 评估的是第 N 轮 PPO 更新前的权重；缺N =
        窗口内未收官被清场。逐局指标列 = 完整 metrics 列（主行 rollout、eval
        子行干净评估），默认隐藏；点「指标配置」选上屏的列、并逐指标选统计量
        （均值/中位数/最小/最大/p90/p99，缺省均值），单元格 hover 仍列全六种。
      </p>
    </div>
  )
}
