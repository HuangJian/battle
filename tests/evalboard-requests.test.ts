/** evalboard-requests.test.ts ↔ tools/training/evalboard/requests.ts（§5.3 请求文件协议）。 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import type { EvalBatch } from '../tools/training/evalboard/batches'
import {
  appendRequest,
  enqueueCovered,
  enqueueQueued,
  pendingRequests,
  readDoneReqIds,
  readRequests,
} from '../tools/training/evalboard/requests'

const batch = (over: Partial<EvalBatch> = {}): EvalBatch => ({
  batch_id: 'b-1',
  course: 'c',
  rung_from: 'c4l1',
  ckpt: 'w',
  requester: 'web',
  created_ts: '2026-09-11T10:00:00.000Z',
  status: 'pending',
  units: { of: 2, done: [] },
  k_seq: 0,
  window_seq: 0,
  trigger: 'standalone',
  iter: 30,
  node_dist: {},
  elapsed_sec: null,
  ...over,
})

describe('requests.jsonl append/read', () => {
  it('追加入队 → 读回；坏行跳过；req_id/ts 自动补', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evalreq-'))
    try {
      const r = appendRequest(dir, {
        kind: 'enqueue',
        requester: 'web',
        course: 'c',
        rung_from: 'c4l1',
        ckpt: 'w',
        policy: 'nn',
        iter: 30,
      })
      expect(r.req_id.startsWith('q-')).toBe(true)
      expect(r.ts.length).toBeGreaterThan(10)
      writeFileSync(path.join(dir, 'requests.jsonl'), '{bad json\n', { flag: 'a' })
      const back = readRequests(dir)
      expect(back.length).toBe(1)
      expect(back[0]?.course).toBe('c')
      expect(pendingRequests(dir).length).toBe(1)
      expect(readDoneReqIds(dir).size).toBe(0)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
  it('done 标记过滤 pending（ladder_* 永不标记，始终可见）', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evalreq-'))
    try {
      const a = appendRequest(dir, { kind: 'abort', requester: 'web', batch_id: 'b-1' })
      appendRequest(dir, { kind: 'ladder_start', requester: 'web', course: 'c', iter: 1 })
      writeFileSync(
        path.join(dir, 'requests.done.jsonl'),
        `${JSON.stringify({ req_id: a.req_id })}\n`,
      )
      const pend = pendingRequests(dir)
      expect(pend.length).toBe(1)
      expect(pend[0]?.kind).toBe('ladder_start')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('enqueueCovered / enqueueQueued（无状态去重）', () => {
  const req = {
    req_id: 'q-1',
    kind: 'enqueue' as const,
    ts: '2026-09-11T10:00:00.000Z',
    requester: 'web',
    course: 'c',
    rung_from: 'c4l1',
    ckpt: 'w',
    iter: 30 as number | undefined,
  }
  it('pending 同 key 批 ⇒ queued；非 enqueue 请求永不覆盖', () => {
    expect(enqueueQueued([batch()], [req], 'c', 'c4l1', 'w')).toBe(true)
    expect(enqueueCovered([batch()], { ...req, kind: 'abort' })).toBe(false)
  })
  it('批 done 且 created_ts >= req.ts ⇒ covered（已物化），queued=false（可重跑须发新请求）', () => {
    const done = batch({ status: 'done', created_ts: '2026-09-11T11:00:00.000Z' })
    expect(enqueueCovered([done], req)).toBe(true)
    expect(enqueueQueued([done], [req], 'c', 'c4l1', 'w')).toBe(false)
  })
  it('未物化请求（批是旧的）⇒ queued=true', () => {
    const old = batch({ status: 'done', created_ts: '2026-09-10T10:00:00.000Z' })
    const fresh = { ...req, req_id: 'q-2', ts: '2026-09-11T12:00:00.000Z' }
    expect(enqueueCovered([old], fresh)).toBe(false)
    expect(enqueueQueued([old], [fresh], 'c', 'c4l1', 'w')).toBe(true)
  })
  it('空台账 + 无请求 ⇒ 不在队列', () => {
    expect(enqueueQueued([], [], 'c', 'c4l1', 'w')).toBe(false)
  })
})
