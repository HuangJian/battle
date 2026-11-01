import { describe, expect, it } from 'bun:test'
import {
  ADMISSION_QUEUE_MAX,
  ADMISSION_WAIT_MS,
  createAdmission,
  type AdmissionClock,
} from '../../tools/agent/admission-queue'

/**
 * 节点准入排队的判据（2026-11-01：`docs/nn/runtime-opt.md` §39 的第一刀）。
 *
 * 旧行为（池满立即 503）的形状在用例 1（`waitMs=0`）里**原样保留**；其余几条钉住新行为：
 * 池满 ⇒ 排队等空槽；队列满 / 等到超时 / 客户端断开 ⇒ 照旧 busy（协调器的瞬断回队语义不变）。
 *
 * 时间靠手动推进：`sleep` 在这类判据里只会让用例变慢且不稳。
 */
function manualClock(): {
  clock: AdmissionClock
  advance: (ms: number) => void
  pending: () => number
} {
  let now = 0
  let seq = 0
  const timers: { at: number; id: number; fn: () => void }[] = []
  return {
    clock: {
      schedule(ms, fn) {
        const t = { at: now + ms, id: ++seq, fn }
        timers.push(t)
        return () => {
          const i = timers.findIndex((x) => x.id === t.id)
          if (i >= 0) timers.splice(i, 1)
        }
      },
    },
    advance(ms) {
      now += ms
      for (const t of timers.filter((x) => x.at <= now).sort((a, b) => a.at - b.at)) {
        const i = timers.findIndex((x) => x.id === t.id)
        if (i < 0) continue
        timers.splice(i, 1)
        t.fn()
      }
    },
    pending: () => timers.length,
  }
}

describe('准入排队：池满时等空槽，而不是立刻 503', () => {
  it('空槽直接进；`waitMs=0` 回到旧行为（池满立即 busy，不留定时器）', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 1, waitMs: 0, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')
    expect(gate.active()).toBe(1)
    // 旧行为回退档：池满 ⇒ 立刻 busy，且不排任何定时器
    expect(await gate.acquire()).toBe('busy')
    expect(gate.waiting()).toBe(0)
    expect(c.pending()).toBe(0)
    expect(gate.counters()).toMatchObject({ admitted: 1, rejected: 1, timedOut: 0 })
  })

  it('池满 ⇒ 排队；release 唤醒队首（FIFO），未轮到的人继续等', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 1, waitMs: 1000, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')

    const order: string[] = []
    const p2 = gate.acquire().then((r) => order.push(`p2:${r}`))
    const p3 = gate.acquire().then((r) => order.push(`p3:${r}`))
    expect(gate.waiting()).toBe(2)
    expect(gate.active()).toBe(1)

    gate.release()
    await p2
    expect(order).toEqual(['p2:admitted'])
    expect(gate.active()).toBe(1)
    expect(gate.waiting()).toBe(1)

    gate.release()
    await p3
    expect(order).toEqual(['p2:admitted', 'p3:admitted'])
    expect(gate.active()).toBe(1)
    expect(gate.waiting()).toBe(0)
  })

  it('等到 waitMs 仍无空槽 ⇒ busy，且醒来后不会被迟到的 release 补进', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 1, waitMs: 50, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')
    const p = gate.acquire()
    c.advance(49)
    expect(gate.waiting()).toBe(1)
    c.advance(1)
    expect(await p).toBe('busy')
    expect(gate.waiting()).toBe(0)
    expect(gate.counters()).toMatchObject({ admitted: 1, timedOut: 1 })

    // 迟到的 release：槽位归还，但**不能**把已超时的人补成 admitted
    gate.release()
    expect(gate.active()).toBe(0)
    expect(gate.counters().admitted).toBe(1)
    expect(c.pending()).toBe(0)
  })

  it('队列满 ⇒ 立即 busy（不排队、不计超时）', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 1, queueMax: 1, waitMs: 1000, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')
    const queued = gate.acquire()
    expect(gate.waiting()).toBe(1)
    expect(await gate.acquire()).toBe('busy')
    expect(gate.counters()).toMatchObject({ queueFull: 1, timedOut: 0 })
    gate.release()
    expect(await queued).toBe('admitted')
  })

  it('客户端断开（abort）⇒ 摘出队列并 busy；不会等到超时也不占队位', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 1, waitMs: 1000, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')
    const ac = new AbortController()
    const p = gate.acquire(ac.signal)
    expect(gate.waiting()).toBe(1)
    ac.abort()
    expect(await p).toBe('busy')
    expect(gate.waiting()).toBe(0)
    expect(c.pending()).toBe(0)
    expect(gate.counters()).toMatchObject({ rejected: 1, timedOut: 0 })
  })

  it('不超发：容量 2、5 个排队者 ⇒ 任意时刻 active ≤ 2，且全部按 FIFO 进', async () => {
    const c = manualClock()
    const gate = createAdmission({ capacity: () => 2, waitMs: 1000, clock: c.clock })
    expect(await gate.acquire()).toBe('admitted')
    expect(await gate.acquire()).toBe('admitted')

    const admitted: number[] = []
    const pending = Array.from({ length: 5 }, (_, i) =>
      gate.acquire().then((r) => {
        if (r === 'admitted') {
          admitted.push(i)
          expect(gate.active()).toBeLessThanOrEqual(2)
        }
        return r
      }),
    )
    expect(gate.waiting()).toBe(5)
    expect(gate.counters().peakWaiting).toBe(5)

    for (let i = 0; i < 5; i++) {
      gate.release()
      await Promise.resolve()
    }
    const results = await Promise.all(pending)
    expect(results.every((r) => r === 'admitted')).toBe(true)
    expect(admitted).toEqual([0, 1, 2, 3, 4])
    expect(gate.active()).toBe(2)
    expect(c.pending()).toBe(0)
  })

  it('缺省参数是「等 12s / 队列 32」（回退档由 --admission-wait-ms 0 给出）', () => {
    expect(ADMISSION_WAIT_MS).toBe(12_000)
    expect(ADMISSION_QUEUE_MAX).toBe(32)
  })
})
