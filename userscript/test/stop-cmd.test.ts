/**
 * stop-cmd.test.ts — 下行命令轮询（`stopOthers` → 其它页停会话）的行为契约。
 *
 * 要点：
 *   ① 不知道自己是谁（session 空）就不发请求 —— 绝不拿别人的命令停自己；
 *   ② 收到 stop-session 就停一次，并落 ack 行 `id=… ok`；
 *   ③ 同一个 id 第二次取到不重复停（服务端 ack 之外的第二道）；
 *   ④ 停失败要 ack `fail` + 原因；网络错/坏 JSON 当这一拍没取到，不炸；
 *   ⑤ 只认 stop-session，其它 action 一律不执行。
 */
import { describe, expect, it } from 'bun:test'
import {
  ackLine,
  createStopCmdPoller,
  parseStopCmds,
  type StopCmdResult,
} from '../src/notebook-companion/app/stop-cmd'
import type { Runtime } from '../src/runtime'

interface Call {
  method: string
  url: string
  headers?: Record<string, string>
}

function harness(
  responses: Array<{ status: number; body: string } | Error>,
  opts: { session?: string; results?: StopCmdResult[] } = {},
) {
  const calls: Call[] = []
  const events: Array<{ op: string; detail?: string }> = []
  let stops = 0
  const queue = [...responses]
  const runtime = {
    gmRequest: async (req: Call) => {
      calls.push(req)
      const next = queue.shift()
      if (!next) throw new Error('no more responses')
      if (next instanceof Error) throw next
      return { status: next.status, responseText: next.body }
    },
  } as unknown as Pick<Runtime, 'gmRequest'>
  const poller = createStopCmdPoller({
    runtime,
    dashboardBase: 'http://127.0.0.1:8900',
    site: 'kaggle',
    session: () => (opts.session === undefined ? 'kaggle-run-1' : opts.session),
    stop: async () => {
      stops += 1
      return opts.results?.shift() ?? { ok: true, detail: '已停止' }
    },
    onEvent: (op, detail) => events.push({ op, ...(detail ? { detail } : {}) }),
  })
  return { poller, calls, events, stops: () => stops }
}

const cmdBody = (ids: string[]): string =>
  JSON.stringify({
    ok: true,
    cmds: ids.map((id) => ({ id, action: 'stop-session', at: 1, by: 'kaggle:self' })),
  })

describe('取件请求', () => {
  it('会话还没定（空串）⇒ 一个请求都不发', async () => {
    const h = harness([], { session: '' })
    expect(await h.poller.pollOnce()).toBe(0)
    expect(h.calls.length).toBe(0)
  })

  it('带自定义头与 site/session 参数，GET 一次', async () => {
    const h = harness([{ status: 200, body: cmdBody([]) }])
    expect(await h.poller.pollOnce()).toBe(0)
    expect(h.calls.length).toBe(1)
    expect(h.calls[0]!.method).toBe('GET')
    expect(h.calls[0]!.url).toBe(
      'http://127.0.0.1:8900/api/notebookCmds?site=kaggle&session=kaggle-run-1',
    )
    expect(h.calls[0]!.headers?.['X-Notebook-Companion']).toBe('1')
  })
})

describe('执行与 ack', () => {
  it('收到 stop-session ⇒ 停一次 + ack id=… ok', async () => {
    const h = harness([{ status: 200, body: cmdBody(['cmd-1']) }])
    expect(await h.poller.pollOnce()).toBe(1)
    expect(h.stops()).toBe(1)
    expect(h.events).toEqual([
      { op: 'cmd:stop-session', detail: 'id=cmd-1 ok 已停止 by=kaggle:self' },
    ])
  })

  it('同一个 id 再取到不重复停（内存集合挡住）', async () => {
    const h = harness([
      { status: 200, body: cmdBody(['cmd-1']) },
      { status: 200, body: cmdBody(['cmd-1']) },
    ])
    expect(await h.poller.pollOnce()).toBe(1)
    expect(await h.poller.pollOnce()).toBe(0)
    expect(h.stops()).toBe(1)
    expect(h.events.length).toBe(1)
  })

  it('停失败 ⇒ ack id=… fail + 原因（绝不静默）', async () => {
    const h = harness([{ status: 200, body: cmdBody(['cmd-9']) }], {
      results: [{ ok: false, reason: '停止按钮没找到' }],
    })
    await h.poller.pollOnce()
    expect(h.events[0]?.detail).toBe('id=cmd-9 fail 停止按钮没找到 by=kaggle:self')
  })

  it('stop() 抛异常 ⇒ 当失败 ack', async () => {
    const calls: string[] = []
    const events: string[] = []
    const poller = createStopCmdPoller({
      runtime: {
        gmRequest: async () => ({ status: 200, responseText: cmdBody(['cmd-x']) }),
      } as unknown as Pick<Runtime, 'gmRequest'>,
      dashboardBase: 'http://127.0.0.1:8900',
      site: 'colab',
      session: () => 'colab-1',
      stop: async () => {
        calls.push('stop')
        throw new Error('adapter 挂了')
      },
      onEvent: (_op, detail) => events.push(detail ?? ''),
    })
    await poller.pollOnce()
    expect(calls.length).toBe(1)
    expect(events[0]).toContain('id=cmd-x fail adapter 挂了')
  })

  it('非 stop-session 的 action 不执行（取件口将来可能加别的命令）', async () => {
    const h = harness([
      {
        status: 200,
        body: JSON.stringify({
          ok: true,
          cmds: [{ id: 'cmd-2', action: 'run-cell', at: 1, by: 'x' }],
        }),
      },
    ])
    expect(await h.poller.pollOnce()).toBe(0)
    expect(h.stops()).toBe(0)
    expect(h.events).toEqual([])
  })
})

describe('坏响应', () => {
  it('HTTP 5xx / 网络异常 / 坏 JSON ⇒ 这一拍当没取到，不炸也不 ack', async () => {
    const cases: Array<{ status: number; body: string } | Error> = [
      { status: 503, body: '' },
      new Error('timeout'),
      { status: 200, body: 'not json' },
      { status: 200, body: JSON.stringify({ ok: false }) },
    ]
    for (const c of cases) {
      const h = harness([c])
      expect(await h.poller.pollOnce()).toBe(0)
      expect(h.stops()).toBe(0)
      expect(h.events).toEqual([])
    }
  })

  it('parseStopCmds 丢掉非法 id / 非对象行', () => {
    expect(parseStopCmds(null)).toEqual([])
    expect(parseStopCmds({ ok: true, cmds: 'x' })).toEqual([])
    expect(
      parseStopCmds({
        ok: true,
        cmds: [
          { id: 'bad id!', action: 'stop-session' },
          null,
          { id: 'good-1', action: 'stop-session', at: 5, by: 'kaggle:self' },
        ],
      }),
    ).toEqual([{ id: 'good-1', action: 'stop-session', at: 5, by: 'kaggle:self' }])
  })

  it('ackLine 把换行拍平（op 日志一行一条）', () => {
    expect(ackLine('cmd-1', { ok: false, reason: 'a\nb' })).toBe('id=cmd-1 fail a b')
    expect(ackLine('cmd-1', { ok: true })).toBe('id=cmd-1 ok')
  })
})
