/** requests.ts — console → runner 请求文件（plan/evalboard-console-ux.md §5.3，P1）。
 *
 * console 是 `requests.jsonl` 的唯一写者（append-only）；runner
 *（`nn-training/rl/batch_eval.py:consume_requests`）是唯一消费方，
 *消费标记 append 到 `requests.done.jsonl`。**任何一方都不重写
 * `requests.jsonl` 本体** ⇒ D-c 双写者竞态按设计消除。
 * runner 侧是同协议的 Python 镜像实现，改一侧必须同步另一侧。
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'fs'
import path from 'path'
import type { EvalBatch } from './batches'

export type RequestKind = 'enqueue' | 'abort' | 'ladder_start' | 'ladder_stop'

export interface EvalRequest {
  req_id: string
  kind: RequestKind
  ts: string
  requester: string
  course?: string
  rung_from?: string
  ckpt?: string
  policy?: 'nn' | 'god'
  iter?: number
  trigger?: 'main' | 'standalone' | 'auto-ladder'
  batch_id?: string
  threshold?: number
  start_rung?: string
  reason?: string
  /** 透传给 runner 建批的可选字段（与 batches.ts EvalBatch 对齐）。 */
  ladder_pos?: number
  k_seq?: number
  init_sha16?: string
  only_rungs?: string[]
}

export function requestsPath(dataRoot: string): string {
  return path.join(dataRoot, 'requests.jsonl')
}

export function requestsDonePath(dataRoot: string): string {
  return path.join(dataRoot, 'requests.done.jsonl')
}

/** 读全部请求（坏行跳过；无 kind 的行不是请求）。 */
export function readRequests(dataRoot: string): EvalRequest[] {
  const p = requestsPath(dataRoot)
  if (!existsSync(p)) return []
  const out: EvalRequest[] = []
  for (const line of readFileSync(p, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as EvalRequest
      if (r && typeof r.kind === 'string') out.push(r)
    } catch {
      /* 坏行跳过 */
    }
  }
  return out
}

/** 已消费请求 id 集（缺失即空集）。 */
export function readDoneReqIds(dataRoot: string): Set<string> {
  const p = requestsDonePath(dataRoot)
  if (!existsSync(p)) return new Set()
  const out = new Set<string>()
  for (const line of readFileSync(p, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as { req_id?: unknown }
      if (r && typeof r.req_id === 'string') out.add(r.req_id)
    } catch {
      /* 坏行跳过 */
    }
  }
  return out
}

function reqKey(r: EvalRequest): string {
  return typeof r.req_id === 'string' && r.req_id ? r.req_id : JSON.stringify(r)
}

/** 未被消费的请求（ladder_* 永不标记，始终可见——console ticker 持有）。 */
export function pendingRequests(dataRoot: string): EvalRequest[] {
  const done = readDoneReqIds(dataRoot)
  return readRequests(dataRoot).filter((r) => !done.has(reqKey(r)))
}

/**
 * 追加入队（console 写请求的唯一入口）。req_id = `q-<紧凑ts>-<rand4>`，
 * ts 缺省 now（ISO）。返回落盘行。
 */
export function appendRequest(
  dataRoot: string,
  spec: Omit<EvalRequest, 'req_id' | 'ts'> & Partial<Pick<EvalRequest, 'ts'>>,
): EvalRequest {
  mkdirSync(dataRoot, { recursive: true })
  const ts = spec.ts ?? new Date().toISOString()
  const req = {
    ...spec,
    req_id: `q-${ts.replace(/[-:.]/g, '').slice(0, 15)}-${Math.floor(Math.random() * 0xffff)
      .toString(16)
      .padStart(4, '0')}`,
    ts,
  } as EvalRequest
  appendFileSync(requestsPath(dataRoot), `${JSON.stringify(req)}\n`, 'utf-8')
  return req
}

interface EnqKey {
  course: string
  rung_from: string
  ckpt: string
}

function sameEnqKey(key: EnqKey): (b: EnqKey) => boolean {
  return (b) => b.course === key.course && b.rung_from === key.rung_from && b.ckpt === key.ckpt
}

function reqEnqKey(r: EvalRequest): EnqKey | null {
  if (!r.course || !r.rung_from || !r.ckpt) return null
  return { course: r.course, rung_from: r.rung_from, ckpt: r.ckpt }
}

/**
 * enqueue 是否已被"覆盖"（ticker/去重用，无状态推导）：
 * 同 key 已有批且 created_ts >= req.ts（已物化）即覆盖。
 */
export function enqueueCovered(batches: EvalBatch[], req: EvalRequest): boolean {
  if (req.kind !== 'enqueue') return false
  const key = reqEnqKey(req)
  if (!key) return false
  const reqTs = req.ts ?? ''
  const same = sameEnqKey(key)
  return batches.some((b) => same(b) && b.created_ts >= reqTs)
}

/**
 * enqueue 是否"仍在排队"（去重谓词）：同 key 有 pending 批，
 * 或有未物化的同 key enqueue 请求。批已 done 后的重跑不在此列。
 */
export function enqueueQueued(
  batches: EvalBatch[],
  reqs: EvalRequest[],
  course: string,
  rungFrom: string,
  ckpt: string,
): boolean {
  const same = sameEnqKey({ course, rung_from: rungFrom, ckpt })
  if (batches.some((b) => b.status === 'pending' && same(b))) return true
  return reqs.some((r) => {
    if (r.kind !== 'enqueue') return false
    const rk = reqEnqKey(r)
    return rk !== null && same(rk) && !enqueueCovered(batches, r)
  })
}
