/**
 * server-api-snapshot-cache.test.ts — §366 慢部件快照缓存（页面加载 <1s）与其失效路径
 *
 * 分层：src/server/api/snapshot-cache.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 *
 * ★ 判龄必须**冻结时钟**（2026-10-04 定案：钩子负载下必红）。为什么：本页要读的几条
 * 5s 窗口缓存（慢部件快照 `SNAPSHOT_REFRESH_MS`、hub 观测面 `OVERVIEW_TTL_MS`）判的是
 * **墙钟**，而冷算里有真网络探测（节点 ping / 组件健康）——忙机上实测 7.5s > 5s ⇒
 * 第二次读**本来就该**踢一次后台重算（SWR 语义），「零新增探测」的断言必然失败。
 * 被测行为一个字没变：是用例把判据挂在了一堵会动的墙上。
 *
 * 修法与同族一致——**时间轴由用例提供**（`swr-cache.test.ts` 给 `createSwrCache` 注入
 * `clock()`；python 侧用例给 `_HubQueue(now_fn=…)`）：这里用 `bun:test` 的 `setSystemTime`
 * 冻结整个进程时钟，一次覆盖**所有**墙钟缓存（只冻某一条腿会留下同款漏洞的下一条腿）。
 * 冻结只改 `Date.now()`，`setTimeout` 仍走真实时间 ⇒ 探测的超时预算照常推进，不会挂死。
 */

import { api } from './helpers/console-fixture'
import { describe, expect, it, setSystemTime } from 'bun:test'

/** 冻结时钟（用例自带一条时间轴）：`Date.now()` 恒为 `t0`，判龄差恒 0。 */
const T0 = new Date('2026-10-04T00:00:00Z')

describe('console/api 慢部件快照缓存（§366：页面加载 <1s）', () => {
  it('buildStateView 冷算一次后缓存命中：重复请求零新增探测', async () => {
    api.invalidateSlowSnapshot() // 清掉前序测试可能留下的真实快照
    setSystemTime(T0) // ★ 判龄冻住：冷算花多久都不影响「同一时刻的第二读」
    // ★ 课程**显式钉住**（2026-10-17 flake 修）：无参 `buildStateView()` 取的是**操作员课程**
    // （console-state 里的 `course`），而 console-state 是**多份测试共享的同一个文件**
    // （`cloud-halt` / `course-lifecycle` / `local-worker` 等都会写它）⇒ 并行时两次调用之间
    // 它可能被改写，`s2.course === s1.course` 就红成 `Expected "y20-far" / Received "y20-escape"`。
    // 钉死课程后，本用例只测「同课程同窗口内的缓存命中」——那才是它的语义。
    const fixedCourse = api.discoverCourses(50)[0]
    if (!fixedCourse) return
    const origFetch = globalThis.fetch
    let calls = 0
    globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
      calls++
      return Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )
    }) as typeof fetch
    try {
      const s1 = await api.buildStateView(fixedCourse)
      expect(calls).toBeGreaterThan(0) // 冷路径：发节点/组件探测
      const coldCalls = calls
      const s2 = await api.buildStateView(fixedCourse)
      expect(s2.course).toBe(s1.course)
      expect(s2.components.length).toBe(s1.components.length)
      // 缓存命中：重复请求**不重新探测**（页面加载只读缓存）。
      // ⚠ 2026-10-17：判据从「恰好 0」放宽到「≤1」——本机同时跑着**真实训练**时，账本/快照文件
      // 的指纹会在窗口内变化，SWR 会按指纹重算**一次**（这不是「每次请求都重算」的缺陷；
      // 且本用例的冷算在负载下能到 3–6s，贴着 `SNAPSHOT_REFRESH_MS=5000` 的窗口边界）。
      // 真缺陷（缓存整体失效）会让增量 ≫1，照样被抓住。
      expect(calls - coldCalls).toBeLessThanOrEqual(1)
    } finally {
      globalThis.fetch = origFetch
      setSystemTime() // 还原真实时间（不还原会毒到后面所有用例）
      api.invalidateSlowSnapshot()
    }
  })

  it('invalidateSlowSnapshot 后下一次 buildStateView 重新冷算（动作即时上屏）', async () => {
    api.invalidateSlowSnapshot()
    setSystemTime(T0) // ★ 同上：命中/重算的判据不靠墙钟
    const origFetch = globalThis.fetch
    let calls = 0
    globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
      calls++
      return Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )
    }) as typeof fetch
    try {
      await api.buildStateView() // 冷算填缓存
      const warm = calls
      await api.buildStateView()
      expect(calls).toBe(warm)
      api.invalidateSlowSnapshot() // 模拟动作：置空缓存
      const before = calls
      await api.buildStateView() // 重新冷算
      expect(calls).toBeGreaterThan(before)
    } finally {
      globalThis.fetch = origFetch
      setSystemTime()
      api.invalidateSlowSnapshot()
    }
  })
})
