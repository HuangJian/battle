/**
 * idle-stop.test.ts — 云机 worker 页「hub 不可达 / 没有待训练任务 ⇒ 停会话省配额」的判决。
 *
 * 口径（2026-11-13）：
 *   · 队列静默（`pending=0 ∧ inflight=0`）持续窗口 ⇒ `empty`；
 *   · 连续 N 拍问不到（dashboard/hub 不可达）⇒ `unreachable`；
 *   · 不可知 ≠ 空队列：`ok:false` / 坏载荷一律按「问不到」计拍，绝不按「没活」；
 *   · 在飞也算活（本机正在算的那份就是 inflight——少这一条会把自己的 job 掐掉）。
 */
import { describe, expect, it } from 'bun:test'
import {
  createIdleStop,
  fetchHubQueueFacts,
  IDLE_WINDOW_MS,
  parseHubQueueFacts,
  UNREACHABLE_ATTEMPTS,
  type HubQueueFacts,
  type IdleStopDue,
} from '../src/notebook-companion/app/idle-stop'
import type { Runtime } from '../src/runtime'

/** 可推进的假时钟。 */
function clock(start = 1_000_000): { now: () => number; add: (ms: number) => void } {
  let t = start
  return { now: () => t, add: (ms: number) => void (t += ms) }
}

function harness(
  facts: () => Promise<HubQueueFacts | null>,
  opts: { enabled?: boolean; autonomous?: boolean } = {},
) {
  const c = clock()
  const due: IdleStopDue[] = []
  const events: string[] = []
  let enabled = opts.enabled ?? true
  let autonomous = opts.autonomous ?? false
  const stop = createIdleStop({
    enabled: () => enabled,
    autonomous: () => autonomous,
    facts,
    onDue: (d) => due.push(d),
    onEvent: (op) => events.push(op),
    now: c.now,
  })
  return {
    stop,
    due,
    events,
    clock: c,
    enable(v: boolean) {
      enabled = v
    },
    setAutonomous(v: boolean) {
      autonomous = v
    },
  }
}

const open = async (): Promise<HubQueueFacts> => ({
  pending: 1,
  inflight: 0,
  pendingUnheld: 1,
  heldLive: 0,
})
const quiet = async (): Promise<HubQueueFacts> => ({
  pending: 0,
  inflight: 0,
  pendingUnheld: 0,
  heldLive: 0,
})
const broke = async (): Promise<HubQueueFacts | null> => null

describe('parseHubQueueFacts：不可知 ≠ 空队列', () => {
  it('ok:true + 四个数字 ⇒ 四个计数（负数/小数夹到非负整数）', () => {
    expect(
      parseHubQueueFacts({ ok: true, pending: 2, inflight: 1, pendingUnheld: 1, heldLive: 1 }),
    ).toEqual({ pending: 2, inflight: 1, pendingUnheld: 1, heldLive: 1 })
    expect(
      parseHubQueueFacts({
        ok: true,
        pending: -3,
        inflight: 1.7,
        pendingUnheld: -1,
        heldLive: 2.9,
      }),
    ).toEqual({ pending: 0, inflight: 1, pendingUnheld: 0, heldLive: 2 })
  })

  it('旧版 dashboard（没有新键）⇒ pendingUnheld 退化成全部 pending、heldLive=0', () => {
    expect(parseHubQueueFacts({ ok: true, pending: 4, inflight: 0 })).toEqual({
      pending: 4,
      inflight: 0,
      pendingUnheld: 4,
      heldLive: 0,
    })
  })

  it('新键在但值坏 ⇒ 整拍不可知（坏值不能当 0 停掉在训会话）', () => {
    expect(parseHubQueueFacts({ ok: true, pending: 0, inflight: 0, pendingUnheld: '0' })).toBeNull()
    expect(
      parseHubQueueFacts({ ok: true, pending: 0, inflight: 0, heldLive: Number.NaN }),
    ).toBeNull()
  })

  it('ok:false（hub 不可达）/ 缺字段 / 坏 JSON 形状 ⇒ null', () => {
    expect(parseHubQueueFacts({ ok: false, pending: 0, inflight: 0, reason: 'x' })).toBeNull()
    expect(parseHubQueueFacts({ pending: 0, inflight: 0 })).toBeNull()
    expect(parseHubQueueFacts({ ok: true, pending: '0', inflight: 0 })).toBeNull()
    expect(parseHubQueueFacts({ ok: true, pending: Number.NaN, inflight: 0 })).toBeNull()
    expect(parseHubQueueFacts(null)).toBeNull()
    expect(parseHubQueueFacts([1, 2])).toBeNull()
    expect(parseHubQueueFacts('ok')).toBeNull()
  })
})

describe('fetchHubQueueFacts：只问回环 dashboard', () => {
  const runtime = (impl: () => Promise<{ status: number; responseText: string }>) =>
    ({ gmRequest: impl }) as unknown as Pick<Runtime, 'gmRequest'>

  it('带自定义头问 /api/hubQueue；200 + ok:true ⇒ 计数', async () => {
    const seen: Array<{ url: string; headers?: Record<string, string> }> = []
    const facts = await fetchHubQueueFacts({
      dashboardBase: 'http://127.0.0.1:8900',
      runtime: runtime(async () => {
        seen.push({ url: 'x' })
        return { status: 200, responseText: '{"ok":true,"pending":4,"inflight":0}' }
      }),
    })
    expect(facts).toEqual({ pending: 4, inflight: 0, pendingUnheld: 4, heldLive: 0 })
    expect(seen).toHaveLength(1)
  })

  it('真的用了 dashboardBase + 自定义头（跨源网页发不了这个头）', async () => {
    let req: { url: string; headers?: Record<string, string> } | null = null
    await fetchHubQueueFacts({
      dashboardBase: 'http://localhost:8910',
      runtime: {
        gmRequest: async (details: { url: string; headers?: Record<string, string> }) => {
          req = details
          return { status: 200, responseText: '{"ok":true,"pending":0,"inflight":0}' }
        },
      } as unknown as Pick<Runtime, 'gmRequest'>,
    })
    expect(req).not.toBeNull()
    expect((req as { url: string } | null)?.url).toBe('http://localhost:8910/api/hubQueue')
    expect(
      (req as { headers?: Record<string, string> } | null)?.headers?.['X-Notebook-Companion'],
    ).toBe('1')
  })

  it('网络异常 / 非 200 / 坏 JSON / ok:false ⇒ null（这一拍不可知）', async () => {
    const cases = [
      async () => {
        throw new Error('offline')
      },
      async () => ({ status: 500, responseText: '' }),
      async () => ({ status: 200, responseText: 'not json' }),
      async () => ({ status: 200, responseText: '{"ok":false,"reason":"hub 不可达"}' }),
    ]
    for (const impl of cases) {
      expect(
        await fetchHubQueueFacts({
          dashboardBase: 'http://127.0.0.1:8900',
          runtime: runtime(impl),
        }),
      ).toBeNull()
    }
  })
})

describe('createIdleStop：判决', () => {
  it('开关关着 ⇒ 一拍都不问，返回 disabled', async () => {
    let asked = 0
    const h = harness(
      async () => {
        asked += 1
        return quiet()
      },
      { enabled: false },
    )
    expect(await h.stop.poll()).toBe('disabled')
    expect(asked).toBe(0)
    expect(h.due).toHaveLength(0)
  })

  it('有 pending ⇒ busy，永远不停（窗口过去了也不停）', async () => {
    const h = harness(open)
    expect(await h.stop.poll()).toBe('busy')
    h.clock.add(IDLE_WINDOW_MS * 5)
    expect(await h.stop.poll()).toBe('busy')
    expect(h.due).toHaveLength(0)
  })

  it('只有 inflight（本机正在算）也算活：不能把自己的 job 掐掉', async () => {
    const h = harness(async () => ({ pending: 0, inflight: 1, pendingUnheld: 0, heldLive: 0 }))
    expect(await h.stop.poll()).toBe('busy')
    h.clock.add(IDLE_WINDOW_MS * 2)
    expect(await h.stop.poll()).toBe('busy')
    expect(h.due).toHaveLength(0)
  })

  it('队列静默满窗口 ⇒ empty（只喊一次；reset 后重新武装）', async () => {
    const h = harness(quiet)
    expect(await h.stop.poll()).toBe('quiet') // 第一拍开始计窗
    expect(h.events).toContain('idleStop:quiet')
    h.clock.add(IDLE_WINDOW_MS - 1)
    expect(await h.stop.poll()).toBe('quiet')
    h.clock.add(1)
    expect(await h.stop.poll()).toBe('due')
    expect(h.due).toHaveLength(1)
    expect(h.due[0]?.kind).toBe('empty')
    expect(h.due[0]?.detail).toContain('可领=0')
    // 再拍也不会重复喊
    expect(await h.stop.poll()).toBe('due')
    expect(h.due).toHaveLength(1)
    h.stop.reset()
    expect(await h.stop.poll()).toBe('quiet')
  })

  it('自主盘：整课在跑时只有 live hold（队列静默）也算活', async () => {
    const h = harness(async () => ({ pending: 0, inflight: 0, pendingUnheld: 0, heldLive: 1 }), {
      autonomous: true,
    })
    expect(await h.stop.poll()).toBe('busy')
    h.clock.add(IDLE_WINDOW_MS * 2)
    expect(await h.stop.poll()).toBe('busy')
    expect(h.due).toHaveLength(0)
  })

  it('协作盘：别的盘 hold 住的课不算自己的活（但自主盘要算）', async () => {
    const facts = async () => ({ pending: 3, inflight: 0, pendingUnheld: 0, heldLive: 1 })
    const collab = harness(facts, { autonomous: false })
    expect(await collab.stop.poll()).toBe('quiet')
    collab.clock.add(IDLE_WINDOW_MS)
    expect(await collab.stop.poll()).toBe('due')

    // 同一份读数换成自主盘：live hold 就是自己正在跑课，不能停
    const auto = harness(facts, { autonomous: true })
    expect(await auto.stop.poll()).toBe('busy')
    auto.setAutonomous(false)
    expect(await auto.stop.poll()).toBe('quiet')
  })

  it('未被 hold 压住的 pending 才算可领（协作盘）', async () => {
    let pendingUnheld = 0
    const h = harness(async () => ({
      pending: 3,
      inflight: 0,
      pendingUnheld,
      heldLive: 1,
    }))
    expect(await h.stop.poll()).toBe('quiet')
    h.clock.add(IDLE_WINDOW_MS)
    expect(await h.stop.poll()).toBe('due')
    h.stop.reset()
    pendingUnheld = 1
    expect(await h.stop.poll()).toBe('busy')
  })

  it('从中断处续窗：一有活就清零（两次静默段不累加）', async () => {
    let facts: HubQueueFacts = { pending: 0, inflight: 0, pendingUnheld: 0, heldLive: 0 }
    const h = harness(async () => facts)
    await h.stop.poll()
    h.clock.add(IDLE_WINDOW_MS / 2)
    facts = { pending: 1, inflight: 0, pendingUnheld: 1, heldLive: 0 }
    expect(await h.stop.poll()).toBe('busy')
    facts = { pending: 0, inflight: 0, pendingUnheld: 0, heldLive: 0 }
    expect(await h.stop.poll()).toBe('quiet')
    h.clock.add(IDLE_WINDOW_MS - 1)
    expect(await h.stop.poll()).toBe('quiet')
    h.clock.add(1)
    expect(await h.stop.poll()).toBe('due')
  })

  it('连续问不到到阈值 ⇒ unreachable（中间问到一次就清零）', async () => {
    let mode: 'quiet' | 'broke' = 'broke'
    const h = harness(async () => (mode === 'broke' ? null : quiet()))
    expect(await h.stop.poll()).toBe('miss')
    expect(await h.stop.poll()).toBe('miss')
    expect(h.due).toHaveLength(0)
    mode = 'quiet'
    expect(await h.stop.poll()).toBe('quiet')
    expect(h.stop.state().misses).toBe(0)
    mode = 'broke'
    for (let i = 1; i <= UNREACHABLE_ATTEMPTS; i += 1) await h.stop.poll()
    expect(h.due).toHaveLength(1)
    expect(h.due[0]?.kind).toBe('unreachable')
    expect(h.due[0]?.detail).toContain(`${UNREACHABLE_ATTEMPTS} 拍`)
  })

  it('中途关闭开关 ⇒ 计数与判决全部清零（不带着旧窗口再打开）', async () => {
    const h = harness(broke)
    await h.stop.poll()
    await h.stop.poll()
    h.enable(false)
    expect(await h.stop.poll()).toBe('disabled')
    expect(h.stop.state().misses).toBe(0)
    h.enable(true)
    expect(await h.stop.poll()).toBe('miss')
    expect(h.due).toHaveLength(0)
  })

  it('读数：last 是最近一拍的合计，问不到时为 null', async () => {
    let facts: HubQueueFacts | null = { pending: 3, inflight: 2, pendingUnheld: 1, heldLive: 2 }
    const h = harness(async () => facts)
    await h.stop.poll()
    expect(h.stop.state().last).toEqual({ pending: 3, inflight: 2, pendingUnheld: 1, heldLive: 2 })
    facts = null
    await h.stop.poll()
    expect(h.stop.state().last).toBeNull()
  })
})
