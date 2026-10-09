/**
 * ppo-worker-live.test.ts — 首页 PPO 区「每台一行」（plan/dashboard-ppo-live-rows，2026-10-09）。
 *
 * 覆盖三层：
 *   ① 解析（`parseHubQueue` / `parseWorkerPrefetch`）：新键宽容——旧 hub 没这些字段时必须
 *      退化成「没有这一行 / 段缺省」，**不炸整页、不编 0**（`it` 缺失是 `null` 不是 0）；
 *   ② 组装（`buildWorkerLive` 纯函数）：三段分档（`computing_ago` 判「真开算」）、hub 与 worker
 *      两路事实按 job 去重、按机器归并、份额与它无关、只留有活动的、行序；
 *   ③ 渲染（`PpoWorkerRows` / `jobRefText`）：空段整段不渲染、缺数据标 `it?`、空数组 ⇒ 不画。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { PpoWorkerRows, jobRefText } from '../src/web/app/panels/WorkerContribution'
import {
  type HubJobRefView,
  type HubQueueCourseView,
  type HubQueueView,
  buildWorkerLive,
  parseHubQueue,
  parseWorkerPrefetch,
} from '../src/web/view'

const J1 = 'a'.repeat(16)
const J2 = 'b'.repeat(16)

function course(over: Partial<HubQueueCourseView> = {}): HubQueueCourseView {
  return {
    hold: null,
    pendingExport: null,
    pending: 0,
    inflight: 0,
    nextJob: null,
    holders: [],
    halt: false,
    inflightDetail: [],
    frozen: [],
    ...over,
  }
}

function hq(over: Partial<HubQueueView> = {}): HubQueueView {
  return {
    courses: {},
    order: [],
    cursor: null,
    activeCourses: 0,
    activeWorkers: 0,
    offlineDisks: null,
    peekedCourses: null,
    workerPrefetch: {},
    halt: false,
    ...over,
  }
}

const ref = (jobId: string, course: string, it: number | null): HubJobRefView => ({
  jobId,
  course,
  it,
})

// ────────────────────────── ① 解析（宽容） ──────────────────────────

describe('parseHubQueue 的新键', () => {
  it('inflight 行带出 course / it；缺字段 ⇒ 空串 / null（不编 0）', () => {
    const q = parseHubQueue({
      courses: {
        'x23-cm2': {
          inflight: [
            { job_id: J1, worker: 'kaggle-c', heartbeat_ago: 3, course: 'x23-cm2', it: 121 },
            { job_id: J2, worker: 'colab-t', heartbeat_ago: 4 }, // 旧 hub：没有 course/it
          ],
        },
      },
    })
    expect(q).not.toBeNull()
    expect(q!.courses['x23-cm2']!.inflightDetail[0]).toMatchObject({
      jobId: J1,
      course: 'x23-cm2',
      it: 121,
    })
    expect(q!.courses['x23-cm2']!.inflightDetail[1]).toMatchObject({
      jobId: J2,
      course: '',
      it: null,
    })
  })

  it('worker_prefetch 缺 / 坏形状 ⇒ 空表（旧 hub 只有 hub 侧两段）', () => {
    expect(parseHubQueue({ courses: {} })!.workerPrefetch).toEqual({})
    expect(parseWorkerPrefetch(undefined)).toEqual({})
    expect(parseWorkerPrefetch({ 'kaggle-c': 'nope' })).toEqual({})
    // 引用缺 job_id ⇒ 丢掉那一条（不是引用），但整表仍可用
    expect(
      parseWorkerPrefetch({ 'kaggle-c': { held: [{ course: 'c1' }], dl: [], age: 2 } }),
    ).toEqual({ 'kaggle-c': { held: [], dl: [], age: 2 } })
  })

  it('worker_prefetch 正常形状：jid → course/it 原样带出', () => {
    const pf = parseWorkerPrefetch({
      'kaggle-c': { held: [{ job_id: J1, course: 'x23-cm2', it: 121 }], dl: [], age: 1.4 },
    })
    expect(pf['kaggle-c']).toEqual({ held: [ref(J1, 'x23-cm2', 121)], dl: [], age: 1.4 })
  })
})

// ────────────────────────── ② 组装（纯函数） ──────────────────────────

describe('buildWorkerLive', () => {
  it('queue 为 null（hub 不可达 / 冷启动首拍）⇒ 空数组，不编行', () => {
    expect(buildWorkerLive(null, [])).toEqual([])
  })

  it('三段分档：已开算 ⇒ 计算中；认领未开算 ⇒ 下载中（不把「已认领」叫计算中）', () => {
    const q = hq({
      courses: {
        'x23-cm2': course({
          inflightDetail: [
            {
              jobId: J1,
              course: 'x23-cm2',
              it: 121,
              worker: 'kaggle-c',
              heartbeatAgo: 1,
              claimedAgo: 60,
              computingAgo: 30, // 已 POST /start
            },
          ],
        }),
        'x23-cm3': course({
          inflightDetail: [
            {
              jobId: J2,
              course: 'x23-cm3',
              it: 122,
              worker: 'kaggle-c',
              heartbeatAgo: 1,
              claimedAgo: 20,
              computingAgo: null, // 认领了、还在取包
            },
          ],
        }),
      },
    })
    const [row] = buildWorkerLive(q, [{ worker: 'kaggle-c', share: 0.5 }])
    expect(row!.computing).toEqual([ref(J1, 'x23-cm2', 121)])
    expect(row!.dl).toEqual([ref(J2, 'x23-cm3', 122)])
  })

  it('worker 上报的软持有进「已下载」；与 hub 的下载中按 job 去重；同一机器多课归并', () => {
    const q = hq({
      courses: {
        'x23-cm2': course({
          inflightDetail: [
            {
              jobId: J2,
              course: 'x23-cm2',
              it: 120,
              worker: 'kaggle-c',
              heartbeatAgo: 1,
              claimedAgo: 5,
              computingAgo: null,
            },
          ],
        }),
      },
      // 同一台的 `hostname:pid` 形态与上报名（不带冒号）必须归并成一行
      workerPrefetch: {
        'kaggle-c': { held: [ref(J1, 'x23-cm0', 119)], dl: [ref(J2, 'x23-cm2', 120)], age: 3 },
      },
    })
    const live = buildWorkerLive(q, [{ worker: 'kaggle-c:24134', share: 0.25 }])
    expect(live).toHaveLength(1)
    expect(live[0]!.worker).toBe('kaggle-c')
    expect(live[0]!.held).toEqual([ref(J1, 'x23-cm0', 119)])
    expect(live[0]!.dl).toEqual([ref(J2, 'x23-cm2', 120)]) // hub 与 worker 报的是同一份
    expect(live[0]!.ageSec).toBe(3)
    expect(live[0]!.share).toBe(0.25)
  })

  it('新机器没有历史份额也出行（share=null ⇒ 屏上「—」）；三段全空的机器不出行', () => {
    const q = hq({
      courses: {
        c1: course({
          inflightDetail: [
            {
              jobId: J1,
              course: 'c1',
              it: 7,
              worker: 'colab-t',
              heartbeatAgo: 1,
              claimedAgo: 2,
              computingAgo: 1,
            },
          ],
        }),
      },
      workerPrefetch: { 'aistudio-c': { held: [], dl: [], age: 12 } }, // 空报告（读面已过滤）
    })
    const live = buildWorkerLive(q, [])
    expect(live.map((r) => r.worker)).toEqual(['colab-t'])
    expect(live[0]!.share).toBeNull()
  })

  it('自主盘：持 live hold ⇒ 出行 + 标记，轮次取「已补传产物」口径（跨 run 取最大）', () => {
    const q = hq({
      courses: {
        'x23-cm2': course({
          hold: {
            workerId: 'aistudio-c',
            state: 'live',
            lastProgressAt: 1,
            at: 1,
            expiresIn: 30,
          },
        }),
        'x23-cm9': course({
          hold: { workerId: 'colab-t', state: 'stale', lastProgressAt: 1, at: 1, expiresIn: 0 },
        }),
      },
    })
    const offline = {
      'x23-cm2': {
        r2: { its: [10, 11], count: 2, lastMtime: 100 },
        r3: { its: [12], count: 1, lastMtime: 200 }, // 最新那段
      },
    }
    const live = buildWorkerLive(q, [], offline)
    expect(live.map((r) => r.worker)).toEqual(['aistudio-c'])
    expect(live[0]!.autonomous).toBe(true)
    expect(live[0]!.computing).toEqual([{ jobId: '', course: 'x23-cm2', it: 12 }])
    // stale 的接管**不**出行（那门课已自动恢复协作派发）
    expect(live.some((r) => r.worker === 'colab-t')).toBe(false)
  })

  it('offlineProgress 缺 ⇒ 轮次不可知（it=null），不编 0', () => {
    const q = hq({
      courses: {
        c1: course({
          hold: { workerId: 'aistudio-o', state: 'live', lastProgressAt: 1, at: 1, expiresIn: 30 },
        }),
      },
    })
    const live = buildWorkerLive(q, [], null)
    expect(live[0]!.computing).toEqual([{ jobId: '', course: 'c1', it: null }])
  })

  it('行序：份额降序、缺份额最后、同份按名字', () => {
    const q = hq({
      courses: {
        c1: course({
          inflightDetail: [1, 2, 3].map((n) => ({
            jobId: `j${n}`.padEnd(16, 'x'),
            course: 'c1',
            it: n,
            worker: n === 1 ? 'b-node' : n === 2 ? 'a-node' : 'z-node',
            heartbeatAgo: 1,
            claimedAgo: 1,
            computingAgo: 1,
          })),
        }),
      },
    })
    const live = buildWorkerLive(q, [
      { worker: 'z-node', share: null },
      { worker: 'a-node', share: 0.1 },
      { worker: 'b-node', share: 0.1 },
    ])
    expect(live.map((r) => r.worker)).toEqual(['a-node', 'b-node', 'z-node'])
  })
})

// ────────────────────────── ③ 渲染 ──────────────────────────

describe('PpoWorkerRows / jobRefText', () => {
  it('引用文本：缺 course ⇒ `?`，缺 it ⇒ `it?`（都不编数）', () => {
    expect(jobRefText(ref(J1, 'x23-cm2', 121))).toBe('§x23-cm2:it121')
    expect(jobRefText(ref(J1, '', 121))).toBe('§?:it121')
    expect(jobRefText(ref(J1, 'x23-cm2', null))).toBe('§x23-cm2:it?')
  })

  it('空数组 / null ⇒ 一点都不渲染（无空区块）', () => {
    expect(renderToString(h(PpoWorkerRows, { live: null }))).toBe('')
    expect(renderToString(h(PpoWorkerRows, { live: [] }))).toBe('')
  })

  it('三段的空段整段不渲染；有段才出段；自主盘带徽标', () => {
    const html = renderToString(
      h(PpoWorkerRows, {
        live: [
          {
            worker: 'kaggle-c',
            share: 0.23,
            autonomous: false,
            ageSec: 4,
            computing: [ref(J1, 'x23-cm2', 121)],
            held: [ref(J2, 'x23-cm0', 119)],
            dl: [],
          },
          {
            worker: 'aistudio-c',
            share: null,
            autonomous: true,
            ageSec: null,
            computing: [{ jobId: '', course: 'x23-cm2', it: null }],
            held: [],
            dl: [],
          },
        ],
      }),
    )
    expect(html).toContain('kaggle-c')
    expect(html).toContain('23.0%')
    expect(html).toContain('§x23-cm2:it121')
    expect(html).toContain('§x23-cm0:it119')
    expect(html).not.toContain('下载中') // 空段：整段不渲染
    expect(html).toContain('aistudio-c')
    expect(html).toContain('自主')
    // 自主盘的轮次是**派生读数**：这里没有任何已补传产物 ⇒ `it?`（不编 0）；
    // course 解析不出时的 `?:itN` 形态由上面的 `jobRefText` 用例单独钉。
    expect(html).toContain('§x23-cm2:it?')
    expect(html).toContain('—') // 无贡献的机器：份额「—」而不是 0%
  })
})
