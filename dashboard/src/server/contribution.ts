/** contribution.ts — 并行 worker 贡献度聚合（fs 层**唯一实现**，plan/worker-contribution-view）。
 *
 *  两条数据链在这里汇合（语义规格见 plan §4；纯函数在 `web/view/contribution.ts`）：
 *    · **采样**：`aggregateNodeHistory()`（跨流窗口聚合）⊕ `projectWindow`/`projectCourseBreakdown`；
 *    · **PPO**：逐课 `tmp/<课>/training_log.jsonl` 尾部事件——
 *      `job_completed`（训练侧写，无身份）**按 job_id join** `job_result_accepted`
 *      （hub 承接结果那一刻写，带 worker；plan W2）得归属；`job_rejected` 即「晚到·白算」。
 *      hub 的 `_JobStore.jsonl_path` 就是同一份课程账本 ⇒ 两行天然同册，不新建账本（N4）。
 *
 *  缓存纪律（§1.3）：**不新增第二个缓存层**——PPO 事件读用进程内 memo（仿 `aggMemo`：
 *  文件指纹 + `PPO_MEMO_MIN_MS` 时间下限）；采样侧直接吃既有 `aggregateNodeHistory` 的 memo。
 *  账本只读**尾部**（`readLedgerTail`，有界内存）；不裁剪账本（保留期 = plan §9-P3 开放问题）。
 */

import { existsSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { tmpPoolDir } from '../core/paths'
import {
  type ContributionSplit,
  type ContributionView,
  type HubQueueView,
  buildContributionMatrix,
  buildPpoContribution,
  buildSamplingContribution,
} from '../web/view'
import { readLedgerTail } from './api/logs'
import {
  type HistoryAggregate,
  type NodeWindow,
  aggMemoReusable,
  projectCourseBreakdown,
  projectWindow,
} from './pool-history'

/** PPO 事件读的 memo 时间下限（与 `AGG_MEMO_MIN_MS` 同节奏）。 */
export const PPO_MEMO_MIN_MS = 30_000
/** 每份课程账本只读尾部这么多行（PPO 事件稀疏；有界内存，不追全史）。 */
const PPO_LEDGER_TAIL_LINES = 4000

/** 一条 PPO 归属事件（窗口投影的原始事实）。 */
export interface PpoEvent {
  course: string
  worker: string
  ms: number
  kind: 'done' | 'rejected'
}

export interface PpoAgg {
  events: PpoEvent[]
  /** 扫到的课程账本数（脚注）。 */
  ledgers: number
}

interface PpoMemo {
  fp: string
  computedAt: number
  val: PpoAgg
}
let ppoMemo: PpoMemo | null = null

/** 课程账本清单：`tmp/<课>/training_log.jsonl`（或 `<课>/traj/` 布局，二选一不双计）。 */
export function listCourseLedgers(): Array<{
  course: string
  path: string
  mtimeMs: number
  size: number
}> {
  const root = tmpPoolDir()
  const out: Array<{ course: string; path: string; mtimeMs: number; size: number }> = []
  let names: string[] = []
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return out
  }
  for (const name of names) {
    for (const rel of [name, `${name}/traj`]) {
      const p = join(root, rel, 'training_log.jsonl')
      try {
        if (!existsSync(p)) continue
        const st = statSync(p)
        out.push({ course: name, path: p, mtimeMs: st.mtimeMs, size: st.size })
        break
      } catch {
        /* 单课程 IO 错误不拖垮整表 */
      }
    }
  }
  return out
}

function eventMs(ts: unknown): number | null {
  return typeof ts === 'number' && Number.isFinite(ts) ? ts * 1000 : null
}

/** 读 PPO 归属事件（memo：指纹相同复用；指纹变了距上次计算不足 `minMs` 也复用）。 */
export function readPpoAttribution(nowMs: number = Date.now(), minMs = PPO_MEMO_MIN_MS): PpoAgg {
  const files = listCourseLedgers()
  const fp = files.map((f) => `${f.path}|${f.mtimeMs}|${f.size}`).join('\n')
  if (ppoMemo && aggMemoReusable(ppoMemo, fp, nowMs, minMs)) return ppoMemo.val

  const events: PpoEvent[] = []
  for (const f of files) {
    let lines: string[]
    try {
      lines = readLedgerTail(f.path, PPO_LEDGER_TAIL_LINES)
    } catch {
      continue
    }
    const accepted = new Map<string, string>()
    const done: Array<{ jid: string; ms: number }> = []
    for (const line of lines) {
      if (!line.trim()) continue
      let e: { event?: unknown; job_id?: unknown; worker?: unknown; ts?: unknown }
      try {
        e = JSON.parse(line) as typeof e
      } catch {
        continue
      }
      const jid = e.job_id
      if (typeof jid !== 'string') continue
      if (e.event === 'job_result_accepted') {
        accepted.set(jid, typeof e.worker === 'string' ? e.worker : '')
      } else if (e.event === 'job_completed') {
        const ms = eventMs(e.ts)
        if (ms != null) done.push({ jid, ms })
      } else if (e.event === 'job_rejected') {
        const ms = eventMs(e.ts)
        if (ms == null) continue
        events.push({
          course: f.course,
          worker: typeof e.worker === 'string' ? e.worker : '',
          ms,
          kind: 'rejected',
        })
      }
    }
    for (const d of done) {
      events.push({
        course: f.course,
        worker: accepted.get(d.jid) ?? '',
        ms: d.ms,
        kind: 'done',
      })
    }
  }
  const val: PpoAgg = { events, ledgers: files.length }
  ppoMemo = { fp, computedAt: nowMs, val }
  return val
}

/** 硬清 PPO memo（测试夹具；生产无需——指纹/时间下限自会失效）。 */
export function invalidatePpoAttributionMemo(): void {
  ppoMemo = null
}

/** hub `/admin/queue` 观测面 → 每 worker 的在飞数（空 worker 记 `(未登记)`，不编身份）。 */
export function inflightByWorkerFromQueue(
  queue: HubQueueView | null | undefined,
): Map<string, number> {
  const out = new Map<string, number>()
  if (!queue) return out
  for (const c of Object.values(queue.courses)) {
    const detail = Array.isArray(c.inflightDetail) ? c.inflightDetail : []
    for (const row of detail) {
      const w = row.worker || '(未登记)'
      out.set(w, (out.get(w) ?? 0) + 1)
    }
  }
  return out
}

/** 组装一屏贡献度（采样 ⊕ PPO ⊕ 课程矩阵）；`inflightByWorker` 来自 hub 5s SWR。 */
export function buildContributionView(
  agg: HistoryAggregate,
  w: NodeWindow,
  inflightByWorker: Map<string, number> = new Map(),
  nowMs: number = Date.now(),
): ContributionView {
  // 采样行：窗口投影（含 local；与节点表同源同一份窗口）。
  const proj = projectWindow(agg, w)
  const sampling = buildSamplingContribution(
    [...proj.hist.entries()].map(([id, h]) => ({
      id,
      split: { rollout: h.winRollout, eval: h.winEval, fail: h.fail },
      lastTs: h.lastTs === '-' ? '' : h.lastTs,
    })),
  )

  // PPO 行：事件按窗口过滤 → worker 聚合（含只在飞的 worker，缩略/面板都看得到）。
  const ppoAgg = readPpoAttribution(nowMs)
  const inWin = ppoAgg.events.filter((e) => e.ms >= w.startMs && e.ms <= w.endMs)
  const byWorker = new Map<string, { done: number; rejected: number; inflight: number }>()
  const touch = (worker: string): { done: number; rejected: number; inflight: number } => {
    let v = byWorker.get(worker)
    if (!v) {
      v = { done: 0, rejected: 0, inflight: 0 }
      byWorker.set(worker, v)
    }
    return v
  }
  const ppoByCourse: Record<string, Record<string, { done: number; rejected: number }>> = {}
  for (const e of inWin) {
    const v = touch(e.worker)
    if (e.kind === 'done') v.done++
    else v.rejected++
    const bc = (ppoByCourse[e.worker] ??= {})
    const cell = (bc[e.course] ??= { done: 0, rejected: 0 })
    if (e.kind === 'done') cell.done++
    else cell.rejected++
  }
  for (const [worker, n] of inflightByWorker) touch(worker).inflight += n
  const ppo = buildPpoContribution([...byWorker.entries()].map(([worker, v]) => ({ worker, ...v })))

  // 课程矩阵：采样半边走 byCourse/事件环投影；PPO 半边走本窗口事件。
  const samplingByCourse: Record<string, Record<string, ContributionSplit>> = {}
  for (const [node, byCourse] of projectCourseBreakdown(agg, w)) {
    samplingByCourse[node] = {}
    for (const [course, split] of byCourse) samplingByCourse[node]![course] = split
  }
  const matrix = buildContributionMatrix(samplingByCourse, ppoByCourse)

  return {
    sampling,
    ppo,
    matrix,
    samplingSources: {
      flows: agg.sources.length,
      truncated: agg.sources.some((s) => s.truncated === true),
      rollingTruncated: agg.rollingTruncated.size > 0,
    },
    ppoSources: { ledgers: ppoAgg.ledgers },
  }
}
