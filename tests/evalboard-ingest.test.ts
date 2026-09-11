/** evalboard-ingest.test.ts ↔ tools/training/evalboard/ingest.ts + batches.ts。 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { ingestEvalRow, ingestRows, type IngestCtx } from '../tools/training/evalboard/ingest'
import {
  claimPending,
  enqueueBatch,
  loadBatches,
  updateBatch,
} from '../tools/training/evalboard/batches'

const ctx: IngestCtx = {
  run_id: 'run1',
  course: 'c4-margin',
  batch_id: 'b-1',
  batch_of: 2,
  rungOfStage: (s) => (s === 0 ? 'c4l1' : 's1l3b1'),
  engine: { git_commit: 'g', dist_codehash: 'd', engine_epoch: 'e' },
  source: 'A',
  ckpt_path: 'w',
  ckpt_sha16: 'c'.repeat(16),
  init_sha16: 'i'.repeat(16),
  difficulty: 'hard',
  maxTicks: 2400,
}

describe('ingestEvalRow', () => {
  it('raw → schema v1（probe_key 五段式，seed_segment，wver 透传）', () => {
    const r = ingestEvalRow(
      { iter: 30, wver: 'w1', stage: 0, seed: 860001, outcome: 'stage_clear', win: 1, cleared: 1 },
      ctx,
    )
    expect(r.schema).toBe(1)
    expect(r.probe_key).toContain('c4l1-hard-t2400-')
    expect(r.probe_key.endsWith('-eval860k')).toBe(true)
    expect(r.seed_segment).toBe(0)
    expect(r.greedy).toBe(true)
    expect(r.metrics_version).toBe(1)
  })
})

describe('ingestRows 幂等', () => {
  it('同键重入不重复', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evalingest-'))
    try {
      const raws = [
        { iter: 1, wver: 'w1', stage: 0, seed: 860001, win: 1, outcome: 'stage_clear' },
        { iter: 1, wver: 'w1', stage: 0, seed: 860002, win: 0, outcome: 'gameover' },
      ]
      expect(ingestRows(dir, raws, ctx)).toEqual({ appended: 2, duplicate: 0 })
      expect(ingestRows(dir, raws, ctx)).toEqual({ appended: 0, duplicate: 2 })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('A11 god 身份透传（DoD#5：账本 batch_id 真实，非 A-<course>）', () => {
  // 生产 ctx（console/evalboard.ts ingestCourseEvalLog）：A 行回退值。
  const aCtx: IngestCtx = { ...ctx, batch_id: 'A-c4-margin', source: 'A' }
  const base = { iter: 1, wver: 'w1', stage: 0, seed: 860001, win: 1, outcome: 'stage_clear' }
  it('C 行保留真实 source/batch_id/rung（不被 A 回退覆盖）', () => {
    const r = ingestEvalRow(
      { ...base, policy: 'god', source: 'C', batch_id: 'b-god123', rung: 'c4l1' },
      aCtx,
    )
    expect(r.source).toBe('C')
    expect(r.batch_id).toBe('b-god123')
    expect(r.rung).toBe('c4l1')
  })
  it('B 行保留真实 source/batch_id/rung', () => {
    const r = ingestEvalRow(
      { ...base, policy: 'nn', source: 'B', batch_id: 'b-nn456', rung: 'c6l1' },
      aCtx,
    )
    expect(r.source).toBe('B')
    expect(r.batch_id).toBe('b-nn456')
    expect(r.rung).toBe('c6l1')
  })
  it('A 行（无自带身份）回退 ctx', () => {
    const r = ingestEvalRow({ ...base, policy: 'nn' }, aCtx)
    expect(r.source).toBe('A')
    expect(r.batch_id).toBe('A-c4-margin')
  })
})

describe('batches 队列 (§3.7/§6.7)', () => {
  it('入队 → 认领 → 更新；重复入队去重', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evalbatch-'))
    try {
      const b = enqueueBatch(dir, {
        course: 'c',
        rung_from: 'c4l1',
        ckpt: 'w1',
        requester: 'web',
        trigger: 'standalone',
        iter: 30,
      })
      expect(b.status).toBe('pending')
      const dup = enqueueBatch(dir, {
        course: 'c',
        rung_from: 'c4l1',
        ckpt: 'w1',
        requester: 'web',
        trigger: 'standalone',
        iter: 30,
      })
      expect(dup.batch_id).toBe(b.batch_id)
      expect(loadBatches(dir).length).toBe(1)
      const claimed = claimPending(dir)
      expect(claimed?.batch_id).toBe(b.batch_id)
      expect(claimed?.status).toBe('running')
      const upd = updateBatch(dir, b.batch_id, { status: 'done', elapsed_sec: 61 })
      expect(upd?.status).toBe('done')
      expect(claimPending(dir)).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
