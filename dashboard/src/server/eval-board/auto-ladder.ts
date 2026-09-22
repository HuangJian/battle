/** auto-ladder.ts — R4 自动爬梯：ticker、无状态进度推导、启停与逐课程状态。 */
import { loadBatches } from '../../evalboard/batches'
import {
  appendRequest,
  enqueueQueued,
  type EvalRequest,
  pendingRequests,
} from '../../evalboard/requests'
import { deriveMetrics } from '../../evalboard/stats'
import type { EvalGameRow } from '../../evalboard/store'
import { loadRowsCached } from './rows'
import type { EvalBoardView } from '../../web/view'
import { evalDataRoot, trackedLadder } from './ladder-data'
import { viewCache } from './view'

// ────────────────────────── R4 自动爬梯（console ticker + 无状态推导） ──────────────────────────

export interface LadderTask {
  course: string
  iter: number
  threshold: number
  startRung: string
  ckpt: string
  ts: string
}

function ladderKey(course: string, iter: number): string {
  return `${course}\u0001${iter}`
}

/** 活跃爬梯任务（请求文件推导：同 key 最后一行是 ladder_start 即活跃；重启不丢）。 */
export function activeLadderTasks(course = ''): LadderTask[] {
  const root = evalDataRoot()
  const byKey = new Map<string, EvalRequest[]>()
  for (const r of pendingRequests(root)) {
    if (r.kind !== 'ladder_start' && r.kind !== 'ladder_stop') continue
    if (course && r.course !== course) continue
    if (typeof r.iter !== 'number') continue
    const k = ladderKey(r.course ?? '', r.iter)
    const arr = byKey.get(k) ?? []
    arr.push(r)
    byKey.set(k, arr)
  }
  const out: LadderTask[] = []
  for (const arr of byKey.values()) {
    arr.sort((a, b) => (a.ts < b.ts ? -1 : 1))
    const last = arr[arr.length - 1]!
    if (last.kind !== 'ladder_start') continue
    out.push({
      course: last.course ?? '',
      iter: last.iter ?? 0,
      threshold: typeof last.threshold === 'number' ? last.threshold : 0.2,
      startRung: last.start_rung ?? 'c4l1',
      ckpt: last.ckpt ?? '',
      ts: last.ts,
    })
  }
  return out.sort((a, b) => (a.ts < b.ts ? -1 : 1))
}

/** POST /api/evalLadderStart（写请求文件；推进由 ticker 执行）。 */
export function startLadder(spec: {
  course: string
  iter: number
  threshold: number
  start_rung: string
  ckpt: string
  requester: string
}): { req_id: string; deduped: boolean } {
  const root = evalDataRoot()
  if (activeLadderTasks(spec.course).some((t) => t.iter === spec.iter)) {
    return { req_id: '', deduped: true }
  }
  const req = appendRequest(root, {
    kind: 'ladder_start',
    course: spec.course,
    iter: spec.iter,
    threshold: spec.threshold,
    start_rung: spec.start_rung,
    ckpt: spec.ckpt,
    requester: spec.requester || 'web',
  })
  return { req_id: req.req_id, deduped: false }
}

/** POST /api/evalLadderStop（写请求文件；ticker 见 stop 即停）。 */
export function stopLadder(spec: {
  course: string
  iter: number
  reason?: string
  requester?: string
}): { req_id: string } {
  const root = evalDataRoot()
  const req = appendRequest(root, {
    kind: 'ladder_stop',
    course: spec.course,
    iter: spec.iter,
    ...(spec.reason ? { reason: spec.reason } : {}),
    requester: spec.requester || 'web',
  })
  return { req_id: req.req_id }
}

export interface LadderProgress {
  reachedRung: string | null
  reachedWin: number | null
  nextRung: string | null
  stopped: boolean
  stoppedReason: string | null
  activeBatches: number
}

/**
 * (course,iter) 爬梯进度（无状态推导：done 批行按 batch_id 聚合胜率；
 * A4：阈值判定为筛查级，不作 verdict、不写门控）。
 */
export function ladderProgress(
  course: string,
  iter: number,
  threshold: number,
  allRows: EvalGameRow[],
  batches: ReturnType<typeof loadBatches>,
  rungOrder: string[],
): LadderProgress {
  const rel = batches.filter(
    (b) =>
      b.course === course &&
      b.iter === iter &&
      (b.policy ?? (b.ckpt === 'god' ? 'god' : 'nn')) === 'nn',
  )
  const activeBatches = rel.filter((b) => b.status === 'pending' || b.status === 'running').length
  const doneIds = new Set(rel.filter((b) => b.status === 'done').map((b) => b.batch_id))
  const byRung = new Map<string, EvalGameRow[]>()
  for (const x of allRows) {
    if (x.source !== 'B' || x.course !== course || x.iter !== iter) continue
    if (!doneIds.has(x.batch_id)) continue
    const arr = byRung.get(x.rung) ?? []
    arr.push(x)
    byRung.set(x.rung, arr)
  }
  let reachedIdx = -1
  let reachedWin: number | null = null
  for (const [rung, rs] of byRung) {
    const i = rungOrder.indexOf(rung)
    if (i > reachedIdx) {
      reachedIdx = i
      reachedWin = deriveMetrics(rs).winRate
    }
  }
  const reachedRung = reachedIdx >= 0 ? rungOrder[reachedIdx]! : null
  if (reachedRung === null) {
    return {
      reachedRung,
      reachedWin,
      nextRung: null,
      stopped: false,
      stoppedReason: null,
      activeBatches,
    }
  }
  if (reachedIdx >= rungOrder.length - 1) {
    return {
      reachedRung,
      reachedWin,
      nextRung: null,
      stopped: true,
      stoppedReason: '已到阶梯末关',
      activeBatches,
    }
  }
  if (reachedWin !== null && reachedWin < threshold && activeBatches === 0) {
    return {
      reachedRung,
      reachedWin,
      nextRung: rungOrder[reachedIdx + 1]!,
      stopped: true,
      stoppedReason:
        `筛查级：${reachedRung} 胜率 ${(reachedWin * 100).toFixed(1)}% < 阈值 ` +
        `${(threshold * 100).toFixed(0)}%（筛查级，不作 verdict）`,
      activeBatches,
    }
  }
  return {
    reachedRung,
    reachedWin,
    nextRung: rungOrder[reachedIdx + 1]!,
    stopped: false,
    stoppedReason: null,
    activeBatches,
  }
}

function ladderRungOrder(): string[] {
  try {
    return trackedLadder().map((r) => r.id)
  } catch {
    return []
  }
}

/**
 * 爬梯 tick（console 常驻，每 30s 一轮；A3）。
 * 每活跃任务至多入队一关；去重靠 pending 批 + 未物化请求（enqueueQueued）。
 */
export function ladderTick(courses: string[]): { tasks: number; enqueued: string[] } {
  const root = evalDataRoot()
  const enqueued: string[] = []
  let tasks = 0
  const order = ladderRungOrder()
  if (order.length === 0) return { tasks, enqueued }
  // 账本行是**根级**的：一次读全（`loadRowsCached` 未变动时零 IO / 零解析），再按课程切——
  // 此前每门课各 `loadRows(root)` 一次，等于把整本账读 N 遍（N 课的时间随课程数线性叠加）。
  const allRowsAll = loadRowsCached(root)
  for (const course of courses) {
    const allRows = allRowsAll.filter((r) => r.course === course)
    const batches = loadBatches(root).filter((b) => b.course === course)
    const reqs = pendingRequests(root)
    for (const t of activeLadderTasks(course)) {
      tasks += 1
      if (!t.ckpt) continue
      const prog = ladderProgress(course, t.iter, t.threshold, allRows, batches, order)
      if (prog.stopped) continue
      const target = prog.reachedRung === null ? t.startRung : prog.nextRung
      if (!target || !order.includes(target)) continue
      const rungBusy = batches.some(
        (b) =>
          b.iter === t.iter &&
          b.rung_from === target &&
          (b.status === 'pending' || b.status === 'running'),
      )
      if (rungBusy) continue
      if (enqueueQueued(batches, reqs, course, target, t.ckpt)) continue
      const req = appendRequest(root, {
        kind: 'enqueue',
        requester: 'ladder-ticker',
        course,
        rung_from: target,
        ckpt: t.ckpt,
        policy: 'nn',
        iter: t.iter,
        trigger: 'auto-ladder',
      })
      enqueued.push(req.req_id)
    }
  }
  if (enqueued.length > 0) invalidateEvalBoard()
  return { tasks, enqueued }
}

/** 无 ladder 请求时跳过重扫描（tick 高频调用的廉价前置）。 */
export function ladderTickAll(courses: string[]): { tasks: number; enqueued: string[] } {
  const hasLadder = pendingRequests(evalDataRoot()).some(
    (r) => r.kind === 'ladder_start' || r.kind === 'ladder_stop',
  )
  if (!hasLadder) return { tasks: 0, enqueued: [] }
  return ladderTick(courses)
}

/** 视图 ladderState（与 tick 同推导，读-only；无任务 ⇒ null）。 */
export function ladderStateFor(
  course: string,
  allRows: EvalGameRow[],
  batches: ReturnType<typeof loadBatches>,
): EvalBoardView['ladderState'] {
  if (!course) return null
  const rows = pendingRequests(evalDataRoot()).filter(
    (r) => (r.kind === 'ladder_start' || r.kind === 'ladder_stop') && r.course === course,
  )
  if (rows.length === 0) return null
  const byKey = new Map<string, EvalRequest[]>()
  for (const r of rows) {
    if (typeof r.iter !== 'number') continue
    const k = ladderKey(r.course ?? '', r.iter)
    const arr = byKey.get(k) ?? []
    arr.push(r)
    byKey.set(k, arr)
  }
  let latest: EvalRequest | null = null
  let latestStart: EvalRequest | null = null
  for (const arr of byKey.values()) {
    arr.sort((a, b) => (a.ts < b.ts ? -1 : 1))
    const last = arr[arr.length - 1]!
    if (!latest || last.ts > latest.ts) {
      latest = last
      latestStart = [...arr].reverse().find((r) => r.kind === 'ladder_start') ?? null
    }
  }
  if (!latest || typeof latest.iter !== 'number') return null
  const threshold =
    latest.kind === 'ladder_start' && typeof latest.threshold === 'number'
      ? latest.threshold
      : latestStart && typeof latestStart.threshold === 'number'
        ? latestStart.threshold
        : 0.2
  const prog = ladderProgress(course, latest.iter, threshold, allRows, batches, ladderRungOrder())
  if (latest.kind === 'ladder_stop') {
    return {
      course,
      iter: latest.iter,
      threshold,
      reachedRung: prog.reachedRung,
      stopped: true,
      stoppedReason: latest.reason ?? '已手动停止',
    }
  }
  return {
    course,
    iter: latest.iter,
    threshold,
    reachedRung: prog.reachedRung,
    stopped: prog.stopped,
    stoppedReason: prog.stoppedReason,
  }
}

export function invalidateEvalBoard(): void {
  viewCache.clear()
}
