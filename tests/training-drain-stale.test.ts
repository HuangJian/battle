import { afterEach, beforeEach, describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { drainStaleJobs } from '../tools/training/hub'

/**
 * 回归锚点（2026-09-10）：drainStaleJobs 曾硬编码 `unlinkSync('payload.zip')`。
 * payload 容器自 d183997 起改为 payload.tar.xz（protocol.PAYLOAD_NAME）⇒ unlinkSync
 * 恒抛 → 被 `catch { /* already gone *\/ }` 吞掉 → n 恒 0 → **陈旧 pending job 永远
 * 下架不掉**，真 Kaggle worker 会白烧 GPU 租约去领它们（正是该函数存在的理由；
 * hub_server.claimable_job_ids 以 find_payload 的存在性判定可领）。
 *
 * 本组钉住三条不变量：① 新旧两代容器名都能下架；② 非 payload 文件不动；
 * ③ 已 job_completed 的 job 不动。
 */
describe('drainStaleJobs：陈旧 pending job 下架', () => {
  let root: string
  let ledger: string

  const mkJob = (jid: string, files: string[]): string => {
    const d = join(root, jid)
    mkdirSync(d, { recursive: true })
    for (const f of files) writeFileSync(join(d, f), 'x')
    return d
  }
  const setLedger = (...events: [string, string][]): void => {
    writeFileSync(
      ledger,
      events.map(([event, job_id]) => JSON.stringify({ event, job_id })).join('\n') + '\n',
    )
  }

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'drain-stale-'))
    ledger = join(root, '_ledger.jsonl')
  })
  afterEach(() => {
    rmSync(root, { recursive: true, force: true })
  })

  it('新容器名 payload.tar.xz 被下架（本次修复的直接锚点）', () => {
    const d = mkJob('j-new', ['payload.tar.xz', 'manifest.json'])
    setLedger(['job_pending', 'j-new'])
    drainStaleJobs(root, ledger)
    expect(existsSync(join(d, 'payload.tar.xz'))).toBe(false)
    expect(existsSync(join(d, 'manifest.json'))).toBe(true) // 非 payload 文件不动
  })

  it('旧容器名 payload.zip 同样被下架（与 legacy 兼容期一致）', () => {
    const d = mkJob('j-old', ['payload.zip'])
    setLedger(['job_pending', 'j-old'])
    drainStaleJobs(root, ledger)
    expect(existsSync(join(d, 'payload.zip'))).toBe(false)
  })

  it('两代名字并存时都被下架', () => {
    const d = mkJob('j-both', ['payload.tar.xz', 'payload.zip'])
    setLedger(['job_pending', 'j-both'])
    drainStaleJobs(root, ledger)
    expect(existsSync(join(d, 'payload.tar.xz'))).toBe(false)
    expect(existsSync(join(d, 'payload.zip'))).toBe(false)
  })

  it('已 job_completed 的 job 不动（不得误伤在跑/已完成任务）', () => {
    const d = mkJob('j-done', ['payload.tar.xz'])
    setLedger(['job_pending', 'j-done'], ['job_completed', 'j-done'])
    drainStaleJobs(root, ledger)
    expect(existsSync(join(d, 'payload.tar.xz'))).toBe(true)
  })

  it('job 目录缺失 / 无 payload / 坏账本行 均不抛', () => {
    mkJob('j-empty', ['manifest.json'])
    writeFileSync(
      ledger,
      '{ not json\n' + JSON.stringify({ event: 'job_pending', job_id: 'j-missing' }) + '\n',
    )
    expect(() => drainStaleJobs(root, ledger)).not.toThrow()
    writeFileSync(ledger, '')
    expect(() => drainStaleJobs(root, ledger)).not.toThrow()
    expect(() => drainStaleJobs(root, join(root, 'nope.jsonl'))).not.toThrow()
  })
})
