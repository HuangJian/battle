/**
 * hub-queue.test.ts — `/api/hubQueue`（notebook 伴生脚本判「还要不要烧配额」的事实面）。
 *
 * 钉三件事：
 *   ① 汇聚口径：逐课 `pending`/`inflight` 合成合计，坏值不传播成 NaN；
 *   ② hub 不可达 ⇒ `ok:false` + 空计数（页面据此计「连续问不到」的拍数，不是「队列空」）；
 *   ③ 接线：路由表登记了这条路径、`server.ts` 装上了带自定义头的 handler（漏接 = 页面永远 403/404）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { summarizeHubQueue } from '../src/server/api/hub-queue'
import { CONSOLE_ROUTES } from '../src/server/route-table'
import type { HubQueueCourseView, HubQueueView } from '../src/web/view'

const DASHBOARD_ROOT = path.join(import.meta.dir, '..')

/** 一门课的队列行（只给本用例关心的数字/hold，其余按 hub 视图的最小合法形状补齐）。 */
function row(
  pending: number,
  inflight: number,
  hold: HubQueueCourseView['hold'] = null,
): HubQueueCourseView {
  return {
    hold,
    pendingExport: null,
    pending,
    inflight,
    nextJob: null,
    holders: [],
    halt: false,
    inflightDetail: [],
    frozen: [],
  }
}

/** 一条 live hold（自主盘正在跑整课）。 */
function liveHold(workerId = 'kaggle-abc123'): HubQueueCourseView['hold'] {
  return {
    workerId,
    state: 'live',
    lastProgressAt: 1_700_000_000,
    at: 1_700_000_000,
    expiresIn: 600,
  }
}

function queue(courses: Record<string, HubQueueCourseView>): HubQueueView {
  return {
    courses,
    order: Object.keys(courses),
    cursor: null,
    activeCourses: 0,
    activeWorkers: 0,
    workerPrefetch: {},
    offlineDisks: null,
    halt: false,
    peekedCourses: null,
  }
}

describe('/api/hubQueue：汇总口径', () => {
  it('逐课合计：pending / inflight 各自相加，逐课明细原样给出', () => {
    const sum = summarizeHubQueue({
      url: 'https://hub.example',
      queue: queue({ g2: row(2, 1), bc: row(0, 0), g4: row(3, 0) }),
    })
    expect(sum).toEqual({
      ok: true,
      hubUrl: 'https://hub.example',
      pending: 5,
      inflight: 1,
      pendingUnheld: 5,
      heldLive: 0,
      courses: {
        g2: { pending: 2, inflight: 1 },
        bc: { pending: 0, inflight: 0 },
        g4: { pending: 3, inflight: 0 },
      },
    })
  })

  it('live hold：该课的 pending 不算「可领」，但记 heldLive（stale 不算）', () => {
    const stale: HubQueueCourseView['hold'] = {
      workerId: 'kaggle-old',
      state: 'stale',
      lastProgressAt: 1,
      at: 1,
      expiresIn: 0,
    }
    const sum = summarizeHubQueue({
      url: 'https://hub.example',
      queue: queue({
        running: row(4, 0, liveHold()),
        waiting: row(2, 0),
        dropped: row(1, 0, stale),
      }),
    })
    expect(sum.pending).toBe(7)
    expect(sum.pendingUnheld).toBe(3) // waiting 2 + dropped 1（stale 不算 hold）
    expect(sum.heldLive).toBe(1)
    expect(sum.courses.running).toEqual({ pending: 4, inflight: 0 })
  })

  it('没有 hub 应答 ⇒ ok:false + 空计数 + 原因（页面按「问不到」计拍，不按「空队列」）', () => {
    const sum = summarizeHubQueue(null)
    expect(sum.ok).toBe(false)
    expect(sum.pending).toBe(0)
    expect(sum.inflight).toBe(0)
    expect(sum.pendingUnheld).toBe(0)
    expect(sum.heldLive).toBe(0)
    expect(sum.courses).toEqual({})
    expect(sum.hubUrl).toBe('')
    expect(sum.reason ?? '').not.toBe('')
  })

  it('坏数字不传播成 NaN（旧 hub / 坏行按 0 计；负数夹到 0）', () => {
    const bad = row(Number.NaN, -3)
    const sum = summarizeHubQueue({ url: 'u', queue: queue({ x: bad }) })
    expect(sum.pending).toBe(0)
    expect(sum.inflight).toBe(0)
    expect(sum.pendingUnheld).toBe(0)
    expect(sum.heldLive).toBe(0)
    expect(sum.courses.x).toEqual({ pending: 0, inflight: 0 })
  })
})

describe('/api/hubQueue：接线', () => {
  it('路由表登记了 GET（静态路径，排在通配之前）', () => {
    const route = CONSOLE_ROUTES.find((r) => r.path === '/api/hubQueue')
    expect(route?.methods).toEqual(['GET'])
    expect(CONSOLE_ROUTES.findIndex((r) => r.path === '/api/hubQueue')).toBeLessThan(
      CONSOLE_ROUTES.findIndex((r) => r.path === '/api/*'),
    )
  })

  it('server.ts 装上了 handler，且与 notebook 面同一道自定义头防线', () => {
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src/server/server.ts'), 'utf8')
    expect(src).toContain("'/api/hubQueue': { GET: guard(hubQueueRoute")
    expect(src).toContain('buildHubQueueSummary')
    // 端点自己带自定义头闸：跨源网页发不了这个头
    const at = src.indexOf('const hubQueueRoute')
    expect(at).toBeGreaterThan(0)
    expect(src.slice(at, at + 400)).toContain("req.headers.get('X-Notebook-Companion')")
  })
})
