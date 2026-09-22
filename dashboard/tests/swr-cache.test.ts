/**
 * swr-cache.test.ts — 单条目「陈旧先用、后台重算」缓存原语（2026-09-22）
 *
 * 分层：src/core/swr-cache.ts
 *
 * 为什么值得单独钉住：控制台里最贵的几笔探测（节点 ping 1.5s、共享 hub 1.2s）靠它
 * 跨课程共用 + 永不在请求路径上阻塞；语义改了（比如「陈旧也阻塞」或「作废后仍回填
 * 旧值」）不会让任何页面报错，只会让切课重新变慢、或让动作的结果静默不生效——
 * 两类都无法从界面直接看出来的退化。故每条语义一个用例。
 */

import { describe, expect, it } from 'bun:test'
import { createSwrCache } from '../src/core/swr-cache'

/** 可控时钟（不用 sleep；每条用例自带一条时间轴）。 */
function clock(start = 0): { now: () => number; advance: (ms: number) => void } {
  let t = start
  return { now: () => t, advance: (ms) => (t += ms) }
}

/** 把微任务队列冲干净（后台重算的 `.then` 落库走的是微任务）。 */
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0))

/** 手动放行的 promise（验「陈旧先回、重算在后台」的时序）。 */
function held<T>(): { promise: Promise<T>; release: (v: T) => void } {
  let release!: (v: T) => void
  const promise = new Promise<T>((r) => {
    release = r
  })
  return { promise, release }
}

describe('createSwrCache（陈旧先用 + 后台重算）', () => {
  it('新鲜窗口内直接给缓存值：不再调 compute', async () => {
    const c = clock()
    const cache = createSwrCache<number>(5000, c.now)
    let calls = 0
    const compute = async (): Promise<number> => ++calls
    expect(await cache.get(compute)).toBe(1)
    c.advance(4999)
    expect(await cache.get(compute)).toBe(1)
    expect(calls).toBe(1)
    expect(cache.peek()).toBe(1)
  })

  it('陈旧（超窗口）**先给旧值**、重算丢后台；落地后下一次 get 才换新值', async () => {
    const c = clock()
    const cache = createSwrCache<string>(5000, c.now)
    let calls = 0
    const second = held<string>()
    const compute = (): Promise<string> => {
      calls++
      return calls === 1 ? Promise.resolve('v1') : second.promise
    }
    expect(await cache.get(compute)).toBe('v1')
    c.advance(5000) // 恰好到期 = 陈旧
    // 关键：这一次**不等**后台重算（否则请求被探测时长压住 = 用户报障的那个「几秒」）
    expect(await cache.get(compute)).toBe('v1')
    expect(calls).toBe(2) // 后台那一次已经起跑
    second.release('v2')
    await flush()
    expect(await cache.get(compute)).toBe('v2')
    expect(calls).toBe(2) // 新值落地后不再重算
  })

  it('陈旧且重算已在飞：并发 get 共享同一次重算、不再叠加探测', async () => {
    const c = clock()
    const cache = createSwrCache<number>(1000, c.now)
    let calls = 0
    const second = held<number>()
    const compute = (): Promise<number> => {
      calls++
      return calls === 1 ? Promise.resolve(1) : second.promise
    }
    expect(await cache.get(compute)).toBe(1)
    c.advance(1000)
    expect(await cache.get(compute)).toBe(1) // 起跑第 2 次
    expect(await cache.get(compute)).toBe(1) // 命中在飞 → 仍给旧值，不新起
    expect(await cache.get(compute)).toBe(1)
    expect(calls).toBe(2)
    second.release(9)
    await flush()
    expect(await cache.get(compute)).toBe(9)
  })

  it('冷启动（无旧值）：第一次 get 必须等——不能无中生有', async () => {
    const cache = createSwrCache<number>(1000, () => 0)
    const first = held<number>()
    let calls = 0
    const p = cache.get(() => {
      calls++
      return first.promise
    })
    expect(calls).toBe(1) // 已起跑
    first.release(7)
    expect(await p).toBe(7)
    expect(cache.peek()).toBe(7)
  })

  it('refresh()：TTL 未到也重算，但**先回旧值**（动作后首帧不冷算）', async () => {
    const c = clock()
    const cache = createSwrCache<string>(5000, c.now)
    let calls = 0
    const second = held<string>()
    const compute = (): Promise<string> => {
      calls++
      return calls === 1 ? Promise.resolve('old') : second.promise
    }
    expect(await cache.get(compute)).toBe('old')
    c.advance(10) // 还远没到期
    cache.refresh()
    // 关键：这一次仍然拿到旧值（请求不等那一笔重算），同时候补那一次重算已经起跑
    expect(await cache.get(compute)).toBe('old')
    expect(calls).toBe(2)
    second.release('new')
    await flush()
    expect(await cache.get(compute)).toBe('new')
    expect(calls).toBe(2)
  })

  it('refresh() 时已在飞的重算不回填（作废前起的那次看不到刚发生的事）', async () => {
    const cache = createSwrCache<string>(5000, () => 0)
    const first = held<string>()
    const p = cache.get(() => first.promise)
    cache.refresh() // 无值可留 → 下一次 get 会起新的
    first.release('pre-action')
    expect(await p).toBe('pre-action') // 等它的人拿到它
    expect(cache.peek()).toBeNull() // 但不许进缓存
    expect(await cache.get(async () => 'post-action')).toBe('post-action')
  })

  it('clear() 后下一次 get **必须重算**（动作结果即时上屏，不许再回旧值）', async () => {
    const cache = createSwrCache<string>(5000, () => 0)
    expect(await cache.get(async () => 'old')).toBe('old')
    cache.clear()
    expect(cache.peek()).toBeNull()
    expect(await cache.get(async () => 'new')).toBe('new')
    expect(cache.peek()).toBe('new')
  })

  it('作废时已在飞的重算**不回填**（否则动作的作废被旧值静默撤销）', async () => {
    const cache = createSwrCache<string>(5000, () => 0)
    const first = held<string>()
    const p = cache.get(() => first.promise)
    cache.clear() // 动作作废 —— 而这一次重算是作废**之前**起的
    first.release('stale')
    expect(await p).toBe('stale') // 等它的人（老请求）仍拿到它
    expect(cache.peek()).toBeNull() // 但缓存里不许留下它
    expect(await cache.get(async () => 'fresh')).toBe('fresh')
  })
})
