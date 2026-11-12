/**
 * ledger-bc-pointer.test.ts — BC 课的进度指针（侧栏「最近训练」/ 顶部 pill 的读数词）。
 *
 * 用户口径（2026-11-12）：「bc 课程应该显示 epXXX，而非 itXXX」——BC 的轮 = 一张 job，
 * `it` 在整段（几十个 epoch）里恒为 1，读作进度等于没有。指针取账本 `bc_epoch` 的最大 epoch，
 * 与面板曲线**同一个段锚**（`job_pending` 优先、退回 `run_start`），否则两处会各报一个数。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { bcPointerFromLedgerTail, buildBcEpochsView } from '../src/server/api/ledger'

const L = (event: string, o: Record<string, unknown> = {}): string =>
  JSON.stringify({ event, ...o })

describe('bcPointerFromLedgerTail：BC 课的 ep 指针', () => {
  it('取段内最大 epoch + 最近 run_start 的总 epoch', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { iters: 1, epochs: 60 }),
      L('bc_epoch', { it: 1, epoch: 1 }),
      L('bc_epoch', { it: 1, epoch: 2 }),
      L('bc_epoch', { it: 1, epoch: 3 }),
    ])
    expect(p).toEqual({ epoch: 3, total: 60 })
  })

  it('重启（多一条 run_start、job 未变）⇒ 指针继续前进（不归零到重启后的段）', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'abc', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 57 }),
      L('run_start', { epochs: 60 }),
      L('bc_epoch', { it: 1, epoch: 58 }),
    ])
    expect(p.epoch).toBe(58)
  })

  it('换 job（新的 job_pending）⇒ 只算新段的 epoch（旧段的 57 不算这一轮的进度）', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'old', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 57 }),
      L('job_pending', { job_id: 'new', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 1 }),
    ])
    expect(p).toEqual({ epoch: 1, total: 60 })
  })

  it('还没有 epoch 行（job 刚起）⇒ epoch=null（不编 0），总 epoch 照给', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'abc', it: 1 }),
    ])
    expect(p).toEqual({ epoch: null, total: 60 })
  })

  it('旧账本没有 run_start 的 epochs ⇒ total=null；坏行/空行不致命', () => {
    const p = bcPointerFromLedgerTail([
      '',
      'not json',
      L('bc_epoch', { it: 1, epoch: 4 }),
      L('bc_epoch', { it: 1, epoch: 0 }),
    ])
    expect(p).toEqual({ epoch: 4, total: null })
  })

  it('完成事件抬到 epochs_completed（早停 24/60，不是课程上限 60）', () => {
    const rows = Array.from({ length: 24 }, (_, i) => L('bc_epoch', { it: 1, epoch: i + 1 }))
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      ...rows,
      L('bc_round_completed', { it: 1, epochs: 60, epochs_completed: 24 }),
    ])
    expect(p).toEqual({ epoch: 24, total: 60 })
  })

  it('完成事件 60、指标只到 59 ⇒ pointer=60 且 metricsEpoch=59', () => {
    const rows = Array.from({ length: 59 }, (_, i) => L('bc_epoch', { it: 1, epoch: i + 1 }))
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      ...rows,
      L('bc_round_completed', { it: 1, epochs: 60, epochs_completed: 60 }),
    ])
    expect(p).toEqual({ epoch: 60, total: 60, metricsEpoch: 59 })
  })

  it('完成事件与指标同高 ⇒ 不写 metricsEpoch（旧行为逐字节不变）', () => {
    const rows = Array.from({ length: 60 }, (_, i) => L('bc_epoch', { it: 1, epoch: i + 1 }))
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      ...rows,
      L('bc_round_completed', { it: 1, epochs: 60, epochs_completed: 60 }),
    ])
    expect(p).toEqual({ epoch: 60, total: 60 })
    expect('metricsEpoch' in p).toBe(false)
  })

  it('完成事件 60、没有任何指标行 ⇒ metricsEpoch=null（不是 0）', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      L('bc_round_completed', { it: 1, epochs: 60, epochs_completed: 60 }),
    ])
    expect(p).toEqual({ epoch: 60, total: 60, metricsEpoch: null })
  })

  it('旧完成事件没有 epochs_completed/epochs ⇒ 仍停在 bc_epoch 最大值', () => {
    const p = bcPointerFromLedgerTail([
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      L('bc_epoch', { it: 1, epoch: 24 }),
      L('bc_round_completed', { it: 1 }),
    ])
    expect(p).toEqual({ epoch: 24, total: 60 })
  })

  it('空账本 ⇒ 两个都不可知（不编 0）', () => {
    expect(bcPointerFromLedgerTail([])).toEqual({ epoch: null, total: null })
  })
})

// 面板表头（`ep57/60`）与侧栏/pill 的 `ep57` 必须同源：同一个函数、同一份尾行。
describe('buildBcEpochsView.pointer：BC 面板表头的进度指针', () => {
  const oldTmp = process.env.BCITY_TMP_LOGS_DIR
  afterEach(() => {
    if (oldTmp === undefined) delete process.env.BCITY_TMP_LOGS_DIR
    else process.env.BCITY_TMP_LOGS_DIR = oldTmp
  })

  it('返回段内最大 epoch + 总 epoch（表头 `ep57/60` 的来源）', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'tc-bc-ptr-'))
    mkdirSync(path.join(root, 'bc-ptr'), { recursive: true })
    writeFileSync(
      path.join(root, 'bc-ptr', 'training_log.jsonl'),
      [
        L('run_start', { iters: 1, epochs: 60 }),
        L('job_pending', { job_id: 'j', it: 1 }),
        L('bc_epoch', { it: 1, epoch: 57 }),
      ].join('\n'),
      'utf8',
    )
    process.env.BCITY_TMP_LOGS_DIR = root
    const v = await buildBcEpochsView('bc-ptr')
    expect(v.pointer).toEqual({ epoch: 57, total: 60 })
  })

  it('150 epoch 课只渲染最后 100 行，但缺号按完整行算（不误报 ep1..50）', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'tc-bc-long-'))
    mkdirSync(path.join(root, 'bc-long'), { recursive: true })
    const lines = [
      L('run_start', { iters: 1, epochs: 150 }),
      L('job_pending', { job_id: 'long', it: 1 }),
      ...Array.from({ length: 150 }, (_, i) => L('bc_epoch', { it: 1, epoch: i + 1 })),
    ]
    writeFileSync(path.join(root, 'bc-long', 'training_log.jsonl'), lines.join('\n'), 'utf8')
    process.env.BCITY_TMP_LOGS_DIR = root
    const v = await buildBcEpochsView('bc-long')
    expect(v.epochs).toHaveLength(100)
    expect(v.epochs[0]!.epoch).toBe(51)
    expect(v.pointer).toEqual({ epoch: 150, total: 150 })
    expect(v.missing).toEqual([]) // 实际存在的 ep1..50 不得被报缺
  })

  it('g1e 形状：完成 60、指标缺 25/27/60 ⇒ 服务端按完整行给出缺号', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'tc-bc-g1e-'))
    mkdirSync(path.join(root, 'bc-g1e'), { recursive: true })
    const rows = Array.from({ length: 59 }, (_, i) => i + 1).filter((e) => e !== 25 && e !== 27)
    const lines = [
      L('run_start', { epochs: 60 }),
      L('job_pending', { job_id: 'j', it: 1 }),
      ...rows.map((epoch) => L('bc_epoch', { it: 1, epoch })),
      L('bc_round_completed', { it: 1, epochs: 60, epochs_completed: 60 }),
    ]
    writeFileSync(path.join(root, 'bc-g1e', 'training_log.jsonl'), lines.join('\n'), 'utf8')
    process.env.BCITY_TMP_LOGS_DIR = root
    const v = await buildBcEpochsView('bc-g1e')
    expect(v.pointer).toEqual({ epoch: 60, total: 60, metricsEpoch: 59 })
    expect(v.missing).toEqual([25, 27, 60])
  })

  it('无课程 ⇒ 指针不可知（不编 0）', async () => {
    const v = await buildBcEpochsView(undefined)
    expect(v.pointer).toEqual({ epoch: null, total: null })
  })
})
