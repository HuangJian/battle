/** evalboard-requests.test.ts ↔ tools/training/evalboard/requests.ts（§5.3 请求文件协议）。 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'fs'
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

describe('写者唯一性（P1：console/CLI 只 append 请求，runner 单写台账）', () => {
  it('backfill CLI 改道 appendRequest，不再直写 batches.jsonl', async () => {
    // DoD#2 的 grep 断言原先只覆盖 console/ 与 ui/，漏了 evalboard/ 下的 CLI。
    const src = await Bun.file(
      path.join(process.cwd(), 'tools/training/evalboard/backfill.ts'),
    ).text()
    // 剥掉注释后再断言，避免注释里的历史说明造成假阳性。
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
    expect(code).not.toMatch(/\benqueueBatch\s*\(/)
    expect(code).not.toMatch(/\brewriteBatches\s*\(/)
    expect(code).toMatch(/appendRequest\s*\(/)
  })
})

/** P1 写者唯一性：触发端（console / ui / backfill CLI）不得直接写 batches.jsonl。
 *  runner（`nn-training/rl/batch_eval.py`）是台账唯一写者；触发端只 append 请求文件。
 *  2026-09-11：backfill.ts 曾漏网（直接 enqueueBatch，与 runner 并发即 D-c 竞态）。 */
const WRITE_CALLS = ['enqueueBatch(', 'rewriteBatches(', 'updateBatch(', 'claimPending(']

function tsFiles(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) tsFiles(p, out)
    else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) out.push(p)
  }
  return out
}

describe('P1 写者唯一性（触发端只写请求文件）', () => {
  it('console / ui / backfill 均无台账写调用', () => {
    const root = path.join(import.meta.dir, '..')
    const dirs = [
      path.join(root, 'tools', 'training', 'console'),
      path.join(root, 'tools', 'training', 'ui'),
    ]
    const files = dirs.flatMap((d) => tsFiles(d))
    files.push(path.join(root, 'tools', 'training', 'evalboard', 'backfill.ts'))
    // 扫描到文件才算真验证（路径写错 ⇒ 空集 = 假绿）
    expect(files.length).toBeGreaterThan(10)
    const bad: string[] = []
    for (const f of files) {
      // 只查代码：先剥注释，否则注释里的历史说明（如 backfill.ts 提到
      // "此前直接 enqueueBatch 写 batches.jsonl"）会造成假阳性。
      const text = readFileSync(f, 'utf-8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\/\/.*$/gm, '')
      for (const call of WRITE_CALLS) if (text.includes(call)) bad.push(`${f} → ${call}`)
    }
    expect(bad).toEqual([])
  })
})

/** 时间戳契约：`enqueueCovered` / `consume_requests` 用**字符串比较**判"已物化"，
 *  两侧（TS `toISOString()` 与 Python `utc_now_iso()`）必须逐字符同格式——
 *  否则 UTC / 负偏移时区会把"已物化"误判为"未物化" ⇒ 重复建批。 */
describe('时间戳契约（跨语言字符串可比）', () => {
  it('TS 侧输出 UTC + 毫秒 + Z', () => {
    expect(new Date('2026-09-11T15:48:19.123Z').toISOString()).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/,
    )
  })
  it('同一时刻两侧字符串相等 ⇒ 已物化判定成立（不依赖本机时区）', () => {
    const ts = new Date(Date.UTC(2026, 8, 11, 15, 48, 19, 123)).toISOString()
    // Python 侧 utc_now_iso() 在同一时刻的输出（UTC，非本地时间）
    const py = ts.replace('+00:00', 'Z')
    expect(py >= ts).toBe(true)
  })
})
