/**
 * server-api-overview.test.ts — 多课程并行总览 + push worker 登记（2026-09-18 单 hub 多课程）
 *
 * 分层：src/web/view/course-overview.ts（纯函数）+ src/server/api/overview.ts（组装）
 *
 * 「hub」一律用**假 server**（Bun.serve 按端点回固定 JSON，不跑任何真实运算）——
 * 断言的是控制台的读法，不是 python 的行为（那是 nn-training 侧 python 用例的事）。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { afterAll, afterEach, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import type { RlConfig } from '../src/core/types'
import { api, loadConfig, scratchConfig, view } from './helpers/console-fixture'

/** 夹具配置 → 被测函数要的 rl-config 形状（只读消费，不写盘）。
 *
 *  `workerUrl` 把 gpu_push 那台指到**假 hub**：本文件的用例要看的是「探过、有结论」（200 /
 *  404 / 无应答三条不同的观测面），而夹具缺省的 `https://127.0.0.1:1` 是死回环端口——只给
 *  「连不上」一个结论（速率上它反而最快：即时 ECONNREFUSED，不欠 DNS 的墙钟）。 */
function cfg(workerUrl?: string): RlConfig {
  const c = loadConfig() as unknown as RlConfig
  if (workerUrl) {
    for (const n of c.nodes) if (n.gpu_push) n.url = workerUrl
  }
  return c
}

// ────────────────────────── 纯函数：hub 队列解析 ──────────────────────────

describe('parseHubQueue（hub /admin/queue 的宽容解析）', () => {
  it('归一化字段名与缺省值：pending_n → pending、inflight 数组 → 计数 + 持有人', () => {
    const q = view.parseHubQueue({
      courses: {
        a: {
          hold: { worker_id: 'tpu-1', state: 'live', last_progress_at: 1758.5, expires_in: 870 },
          pending_n: 3,
          pending: ['j1', 'j2', 'j3'],
          inflight: [
            { job_id: 'j1', worker: 'gpu-1', heartbeat_ago: 2.5 },
            { job_id: 'j2', worker: '' },
          ],
          next_job: 'j1',
        },
        // ★M4：旧 `mode` / 顶层 `offline` 名集已退役——接管就是「有人领了这门课」。
        b: { pending_n: 0, pending: [], inflight: [], next_job: null },
      },
      order: ['a', 'b'],
      offline_disk: { recent: ['tpu-1'], recent_n: 1 },
      active_courses: 1,
      active_workers: 2,
      halt: false,
    })!
    expect(q.courses.a!.pending).toBe(3)
    expect(q.courses.a!.inflight).toBe(2)
    expect(q.courses.a!.holders).toEqual(['gpu-1', ''])
    expect(q.courses.a!.nextJob).toBe('j1')
    expect(q.courses.a!.hold).toEqual({
      workerId: 'tpu-1',
      state: 'live',
      lastProgressAt: 1758.5,
      at: 0,
      expiresIn: 870,
    })
    expect(q.courses.b!.hold).toBeNull()
    // 自主盘的「最近露面面」（P2-5）：旧 hub 没这一块 ⇒ null（不可知 ≠ 没盘）
    expect(q.offlineDisks).toEqual(['tpu-1'])
    expect(q.activeWorkers).toBe(2)
  })

  it('形状不符 / 字段缺失 → null 或缺省（观测面坏了不该把整页带崩）', () => {
    expect(view.parseHubQueue(null)).toBeNull()
    expect(view.parseHubQueue('nope')).toBeNull()
    expect(view.parseHubQueue({})).toBeNull() // 无 courses 块 = 不是这个端点
    const q = view.parseHubQueue({ courses: { a: {} } })!
    // 字段全缺 → 全零/缺省，绝不 NaN 或抛错；hold / pending_export 缺 =
    // **没有接管/没有导包**（旧 hub 不说这两件事，dashboard 也不据此编状态）
    expect(q.courses.a).toEqual({
      hold: null,
      pendingExport: null,
      pending: 0,
      inflight: 0,
      nextJob: null,
      holders: [],
      halt: false,
      inflightDetail: [],
      frozen: [],
    })
    expect(q.order).toEqual([])
    expect(q.activeCourses).toBe(0)
    expect(q.halt).toBe(false)
    // 旧 hub 没有 peeked_courses ⇒ null（不可知，不是「不在窗口」）
    expect(q.peekedCourses).toBeNull()
  })

  it('★M4：每课行带 hold + pending_export（courseStatus 从这里读，不猜）', () => {
    const q = view.parseHubQueue({
      courses: {
        a: {
          hold: { worker_id: 'tpu-1', state: 'live', last_progress_at: 1758.5 },
          pending_export: { by: 'tpu-2', at: 1750 },
        },
        // `state` 缺省按 **stale** 读（拿不到活性证据时不冒充 live——误报 live 会把一门
        // 正常课压住，反向的代价只是「照常派发 + 本机照跑」）。
        b: { hold: { worker_id: 'tpu-2' } },
        c: { hold: { state: 'live' } }, // 既没 worker_id 也没 last_progress_at ⇒ 根本不是 hold 块
        d: { hold: 'bogus', pending_export: 'bogus' }, // 形状不符 ⇒ 都当没有，不报错
      },
    })!
    expect(q.courses.a!.hold).toEqual({
      workerId: 'tpu-1',
      state: 'live',
      lastProgressAt: 1758.5,
      at: 0,
      expiresIn: 0,
    })
    expect(q.courses.a!.pendingExport).toEqual({ by: 'tpu-2', at: 1750 })
    expect(q.courses.b!.hold!.state).toBe('stale')
    expect(q.courses.b!.pendingExport).toBeNull()
    expect(q.courses.c!.hold).toBeNull()
    expect(q.courses.d!.hold).toBeNull()
    expect(q.courses.d!.pendingExport).toBeNull()
  })

  it('★2026-10-05 P0-11：/admin/offline 的 leases 解析（stale/墓碑；整块缺 ⇒ null）', () => {
    const admin = view.parseOfflineAdmin({
      progress: {},
      leases: {
        a: { worker_id: 'tpu-1', silent_sec: 512.5, stale: true, revoked: false, expires_in: 388 },
        b: { worker_id: 'tpu-2', silent_sec: 3, stale: false, revoked: true, expires_in: 800 },
      },
    })!
    expect(admin.leases).toEqual({
      a: { workerId: 'tpu-1', silentSec: 512.5, stale: true, revoked: false, expiresIn: 388 },
      b: { workerId: 'tpu-2', silentSec: 3, stale: false, revoked: true, expiresIn: 800 },
    })
    // 旧 hub 没有 leases 键 ⇒ null（未知，不画 stale/墓碑徽标）
    expect(view.parseOfflineAdmin({ progress: {} })!.leases).toBeNull()
  })

  it('★2026-10-02 pill 精确化：inflight 明细（认领/开算/心跳龄）与 peeked_courses', () => {
    const q = view.parseHubQueue({
      courses: {
        a: {
          mode: 'online',
          pending_n: 2,
          inflight: [
            {
              job_id: 'j9',
              worker: 'gpu-7',
              heartbeat_ago: 4,
              claimed_ago: 1620,
              computing_ago: null,
            },
          ],
          next_job: 'j8',
          halt: true,
        },
      },
      peeked_courses: ['a', 'other'],
    })!
    expect(q.courses.a!.halt).toBe(true)
    expect(q.courses.a!.nextJob).toBe('j8')
    // `course` / `it`（2026-10-09，plan/dashboard-ppo-live-rows）：这一拍的体里没带它们 ⇒
    // 解析成「不可知」（空串 / null），**不编 0**（旧 hub 的诚实形态）。
    expect(q.courses.a!.inflightDetail).toEqual([
      {
        jobId: 'j9',
        course: '',
        it: null,
        worker: 'gpu-7',
        heartbeatAgo: 4,
        claimedAgo: 1620,
        computingAgo: null,
      },
    ])
    expect(q.peekedCourses).toEqual(['a', 'other'])
  })
})

// ────────────────────────── 纯函数：账本尾行轮次 ──────────────────────────

describe('latestIterFromLedgerTail（只认 iteration 事件）', () => {
  it('取最后一个 iteration；忽略 hub 追加的 job_completed / run_start', () => {
    const lines = [
      JSON.stringify({ event: 'iteration', iter: 5 }),
      JSON.stringify({ event: 'job_pending', job_id: 'x', it: 6 }),
      JSON.stringify({ event: 'job_completed', job_id: 'x', it: 6 }),
      JSON.stringify({ event: 'iteration', iter: 6, time: 't' }),
      JSON.stringify({ event: 'job_completed', job_id: 'y', it: 99 }),
    ]
    // 尾行的 job_completed 带 it=99 —— 若把它当轮次就会读出「还没跑完的那一轮」
    expect(view.latestIterFromLedgerTail(lines)).toBe(6)
  })

  it('无 iteration 事件 → null；半行 JSON 跳过继续看更早的行', () => {
    expect(view.latestIterFromLedgerTail([])).toBeNull()
    expect(view.latestIterFromLedgerTail([JSON.stringify({ event: 'run_start' })])).toBeNull()
    expect(
      view.latestIterFromLedgerTail([
        JSON.stringify({ event: 'iteration', iter: 7 }),
        '{"event":"iteration","iter":8', // 写半行竞态
        '',
      ]),
    ).toBe(7)
  })
})

// ────────────────────────── 纯函数：行组装 ──────────────────────────

describe('overviewCourseNames / buildCourseRows', () => {
  it('课程清单 = 在训 ∪ hub 课程表 ∪ 课程列表 ∪ 查看课程（去重保序、空串丢弃）', () => {
    expect(
      view.overviewCourseNames({
        courses: ['c', 'a'],
        training: ['b'],
        hubOrder: ['a', 'd'],
        viewing: 'e',
      }),
    ).toEqual(['b', 'a', 'd', 'c', 'e'])
    expect(
      view.overviewCourseNames({ courses: [], training: [], hubOrder: [], viewing: '' }),
    ).toEqual([])
  })

  it('每行四态可分辨：在训 / 接管 / hub 未注册 / 停止（队列列无 hub 时归零）', () => {
    const rows = view.buildCourseRows({
      courses: ['train', 'held', 'ghost', 'idle'],
      training: ['train', 'ghost'],
      iters: { train: 12, held: 3 },
      queue: view.parseHubQueue({
        courses: {
          train: { pending_n: 2, inflight: [{ worker: 'gpu-1' }] },
          // ★M4：「离线」布尔已退役——取而代之的是 hold（谁在跑这门课的唯一真源）。
          held: {
            pending_n: 5,
            inflight: [],
            hold: { worker_id: 'tpu-1', state: 'live', last_progress_at: 1758 },
          },
          idle: { pending_n: 0, inflight: [] },
        },
        order: ['train', 'held', 'idle'],
        active_courses: 2,
        active_workers: 1,
      }),
    })
    const byName = Object.fromEntries(rows.map((r) => [r.course, r]))
    expect(byName.train).toMatchObject({
      training: true,
      iter: 12,
      queuePending: 2,
      inflight: 1,
      hold: null,
      hubSeen: true,
    })
    expect(byName.held).toMatchObject({
      training: false,
      hold: { workerId: 'tpu-1', state: 'live' },
      queuePending: 5,
    })
    // ghost 在训但 hub 不认识它 —— UI 用这一行提示「以 --course 重启 hub」
    expect(byName.ghost).toMatchObject({ training: true, hubSeen: false, queuePending: 0 })
    expect(byName.idle).toMatchObject({ training: false, hubSeen: true, iter: null })
  })

  it('★2026-10-02：stuckSec / peeked / nextJob / inflightDetail 从 hub 视图落到课程行', () => {
    const queue = view.parseHubQueue({
      courses: {
        a: {
          mode: 'online',
          pending_n: 1,
          inflight: [{ job_id: 'j1', worker: 'w1', heartbeat_ago: 5, claimed_ago: 900 }],
          next_job: 'j2',
        },
      },
      peeked_courses: ['a'],
    })!
    const [row] = view.buildCourseRows({ courses: ['a'], training: [], queue, iters: {} })
    expect(row).toMatchObject({ stuckSec: 900, peeked: true, nextJob: 'j2', halt: false })
    expect(row!.inflightDetail[0]).toMatchObject({ jobId: 'j1', worker: 'w1', claimedAgo: 900 })

    // 旧 hub：无 peeked_courses ⇒ peeked=null（不可知）；无 claimed_ago ⇒ stuckSec=null（不编龄）
    const oldQueue = view.parseHubQueue({
      courses: { a: { mode: 'online', pending_n: 0, inflight: [{ job_id: 'j1', worker: 'w1' }] } },
    })!
    const [oldRow] = view.buildCourseRows({
      courses: ['a'],
      training: [],
      queue: oldQueue,
      iters: {},
    })
    expect(oldRow!.peeked).toBeNull()
    expect(oldRow!.stuckSec).toBeNull()
    // hub 不可达（queue=null）⇒ 同样不可知，不编状态
    const [noHub] = view.buildCourseRows({ courses: ['a'], training: [], queue: null, iters: {} })
    expect(noHub!.peeked).toBeNull()
  })
})

// ────────────────────────── 组装：真（假）hub 探测 ──────────────────────────

/** 假 hub：按端点回固定 JSON（不跑真实运算；只验控制台读法）。 */
function fakeHub(opts: {
  queue?: unknown
  offline?: unknown
  pushWorkers?: unknown
  pushStatus?: number
}): {
  url: string
  stop: () => void
  hits: string[]
} {
  const hits: string[] = []
  const srv = Bun.serve({
    port: 0,
    fetch(req) {
      const p = new URL(req.url).pathname
      hits.push(p)
      if (p === '/admin/queue') {
        return new Response(JSON.stringify(opts.queue ?? {}), { status: 200 })
      }
      if (p === '/admin/offline') {
        // 旧 hub 形状（没给 offline）= 404：`offline`/`leases` 退化为 null，不编。
        if (opts.offline === undefined) return new Response('{}', { status: 404 })
        return new Response(JSON.stringify(opts.offline), { status: 200 })
      }
      if (p === '/admin/push-workers') {
        return new Response(JSON.stringify(opts.pushWorkers ?? {}), {
          status: opts.pushStatus ?? 200,
        })
      }
      return new Response('{}', { status: 404 })
    },
  })
  return { url: `http://127.0.0.1:${srv.port}`, stop: () => srv.stop(true), hits }
}

/** 让控制台把基址解析到假 hub：账本里放一条**活着**的 hub 条目（pid = 本进程）。 */
const SCRATCH: string[] = []
afterAll(() => {
  for (const d of SCRATCH) rmSync(d, { recursive: true, force: true })
})

function withLiveHub(url: string): () => void {
  const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-ovw-'))
  SCRATCH.push(scratch)
  const file = path.join(scratch, 'registry.json')
  writeFileSync(
    file,
    JSON.stringify({ hubServers: { someCourse: { pid: process.pid, course: 'someCourse', url } } }),
  )
  const prev = process.env.BCITY_REGISTRY_FILE
  process.env.BCITY_REGISTRY_FILE = file
  return () => {
    if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
    else process.env.BCITY_REGISTRY_FILE = prev
  }
}

afterEach(() => {
  api.invalidateHubAdmin()
})

const QUEUE = {
  courses: {
    c4: {
      pending_n: 1,
      pending: ['job-1'],
      inflight: [{ job_id: 'job-2', worker: 'gpu-1', heartbeat_ago: 1.5 }],
      next_job: 'job-1',
    },
    // ★M4：c5 被自主盘接管（旧的 `mode` / `authority` / `pinned` 三件已退役）
    c5: {
      hold: { worker_id: 'tpu-1', state: 'live', last_progress_at: 1758, expires_in: 870 },
      pending_n: 0,
      pending: [],
      inflight: [],
      next_job: null,
    },
  },
  order: ['c4', 'c5'],
  active_courses: 1,
  active_workers: 3,
  halt: true,
}

describe('buildOverview（hub 观测 → 总览行）', () => {
  it('hub 应答：基址/双方判据/停机 + 每课队列数上屏', async () => {
    const hub = fakeHub({
      queue: QUEUE,
      pushWorkers: { dispatcher: {}, registry: { workers: [] } },
    })
    const restore = withLiveHub(hub.url)
    try {
      const ov = await api.buildOverview(cfg(hub.url), ['c4'], 'c4')
      expect(ov.hubUrl).toBe(hub.url)
      expect(ov.hubOnline).toBe(true)
      expect(ov.activeCourses).toBe(1)
      expect(ov.activeWorkers).toBe(3)
      expect(ov.halt).toBe(true)
      const row = ov.rows.find((r) => r.course === 'c4')!
      expect(row.queuePending).toBe(1)
      expect(row.inflight).toBe(1)
      const off = ov.rows.find((r) => r.course === 'c5')!
      // ★M4 接线：hold 从 `/admin/queue` 每课行**透传**到总览行（dashboard 读它、不猜「谁在跑」）。
      expect(row.hold).toBeNull()
      expect(off.hold).toMatchObject({ workerId: 'tpu-1', state: 'live' })
      expect(hub.hits).toContain('/admin/queue')
    } finally {
      restore()
      hub.stop()
    }
  })

  it('★2026-10-05 P1-6/P1-10 + ★M4：`/admin/offline.leases` 透传 + 接管徽标与租约徽标（接线用例）', async () => {
    const hub = fakeHub({
      queue: QUEUE,
      offline: {
        progress: {},
        leases: {
          c5: { worker_id: 'tpu-1', silent_sec: 512, stale: true, revoked: false, expires_in: 388 },
        },
      },
      pushWorkers: { dispatcher: {}, registry: { workers: [] } },
    })
    const restore = withLiveHub(hub.url)
    try {
      const ov = await api.buildOverview(cfg(hub.url), ['c4', 'c5'], 'c4')
      expect(ov.rows.find((r) => r.course === 'c5')!.lease).toMatchObject({
        workerId: 'tpu-1',
        stale: true,
      })
      // 端到端：hub 行 → CourseRow → mergeCourseRows → courseStatus 的徽标
      const merged = view.mergeCourseRows({ overview: ov, queue: null, viewing: '', nowSec: 0 })
      // ★M4：顶层旧 `offline` 键已删（段内轮次只从逐课行的 `offlineProgress` 派生，一个源）。
      expect(Object.keys(ov)).not.toContain('offline')
      const c4 = merged.find((r) => r.course === 'c4')!
      expect(c4.holdBadge).toBeNull() // 没有接管 ⇒ 不画徽标
      // c4 在 hub 的队列里有待领 job，而训练侧没进程（本用例没有传 training）⇒ 正是那一档 warn。
      expect(c4.status.text).toBe('hub 已注册 · 无进程')
      const c5 = merged.find((r) => r.course === 'c5')!
      // live 接管不另画徽标（状态列与接管列已经在说它）；租约徽标仍由 hub 的 leases 面驱动。
      expect(c5.holdBadge).toBeNull()
      expect(c5.leaseBadge?.text).toBe('可接管')
      expect(c5.status.text).toBe('接管中（云机）')
      expect(c5.hold?.text).toContain('tpu-1')
    } finally {
      restore()
      hub.stop()
    }
  })

  it('无 hub 应答：hubUrl=null（UI 显示「hub 无应答」而不是编一个地址），行仍出', async () => {
    // 候选清单里只有槽位兜底（账本指向被清空）→ 探测必然失败
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(os.tmpdir(), 'bcity-ovw-absent.json')
    try {
      const ov = await api.buildOverview(cfg('http://127.0.0.1:1'), ['c4'], 'c4')
      expect(ov.hubUrl).toBeNull()
      expect(ov.hubOnline).toBe(false)
      expect(ov.rows.map((r) => r.course)).toEqual(['c4'])
      expect(ov.rows[0]).toMatchObject({ hubSeen: false, queuePending: 0, inflight: 0 })
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
    }
  })
})

describe('buildWorkerRegistry（rl-config 为条目来源 + hub 侧探活列）', () => {
  it('登记表里的在线结论挂到对应 worker 行；hub 未挂载 → mounted=false 且 hubOnline=null', async () => {
    const hub = fakeHub({
      queue: QUEUE,
      pushWorkers: {
        dispatcher: { inflight: {} },
        registry: { workers: [{ id: 'gpu1', online: true }] },
      },
    })
    const restore = withLiveHub(hub.url)
    try {
      const reg = await api.buildWorkerRegistry(cfg(hub.url), 'c4')
      expect(reg.hubUrl).toBe(hub.url)
      expect(reg.mounted).toBe(true)
      const gpu1 = reg.workers.find((w) => w.id === 'gpu1')!
      expect(gpu1.hubOnline).toBe(true)
      // 直探那一列同样有值（假 hub 只答 /admin/*，故 worker 的 /ping 必然不通）
      expect(gpu1.online).toBe(false)
      // 非 gpu_push 节点（采集节点）不进登记视图
      expect(reg.workers.some((w) => w.id === 'self')).toBe(false)
    } finally {
      restore()
      hub.stop()
    }
  })

  it('hub 未启用 push 派发（409）→ mounted=false，登记表列仍是 rl-config 的那几条', async () => {
    const hub = fakeHub({ queue: QUEUE, pushStatus: 409 })
    const restore = withLiveHub(hub.url)
    try {
      const reg = await api.buildWorkerRegistry(cfg(hub.url), 'c4')
      expect(reg.mounted).toBe(false)
      expect(reg.workers.map((w) => w.id)).toEqual(['gpu1'])
      expect(reg.workers[0]!.hubOnline).toBeNull()
    } finally {
      restore()
      hub.stop()
    }
  })
})

// ────────────────────────── 共享 trainer 存活（registry 为唯一事实源） ──────────────────────────

describe('sharedTrainerAlive / buildStateView 注入', () => {
  it('只认**共享槽**（空串）里 trainingLoop 的存活——按课查存活是共享 trainer 时代的假事实', () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'bcity-ovw2-'))
    SCRATCH.push(scratch)
    const file = path.join(scratch, 'registry.json')
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = file
    try {
      // 共享槽 + 活 pid ⇒ 在跑
      writeFileSync(file, JSON.stringify({ trainingLoops: { '': { pid: process.pid } } }))
      expect(api.sharedTrainerAlive()).toBe(true)
      // 共享槽 + 死 pid ⇒ 不在跑
      writeFileSync(file, JSON.stringify({ trainingLoops: { '': { pid: 999999999 } } }))
      expect(api.sharedTrainerAlive()).toBe(false)
      // 只有旧形状的**每课**槽（且存活）⇒ 仍算不在跑：一个进程服务所有课程，槽恒 `''`
      writeFileSync(file, JSON.stringify({ trainingLoops: { 'x1-legacy': { pid: process.pid } } }))
      expect(api.sharedTrainerAlive()).toBe(false)
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
    }
  })

  it('buildStateView 注入 overview / workerRegistry / trainingCourses（观测面坏了不 500）', async () => {
    // 注：`trainingCourses` 现在由**调度器队列行**推出（调度器存活 ∧ 该课未收官，R3-5）——
    // 本用例断言的是「注入面在、且读失败时不炸」，故只看形状。
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(os.tmpdir(), 'bcity-ovw2-absent.json')
    try {
      const s = await api.buildStateView()
      expect(Array.isArray(s.trainingCourses)).toBe(true)
      expect(s.overview?.hubOnline).toBe(false)
      expect(s.workerRegistry?.mounted).toBe(false)
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
    }
    // 守卫：读的还是夹具配置（没被这条用例改坏）
    expect(JSON.parse(readFileSync(scratchConfig, 'utf-8')).version).toBe(1)
  })
})
