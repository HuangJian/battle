/**
 * server-actions-worker-register.test.ts — GPU push worker 登记（回写 rl-config + 叫醒 hub）
 *
 * 分层：src/server/actions/workers.ts（动作层）+ src/server/api/route.ts 的动作接线
 *
 * 口径（2026-09-18 用户指令「dashboard 提供 worker 登记入口，回填 rl-config.json」）：
 *   · 登记 = upsert `nodes[]` 的 `gpu_push` 条目；**ping 不通不拦登记**（云机没开机是常态），
 *     但返回值如实报 `ok:false` + 怎么查；
 *   · 探活失败的写法与被测代码同源（fail-loud，绝不静默）；
 *   · 节点的增删改**只动 `nodes[]` 一处**（2026-09-19 用户口径「课程任务与 worker 节点互相正交」）：
 *     旧形状还要顺带改写 `courses.<课>.push_node_url`——那个键把「哪门课钉到哪台机器」变成课程
 *     属性，已从类型表与 python 读面一并拿掉（残留值由启动时的 prune 清）。
 *
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { afterAll, afterEach, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { loadConfig, postJson, readConfigText, scratchConfig } from './helpers/console-fixture'
import { api } from './helpers/console-fixture'

/** 夹具种子原文（每条用例后还原，互不串味）。 */
const SEED = readFileSync(scratchConfig, 'utf-8')

afterEach(() => {
  writeFileSync(scratchConfig, SEED)
  api.invalidateHubAdmin()
})

/** 往（scratch）配置里并一层补丁（courses 块等；落盘后动作层读的就是它）。 */
function seed(patch: Record<string, unknown>): void {
  const cur = JSON.parse(readFileSync(scratchConfig, 'utf-8')) as Record<string, unknown>
  writeFileSync(scratchConfig, JSON.stringify({ ...cur, ...patch }, null, 2))
}

/** 一条 gpu_push 节点在配置里的样子（找不到 → null）。 */
function worker(id: string): Record<string, unknown> | null {
  const n = loadConfig().nodes.find((x) => x.id === id)
  return (n as unknown as Record<string, unknown>) ?? null
}

/** 课程块（原文读，用于断言「节点增删改不碰课程块」）。 */
function courseBlock(course: string): Record<string, unknown> {
  const cfg = JSON.parse(readFileSync(scratchConfig, 'utf-8')) as {
    courses?: Record<string, Record<string, unknown>>
  }
  return cfg.courses?.[course] ?? {}
}

/** 假 worker_server（`/ping` 回 200 + busy）——只为 ping 门给一个确定的「通」。 */
function fakeWorker(busy = false): { url: string; stop: () => void } {
  const srv = Bun.serve({
    port: 0,
    fetch: () => new Response(JSON.stringify({ ok: true, busy }), { status: 200 }),
  })
  return { url: `http://127.0.0.1:${srv.port}`, stop: () => srv.stop(true) }
}

/** 假 worker_server：**401**（= 探过、不通的确定结论）。
 *
 *  为什么不用 `http://127.0.0.1:1` 之类不可达地址：本环境对回环拒绝连接是 **DROP**，
 *  fetch 会一路挂到 1500ms 直探超时——用例白等 1.5s/条，且验的是超时不是鉴权。 */
function fakeWorkerReject(): { url: string; stop: () => void } {
  const srv = Bun.serve({
    port: 0,
    fetch: () => new Response('{"error":"unauthorized"}', { status: 401 }),
  })
  return { url: `http://127.0.0.1:${srv.port}`, stop: () => srv.stop(true) }
}

/** 让「叫醒 hub」那条路径有对象：账本里一条**活着**的 hub 条目 + 记账用的假 hub。 */
const SCRATCH: string[] = []
afterAll(() => {
  for (const d of SCRATCH) rmSync(d, { recursive: true, force: true })
})

function withLiveHub(url: string): () => void {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-wreg-'))
  SCRATCH.push(dir)
  const file = path.join(dir, 'registry.json')
  writeFileSync(file, JSON.stringify({ hubServers: { c: { pid: process.pid, course: 'c', url } } }))
  const prev = process.env.BCITY_REGISTRY_FILE
  process.env.BCITY_REGISTRY_FILE = file
  return () => {
    if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
    else process.env.BCITY_REGISTRY_FILE = prev
  }
}

// ────────────────────────── 登记 ──────────────────────────

describe('registerPushWorker：ping 不通不拦登记（如实报告）', () => {
  it('新 worker 写进 nodes[]（gpu_push/enabled/并发），返回值点名怎么查', async () => {
    const w = fakeWorkerReject() // 401 = 探过、不通（确定结论）
    try {
      const r = await postJson('registerPushWorker', {
        id: 'gpu-new',
        url: w.url,
        authKey: 'k',
        concurrency: 3,
      })
      expect(r.ok).toBe(false)
      expect(String(r.message)).toContain('gpu-new')
      expect(String(r.message)).toContain('/ping 不通')
      expect(String(r.message)).toContain('authKey')
      // 配置照写（登记 ≠ 探活成功）
      expect(worker('gpu-new')).toMatchObject({
        url: w.url,
        authKey: 'k',
        concurrency: 3,
        enabled: true,
        gpu_push: true,
      })
      // 明细里如实说明 hub 没被叫醒（没 hub 在跑）
      expect((r.detail as string[]).join(' ')).toContain('hub 热重载：未叫醒')
    } finally {
      w.stop()
    }
  })

  it('ping 通 → ok:true；URL 去空格 + 去尾斜杠，authKey 去空格', async () => {
    const w = fakeWorker()
    try {
      const r = await postJson('registerPushWorker', {
        id: 'gpu-ok',
        url: `  ${w.url}///  `,
        authKey: ' k2 ',
        concurrency: 1,
      })
      expect(r.ok).toBe(true)
      expect(String(r.message)).toContain('ping 通')
      expect(worker('gpu-ok')).toMatchObject({ url: w.url, authKey: 'k2', concurrency: 1 })
    } finally {
      w.stop()
    }
  })

  it('并发缺省 = 1（不在这里编默认值的第二份口径）', async () => {
    const w = fakeWorkerReject()
    try {
      await postJson('registerPushWorker', { id: 'gpu-d', url: w.url, authKey: 'k' })
      expect(worker('gpu-d')!.concurrency).toBe(1)
    } finally {
      w.stop()
    }
  })

  it('写盘后叫醒 hub 立即重读配置（账本里有活 hub 时）', async () => {
    const hits: string[] = []
    const hub = Bun.serve({
      port: 0,
      fetch(req) {
        hits.push(`${req.method} ${new URL(req.url).pathname}`)
        return new Response('{"action":"reload"}', { status: 200 })
      },
    })
    const w = fakeWorkerReject()
    const restore = withLiveHub(`http://127.0.0.1:${hub.port}`)
    try {
      const r = await postJson('registerPushWorker', {
        id: 'gpu-nudge',
        url: w.url,
        authKey: 'k',
      })
      expect((r.detail as string[]).join(' ')).toContain('已立即拾取')
      expect(hits).toContain('POST /admin/push-workers')
    } finally {
      restore()
      hub.stop(true)
      w.stop()
    }
  })
})

describe('registerPushWorker：非法入参响亮拒（磁盘保持原样）', () => {
  const cases: Array<[string, Record<string, unknown>, RegExp]> = [
    [
      'id 非法（空格/路径分隔符会进日志与 job 归属标记）',
      { id: 'a b', url: 'http://x', authKey: 'k' },
      /worker id 非法/,
    ],
    ['id 空', { id: '', url: 'http://x', authKey: 'k' }, /worker id 非法/],
    ['authKey 空', { id: 'ok-id', url: 'http://x', authKey: '  ' }, /authKey 不能为空/],
    ['url 空', { id: 'ok-id', url: '', authKey: 'k' }, /不能为空/],
    ['url 协议非法', { id: 'ok-id', url: 'ftp://x', authKey: 'k' }, /协议非法/],
    [
      '并发非整数',
      { id: 'ok-id', url: 'http://x', authKey: 'k', concurrency: 0 },
      /并发数需为 1-64/,
    ],
    [
      '并发越界',
      { id: 'ok-id', url: 'http://x', authKey: 'k', concurrency: 65 },
      /并发数需为 1-64/,
    ],
  ]
  for (const [name, body, re] of cases) {
    it(name, async () => {
      const before = readConfigText()
      const r = await postJson('registerPushWorker', body)
      expect(r.__status).toBe(409)
      expect(String(r.message)).toMatch(re)
      expect(readConfigText()).toBe(before) // 没有任何半截/越界落盘
    })
  }

  it('id 撞上 rollout 节点 → 拒（不把采集节点改写成 push worker）', async () => {
    const before = readConfigText()
    const r = await postJson('registerPushWorker', {
      id: 'self',
      url: 'http://127.0.0.1:1',
      authKey: 'k',
    })
    expect(r.__status).toBe(409)
    expect(String(r.message)).toContain('已是 rollout 节点')
    expect(readConfigText()).toBe(before)
  })
})

describe('registerPushWorker：只动 nodes[]，不碰课程块（正交性）', () => {
  it('同 id 改 url：nodes[] 原地更新；课程块**一个字不动**（旧形状的 push_node_url 已无读者）', async () => {
    const a = fakeWorkerReject()
    const b = fakeWorkerReject()
    try {
      seed({
        courses: {
          c4: { slot: 0, remote_degrade_after: 3 },
          c5: { slot: 1 },
        },
      })
      const beforeC4 = courseBlock('c4')
      const first = await postJson('registerPushWorker', { id: 'gpu1', url: a.url, authKey: 'k' })
      expect(first.ok).toBe(false) // 探不通，但配置已落
      expect(worker('gpu1')!.url).toBe(a.url)
      // 改地址：只换 nodes[] 里那一行
      const second = await postJson('registerPushWorker', { id: 'gpu1', url: b.url, authKey: 'k' })
      expect(worker('gpu1')!.url).toBe(b.url)
      expect(courseBlock('c4')).toEqual(beforeC4)
      expect(courseBlock('c5')).toEqual({ slot: 1 })
      expect((second.detail as string[]).join(' ')).not.toContain('课程指针')
    } finally {
      a.stop()
      b.stop()
    }
  })
})

// ────────────────────────── 移除 ──────────────────────────

describe('removePushWorker', () => {
  it('移除节点后它不再是候选；课程块不动（没有「指向它」这回事了）', async () => {
    seed({ courses: { c4: { slot: 0 }, c5: {} } })
    const r = await postJson('removePushWorker', { id: 'gpu1' })
    expect(r.ok).toBe(true)
    expect(worker('gpu1')).toBeNull()
    expect(courseBlock('c4')).toEqual({ slot: 0 })
    expect((r.detail as string[]).join(' ')).toContain('未登记即不再是候选')
  })

  it('不存在 → 409；非 push worker（采集节点）→ 409 且不动它', async () => {
    expect((await postJson('removePushWorker', { id: 'nope' })).__status).toBe(409)
    const r = await postJson('removePushWorker', { id: 'self' })
    expect(r.__status).toBe(409)
    expect(String(r.message)).toContain('不是 push worker')
    expect(worker('self')).not.toBeNull()
  })
})

// ────────────────────────── 手动重载 ──────────────────────────

describe('reloadPushWorkers', () => {
  it('没有 hub 应答 → ok:false（配置已落盘，hub 起来后按 mtime 自动拾取），不抛', async () => {
    const prev = process.env.BCITY_REGISTRY_FILE
    process.env.BCITY_REGISTRY_FILE = path.join(os.tmpdir(), 'bcity-wreg-absent.json')
    try {
      const r = await postJson('reloadPushWorkers', {})
      expect(r.ok).toBe(false)
      expect(String(r.message)).toContain('没有 hub 在应答')
    } finally {
      if (prev === undefined) delete process.env.BCITY_REGISTRY_FILE
      else process.env.BCITY_REGISTRY_FILE = prev
    }
  })
})
