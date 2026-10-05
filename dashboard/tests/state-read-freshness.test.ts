/**
 * state-read-freshness.test.ts — 读面新鲜度（plan/offline-online-status-switch §3.10-3 / P1-11）。
 *
 * R4-e 的旧形状：`getLoopQueueView` 失败返回**空行 + error** 并把它存进 SWR ⇒ 此后 ≥TTL 内
 * 整列「视图不可用」，而表头还写着「上一拍读失败（显示缓存）」——**根本没有缓存**在显示
 * （文案与行为不符）。本文件钉住新语义：
 *
 *   ① 失败**不替换旧值**：保值窗口内旧行照旧上屏 + `stale: {since, reason}` 标注；
 *   ② 超窗（一个刷新周期）⇒ 退化到「未知」（空行 + error），不无限保旧；
 *   ③ hub 探针失败同理（旧探测仍在、`stale` 非空），窗口判定是导出纯函数（用例不必等 5s）；
 *   ④ 恢复成功 ⇒ `stale` 消失（代际/软刷新语义不因此改变）。
 *
 * 环境重定向见 `./helpers/console-fixture.ts`。
 */

import { afterEach, describe, expect, it } from 'bun:test'
import type { RlConfig } from '../src/core/types'
import { api, loadConfig } from './helpers/console-fixture'

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

function cfg(): RlConfig {
  return loadConfig() as unknown as RlConfig
}

const okRun = (): {
  code: number
  stdout: string
  stderr: string
  timeout: boolean
} => ({
  code: 0,
  stdout: JSON.stringify({
    courses: [
      {
        course: 'c4',
        it: 7,
        state: 'ready',
        current: 'ppo',
        pending: ['ppo'],
        inflight: [],
        facts: {},
        waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
      },
    ],
    pools: {},
  }),
  stderr: '',
  timeout: false,
})

const badRun = (): { code: number; stdout: string; stderr: string; timeout: boolean } => ({
  code: 1,
  stdout: '',
  stderr: 'ModuleNotFoundError: 没装依赖',
  timeout: false,
})

// ────────────────────────── 训练侧（调度器读面） ──────────────────────────

describe('getLoopQueueView：读失败保旧值（P1-11）', () => {
  it('失败（保值窗口内）⇒ 旧行仍在 + stale 标；恢复成功 ⇒ 标注消失', async () => {
    api.invalidateLoopQueue()
    const v1 = await api.getLoopQueueView(okRun)
    expect(v1.rows.map((r) => r.course)).toEqual(['c4'])
    expect(v1.stale ?? null).toBeNull()
    // 软刷新（动作路径语义）：旧值照给，重算丢后台；后台失败 ⇒ 下一读带标注。
    api.refreshLoopQueue()
    await api.getLoopQueueView(badRun)
    await sleep(10)
    const v2 = await api.getLoopQueueView(badRun)
    expect(v2.rows.map((r) => r.course)).toEqual(['c4']) // ★ 行没被清空
    expect(v2.stale).not.toBeNull()
    expect(v2.error).toContain('退出码 1')
    expect(v2.stale!.reason).toContain('ModuleNotFoundError')
    // 恢复：成功重算落地后标注消失、行仍在
    api.refreshLoopQueue()
    await api.getLoopQueueView(okRun)
    await sleep(10)
    const v3 = await api.getLoopQueueView(okRun)
    expect(v3.rows).toHaveLength(1)
    expect(v3.stale ?? null).toBeNull()
  })

  it('从没成功过（冷启动失败）⇒ 空行 + error，不假装有缓存', async () => {
    api.invalidateLoopQueue()
    const v = await api.getLoopQueueView(badRun)
    expect(v.rows).toEqual([])
    expect(v.error).toContain('退出码 1')
    expect(v.stale ?? null).toBeNull()
  })

  it('超窗判定（纯函数）：窗口内保旧值，超窗给未知', () => {
    const now = 1_700_000_000_000
    expect(api.loopQueueKeepWithin(now - 1, now)).toBe(true)
    expect(api.loopQueueKeepWithin(now - api.LOOP_QUEUE_KEEP_MS, now)).toBe(true)
    expect(api.loopQueueKeepWithin(now - api.LOOP_QUEUE_KEEP_MS - 1, now)).toBe(false)
    expect(api.loopQueueKeepWithin(0, now)).toBe(false) // 没在失败
  })
})

// ────────────────────────── hub 侧（机群级探测） ──────────────────────────

describe('getHubAdmin：探针失败保旧探（P1-11）', () => {
  const origFetch = globalThis.fetch
  afterEach(() => {
    globalThis.fetch = origFetch
  })

  it('失败（窗口内）⇒ 上一拍 hub 事实仍在 + stale；超窗判定是纯函数', async () => {
    api.invalidateHubAdmin()
    let down = false
    globalThis.fetch = (async (input: string | URL | Request) => {
      if (down) throw new Error('ECONNREFUSED 127.0.0.1:18787')
      const u = String(input)
      if (u.includes('/admin/queue')) {
        return new Response(
          JSON.stringify({
            courses: {
              c4: {
                mode: 'online',
                authority: 'auto',
                pinned: false,
                pending_n: 0,
                inflight: [],
              },
            },
            order: ['c4'],
            offline: [],
            active_courses: 1,
            active_workers: 0,
            halt: false,
          }),
        )
      }
      if (u.includes('/admin/offline')) {
        return new Response(JSON.stringify({ progress: {}, leases: {} }))
      }
      if (u.includes('/admin/push-workers')) {
        return new Response(JSON.stringify({ registry: { workers: [] } }))
      }
      return new Response(JSON.stringify({}), { status: 200 })
    }) as typeof fetch

    const a1 = await api.getHubAdmin(cfg(), 'c4')
    expect(a1.url).not.toBeNull()
    expect(a1.queue?.courses.c4).toBeDefined()
    expect(a1.stale ?? null).toBeNull()

    down = true
    api.refreshHubAdmin()
    await api.getHubAdmin(cfg(), 'c4') // 软刷新：旧值先给，重算丢后台
    await sleep(10)
    const a2 = await api.getHubAdmin(cfg(), 'c4')
    expect(a2.url).not.toBeNull() // ★ last-known-good
    expect(a2.queue?.courses.c4).toBeDefined()
    expect(a2.stale).not.toBeNull()
    expect(a2.stale!.reason).toContain('没有 hub 在应答')
    // 超窗（纯函数）：不再保旧值
    const now = Date.now()
    expect(api.hubKeepWithin(now - 1, now)).toBe(true)
    expect(api.hubKeepWithin(now - api.HUB_KEEP_MS - 1, now)).toBe(false)
  })

  it('从没探到过 ⇒ 空探（url=null）：不编 hub 事实', async () => {
    api.invalidateHubAdmin()
    globalThis.fetch = (async () => {
      throw new Error('ECONNREFUSED')
    }) as unknown as typeof fetch
    const a = await api.getHubAdmin(cfg(), 'c4')
    expect(a.url).toBeNull()
    expect(a.queue).toBeNull()
    expect(a.stale).not.toBeNull() // 失败原因在案（诊断入口）
  })
})
