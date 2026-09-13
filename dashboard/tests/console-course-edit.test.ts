import { describe, expect, it } from 'bun:test'
import { courseEditFromLedgerTail } from '../src/server/api'

describe('courseEditFromLedgerTail', () => {
  it('窗口内最近一条 course_edit 胜出（跨后续 iteration 事件持久）', () => {
    const lines = [
      JSON.stringify({ event: 'course_edit', verdict: 'applied', fields: ['iters'], it: 5 }),
      JSON.stringify({ event: 'iteration', iter: 6, winRate: 0.6 }),
      JSON.stringify({ event: 'course_edit', verdict: 'rejected', fields: ['reward'], it: 7 }),
      JSON.stringify({ event: 'iteration', iter: 7, winRate: 0.6 }),
      JSON.stringify({ event: 'iteration', iter: 8, winRate: 0.6 }),
    ]
    const r = courseEditFromLedgerTail(lines)
    expect(r?.verdict).toBe('rejected')
    expect(r?.fields).toEqual(['reward'])
    expect(r?.it).toBe(7)
  })

  it('restored 顶掉 rejected（改回文件后横幅自然消失）', () => {
    const lines = [
      JSON.stringify({ event: 'course_edit', verdict: 'rejected', fields: ['reward'], it: 3 }),
      JSON.stringify({ event: 'iteration', iter: 4 }),
      JSON.stringify({ event: 'course_edit', verdict: 'restored', it: 5 }),
    ]
    expect(courseEditFromLedgerTail(lines)?.verdict).toBe('restored')
  })

  it('无 course_edit 事件 / 空账本 → null', () => {
    expect(courseEditFromLedgerTail([])).toBeNull()
    expect(courseEditFromLedgerTail([JSON.stringify({ event: 'iteration', iter: 1 })])).toBeNull()
  })

  it('写半行竞态（尾行非 JSON）跳过继续向前找，不吞事件', () => {
    const lines = [
      JSON.stringify({ event: 'course_edit', verdict: 'rejected', fields: ['reward'], it: 2 }),
      '{"event": "iteration", "iter": 3', // 半行
    ]
    expect(courseEditFromLedgerTail(lines)?.verdict).toBe('rejected')
  })

  it('非法 verdict → null（防御性）', () => {
    expect(
      courseEditFromLedgerTail([JSON.stringify({ event: 'course_edit', verdict: '??' })]),
    ).toBeNull()
  })
})
