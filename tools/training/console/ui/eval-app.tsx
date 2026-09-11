/** eval-app.tsx — /eval 独立评估页（plan/evalboard-console-ux.md §4-R8/R9）。
 *
 * 照 /log/<key> 范式：SSR 首帧 + hydrate，独立 bundle（eval.js）。
 * 区块：运行态 / 异常清单 / 阶梯表 / iter 矩阵 / 批次台账 / 多课程对比 / 触发区 / CSV 导出。
 * 多课程（A8）：列按 probe_key（= 同一 rung id）对齐；跨课程/跨 rung 一律标「不可比」，
 * 禁止相减（§3.5）。非阶梯课程（落 stage-<id>）给显式提示而非空白。
 */

import { useCallback, useEffect, useState } from 'preact/hooks'
import type {
  EvalAlert,
  EvalBatchRow,
  EvalBoardView,
  EvalCsvCol,
  EvalIterRow,
  EvalLadderRow,
  EvalMetricKey,
  EvalPageOptions,
  EvalPagePayload,
} from '../../ui/view'
import { EVAL_METRIC_KEYS, EVAL_METRIC_LABELS, buildEvalCsv, rungLabel } from '../../ui/view'
import { Badge, Pill } from '../../ui/components/Pill'
import { DataTable, type Col } from '../../ui/components/DataTable'
import { EvalMatrix } from './panels/eval-matrix'
import { fetchEvalBoard, postAction } from './lib/api-client'
import { usePolling } from './lib/usePolling'

const pct = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)
const pp = (v: number | null): string =>
  v === null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}pp`

const METRICS_STORE = 'tc.eval.page.metrics'

function loadMetricKeys(prefix: string): EvalMetricKey[] {
  try {
    const raw = localStorage.getItem(prefix)
    // 跨课程默认收敛为胜率（R8 规模警告：C×8×M 列爆炸）。
    if (!raw) return ['winRate']
    const arr = JSON.parse(raw) as unknown
    if (!Array.isArray(arr)) return ['winRate']
    const valid = arr.filter((k): k is EvalMetricKey =>
      (EVAL_METRIC_KEYS as readonly string[]).includes(String(k)),
    )
    return valid.length > 0 ? valid : ['winRate']
  } catch {
    return ['winRate']
  }
}

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
  {
    key: 'rung',
    label: 'rung',
    thTitle: '关卡画像见 hover 文本',
    cell: (r) => <b title={rungLabel(r)}>{r.rung}</b>,
  },
  {
    key: 'god',
    label: 'God',
    align: 'num',
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
    key: 'aTrend',
    label: 'A 趋势',
    align: 'num',
    sortValue: (r) => r.aTrend?.winRate ?? -1,
    cell: (r) =>
      r.aTrend === null ? (
        <span className="tc-muted">—</span>
      ) : (
        <span title={`A 层自动入账 it${r.aTrend.iter} / ${r.aTrend.n} 局（不判能力）`}>
          {pct(r.aTrend.winRate)}
        </span>
      ),
  },
  {
    key: 'latest',
    label: 'latest',
    align: 'num',
    sortValue: (r) => r.latestWin ?? -1,
    cell: (r) => pct(r.latestWin),
  },
  {
    key: 'window',
    label: '窗均值',
    align: 'num',
    sortValue: (r) => r.windowWin ?? -1,
    cell: (r) => pct(r.windowWin),
  },
  {
    key: 'delta',
    label: 'Δ',
    align: 'num',
    sortValue: (r) => r.deltaVsGod ?? -999,
    cell: (r) => pp(r.deltaVsGod),
  },
  { key: 'gate', label: '过门', cell: gateCell },
]

/** 非阶梯课程判定：所有 rung 无任何读数（落 stage-<id>，见 §2.3）。 */
function offLadder(view: EvalBoardView): boolean {
  return view.ladder.every((r) => r.n === 0 && r.god.winRate === null && r.aTrend === null)
}

function viewLadderKey(view: EvalBoardView): string {
  return view.ladder
    .filter((r) => r.n > 0 || r.god.winRate !== null)
    .map((r) => r.rung)
    .sort()
    .join(',')
}

function downloadCsv(name: string, text: string): void {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(a.href)
}

export function EvalApp({
  initial,
  options,
}: {
  initial: EvalPagePayload
  options: EvalPageOptions
}) {
  const [views, setViews] = useState<EvalBoardView[]>(initial.views)
  const [courses, setCourses] = useState<string[]>(
    options.courses.length > 0
      ? options.courses
      : initial.views.map((v) => v.course).filter(Boolean),
  )
  const [metricKeys, setMetricKeys] = useState<EvalMetricKey[]>(() => loadMetricKeys(METRICS_STORE))
  const [flash, setFlash] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [triggerCourse, setTriggerCourse] = useState(courses[0] ?? '')
  const [rung, setRung] = useState('c4l1')
  const [ckpt, setCkpt] = useState('')
  const [policy, setPolicy] = useState<'nn' | 'god'>('nn')
  const [ladderIter, setLadderIter] = useState('')
  const [threshold, setThreshold] = useState('0.2')

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
    if (courses.length === 0) return
    try {
      const next = await Promise.all(courses.map((c) => fetchEvalBoard(false, c)))
      setViews(next)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [courses])

  useEffect(() => {
    void refresh()
    // URL 可分享（A8/R8）：?courses=a,b。
    try {
      const q = courses.length > 0 ? `?courses=${encodeURIComponent(courses.join(','))}` : ''
      history.replaceState(null, '', `/eval${q}`)
    } catch {
      /* SSR/无 history 环境 */
    }
  }, [refresh, courses])
  usePolling({ enabled: true, intervalSec: 300, fetch: refresh })

  const toggleCourse = (c: string): void => {
    setCourses((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))
  }

  const primary = views[0] ?? null
  const runner = primary?.runnerState ?? null
  const mergedAlerts: Array<EvalAlert & { course: string }> = []
  for (const v of views) for (const a of v.alerts) mergedAlerts.push({ ...a, course: v.course })
  const mergedBatches: EvalBatchRow[] = views.flatMap((v) => v.batches)
  const mergedIterRows: EvalIterRow[] = views.flatMap((v) => v.iterRows)
  const flips: Array<EvalBoardView['flips'][number] & { course: string }> = views.flatMap((v) =>
    v.flips.map((f) => ({ ...f, course: v.course })),
  )
  const ladderKeys = new Set(views.map(viewLadderKey))
  const incomparable = views.length > 1 && ladderKeys.size > 1

  const onExport = (): void => {
    if (!primary) return
    const cols: EvalCsvCol[] = [
      { header: 'n', value: (r) => (r.n > 0 ? r.n : null) },
      ...primary.ladder.flatMap((rungRow) =>
        metricKeys.map((metric) => ({
          header: `${rungRow.rung} / ${EVAL_METRIC_LABELS[metric]}`,
          value: (r: EvalIterRow) => r.cells[`${rungRow.rung}.${metric}`] ?? null,
        })),
      ),
    ]
    const rows = mergedIterRows.length > 0 ? mergedIterRows : primary.iterRows
    const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)
    downloadCsv(`evalboard-${stamp}.csv`, buildEvalCsv(rows, cols))
    setFlash(`已导出 ${rows.length} 行 CSV（当前可见列）`)
  }

  const onTrigger = useCallback(async () => {
    if (options.readOnly) return
    if (!triggerCourse || !ckpt.trim()) {
      setFlash('先选课程并填 ckpt 路径')
      return
    }
    const r = await postAction('evalProbeRun', {
      course: triggerCourse,
      ckpt: ckpt.trim(),
      rung_from: rung,
      policy,
      requester: 'eval-page',
    })
    setFlash(r.message)
    void refresh()
  }, [ckpt, options.readOnly, policy, refresh, rung, triggerCourse])

  const onAbort = useCallback(
    async (batchId: string) => {
      if (options.readOnly) return
      const r = await postAction('evalBatchAbort', { batch_id: batchId, requester: 'eval-page' })
      setFlash(r.message)
      void refresh()
    },
    [options.readOnly, refresh],
  )

  const onLadderStart = useCallback(async () => {
    if (options.readOnly) return
    if (!triggerCourse || !ckpt.trim()) {
      setFlash('先选课程并填 ckpt 路径')
      return
    }
    const r = await postAction('evalLadderStart', {
      course: triggerCourse,
      ckpt: ckpt.trim(),
      ...(ladderIter.trim() ? { iter: Number(ladderIter) } : {}),
      threshold: Number(threshold) || 0.2,
      start_rung: rung,
      requester: 'eval-page',
    })
    setFlash(r.message)
    void refresh()
  }, [ckpt, ladderIter, options.readOnly, refresh, rung, threshold, triggerCourse])

  const onLadderStop = useCallback(async () => {
    if (options.readOnly) return
    if (!triggerCourse) return
    const r = await postAction('evalLadderStop', {
      course: triggerCourse,
      ...(ladderIter.trim() ? { iter: Number(ladderIter) } : {}),
      requester: 'eval-page',
    })
    setFlash(r.message)
    void refresh()
  }, [ladderIter, options.readOnly, refresh, triggerCourse])

  return (
    <div className="tc-evalpage">
      <header className="tc-evalpage__hd">
        <h1>EvalBoard 评估页</h1>
        <div className="tc-row tc-small">
          <a className="tc-btn tc-btn--sm" href="/">
            ‹ 返回控制台
          </a>
          <button type="button" className="tc-btn tc-btn--sm" onClick={() => void refresh()}>
            刷新
          </button>
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={!primary}
            onClick={onExport}
            title="导出当前视图（受指标显隐影响）：UTF-8 BOM + RFC4180"
          >
            导出 CSV
          </button>
        </div>
      </header>

      <section className="tc-evalpage__courses">
        <b>课程</b>{' '}
        {options.allCourses.map((c) => (
          <label key={c} className="tc-small">
            <input type="checkbox" checked={courses.includes(c)} onChange={() => toggleCourse(c)} />
            <span>{c}</span>
          </label>
        ))}
        {courses.length === 0 ? <span className="tc-muted">（未选课程）</span> : null}
        {incomparable ? (
          <Pill tone="y" title="不同课程的 rung 集不同 ⇒ probe_key 不同 ⇒ 禁止相减（§3.5）">
            不可比
          </Pill>
        ) : null}
      </section>

      {runner ? (
        <div className="tc-banner tc-banner--info" role="status">
          {runner.windowOpen ? (
            <>
              <Pill tone="g">窗口开启中</Pill>
              {runner.rung ? ` · ${runner.rung}` : ''}
              {runner.unitIdx !== null ? ` u${runner.unitIdx}` : ''}
            </>
          ) : (
            <>
              <Pill tone="y">训练忙碌中</Pill>
              {runner.remainingUnits > 0 ? ` · 队列排位 ${runner.remainingUnits}` : ''}
            </>
          )}
        </div>
      ) : (
        <div className="tc-banner tc-banner--info" role="status">
          无运行态心跳（训练未在跑 ⇒ pending 批不会被认领；可本机运行
          <code> python tools/training/evalboard/kick-once.py</code>）
        </div>
      )}

      {primary?.ladderState ? (
        <div className="tc-banner tc-banner--info" role="status">
          <Pill tone={primary.ladderState.stopped ? 'r' : 'a'}>
            爬梯{primary.ladderState.stopped ? '已停' : '进行中'}
          </Pill>
          {` · it${primary.ladderState.iter} · 阈值 ${(primary.ladderState.threshold * 100).toFixed(0)}%`}
          {primary.ladderState.reachedRung
            ? ` · 已达 ${primary.ladderState.reachedRung}`
            : ' · 尚未完成首关'}
          {primary.ladderState.stoppedReason ? ` · ${primary.ladderState.stoppedReason}` : ''}
        </div>
      ) : null}

      {flash ? (
        <p className="tc-small" role="status">
          {flash}
        </p>
      ) : null}
      {error ? (
        <div className="tc-banner tc-banner--err" role="alert">
          {error}
        </div>
      ) : null}

      <section>
        <h2>
          异常清单{' '}
          <span className="tc-muted tc-small">{views.reduce((s, v) => s + v.rows, 0)} 局入账</span>
        </h2>
        {mergedAlerts.length === 0 ? (
          <p className="tc-muted">无异常（或数据不足未武装）。</p>
        ) : (
          <ul className="tc-eval-alerts">
            {mergedAlerts.map((a, i) => (
              <li key={i}>
                {alertBadge(a)} <span className="tc-muted">{a.course}</span>{' '}
                <span>{a.message}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {views.map((v) => (
        <section key={v.course}>
          <h2>
            阶梯表 · {v.course || '(当前课程)'}
            {offLadder(v) ? <Pill tone="gray">该课程不在阶梯上</Pill> : null}
          </h2>
          {offLadder(v) ? (
            <p className="tc-muted tc-small">
              该课程未落 8 级阶梯（stage 落 <code>stage-&lt;id&gt;</code>），阶梯列整行为空属预期。
            </p>
          ) : (
            <DataTable<EvalLadderRow>
              columns={ladderColumns}
              rows={v.ladder}
              rowKey={(r) => `${v.course}:${r.rung}`}
              searchKeys={['rung']}
              storagePrefix={`tc.eval.page.ladder.${v.course}`}
              emptyText="阶梯无数据"
              ariaLabel={`评估阶梯 ${v.course}`}
            />
          )}
        </section>
      ))}

      <section>
        <h2>iter 矩阵（主课程 {primary?.course || '—'}）</h2>
        {primary ? (
          <EvalMatrix
            ladder={primary.ladder}
            rows={primary.iterRows}
            metricKeys={metricKeys}
            onToggleMetric={toggleMetric}
            storagePrefix="tc.eval.page.matrix"
            ariaLabel="评估页 iter 矩阵"
            emptyText="尚无 B 层数据"
          />
        ) : (
          <p className="tc-muted">未选课程。</p>
        )}
      </section>

      <section>
        <h2>批次台账</h2>
        <DataTable
          columns={[
            {
              key: 'batch_id',
              label: 'batch',
              cell: (r: EvalBatchRow) => <span className="tc-small">{r.batch_id}</span>,
            },
            { key: 'course', label: '课程', cell: (r) => r.course },
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
            {
              key: 'op',
              label: '操作',
              cell: (r: EvalBatchRow) =>
                r.status === 'pending' || r.status === 'running' ? (
                  <button
                    type="button"
                    className="tc-btn tc-btn--sm"
                    disabled={options.readOnly}
                    title="温和中止：在途单元跑完即停"
                    onClick={() => void onAbort(r.batch_id)}
                  >
                    中止
                  </button>
                ) : (
                  <span className="tc-muted tc-small">—</span>
                ),
            },
          ]}
          rows={mergedBatches}
          rowKey={(r) => r.batch_id}
          searchKeys={['batch_id']}
          storagePrefix="tc.eval.page.batches"
          emptyText="暂无批次——用下方触发区入队"
          ariaLabel="批次台账"
        />
      </section>

      <section>
        <h2>翻转矩阵（行=对比对，格=Δ/翻转率）</h2>
        {flips.length === 0 ? (
          <p className="tc-muted">暂无相邻批次对比。</p>
        ) : (
          <DataTable
            columns={[
              {
                key: 'rung',
                label: 'rung',
                cell: (r: (typeof flips)[number]) => (
                  <span title={r.course}>
                    {r.rung} <span className="tc-muted tc-small">{r.course}</span>
                  </span>
                ),
              },
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
                cell: (r) =>
                  r.rate === null ? '—' : `${(r.rate * 100).toFixed(0)}% (${r.flips}局)`,
              },
            ]}
            rows={flips}
            rowKey={(r) => `${r.course}:${r.rung}:${r.from}:${r.to}`}
            searchKeys={['rung']}
            storagePrefix="tc.eval.page.flips"
            emptyText="暂无对比"
            ariaLabel="翻转矩阵"
          />
        )}
      </section>

      <section>
        <h2>触发区</h2>
        <div className="tc-row tc-small">
          <label>
            课程{' '}
            <select
              value={triggerCourse}
              onChange={(e) => setTriggerCourse((e.target as HTMLSelectElement).value)}
            >
              {options.allCourses.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>{' '}
          <label>
            rung{' '}
            <select value={rung} onChange={(e) => setRung((e.target as HTMLSelectElement).value)}>
              {(primary?.ladder ?? []).map((l) => (
                <option key={l.rung} value={l.rung}>
                  {l.rung}
                </option>
              ))}
            </select>
          </label>{' '}
          <label>
            policy{' '}
            <select
              value={policy}
              onChange={(e) => setPolicy((e.target as HTMLSelectElement).value as 'nn' | 'god')}
            >
              <option value="nn">nn</option>
              <option value="god">god</option>
            </select>
          </label>{' '}
          <label>
            ckpt{' '}
            <input
              type="text"
              size={32}
              placeholder={
                policy === 'god' ? 'god' : 'nn-training/weights/<leg>/<leg>.it<N>.<ts>.json'
              }
              value={ckpt}
              onInput={(e) => setCkpt((e.target as HTMLInputElement).value)}
            />
          </label>{' '}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={options.readOnly}
            onClick={() => void onTrigger()}
          >
            入队评估批
          </button>
          {options.readOnly ? (
            <span className="tc-muted">（只读视图：请在本机 localhost 触发）</span>
          ) : null}
        </div>
        <p className="tc-muted tc-small">
          入队 ≠ 执行：批由训练空闲窗认领，或本机 <code>kick-once.py</code>。
        </p>
        <div className="tc-row tc-small">
          <label>
            iter{' '}
            <input
              type="text"
              size={6}
              placeholder="同 ckpt"
              value={ladderIter}
              onInput={(e) => setLadderIter((e.target as HTMLInputElement).value)}
            />
          </label>{' '}
          <label>
            阈值{' '}
            <input
              type="text"
              size={5}
              value={threshold}
              onInput={(e) => setThreshold((e.target as HTMLInputElement).value)}
            />
          </label>{' '}
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={options.readOnly}
            title="从起始 rung 顺序推进，胜率低于阈值自动停（筛查级，不作 verdict）"
            onClick={() => void onLadderStart()}
          >
            启动爬梯
          </button>
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={options.readOnly}
            onClick={() => void onLadderStop()}
          >
            停止爬梯
          </button>
        </div>
      </section>
    </div>
  )
}
