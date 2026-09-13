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
