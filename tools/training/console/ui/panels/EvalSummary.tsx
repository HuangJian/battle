/** EvalSummary.tsx — 首页 EvalBoard 摘要（plan/evalboard-console-ux.md §4）。
 *
 * 口径（用户 2026-09-11 拍板 A1/A2/A9/A12/A13，**推翻** 2026-09-10「行列互换 / 去维度行」）：
 * - 矩阵：**行(iter) × 列(rung×指标)**；iter 层级 = **B 层**（God 行独立）。
 * - 列 = 扁平 key `${rung}.${metric}`，两段文本（rung / 指标）挂在表头 title。
 * - B 层 0 行时显示**空态 + 引导**（A12），不用 A 层数据充数。
 * - 头部：**iter select + 「评估」按钮**（A13，用户核心诉求：自己点出数据）。
 *   按钮 = **入队**，≠ 执行：批要跑起来需训练空闲窗或手动 `kick-once.py`（见运行态提示）。
 * - A 层（训练内自动 eval）仍不混入本表，只做趋势（§2.1）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type {
  EvalBatchRow,
  EvalBoardView,
  EvalCkptFile,
  EvalIterRow,
  EvalLadderRow,
  EvalMetricKey,
} from '../../../ui/view'
import { EVAL_METRIC_KEYS, EVAL_METRIC_LABELS, rungLabel } from '../../../ui/view'
import { Pill } from '../../../ui/components/Pill'
import { DataTable, type Col } from '../../../ui/components/DataTable'
import { usePolling } from '../lib/usePolling'
import { fetchEvalBoard, fetchEvalCkpts, postAction } from '../lib/api-client'

export interface EvalSummaryProps {
  /** 当前查看课程。 */
  course?: string
  /** 首页可见时才轮询（后台 tab 停链）。 */
  enabled?: boolean
  /** LAN 只读：按钮禁用。 */
  readOnly?: boolean
  /** 打开完整评估页（/eval）。 */
  onMore: () => void
}

const pct = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

/** 指标值格式化（列渲染 + 后续导出共用口径）。 */
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

/** 批次 policy：字段优先；旧台账无 policy 时用 ckpt==='god' 回退。 */
function batchPolicy(b: EvalBatchRow): 'nn' | 'god' {
  if (b.policy === 'god' || b.policy === 'nn') return b.policy
  return b.ckpt === 'god' ? 'god' : 'nn'
}

/** 矩阵列（扁平 key = `${rung}.${metric}`）。 */
function matrixColumns(ladder: EvalLadderRow[], metricKeys: EvalMetricKey[]): Col<EvalIterRow>[] {
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

/** 指标显隐 checkbox（toolbarLeft 插槽；勾掉 = 该指标全部 rung 列消失）。 */
function MetricToggles({
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

/** 空态（A12）：B 层 0 行 → 明确引导，不显示误导性空数据行。 */
function EmptyState({ course, batches }: { course: string; batches: EvalBatchRow[] }) {
  const pending = batches.filter(
    (b) => batchPolicy(b) === 'nn' && (b.status === 'pending' || b.status === 'running'),
  )
  return (
    <div className="tc-empty tc-eval-summary__empty">
      <p>
        <b>尚无 B 层数据（至今未执行过任何 evalB）。</b>
      </p>
      <p className="tc-muted tc-small">
        ① 选 iter → 点上方「评估」入队；批由训练空闲窗（rollout/A-eval 收官后）或本机手动执行，
        <b>入队 ≠ 立即执行</b>。
      </p>
      <p className="tc-muted tc-small">
        ② 或本机手动跑一次：
        <code> python tools/training/evalboard/kick-once.py</code>
      </p>
      {pending.length > 0 ? (
        <p className="tc-small">
          当前在途 nn 批 {pending.length} 个：
          {pending.map((b) => (
            <span key={b.batch_id}>
              {' '}
              <Pill tone="gray" title={`${b.batch_id} · ${b.status}`}>
                it{b.iter} {b.status}
              </Pill>
            </span>
          ))}
        </p>
      ) : null}
      {course ? null : <p className="tc-muted tc-small">未选课程。</p>}
    </div>
  )
}

/** 纯展示矩阵（可单测：注入 view，不碰 fetch）。 */
export function EvalSummaryTable({
  ladder,
  iterRows,
  course,
  metricKeys,
  onToggleMetric,
  batches,
}: {
  ladder: EvalLadderRow[]
  iterRows: EvalIterRow[]
  course?: string
  /** 兼容旧调用方（本表只读，无按钮）。 */
  readOnly?: boolean
  metricKeys: EvalMetricKey[]
  onToggleMetric?: (k: EvalMetricKey) => void
  batches?: EvalBatchRow[]
}) {
  const list = batches ?? []
  const hasBData = iterRows.some((r) => r.kind === 'iter' && r.n > 0)
  const columns = useMemo(() => matrixColumns(ladder, metricKeys), [ladder, metricKeys])

  // A12：B 层 0 行 ⇒ 空态 + 引导（绝不显示误导性空数据行，也不拿 A 层充数）。
  if (!hasBData) return <EmptyState course={course ?? ''} batches={list} />

  return (
    <>
      {iterRows.some((r) => r.screening && r.kind === 'iter') ? (
        <p className="tc-muted tc-small">
          「筛查级」：单批/未满窗阈值读数，不作 verdict、不写门控。
        </p>
      ) : null}
      <DataTable<EvalIterRow>
        columns={columns}
        rows={iterRows}
        rowKey={(r) => `${r.kind}:${r.iter}`}
        searchKeys={['iter']}
        storagePrefix="tc.eval.summary"
        emptyText="无匹配行"
        ariaLabel="评估 iter 矩阵"
        toolbarLeft={
          onToggleMetric ? (
            <MetricToggles metricKeys={metricKeys} onToggle={onToggleMetric} />
          ) : null
        }
      />
      <p className="tc-eval-summary__note tc-muted tc-small">
        行 = B 层 iter（含 God 基线行，仅填胜率列）；列 = rung×指标（hover 表头看关卡画像）。 学生 =
        EvalBoard <b>B 层</b>；训练 rollout / A 层 eval 与本表不同口径，不混入。
        {course ? <> · 课程 {course}</> : null}
        {list.length > 0 ? ` · 台账 ${list.length} 批` : ''}
      </p>
    </>
  )
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** 后台盯批：每 15s fresh 拉一次 /api/evalboard，直到该 batch 终态（无硬超时）。 */
const WATCH_POLL_MS = 15_000

/** 指标显隐持久化 key（与其他 tc.* 表一致）。 */
const METRICS_STORE = 'tc.eval.summary.metrics'

function loadMetricKeys(): EvalMetricKey[] {
  try {
    const raw = localStorage.getItem(METRICS_STORE)
    if (!raw) return [...EVAL_METRIC_KEYS]
    const arr = JSON.parse(raw) as unknown
    if (!Array.isArray(arr)) return [...EVAL_METRIC_KEYS]
    const valid = arr.filter((k): k is EvalMetricKey =>
      (EVAL_METRIC_KEYS as readonly string[]).includes(String(k)),
    )
    return valid.length > 0 ? valid : [...EVAL_METRIC_KEYS]
  } catch {
    return [...EVAL_METRIC_KEYS]
  }
}

export function EvalSummary({
  course = '',
  enabled = true,
  readOnly = false,
  onMore,
}: EvalSummaryProps) {
  const [view, setView] = useState<EvalBoardView | null>(null)
  const [ckpts, setCkpts] = useState<EvalCkptFile[]>([])
  const [ckpt, setCkpt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [metricKeys, setMetricKeys] = useState<EvalMetricKey[]>(() => loadMetricKeys())
  /** 取消旗标：卸载 / 换课程时置位，后台 watch 立即退出。 */
  const watchAlive = useRef(true)

  useEffect(() => {
    watchAlive.current = true
    return () => {
      watchAlive.current = false
    }
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(METRICS_STORE, JSON.stringify(metricKeys))
    } catch {
      /* 不可写不致命 */
    }
  }, [metricKeys])

  const toggleMetric = useCallback((k: EvalMetricKey) => {
    setMetricKeys((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]))
  }, [])

  const refresh = useCallback(async () => {
    try {
      const v = await fetchEvalBoard(false, course)
      if (!watchAlive.current) return
      setView(v)
      setError(null)
    } catch (e) {
      if (!watchAlive.current) return
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [course])

  // iter 候选（R7 发现端点）；课程切换重新拉。
  useEffect(() => {
    let alive = true
    if (!course) {
      setCkpts([])
      setCkpt('')
      return () => {
        alive = false
      }
    }
    void fetchEvalCkpts(course)
      .then((c) => {
        if (!alive) return
        setCkpts(c.files)
        // 默认选活动权重；没有则选最新归档。
        const active = c.files.find((f) => f.leg === '(active)')
        const first = c.files.find((f) => f.iter !== null)
        setCkpt(active?.path ?? first?.path ?? `tmp/${course}/weights.json`)
      })
      .catch(() => {
        /* 端点失败 → 手填兜底 */
      })
    return () => {
      alive = false
    }
  }, [course])

  useEffect(() => {
    if (enabled) void refresh()
  }, [enabled, refresh])
  usePolling({ enabled, intervalSec: 300, fetch: refresh })

  /** 入队成功后异步等批结束并刷表——不 await 阻塞 UI；无硬超时（仍在跑就继续等）。 */
  const watchBatch = useCallback(
    async (batchId: string | null) => {
      let waited = 0
      while (watchAlive.current) {
        await sleep(WATCH_POLL_MS)
        waited += WATCH_POLL_MS
        if (!watchAlive.current) return
        try {
          const v = await fetchEvalBoard(true, course)
          if (!watchAlive.current) return
          setView(v)
          const target = batchId ? v.batches.find((b) => b.batch_id === batchId) : null
          const done = batchId
            ? !!target && (target.status === 'done' || target.status === 'aborted')
            : !v.batches.some(
                (b) => batchPolicy(b) === 'nn' && b.status !== 'done' && b.status !== 'aborted',
              )
          if (done) {
            setFlash(batchId ? `评估批 ${batchId} 已结束，表已刷新` : '评估批已结束，表已刷新')
            return
          }
          if (waited >= 20 * 60_000) {
            setFlash('评估批仍在排队（训练忙碌中）——可本机运行 kick-once.py 立即执行')
            waited = 0
          }
        } catch {
          /* 单次拉取失败继续盯 */
        }
      }
    },
    [course],
  )

  const onSubmit = useCallback(() => {
    if (readOnly || !course) return
    if (!ckpt.trim()) {
      setFlash('先选 iter（或手填 ckpt 路径）')
      return
    }
    setSubmitting(true)
    setFlash(`已入队 ${ckpt.trim()}，后台等待中…`)
    void (async () => {
      try {
        const iter = iterValue(ckpts, ckpt)
        const r = await postAction('evalProbeRun', {
          course,
          ckpt: ckpt.trim(),
          rung_from: 'c4l1',
          policy: 'nn',
          requester: 'eval-summary',
          ...(iter !== null ? { iter } : {}),
        })
        if (!watchAlive.current) return
        setFlash(r.message)
        if (!r.ok) {
          setSubmitting(false)
          return
        }
        const m = /评估批已入队\s+(\S+)/.exec(r.message)
        setSubmitting(false)
        void watchBatch(m ? m[1]! : null)
      } catch (e) {
        if (!watchAlive.current) return
        setSubmitting(false)
        setFlash(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [ckpt, course, ckpts, readOnly, watchBatch])

  const runner = view?.runnerState ?? null
  const busyMin =
    runner && !runner.windowOpen && runner.lastWindowClosedTs !== null
      ? Math.max(0, Math.round((Date.now() - runner.lastWindowClosedTs) / 60000))
      : 0

  return (
    <section className="tc-eval-summary" aria-label="评估摘要（iter 矩阵）">
      <header className="tc-eval-summary__hd">
        <h2 className="tc-eval-summary__title">
          EvalBoard 摘要
          <span className="tc-muted tc-small">
            {' '}
            · 行 = B 层 iter · 列 = rung×指标（{course || '当前课程'}）
          </span>
        </h2>
        <div className="tc-row tc-small">
          <label>
            iter{' '}
            <select
              aria-label="选择评估 iter"
              value={ckpt}
              disabled={!course}
              onChange={(e) => setCkpt((e.target as HTMLSelectElement).value)}
            >
              {ckpts.length === 0 ? <option value="">（无发现结果）</option> : null}
              {ckpts.map((f) => (
                <option key={`${f.leg}:${f.path}:${f.mtime}`} value={f.path}>
                  {f.iter !== null ? `it${f.iter}` : f.leg === '(active)' ? '活动权重' : '—'}
                  {` · ${f.path.split('/').pop()}`}
                </option>
              ))}
            </select>
          </label>
          {/* 只读不物理禁用按钮（只读是动作边界，点击由服务端 403 + flash 兜底）。 */}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={!course || submitting}
            title={
              readOnly
                ? '只读模式：操作仅限本机 localhost'
                : '入队 B 层评估批（＝入队，不等于立即执行；由训练空闲窗或 kick-once.py 执行）'
            }
            onClick={onSubmit}
          >
            评估
          </button>
          <button type="button" className="tc-btn tc-btn--sm" onClick={onMore}>
            完整评估看板 ›
          </button>
        </div>
      </header>
      {runner ? (
        <p className="tc-eval-summary__note tc-small" role="status">
          {runner.windowOpen ? (
            <>
              <Pill tone="g">窗口开启中</Pill>
              {runner.rung ? ` · ${runner.rung}` : ''}
              {runner.unitIdx !== null ? ` u${runner.unitIdx}` : ''}
              {runner.remainingUnits > 0 ? ` · 剩余 ${runner.remainingUnits} 单元` : ''}
            </>
          ) : (
            <>
              <Pill tone="y">训练忙碌中</Pill>
              {busyMin > 0 ? ` · 已等 ${busyMin} 分钟` : ''}
              {runner.remainingUnits > 0 ? ` · 队列排位 ${runner.remainingUnits}` : ''}
            </>
          )}
        </p>
      ) : null}
      {view?.abWarn ? (
        <div className="tc-banner tc-banner--err" role="alert">
          {view.abWarn}
        </div>
      ) : null}
      {flash ? (
        <p className="tc-eval-summary__note tc-small" role="status">
          {flash}
        </p>
      ) : null}
      {error ? (
        <div className="tc-banner tc-banner--err" role="alert">
          {error}
        </div>
      ) : !view ? (
        <div className="tc-loading">加载评估摘要…</div>
      ) : (
        <EvalSummaryTable
          ladder={view.ladder}
          iterRows={view.iterRows}
          course={course}
          readOnly={readOnly}
          metricKeys={metricKeys}
          onToggleMetric={toggleMetric}
          batches={view.batches}
        />
      )}
    </section>
  )
}

/** ckpt 路径 → iter（活动权重 = null；归档文件取元数据里的 iter）。 */
function iterValue(files: EvalCkptFile[], ckpt: string): number | null {
  const f = files.find((x) => x.path === ckpt)
  return f ? f.iter : null
}
