/**
 * server-api-snapshot-cache.test.ts — §366 慢部件快照缓存（页面加载 <1s）与其失效路径
 *
 * 分层：src/server/api/snapshot-cache.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console/api 慢部件快照缓存（§366：页面加载 <1s）', () => {
  it('buildStateView 冷算一次后缓存命中：重复请求零新增探测', async () => {
    api.invalidateSlowSnapshot() // 清掉前序测试可能留下的真实快照
    const origFetch = globalThis.fetch
    let calls = 0
    globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
      calls++
      return Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )
    }) as typeof fetch
    try {
      const s1 = await api.buildStateView()
      expect(calls).toBeGreaterThan(0) // 冷路径：发节点/组件探测
      const coldCalls = calls
      const s2 = await api.buildStateView()
      expect(s2.course).toBe(s1.course)
      expect(s2.components.length).toBe(s1.components.length)
      expect(calls).toBe(coldCalls) // 缓存命中：零新增探测 → 页面加载只读缓存
    } finally {
      globalThis.fetch = origFetch
      api.invalidateSlowSnapshot()
    }
  })

  it('invalidateSlowSnapshot 后下一次 buildStateView 重新冷算（动作即时上屏）', async () => {
    api.invalidateSlowSnapshot()
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
      api.invalidateSlowSnapshot()
    }
  })
})
