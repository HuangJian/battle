/**
 * server-api-node-views.test.ts — §365 节点并行 ping：串行导致 /api/state 超时空回复
 *
 * 分层：src/server/api/views.ts（nodeViews）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console/api.nodeViews 并行 ping（§365：串行导致 /api/state 超时空回复）', () => {
  it('enabled 节点并发探测：全部同时发起，顺序保持，disabled 不探测', async () => {
    const origFetch = globalThis.fetch
    let active = 0
    let maxActive = 0
    const started: string[] = []
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    globalThis.fetch = ((url: unknown, init?: RequestInit) => {
      active++
      maxActive = Math.max(maxActive, active)
      started.push(String(url))
      return new Promise<Response>((resolve, reject) => {
        const onAbort = (): void => {
          active--
          reject(new DOMException('aborted', 'AbortError'))
        }
        init?.signal?.addEventListener('abort', onAbort)
        void gate.then(() => {
          init?.signal?.removeEventListener('abort', onAbort)
          active--
          resolve(
            new Response(JSON.stringify({ codeHash: 'abcd1234', cpus: 8 }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        })
      })
    }) as typeof fetch
    try {
      const cfg = {
        version: 1,
        nodes: [
          { id: 'a', url: 'http://node-a', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'b', url: 'http://node-b', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'c', url: 'http://node-c', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'off', url: 'http://node-off', authKey: 'k', concurrency: 2, enabled: false },
        ],
        rl: { hub_port: 8900, agent_port: 8910, remote_token: 't' },
      } as Parameters<typeof api.nodeViews>[0]
      const p = api.nodeViews(cfg)
      // 并行实现下全部 enabled 节点在同一微任务批次已发起 fetch（串行实现此刻仅 1 个挂起）
      expect(started.length).toBe(3)
      expect(started.every((u) => u.endsWith('/v1/ping'))).toBe(true)
      expect(maxActive).toBe(3) // 三个 ping 同时挂起 = 并行；串行永远 maxActive=1
      release()
      const nv = await p
      expect(nv.map((n) => n.id)).toEqual(['a', 'b', 'c', 'off']) // Promise.all 保序
      expect(nv.filter((n) => n.online === true).length).toBe(3)
      expect(nv.find((n) => n.id === 'off')!.online).toBeNull() // disabled 不探测
    } finally {
      release()
      globalThis.fetch = origFetch
    }
  })
})

describe('磁盘水位透传（plan/self-node-disk-alert：ping 的纯加法字段）', () => {
  it('新 agent（五字段齐）⇒ disk 解析出来；旧 agent（无字段）⇒ null，不编事实', async () => {
    const origFetch = globalThis.fetch
    globalThis.fetch = ((url: unknown) => {
      const body = String(url).includes('node-new')
        ? {
            codeHash: 'abcd1234',
            cpus: 8,
            diskFreeMB: 1212,
            diskLevel: 'warn',
            diskWarnMB: 4096,
            diskFloorMB: 2048,
            diskLevelSince: 1_700_000_000_000,
          }
        : { codeHash: 'abcd1234', cpus: 8 }
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }) as typeof fetch
    try {
      const cfg = {
        version: 1,
        nodes: [
          { id: 'self', url: 'http://node-new', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'old', url: 'http://node-old', authKey: 'k', concurrency: 2, enabled: true },
        ],
        rl: { hub_port: 8900, agent_port: 8910, remote_token: 't' },
      } as Parameters<typeof api.nodeViews>[0]
      const nv = await api.nodeViews(cfg)
      // 五字段齐备 ⇒ 原样落地（阈值也是 agent 报的，看板不硬编码）
      expect(nv[0]!.disk).toEqual({
        freeMB: 1212,
        level: 'warn',
        warnMB: 4096,
        floorMB: 2048,
        since: 1_700_000_000_000,
      })
      // 旧 agent：探测照样成功（online=true），只是**没有盘位事实** ⇒ null
      expect(nv[1]!.online).toBe(true)
      expect(nv[1]!.disk).toBeNull()
    } finally {
      globalThis.fetch = origFetch
    }
  })
})
