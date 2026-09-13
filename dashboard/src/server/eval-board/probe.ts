/** probe.ts — 探针批次入队与中止（单次/批量评估任务）。 */
import { loadBatches } from '../../evalboard/batches'
import { appendRequest, enqueueQueued, pendingRequests } from '../../evalboard/requests'
import { evalDataRoot } from './ladder-data'

export function enqueueProbeRun(spec: {
  course: string
  rung_from: string
  ckpt: string
  requester: string
  iter: number
  policy?: 'nn' | 'god'
  ladder_pos?: number
  k_seq?: number
  init_sha16?: string
}): { req_id: string; deduped: boolean } {
  const root = evalDataRoot()
  const batches = loadBatches(root)
  const reqs = pendingRequests(root)
  if (enqueueQueued(batches, reqs, spec.course, spec.rung_from, spec.ckpt)) {
    const prior =
      reqs.find(
        (r) =>
          r.kind === 'enqueue' &&
          r.course === spec.course &&
          r.rung_from === spec.rung_from &&
          r.ckpt === spec.ckpt,
      )?.req_id ?? ''
    return { req_id: prior, deduped: true }
  }
  const req = appendRequest(root, {
    kind: 'enqueue',
    course: spec.course,
    rung_from: spec.rung_from,
    ckpt: spec.ckpt,
    requester: spec.requester || 'web',
    trigger: 'standalone',
    iter: spec.iter,
    ...(spec.policy ? { policy: spec.policy } : {}),
    ...(spec.ladder_pos !== undefined ? { ladder_pos: spec.ladder_pos } : {}),
    ...(spec.k_seq !== undefined ? { k_seq: spec.k_seq } : {}),
    ...(spec.init_sha16 ? { init_sha16: spec.init_sha16 } : {}),
  })
  return { req_id: req.req_id, deduped: false }
}

/** POST /api/evalBatchAbort 温和中止（P4：只写 abort 请求，runner 消费后标 aborted）。 */
export function abortEvalBatch(
  batchId: string,
  requester: string,
): { req_id: string; deduped: boolean } {
  const root = evalDataRoot()
  const b = loadBatches(root).find((x) => x.batch_id === batchId)
  if (!b) throw new Error(`未知批次 ${batchId}`)
  if (b.status !== 'pending' && b.status !== 'running') {
    throw new Error(`批次已结束（${b.status}），无需中止`)
  }
  const dup = pendingRequests(root).find((r) => r.kind === 'abort' && r.batch_id === batchId)
  if (dup?.req_id) return { req_id: dup.req_id, deduped: true }
  const req = appendRequest(root, { kind: 'abort', requester, batch_id: batchId })
  return { req_id: req.req_id, deduped: false }
}
