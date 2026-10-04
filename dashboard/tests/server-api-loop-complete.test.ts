/**
 * server-api-loop-complete.test.ts — 账本尾行 run_complete → 停车态（2026-09-12 c5-ent it80 不再误报意外退出）；尾行 iteration / 空账本 → null
 *
 * 分层：src/server/api/loop-complete.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console/api.loopCompleteFromLedgerTail（正常完成停车横幅派生）', () => {
  const complete = (reason: string) =>
    JSON.stringify({
      event: 'run_complete',
      iter: 80,
      iters: 80,
      time: '2026-09-12 01:59:45',
      reason,
    })

  it('账本尾行 run_complete → 停车态（2026-09-12 c5-ent it80 不再误报意外退出）', () => {
    const v = api.loopCompleteFromLedgerTail([
      JSON.stringify({ event: 'iteration', iter: 80, time: '2026-09-12 01:59:45' }),
      complete('iters 跑满（it80/80），本地停采、云机已停机'),
    ])
    expect(v).not.toBeNull()
    expect(v!.iters).toBe(80)
    expect(v!.reason).toContain('本地停采')
  })

  it('尾行是 iteration（resume 后）→ null（横幅自动消失）', () => {
    expect(
      api.loopCompleteFromLedgerTail([
        complete('iters 跑满'),
        JSON.stringify({ event: 'run_start', time: '2026-09-12 03:00:00' }),
      ]),
    ).toBeNull()
  })

  it('空账本 / 尾行非 JSON → null（不误报）', () => {
    expect(api.loopCompleteFromLedgerTail([])).toBeNull()
    expect(api.loopCompleteFromLedgerTail(['[run_rl] boot...'])).toBeNull()
  })
})

describe('console/api.collectLoopCompletes（多课收官聚合；2026-10-03 plan/dashboard-banner-global §4.1）', () => {
  const tail = (at: string): string[] => [
    JSON.stringify({ event: 'run_complete', iter: 40, iters: 40, time: at, reason: 'iters 跑满' }),
  ]

  it('共享 trainer 不在跑 ⇒ 空表（没有「停车等待重启」这回事）', () => {
    expect(api.collectLoopCompletes(['b', 'a'], false, (c) => tail(`T-${c}`))).toEqual({})
  })

  it('在跑 ⇒ 逐课产出；课程名确定性排序（不依赖输入/对象键序）', () => {
    const tails: Record<string, string[]> = { b: tail('T2'), a: tail('T1') }
    const got = api.collectLoopCompletes(['b', 'a'], true, (c) => tails[c] ?? [])
    expect(Object.keys(got)).toEqual(['a', 'b'])
    expect(got.a!.at).toBe('T1')
    expect(got.b!.at).toBe('T2')
  })

  it('去重 + 空课程名跳过（查看课程可能是空串）', () => {
    const got = api.collectLoopCompletes(['a', 'a', ''], true, (c) => tail(`T-${c}`))
    expect(Object.keys(got)).toEqual(['a'])
  })

  it('单课读失败只少一门，不吞其它课（下一拍重试）', () => {
    const got = api.collectLoopCompletes(['bad', 'good'], true, (c) => {
      if (c === 'bad') throw new Error('IO')
      return tail('T')
    })
    expect(Object.keys(got)).toEqual(['good'])
  })

  it('尾行不是 run_complete ⇒ 该课不产条目（resume 后自动消失）', () => {
    const got = api.collectLoopCompletes(['a', 'b'], true, (c) =>
      c === 'a' ? tail('T1') : [JSON.stringify({ event: 'iteration', iter: 1 })],
    )
    expect(Object.keys(got)).toEqual(['a'])
  })
})
