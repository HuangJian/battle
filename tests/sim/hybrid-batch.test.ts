import { describe, it, expect } from 'bun:test'
import { TailRaceBatch } from '../../tools/lib/hybrid-batch'
import { BatchLedger, ledgerKey } from '../../tools/lib/batch-ledger'
import { rmSync, existsSync, readFileSync } from 'node:fs'

describe('TailRaceBatch（rollout v3.7 调度语义的 TS 提取）', () => {
  it('游标模式：claim 顺序发号 0..n-1', () => {
    const b = new TailRaceBatch(5)
    expect([b.claim(false), b.claim(false), b.claim(false)]).toEqual([0, 1, 2])
  })

  it('幂等结算：重复 settle 返回 false，done 只计一次', () => {
    const b = new TailRaceBatch(3)
    const i = b.claim(false)
    expect(b.settle(i)).toBe(true)
    expect(b.settle(i)).toBe(false)
    expect(b.done).toBe(1)
  })

  it('竞速进入：游标耗尽且 remaining ≤ fanoutN → 给未结算任务加副本（每任务 ≤ dup）', () => {
    const b = new TailRaceBatch(6, 4, 2)
    for (let k = 0; k < 6; k++) b.claim(false) // 游标耗尽，每个任务已有 1 个在跑副本
    b.settle(0)
    b.settle(1)
    b.settle(2) // remaining = 3 ≤ 4
    // 竞速：无主副本在跑 → -1（复制无意义）
    expect(b.claim(false)).toBe(-1)
    // 有在跑副本 → 给最小未结算任务加副本（3: 1→2 副本），加满轮到下一个
    expect(b.claim(true)).toBe(3)
    expect(b.claim(true)).toBe(4)
    expect(b.claim(true)).toBe(5)
    expect(b.claim(true)).toBe(-1) // 全部未结算任务都到 dup 上限
    // 结算 3 释放其副本计数 → 可再复制
    b.settle(3)
    expect(b.claim(true)).toBe(-1) // 4/5 仍满；3 已结算不可再竞速
    b.settle(4)
    b.settle(5)
    expect(b.claim(true)).toBe(-1) // remaining = 0 ⇒ 无任务可发
    expect(b.done).toBe(6)
  })

  it('竞速不进入：remaining > fanoutN → -1', () => {
    const b = new TailRaceBatch(10, 4, 2)
    for (let k = 0; k < 10; k++) b.claim(false)
    b.settle(0)
    expect(b.claim(true)).toBe(-1) // remaining = 9 > 4
  })

  it('竞速副本结算后释放；先到者胜（幂等）', () => {
    const b = new TailRaceBatch(4, 2, 2)
    for (let k = 0; k < 4; k++) b.claim(false)
    b.settle(0)
    b.settle(1)
    // remaining = 2 ≤ 2 → 竞速
    const a = b.claim(true)
    const c = b.claim(true)
    expect(a).toBe(2)
    expect([2, 3]).toContain(c)
    expect(b.settle(a)).toBe(true)
    expect(b.settle(a)).toBe(false) // 后到副本被弃
    expect(b.done).toBe(3)
  })

  it('failUnsettled：全部未结算标败收尾', () => {
    const b = new TailRaceBatch(5)
    b.claim(false)
    b.settle(0)
    const failed = b.failUnsettled()
    expect(failed).toEqual([1, 2, 3, 4])
    expect(b.done).toBe(5)
  })
})

describe('BatchLedger（断点续跑账本）', () => {
  const path = 'tmp/test-ledger.jsonl'

  it('写入-加载往返：ok 行计入，wver 不匹配与失败行不计入', () => {
    rmSync(path, { force: true })
    const l1 = new BatchLedger(path, 'aaa')
    l1.append({
      wver: 'aaa',
      stage: 0,
      seed: 1,
      ok: true,
      outcome: 'stage_clear',
      ticks: 100,
      killCount: 5,
      baseAlive: true,
    })
    l1.append({
      wver: 'aaa',
      stage: 0,
      seed: 2,
      ok: false,
      outcome: 'error',
      ticks: 0,
      killCount: 0,
      baseAlive: false,
    })
    l1.append({
      wver: 'bbb',
      stage: 0,
      seed: 3,
      ok: true,
      outcome: 'stage_clear',
      ticks: 50,
      killCount: 2,
      baseAlive: true,
    })
    const l2 = new BatchLedger(path, 'aaa')
    const done = l2.loadDone()
    expect(done.size).toBe(1)
    expect(done.get(ledgerKey(0, 1))?.killCount).toBe(5)
    expect(done.has(ledgerKey(0, 2))).toBe(false) // 失败行不算完成
    expect(done.has(ledgerKey(0, 3))).toBe(false) // wver 不匹配
  })

  it('错误重跑：追加的 ok 行覆盖先前的失败行', () => {
    rmSync(path, { force: true })
    const l1 = new BatchLedger(path, 'aaa')
    l1.append({
      wver: 'aaa',
      stage: 1,
      seed: 1,
      ok: false,
      outcome: 'error',
      ticks: 0,
      killCount: 0,
      baseAlive: false,
    })
    l1.append({
      wver: 'aaa',
      stage: 1,
      seed: 1,
      ok: true,
      outcome: 'stage_clear',
      ticks: 90,
      killCount: 3,
      baseAlive: true,
    })
    const l2 = new BatchLedger(path, 'aaa')
    const done = l2.loadDone()
    expect(done.get(ledgerKey(1, 1))?.outcome).toBe('stage_clear')
    const raw = readFileSync(path, 'utf8').trim().split('\n')
    expect(raw.length).toBe(2) // 追加不覆盖，审计留痕
  })

  it('无账本文件时 loadDone 返回空', () => {
    rmSync(path, { force: true })
    expect(existsSync(path)).toBe(false)
    const l = new BatchLedger(path, 'aaa')
    expect(l.loadDone().size).toBe(0)
  })
})
