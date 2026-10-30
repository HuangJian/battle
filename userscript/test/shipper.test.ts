import { describe, expect, it } from 'bun:test'
import {
  createShipState,
  diffCell,
  shipOnce,
  type ShipInput,
  type ShipState,
} from '../src/notebook-companion/log-shipper'
import type { NotebookChunk, NotebookResponse } from '../src/notebook-companion/protocol'
import type { CellRef } from '../src/notebook-companion/sites/types'

const CELL: CellRef = { id: 'cell-a', index: 1, kind: 'code' }
const base = {
  site: 'kaggle' as const,
  session: 'kaggle-owner-rollout-run1',
  writer: 'w1',
  now: 1_790_000_000_000,
}

function recorder(reply?: (c: NotebookChunk) => { status: number; body: NotebookResponse | null }) {
  const sent: NotebookChunk[] = []
  const send = async (c: NotebookChunk) => {
    sent.push(c)
    return reply ? reply(c) : { status: 200, body: { ok: true as const, epoch: 0 } }
  }
  return { sent, send }
}

function run(
  state: ShipState,
  text: string,
  send: ShipInput['send'],
  extra: Partial<ShipInput> = {},
) {
  return shipOnce({ ...base, state, cells: [{ ref: CELL, text }], send, ...extra })
}

describe('diffCell：尾锚只扫 256 字符', () => {
  it('无游标 ⇒ reset 全文', () => {
    expect(diffCell('abc', undefined, 8, 4)).toMatchObject({ mode: 'reset', text: 'abc' })
  })

  it('尾锚命中 ⇒ 只发后缀', async () => {
    const state = createShipState()
    const { send } = recorder()
    await run(state, 'abc', send)
    const d = diffCell('abcdef', state.cursors.get(CELL.id), 8 * 1024 * 1024, 1024)
    expect(d).toMatchObject({ mode: 'append', text: 'def', offsetStart: 3 })
  })

  it('尾部改写 / 长度回退 ⇒ reset', async () => {
    const state = createShipState()
    const { send } = recorder()
    await run(state, 'abcdef', send)
    expect(diffCell('abcXef', state.cursors.get(CELL.id), 8 * 1024 * 1024, 1024).mode).toBe('reset')
    expect(diffCell('ab', state.cursors.get(CELL.id), 8 * 1024 * 1024, 1024).mode).toBe('reset')
  })

  it('积压超限 ⇒ 只留末尾 keepBytes + dropped 显式计数', () => {
    const state = createShipState()
    // 手工造一个已落盘 10 字符的游标
    state.cursors.set(CELL.id, { offset: 10, tail: '' })
    // 故意让尾锚不命中 ⇒ 走 reset；这里直接测 jump 分支需要尾锚命中，故手算 tail：
    const before = 'a'.repeat(10)
    const dom = before + 'b'.repeat(200)
    const { hashRange } = require('../src/utils') as typeof import('../src/utils')
    state.cursors.set(CELL.id, { offset: 10, tail: hashRange(before, 0, 10) })
    const d = diffCell(dom, state.cursors.get(CELL.id), 50, 20)
    expect(d.mode).toBe('reset')
    expect(d.text).toBe('b'.repeat(20))
    expect(d.dropped).toBeGreaterThan(0)
    expect(d.nextOffset).toBe(dom.length)
  })
})

describe('shipOnce：确认后才前进 / 协议分支', () => {
  it('首拍 reset，第二拍只发后缀（seq 递增、offset 连续）', async () => {
    const state = createShipState()
    const { sent, send } = recorder()
    await run(state, 'abc', send)
    await run(state, 'abcdef', send)
    expect(sent[0]).toMatchObject({ mode: 'reset', seq: 1, offsetStart: 0, text: 'abc' })
    expect(sent[1]).toMatchObject({ mode: 'append', seq: 2, offsetStart: 3, text: 'def' })
    expect(state.cursors.get(CELL.id)?.offset).toBe(6)
  })

  it('200 才前进；网络错误不前进、下一拍同 seq 重发', async () => {
    const state = createShipState()
    let fail = true
    const sent: NotebookChunk[] = []
    const send: ShipInput['send'] = async (c) => {
      sent.push(c)
      if (fail) throw new Error('network down')
      return { status: 200, body: { ok: true, epoch: 0 } }
    }
    const r1 = await run(state, 'abc', send)
    expect(r1.errors).toContain('network down')
    expect(state.cursors.get(CELL.id)).toBeUndefined()

    fail = false
    await run(state, 'abc', send)
    expect(sent[1]?.seq).toBe(sent[0]?.seq) // 同一块重发，seq 不跳
    expect(sent[1]).toMatchObject({ mode: 'reset', text: 'abc' })
    expect(state.cursors.get(CELL.id)?.offset).toBe(3)
  })

  it('409 offset-mismatch + tail 命中 ⇒ 续写（不重发历史）；tail 不中 ⇒ 下一拍 reset', async () => {
    const state = createShipState()
    const { sent, send } = recorder((c) =>
      c.seq === 1 && c.mode === 'reset'
        ? {
            status: 409,
            body: {
              ok: false,
              reason: 'offset-mismatch',
              epoch: 0,
              cell: { seq: 7, chars: 3, tail: 'abc' },
            },
          }
        : { status: 200, body: { ok: true, epoch: 0 } },
    )
    await run(state, 'abcdef', send)
    expect(state.cursors.get(CELL.id)).toEqual({ offset: 3, tail: expect.any(String) })
    expect(state.seqs.get(CELL.id)).toBe(7)
    await run(state, 'abcdef', send)
    expect(sent[1]).toMatchObject({ mode: 'append', seq: 8, offsetStart: 3, text: 'def' })
  })

  it('409 stale-epoch ⇒ 清游标、更新 epoch（下一条 reset）', async () => {
    const state = createShipState()
    const { sent, send } = recorder(() => ({
      status: 409,
      body: { ok: false, reason: 'stale-epoch', epoch: 4 },
    }))
    await run(state, 'abc', send)
    expect(state.epoch).toBe(4)
    expect(state.cursors.get(CELL.id)).toBeUndefined()
    expect(sent[0]?.mode).toBe('reset')
  })

  it('409 writer-conflict ⇒ 被动期，下一拍不发送', async () => {
    const state = createShipState()
    const { sent, send } = recorder(() => ({
      status: 409,
      body: { ok: false, reason: 'writer-conflict', epoch: 0, leaseRemainingMs: 5000 },
    }))
    const r1 = await run(state, 'abc', send)
    expect(r1.passive).toBe(true)
    expect(state.passiveUntil).toBeGreaterThan(base.now)
    const r2 = await run(state, 'abc', send)
    expect(r2.passive).toBe(true)
    expect(sent).toHaveLength(1)
  })

  it('413 ⇒ 永久停发该 cell（不再无限重试）', async () => {
    const state = createShipState()
    const { sent, send } = recorder(() => ({
      status: 413,
      body: { ok: false, reason: 'text too big', epoch: 0 },
    }))
    const r = await run(state, 'abc', send)
    expect(r.stopped).toEqual([CELL.id])
    expect(state.stopped.get(CELL.id)).toBe('text too big')
    await run(state, 'abc', send)
    expect(sent).toHaveLength(1)
  })

  it('每拍全局预算：预算 48 KiB 只发一块', async () => {
    const state = createShipState()
    const { sent, send } = recorder()
    const text = 'z'.repeat(100 * 1024)
    const r = await run(state, text, send, { budgetBytes: 48 * 1024 })
    expect(r.chunks).toBe(1)
    expect(sent).toHaveLength(1)
  })

  it('空 diff 不发送（offset 对齐到 dom 末尾）', async () => {
    const state = createShipState()
    const { sent, send } = recorder()
    await run(state, 'abc', send)
    await run(state, 'abc', send)
    expect(sent).toHaveLength(1)
  })
})
