/** EvalSummary.tsx — 首页节点行下方简易 EvalBoard（列 = 阶梯 8 级）。
 *
 * 口径（用户拍板 2026-09-10；行列互换 / 去维度行 / 学生仅 B 层 2026-09-10）：
 * - 列 = 阶梯 rung；行 = 指标（God/学生/n/Δ）；维度见表头 title
 * - God = ladder 工作副本基线；TBD = 基线未跑 →「eval now」(policy=god)
 * - 学生 = **EvalBoard B 层**窗均值（有则 latest）——训练 rollout / A 层 eval
 *   与 evalboard 不同口径，**禁止混入**（用户指令）
 * - 无数据的格：God/学生各自「eval now」——**入队后立即返回**，后台轮询批次
 *   直至 done/abort 再刷表，不阻塞首页（用户指令 2026-09-10）
 * - 门控/批次/翻转矩阵进抽屉「评估」——本表只做一眼扫读
 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import type { EvalBatchRow, EvalBoardView, EvalLadderRow } from '../../../ui/view'
import { Pill } from '../../../ui/components/Pill'
import { usePolling } from '../lib/usePolling'
import { fetchEvalBoard, postAction } from '../lib/api-client'

export interface EvalSummaryProps {
  /** 当前查看课程（学生列按课程过滤 + eval now 入队课程）。 */
  course?: string
  /** 首页可见时才轮询（后台 tab 停链）。 */
  enabled?: boolean
  /** LAN 只读：按钮禁用。 */
  readOnly?: boolean
  /** 打开抽屉「评估」完整看板。 */
  onMore: () => void
}

const pct = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

const pp = (v: number | null): string =>
  v === null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}pp`

/** 学生读数：只取 B 层（窗均值优先；partial 窗回退 latest）。训练 A 层不参与。 */
function studentWin(row: EvalLadderRow): { win: number | null; n: number; note: string } {
  if (!row.partial && row.windowWin !== null && row.n > 0)
    return { win: row.windowWin, n: row.n, note: `B 层窗均值 n=${row.n}` }
  if (row.latestWin !== null)
    return { win: row.latestWin, n: row.n, note: `B 层 latest（窗 partial，n=${row.n}）` }
  return { win: null, n: 0, note: '尚无 EvalBoard B 层入账' }
}

/** 批次 policy：字段优先；旧台账/旧 API 无 policy 时用 ckpt==='god' 回退。 */
function batchPolicy(b: EvalBatchRow): 'nn' | 'god' {
  if (b.policy === 'god' || b.policy === 'nn') return b.policy
  return b.ckpt === 'god' ? 'god' : 'nn'
}

/** 在途批（pending/running）：刷新后仍可见，避免再点一遍 eval now。 */
function activeBatch(
  batches: EvalBatchRow[],
  rung: string,
  policy: 'nn' | 'god',
  course: string,
): EvalBatchRow | null {
  for (let i = batches.length - 1; i >= 0; i--) {
    const b = batches[i]!
    if (b.rung_from !== rung) continue
    if (b.status !== 'pending' && b.status !== 'running') continue
    if (batchPolicy(b) !== policy) continue
    // 学生批必须对上当前查看课程；God 基线与权重无关，不绑课程。
    if (policy === 'nn' && course && b.course !== course) continue
    return b
  }
  return null
}

/** 在途批状态条（不阻塞、可刷新后恢复）。 */
function QueuedChip({ batch, pending }: { batch: EvalBatchRow | null; pending: boolean }) {
  if (batch) {
    const label = batch.status === 'running' ? '批执行中' : '已入队'
    return (
      <Pill
        tone={batch.status === 'running' ? 'a' : 'gray'}
        title={`${batch.batch_id} · ${batch.status} · units ${batch.units.done.length}/${batch.units.of} · 等训练 runner 拾取派发`}
      >
        {label}
        <span className="tc-small"> {batch.batch_id.slice(-6)}</span>
      </Pill>
    )
  }
  return (
    <Pill tone="gray" title="本页刚触发，台账尚未回读">
      {pending ? '入队中…' : '已入队'}
    </Pill>
  )
}

function GodCell({
  rung,
  god,
  readOnly,
  onEval,
  pending,
  queued,
}: {
  rung: string
  god: EvalLadderRow['god']
  readOnly: boolean
  onEval: (rung: string, policy: 'nn' | 'god') => void
  pending: boolean
  queued: EvalBatchRow | null
}) {
  if (god.winRate !== null) {
    return (
      <span title={`n=${god.n}${god.provisional ? ' · provisional（God<30% 穿过不判）' : ''}`}>
        {pct(god.winRate)}
        {god.provisional ? <span className="tc-muted"> prov</span> : null}
      </span>
    )
  }
  if (queued || pending) return <QueuedChip batch={queued} pending={pending} />
  return (
    <button
      type="button"
      className="tc-btn tc-btn--sm"
      disabled={readOnly}
      title={
        readOnly
          ? '局域网只读：仅本机 localhost 可入队'
          : `入队 God 基线批（policy=god）@ ${rung}；入队后等 runner 派发，不阻塞页面`
      }
      onClick={() => onEval(rung, 'god')}
    >
      eval now
    </button>
  )
}

/** 学生格：有 B 读数 → win%；无 → 在途批或「eval now」。 */
function StudentCell({
  row,
  course,
  readOnly,
  onEval,
  pending,
  queued,
}: {
  row: EvalLadderRow
  course: string
  readOnly: boolean
  onEval: (rung: string, policy: 'nn' | 'god') => void
  pending: boolean
  queued: EvalBatchRow | null
}) {
  const s = studentWin(row)
  if (s.win !== null) {
    return (
      <span title={s.note}>
        <b>{pct(s.win)}</b>
        {s.n > 0 ? <span className="tc-muted tc-small"> n={s.n}</span> : null}
      </span>
    )
  }
  if (queued || pending) return <QueuedChip batch={queued} pending={pending} />
  return (
    <button
      type="button"
      className="tc-btn tc-btn--sm"
      disabled={readOnly || !course}
      title={
        readOnly
          ? '局域网只读：仅本机 localhost 可入队'
          : course
            ? `入队 B 层评估批：${course} @ ${row.rung}`
            : '先选择课程'
      }
      onClick={() => onEval(row.rung, 'nn')}
    >
      eval now
    </button>
  )
}

/** 纯展示表（可单测：注入 view，不碰 fetch）。列 = rung，行 = 指标。 */
export function EvalSummaryTable({
  ladder,
  course,
  cachedCourse,
  readOnly = false,
  onEval,
  pendingKeys,
  batches,
}: {
  ladder: EvalLadderRow[]
  course?: string
  cachedCourse?: string
  readOnly?: boolean
  onEval: (rung: string, policy: 'nn' | 'god') => void
  /** key = `${policy}:${rung}`；true = 本页刚触发、台账尚未回读。 */
  pendingKeys?: Record<string, boolean>
  /** 台账在途批（刷新后仍显示「已入队」，不再变回 eval now）。 */
  batches?: EvalBatchRow[]
}) {
  const isPend = (policy: 'nn' | 'god', rung: string): boolean =>
    !!pendingKeys?.[`${policy}:${rung}`]
  const list = batches ?? []
  return (
    <>
      <div className="tc-tablewrap tc-eval-summary__wrap">
        <table className="tc-table tc-table--dense" aria-label="评估阶梯摘要">
          <thead>
            <tr>
              <th className="tc-firstcol">指标</th>
              {ladder.map((r) => (
                <th key={r.rung} title={r.dimension}>
                  {r.rung}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th className="tc-firstcol" scope="row" title="God 全阶梯基线（ladder 工作副本）">
                God
              </th>
              {ladder.map((r) => (
                <td key={r.rung}>
                  <GodCell
                    rung={r.rung}
                    god={r.god}
                    readOnly={readOnly}
                    onEval={onEval}
                    pending={isPend('god', r.rung)}
                    queued={activeBatch(list, r.rung, 'god', course ?? '')}
                  />
                </td>
              ))}
            </tr>
            <tr>
              <th
                className="tc-firstcol"
                scope="row"
                title="EvalBoard B 层学生窗均值（不含训练 rollout / A 层）"
              >
                学生
              </th>
              {ladder.map((r) => (
                <td key={r.rung}>
                  <StudentCell
                    row={r}
                    course={course ?? ''}
                    readOnly={readOnly}
                    onEval={onEval}
                    pending={isPend('nn', r.rung)}
                    queued={activeBatch(list, r.rung, 'nn', course ?? '')}
                  />
                </td>
              ))}
            </tr>
            <tr>
              <th className="tc-firstcol tc-muted" scope="row" title="B 层入账局数">
                n
              </th>
              {ladder.map((r) => (
                <td key={r.rung} className="tc-num tc-muted">
                  {r.n > 0 ? r.n : '—'}
                </td>
              ))}
            </tr>
            <tr>
              <th className="tc-firstcol" scope="row" title="学生 B − God">
                Δ
              </th>
              {ladder.map((r) => {
                const d = r.deltaVsGod
                return (
                  <td key={r.rung}>
                    {d === null || d === undefined ? (
                      <span className="tc-muted">—</span>
                    ) : (
                      <span title="Δ = B 层窗 win − God win（与抽屉门控同口径）">{pp(d)}</span>
                    )}
                  </td>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="tc-eval-summary__note tc-muted tc-small">
        学生 = EvalBoard <b>B 层</b>窗均值；训练 rollout / A 层 eval 与本表不同口径，不混入。
        「已入队/批执行中」= 台账在途；训练空闲窗（本轮 rollout/A-eval 收官后）自动领取， 新 rollout
        会抢占暂停让出集群。God 数字写在 ladder 工作副本，批完成后才会出现。
        门控与批次见抽屉「评估」。
        {course ? <> · 课程 {course}</> : null}
        {cachedCourse && cachedCourse !== course ? (
          <span className="tc-muted"> · 缓存课程 {cachedCourse}</span>
        ) : null}
      </p>
    </>
  )
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/** 后台盯批：每 15s fresh 拉一次 /api/evalboard，直到该 batch 终态或超时。 */
const WATCH_POLL_MS = 15_000
const WATCH_TIMEOUT_MS = 20 * 60_000

export function EvalSummary({
  course = '',
  enabled = true,
  readOnly = false,
  onMore,
}: EvalSummaryProps) {
  const [view, setView] = useState<EvalBoardView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [pendingKeys, setPendingKeys] = useState<Record<string, boolean>>({})
  /** 取消旗标：卸载 / 换课程时置位，后台 watch 立即退出。 */
  const watchAlive = useRef(true)

  useEffect(() => {
    watchAlive.current = true
    return () => {
      watchAlive.current = false
    }
  }, [])

  const setPend = useCallback((key: string, on: boolean) => {
    setPendingKeys((p) => {
      const n = { ...p }
      if (on) n[key] = true
      else delete n[key]
      return n
    })
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

  useEffect(() => {
    if (enabled) void refresh()
  }, [enabled, refresh])
  // 首页慢轮询：与抽屉评估同 300s（服务端另有 30s TTL 缓存）。
  usePolling({ enabled, intervalSec: 300, fetch: refresh })

  /** 入队成功后异步等批结束并刷表——不 await 阻塞 UI。 */
  const watchBatch = useCallback(
    async (key: string, batchId: string | null) => {
      const deadline = Date.now() + WATCH_TIMEOUT_MS
      while (watchAlive.current && Date.now() < deadline) {
        await sleep(WATCH_POLL_MS)
        if (!watchAlive.current) return
        try {
          // fresh=1 绕过 30s TTL，批状态及时可见。
          const v = await fetchEvalBoard(true, course)
          if (!watchAlive.current) return
          setView(v)
          const target = batchId ? v.batches.find((b) => b.batch_id === batchId) : null
          // 无 batchId（解析失败）时：该 rung 是否还有 pending/running。
          // 有 batchId：必须看到该批且已终态；未见 = 仍在途（或缓存），继续等。
          const done = batchId
            ? !!target && (target.status === 'done' || target.status === 'aborted')
            : !v.batches.some(
                (b) =>
                  b.course === course &&
                  b.rung_from === key.split(':').slice(1).join(':') &&
                  (b.status === 'pending' || b.status === 'running'),
              )
          if (done) {
            setPend(key, false)
            setFlash(batchId ? `评估批 ${batchId} 已结束，表已刷新` : '评估批已结束，表已刷新')
            return
          }
        } catch {
          /* 单次拉取失败继续盯 */
        }
      }
      if (watchAlive.current) {
        setPend(key, false)
        setFlash('评估批仍在跑，已停止等待（详情见抽屉批次台账）')
      }
    },
    [course, setPend],
  )

  /** 点击：立刻标 pending → fire 入队 → 后台 watch；绝不 await 整批结果。 */
  const onEval = useCallback(
    (rung: string, policy: 'nn' | 'god') => {
      if (readOnly) return
      if (policy === 'nn' && !course) return
      const key = `${policy}:${rung}`
      if (pendingKeys[key]) return
      setPend(key, true)
      setFlash(`已入队 ${rung}（${policy}），后台等待中…`)
      void (async () => {
        try {
          const ckpt = policy === 'god' ? 'god' : `tmp/${course}/weights.json`
          const r = await postAction('evalProbeRun', {
            course,
            ckpt,
            rung_from: rung,
            policy,
            requester: 'eval-summary',
          })
          if (!watchAlive.current) return
          if (!r.ok) {
            setPend(key, false)
            setFlash(r.message)
            return
          }
          setFlash(r.message)
          // 从「评估批已入队 <id>」抠 batch_id；失败则 watch 退化为按 rung 轮询。
          const m = /评估批已入队\s+(\S+)/.exec(r.message)
          void watchBatch(key, m ? m[1]! : null)
        } catch (e) {
          if (!watchAlive.current) return
          setPend(key, false)
          setFlash(e instanceof Error ? e.message : String(e))
        }
      })()
    },
    [course, pendingKeys, readOnly, setPend, watchBatch],
  )

  return (
    <section className="tc-eval-summary" aria-label="评估摘要（阶梯）">
      <header className="tc-eval-summary__hd">
        <h2 className="tc-eval-summary__title">
          EvalBoard 摘要
          <span className="tc-muted tc-small">
            {' '}
            · 列 = 阶梯 8 级 · God vs 学生（
            {course || '当前课程'}
            ）B 层
          </span>
        </h2>
        <button type="button" className="tc-btn tc-btn--sm" onClick={onMore}>
          完整评估看板 ›
        </button>
      </header>
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
          course={course}
          cachedCourse={view.course}
          readOnly={readOnly}
          onEval={onEval}
          pendingKeys={pendingKeys}
          batches={view.batches}
        />
      )}
    </section>
  )
}

void null
