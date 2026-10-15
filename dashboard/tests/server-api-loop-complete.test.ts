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

  // ───────── ★2026-10-15（plan/run-complete-cause-banner §3.3）：原因从哪来 ─────────

  it('★ 尾行带 cause/detail（新账本）⇒ 原样透传，且带上收工那一轮', () => {
    const v = api.loopCompleteFromLedgerTail([
      JSON.stringify({
        event: 'run_complete',
        iter: 239,
        iters: 500,
        time: '2026-10-15 17:21:00',
        reason: '正常收官（it239/500）：墙钟 12h 预算到顶，本地停采、云机已停机',
        cause: 'max_hours',
        detail: '墙钟 12h 预算到顶（从课程首启累计 19.5h）',
      }),
    ])
    expect(v).toMatchObject({
      cause: 'max_hours',
      detail: '墙钟 12h 预算到顶（从课程首启累计 19.5h）',
      it: 239,
      iters: 500,
    })
  })

  it('★ 枚举外的 cause ⇒ 当没有（不许把野字符串当成原因上屏）', () => {
    const v = api.loopCompleteFromLedgerTail([
      JSON.stringify({ event: 'run_complete', iter: 1, iters: 1, reason: 'x', cause: 'whatever' }),
    ])
    expect(v!.cause).toBeUndefined()
  })

  it('★ 老账本（尾行无 cause）+ 上一行是 STOP 判决 ⇒ 归到 max_hours 并**改写成说人话**', () => {
    // 现场形状（`tmp/y5-term` 2026-10-15 17:13）：gate_verdict 的 reason 是内部术语，
    // 直接上屏违反 plan §5 DoD ⇒ 认出来但重写；hours 从原文里取。
    const v = api.loopCompleteFromLedgerTail([
      JSON.stringify({
        event: 'gate_verdict',
        iter: 238,
        verdict: 'STOP',
        reason: 'max_hours 12.0h 到顶（轮级硬断）',
        decider: 'loop',
      }),
      JSON.stringify({
        event: 'run_complete',
        iter: 238,
        iters: 500,
        time: '2026-10-15 17:13:14',
        reason: '正常收官（it238/500），本地停采、云机已停机',
      }),
    ])
    expect(v!.cause).toBe('max_hours')
    expect(v!.detail).toContain('12h')
    expect(v!.detail).not.toContain('轮级硬断') // 内部术语不上屏
  })

  it('★ 上一行是 ABORT / 非判决 / 什么都没有 ⇒ 不带 cause（宁缺不编）', () => {
    const tail = complete('正常收官（it80/80），本地停采、云机已停机')
    expect(
      api.loopCompleteFromLedgerTail([
        JSON.stringify({ event: 'gate_verdict', verdict: 'ABORT', reason: 'circuit-break: kl' }),
        tail,
      ])!.cause,
    ).toBeUndefined()
    expect(
      api.loopCompleteFromLedgerTail([JSON.stringify({ event: 'iteration', iter: 80 }), tail])!
        .cause,
    ).toBeUndefined()
    expect(api.loopCompleteFromLedgerTail([tail])!.cause).toBeUndefined()
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
