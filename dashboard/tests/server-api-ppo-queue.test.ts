/**
 * server-api-ppo-queue.test.ts — PPO 队列 >5min 无 worker 领取 → warning；账本 job_cancelled / job_completed 与空目录不误报
 *
 * 分层：src/server/api/ppo-queue.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import os from 'os'
import path from 'path'
import { afterAll, describe, expect, it } from 'bun:test'
import { api } from './helpers/console-fixture'
import { mkdtempSync, mkdirSync, rmSync, utimesSync, writeFileSync } from 'fs'

describe('detectPpoQueueStall（PPO 队列 >5min 无 worker 领取 → warning）', () => {
  const traj = mkdtempSync(path.join(os.tmpdir(), 'bcity-ppo-stall-'))
  const jobRoot = path.join(traj, 'remote-jobs')
  mkdirSync(jobRoot, { recursive: true })
  const logPath = path.join(traj, 'training_log.jsonl')

  afterAll(() => {
    rmSync(traj, { recursive: true, force: true })
  })

  const mkJob = (
    id: string,
    opts: { claimed?: boolean; result?: boolean; payload?: boolean; it?: number },
  ) => {
    const jd = path.join(jobRoot, id)
    mkdirSync(jd, { recursive: true })
    if (opts.payload !== false) writeFileSync(path.join(jd, 'payload.tar.xz'), 'x')
    writeFileSync(path.join(jd, 'manifest.json'), JSON.stringify({ it: opts.it ?? 7 }))
    if (opts.claimed) writeFileSync(path.join(jd, 'claimed'), '1')
    if (opts.result) mkdirSync(path.join(jd, 'result'), { recursive: true })
    return jd
  }

  const writeLedger = (events: Array<Record<string, unknown>>) => {
    writeFileSync(logPath, events.map((e) => JSON.stringify(e)).join('\n') + '\n')
  }

  const ageDir = (dir: string, ageMs: number) => {
    const t = (Date.now() - ageMs) / 1000
    utimesSync(dir, t, t)
  }

  it('无 result / 无 claimed 且超过 5min → 报警；含 claimed / result / 未超时 → null', () => {
    const now = Date.now()
    const stalled = mkJob('job-stalled', {})
    ageDir(stalled, 6 * 60_000)
    const claimed = mkJob('job-claimed', { claimed: true })
    ageDir(claimed, 10 * 60_000)
    const done = mkJob('job-done', { result: true })
    ageDir(done, 10 * 60_000)
    const fresh = mkJob('job-fresh', {})
    ageDir(fresh, 30_000)
    // 无账本 → 磁盘口径
    rmSync(logPath, { force: true })

    const hit = api.detectPpoQueueStall(jobRoot, now)
    expect(hit).not.toBeNull()
    expect(hit!.jobId).toBe('job-stalled')
    expect(hit!.it).toBe(7)
    expect(hit!.waitedSec).toBeGreaterThanOrEqual(5 * 60)

    // 只剩 claimed/done/fresh → 不报警
    rmSync(stalled, { recursive: true, force: true })
    expect(api.detectPpoQueueStall(jobRoot, now)).toBeNull()
    rmSync(claimed, { recursive: true, force: true })
    rmSync(done, { recursive: true, force: true })
    rmSync(fresh, { recursive: true, force: true })
  })

  it('账本 job_cancelled / job_completed：目录仍在盘也不告警（it30 悬空 job 回归）', () => {
    const now = Date.now()
    // 故障遗留：payload 在、无 claimed/result，但已 cancel_stale 作废
    const orphan = mkJob('job-orphan-cancelled', { it: 30 })
    ageDir(orphan, 20 * 60_000)
    const doneDir = mkJob('job-done-still-dir', { it: 29 })
    ageDir(doneDir, 20 * 60_000)
    const live = mkJob('job-live-stalled', { it: 32 })
    ageDir(live, 6 * 60_000)

    writeLedger([
      { event: 'job_pending', job_id: 'job-orphan-cancelled', it: 30, ts: 1 },
      { event: 'job_cancelled', job_id: 'job-orphan-cancelled', it: 30, ts: 2 },
      { event: 'job_pending', job_id: 'job-done-still-dir', it: 29, ts: 3 },
      { event: 'job_completed', job_id: 'job-done-still-dir', ts: 4 },
      { event: 'job_pending', job_id: 'job-live-stalled', it: 32, ts: 5 },
    ])

    const hit = api.detectPpoQueueStall(jobRoot, now)
    expect(hit).not.toBeNull()
    expect(hit!.jobId).toBe('job-live-stalled')
    expect(hit!.it).toBe(32)
    expect(hit!.jobId).not.toBe('job-orphan-cancelled')
  })

  it('空目录 / 不存在 → null', () => {
    expect(api.detectPpoQueueStall(path.join(jobRoot, 'nope'))).toBeNull()
  })
})
