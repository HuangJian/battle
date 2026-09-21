/**
 * poison-unfreeze.test.ts — §4.1 毒包熔断的**控制台面**（plan/accident.plan.md §4.1）。
 *
 * 覆盖三件盘上事实：
 *  ① 动作层 `unfreezeJob`：把「逐 job 人工解冻」如实转发给 hub，并把 hub 的原话带回来
 *     —— 200（已解冻）/ 404（未知 job）/ 409（本来就没冻）/ 不可达 四种答复**都必须可区分**
 *     （一律静默成 ok:false 会让操作员对着同一个按钮反复点）；
 *  ② 路由层：job_id 形状错误是**参数错误（400）**，不是 busy（409）——判据与动作层同源；
 *  ③ 视图层 `parseFrozenBlock` / `frozenJobs`：`/admin/queue` 每课的 `frozen` 块 → 上屏列表。
 *
 * hub 一律用**假 server**（Bun.serve 按 job_id 回固定答复）——只有真收到请求才能断言
 * 「线上字节形状」（查询串转义、Bearer 头、method）。环境重定向（config / registry /
 * console log）在 import 前赋值，绝不写脏线上 tmp/。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

const DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-poison-'))
process.env.BCITY_RL_CONFIG = path.join(DIR, 'rl-config.json')
process.env.BCITY_REGISTRY_FILE = path.join(DIR, 'registry.json')
process.env.BCITY_CONSOLE_LOG = path.join(DIR, 'console.log')
process.env.BCITY_CONSOLE_STATE = path.join(DIR, 'console-state.json')
writeFileSync(process.env.BCITY_REGISTRY_FILE, '{}', 'utf-8')
afterAll(() => {
  try {
    rmSync(DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

import { REPO_ROOT } from '../src/core/paths'
import { unfreezeJob, jobIdError } from '../src/server/actions'
import { hubUnfreeze } from '../src/stack/hub-admin'
import { routeAction } from '../src/server/api/route'
import { FROZEN_RECLAIMS, frozenJobs, parseFrozenBlock, parseHubQueue } from '../src/web/view'

// ────────────────────────── ⓪ 镜像常量（权威在 python；这里防漂） ──────────────────────────

describe('镜像常量（权威在 python）', () => {
  it('面板说的阈值 = `remote/hub_server.py::FREEZE_AFTER_RECLAIMS`', () => {
    // 源码级核对：不 import python，只读文本（同 `kickstart-receipt.test.ts` 的做法）。
    const src = readFileSync(
      path.join(REPO_ROOT, 'nn-training', 'remote', 'hub_server.py'),
      'utf-8',
    )
    const m = src.match(/^FREEZE_AFTER_RECLAIMS\s*=\s*([0-9]+)/m)
    expect(m, 'FREEZE_AFTER_RECLAIMS 未在 hub_server.py 里找到（改名了？同步镜像常量）')
    expect(FROZEN_RECLAIMS).toBe(Number(m![1]))
  })
})

/** 假 hub：按 job_id 回不同答复；记录每次请求（path+search / method / auth）。 */
function fakeHub() {
  const seen: { url: string; method: string; auth: string }[] = []
  const srv = Bun.serve({
    port: 0,
    hostname: '127.0.0.1',
    fetch(req) {
      const u = new URL(req.url)
      seen.push({
        url: u.pathname + u.search,
        method: req.method,
        auth: req.headers.get('authorization') ?? '',
      })
      const jid = u.searchParams.get('job_id') ?? ''
      if (jid.startsWith('missing'))
        return Response.json({ error: `未知 job: ${jid}` }, { status: 404 })
      if (jid.startsWith('notfrozen'))
        return Response.json({ error: '该 job 未被冻结' }, { status: 409 })
      return Response.json({ ok: true })
    },
  })
  /** 写 rl-config：把共享 hub 地址指向这台假 server。 */
  const use = (port: number, token = 'tok-x') =>
    writeFileSync(
      process.env.BCITY_RL_CONFIG!,
      JSON.stringify({ version: 1, nodes: [], rl: { hub_port: port, remote_token: token } }),
      'utf-8',
    )
  return { srv, seen, use }
}

describe('jobIdError（形状判据：参数错误，不是 busy）', () => {
  it('空 / 纯空白 → 需要 job_id', () => {
    expect(jobIdError('')).toContain('需要 job_id')
    expect(jobIdError('   ')).toContain('需要 job_id')
    expect(jobIdError(undefined)).toContain('需要 job_id')
  })

  it('含空格 / 路径符 / 超长 → 非法（它要进查询串与 hub 日志）', () => {
    for (const bad of ['a b', '../x', 'a/b', 'x'.repeat(65)]) {
      expect(jobIdError(bad), bad).toContain('非法')
    }
  })

  it('hub 的 job_id 形状（十六进制/短横线/点/下划线）全部放行', () => {
    for (const good of ['it58-0c1f', 'abc.DEF_123', 'a']) expect(jobIdError(good), good).toBeNull()
  })
})

describe('unfreezeJob：四种 hub 答复必须可区分', () => {
  it('200 → 已解冻，且带 hub 基址 + 「重发不解冻」的提醒', async () => {
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const r = await unfreezeJob('x20-clutch', 'job-ok-1')
      expect(r.ok).toBe(true)
      expect(r.message).toContain('已解冻')
      expect(r.detail?.[0]).toContain(`127.0.0.1:${hub.srv.port}`)
      expect(r.detail?.[1]).toContain('重发**不会**替你解冻')
      // 线上字节形状：POST + job_id 查询串 + Bearer token（token 来自 rl-config，非硬编码）
      expect(hub.seen).toEqual([
        { url: '/admin/unfreeze?job_id=job-ok-1', method: 'POST', auth: 'Bearer tok-x' },
      ])
    } finally {
      hub.srv.stop(true)
    }
  })

  it('带保留字符的 job_id 在动作层就被挡下（一个请求都不发）', async () => {
    // 形状判据（`[A-Za-z0-9._-]`）比转义更早生效——所以这里断言的是「没发出去」。
    // 转义本身由下一例直接钉在 hub 客户端（`hubUnfreeze`，动作层之下那一层）。
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const r = await unfreezeJob('c', 'a+b&c')
      expect(r.ok).toBe(false)
      expect(hub.seen).toEqual([])
    } finally {
      hub.srv.stop(true)
    }
  })

  it('hub 客户端本身对查询串转义（不许把 URL 拼坏——读的是线上字节）', async () => {
    const hub = fakeHub()
    try {
      await hubUnfreeze(`http://127.0.0.1:${hub.srv.port}/`, 'tok-x', 'a+b&c')
      expect(hub.seen[0]!.url).toBe('/admin/unfreeze?job_id=a%2Bb%26c')
    } finally {
      hub.srv.stop(true)
    }
  })

  it('404 未知 job → 不 ok，且把 hub 的原话带回来（「本来就没冻」与「解冻失败」是两件事）', async () => {
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const r = await unfreezeJob('c', 'missing-9')
      expect(r.ok).toBe(false)
      expect(r.message).toContain('未解冻 job missing-9')
      expect(r.message).toContain('未知 job: missing-9')
      expect(r.detail?.[0]).toContain('先看队列里那一行的 frozen 块')
    } finally {
      hub.srv.stop(true)
    }
  })

  it('409 未被冻结 → 同样是非 ok（不静默成同一个 false）', async () => {
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const r = await unfreezeJob('c', 'notfrozen-1')
      expect(r.ok).toBe(false)
      expect(r.message).toContain('未被冻结')
    } finally {
      hub.srv.stop(true)
    }
  })

  it('hub 不可达 → 失败并点名试过的地址（不是静默成功）', async () => {
    // 端口 1 → 立即连接拒绝。
    writeFileSync(
      process.env.BCITY_RL_CONFIG!,
      JSON.stringify({ version: 1, nodes: [], rl: { hub_port: 1, remote_token: 'tok-x' } }),
      'utf-8',
    )
    const r = await unfreezeJob('c', 'job-ok-1')
    expect(r.ok).toBe(false)
    expect(r.detail?.join('\n')).toContain('试过的 hub')
  })

  it('脏 job_id 直连动作层也不发出去（防御性同源校验）', async () => {
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const r = await unfreezeJob('c', 'bad id')
      expect(r.ok).toBe(false)
      expect(r.message).toContain('非法')
      expect(hub.seen).toEqual([]) // 一个请求都没发
    } finally {
      hub.srv.stop(true)
    }
  })
})

describe('路由接线：unfreeze-job', () => {
  it('★ 形状错误 = 400（参数错误），不是 409（busy）', async () => {
    const resp = await routeAction('unfreeze-job', { course: 'c', jobId: 'bad id' })
    expect(resp!.status).toBe(400)
    expect(((await resp!.json()) as { ok: boolean }).ok).toBe(false)
    const empty = await routeAction('unfreeze-job', { course: 'c' })
    expect(empty!.status).toBe(400)
  })

  it('合法 id → 200，业务失败也只是 ok:false（HTTP 照旧 200，UI 认 ok 字段）', async () => {
    const hub = fakeHub()
    try {
      hub.use(Number(hub.srv.port))
      const ok = await routeAction('unfreeze-job', { course: 'x20-clutch', jobId: 'job-ok-2' })
      expect(ok!.status).toBe(200)
      expect((await ok!.json()) as { ok: boolean }).toEqual(expect.objectContaining({ ok: true }))
      expect(hub.seen[0]!.url).toBe('/admin/unfreeze?job_id=job-ok-2')
    } finally {
      hub.srv.stop(true)
    }
    // hub 不可达：仍 200 + ok:false（横幅一句人话，而不是 5xx）
    writeFileSync(
      process.env.BCITY_RL_CONFIG!,
      JSON.stringify({ version: 1, nodes: [], rl: { hub_port: 1, remote_token: 'x' } }),
      'utf-8',
    )
    const fail = await routeAction('unfreeze-job', { course: 'c', jobId: 'job-ok-3' })
    expect(fail!.status).toBe(200)
    expect(((await fail!.json()) as { ok: boolean }).ok).toBe(false)
  })
})

describe('冻结上屏：parseFrozenBlock / frozenJobs', () => {
  it('解析 `{job_id: {reclaims, worker, ts}}`，按零回传次数降序（最该看的在最前）', () => {
    const list = parseFrozenBlock({
      'job-b': { reclaims: 3, worker: 'w-b', ts: 100 },
      'job-a': { reclaims: 7, worker: 'w-a', ts: 200 },
      'job-c': { reclaims: 3, worker: '', ts: 0 },
    })
    expect(list.map((f) => f.jobId)).toEqual(['job-a', 'job-b', 'job-c'])
    expect(list[0]).toEqual({ jobId: 'job-a', reclaims: 7, worker: 'w-a', ts: 200 })
  })

  it('形状不符 / 缺块 → 空数组（宽 parse：缺这一块 = 这个 hub 版本还没有熔断）', () => {
    for (const bad of [null, undefined, 'nope', 42, []]) expect(parseFrozenBlock(bad)).toEqual([])
    expect(parseFrozenBlock({ 'job-x': 'garbage' })).toEqual([
      { jobId: 'job-x', reclaims: 0, worker: '', ts: 0 },
    ])
  })

  it('端到端：`/admin/queue` 的 frozen 块 → 课程行 → 跨课程展平列表', () => {
    const q = parseHubQueue({
      courses: {
        a: { frozen: { 'job-1': { reclaims: 3, worker: 'w1', ts: 5 } } },
        b: { frozen: { 'job-2': { reclaims: 4, worker: 'w2', ts: 6 } } },
        c: {},
      },
    })!
    expect(q.courses.a!.frozen).toHaveLength(1)
    expect(q.courses.c!.frozen).toEqual([])
    const overview = {
      hubUrl: 'http://127.0.0.1:1',
      rows: [
        { course: 'a', frozen: q.courses.a!.frozen },
        { course: 'b', frozen: q.courses.b!.frozen },
      ],
      hubOnline: true,
    } as unknown as Parameters<typeof frozenJobs>[0]
    expect(frozenJobs(overview).map((f) => `${f.course}/${f.jobId}`)).toEqual([
      'a/job-1',
      'b/job-2',
    ])
    expect(frozenJobs(null)).toEqual([])
  })
})
