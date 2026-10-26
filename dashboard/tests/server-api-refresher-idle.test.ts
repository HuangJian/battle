/**
 * server-api-refresher-idle.test.ts — 刷新器的**空闲闸**：「没人在看就不刷」。
 *
 * 分层：src/server/api/snapshot-refresher.ts（`refreshSnapshotTick` / `markConsoleActivity`）
 *
 * 为什么要有这条闸（2026-10-25）：控制台的慢快照刷新器每 5s 一拍，拍里是**主动出网**的
 * 探测（节点 ping 1.5s / 共享 hub 1.2–1.5s / 隧道 2.5s）与磁盘聚合。页面关掉之后它照样
 * 每 5s 出去探一轮——空转的 CPU Bun 1.4.3 已经省了，但「没人看的时候还去敲云机」这件事
 * 只能靠「有没有请求」判。
 *
 * 判据纪律（同 swr-cache / snapshot-cache 两家）：**时间轴由用例提供**
 * （`refreshSnapshotTick(now)` + `setSystemTime`），不 sleep、不等墙钟；探测次数用全局
 * `fetch` 计数观察（夹具配置指向死端点，毫秒级返回）。
 *
 * 依赖顺序：`./helpers/console-fixture` 必须在被测模块之前 import（它先把 env/盘重定向到
 * 临时根 —— 与同族用例同规）。
 */

import { afterEach, describe, expect, it, setSystemTime } from 'bun:test'
import { readFileSync } from 'fs'
import { api } from './helpers/console-fixture'

/** 固定时间轴：空闲闸与快照判龄都读 `Date.now()`，一次冻住两条。 */
const T0 = Date.parse('2026-10-25T00:00:00Z')

/** 本拍是否发出了探测（`fetch` 计数）；`fn` 期间替换全局 fetch。
 *
 *  `settleMs`：`fn` 返回后继续计数一段（默认 0）。SWR 的重算是**丢后台**的——请求已经
 *  返回、`fetch` 却还没发出来，所以「后台真的起跑了」必须让它跑完这一拍再看计数，
 *  否则断言的是「请求里同步发完了」（那恰恰是本模块避免的事）。 */
async function countingProbes(fn: () => Promise<void>, settleMs = 0): Promise<number> {
  const orig = globalThis.fetch
  let calls = 0
  globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
    calls++
    return Promise.resolve(
      new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
    )
  }) as typeof fetch
  try {
    await fn()
    if (settleMs > 0) await new Promise((r) => setTimeout(r, settleMs))
  } finally {
    globalThis.fetch = orig
  }
  return calls
}

afterEach(() => {
  setSystemTime() // 还原真实时钟（不还原会毒到后面所有用例）
  api.invalidateSlowSnapshot()
  api.markConsoleActivity() // 复位成「刚刚有人看」：不把空闲状态漏给别的用例
})

describe('刷新器空闲闸：没人在看就不出去探', () => {
  it('刚有人看 ⇒ 这一拍照常重算（暖机第一拍也在内）', async () => {
    api.invalidateSlowSnapshot()
    api.markConsoleActivity(T0)
    let verdict = ''
    const calls = await countingProbes(async () => {
      verdict = await api.refreshSnapshotTick(T0 + 5_000)
    })
    expect(verdict).toBe('refreshed')
    expect(calls).toBeGreaterThan(0) // 真的探了（不是「返回 refreshed 却什么都没做」）
  })

  it('空闲超过窗口 ⇒ 一拍都不探（零 fetch）：判据只看「有没有请求」', async () => {
    api.invalidateSlowSnapshot()
    api.markConsoleActivity(T0)
    let verdict = ''
    const calls = await countingProbes(async () => {
      verdict = await api.refreshSnapshotTick(T0 + api.IDLE_REFRESH_SKIP_MS + 1)
    })
    expect(verdict).toBe('idle')
    expect(calls).toBe(0)
  })

  it('空闲后再来请求：照样返回完整视图，且陈旧探测只走后台（请求不等它）', async () => {
    // ① 先在 T0 暖一拍（缓存有值）
    setSystemTime(T0)
    api.invalidateSlowSnapshot()
    api.markConsoleActivity(T0)
    await countingProbes(() => api.refreshSnapshotTick(T0).then(() => undefined))
    const course = api.discoverCourses(50)[0]

    // ② 空闲一段（超过闸门），这一拍只清内存、零探测
    const idleAt = T0 + api.IDLE_REFRESH_SKIP_MS + 1
    setSystemTime(idleAt)
    const idleCalls = await countingProbes(async () => {
      expect(await api.refreshSnapshotTick(idleAt)).toBe('idle')
    })
    expect(idleCalls).toBe(0)

    // ③ 第一帧：快照判龄过期 ⇒ 现算（毫秒级），陈旧探测值先给、重算丢后台。
    //    断言「拿到了完整视图」+「后台真的起跑了」；不拿墙钟给请求计时（忙机上必抖）。
    const calls = await countingProbes(async () => {
      const view = await api.buildStateView(course)
      expect(view.components.length).toBeGreaterThan(0)
      // 结构事实是现算的：组件行与节点行都在（不是「等探测等出来的空壳」）
      expect(view.nodes.length).toBeGreaterThan(0)
    }, 50) // 后台重算不阻塞请求 ⇒ 请求返回后再看它有没有起跑
    expect(calls).toBeGreaterThan(0)
  })

  it('接线：server.ts 的请求入口（guard）第一件事就是 markConsoleActivity', () => {
    const src = readFileSync(new URL('../src/server/server.ts', import.meta.url), 'utf-8')
    // ★ 2026-10-25：请求入口从单条 `fetch` 链改为原生路由表 + 统一 `guard`——判据跟着
    //   入口走：所有 handler（含页面/API/bundle/兜底）都经这一个 guard。
    const guardAt = src.indexOf('const guard =')
    expect(guardAt).toBeGreaterThan(-1)
    const body = src.slice(guardAt, guardAt + 900)
    expect(body).toContain('markConsoleActivity()')
    // 必须早于 LAN 门控（只读来源也经过这道判据）
    expect(body.indexOf('markConsoleActivity()')).toBeLessThan(body.indexOf('isReadonlyAction'))
    // 且所有路由都由 guard 包过（漏包 = 那条入口既不记活动也不做只读门控）。
    const handlers = src.slice(
      src.indexOf('const routeHandlers'),
      src.indexOf('const server = Bun.serve'),
    )
    expect(handlers).not.toMatch(/: \{ GET: (?!guard\()/)
  })
})
