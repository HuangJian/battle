import { describe, expect, it } from 'bun:test'
import { createOpLog, OP_MAX_DETAIL, type OpEntry } from '../src/notebook-companion/app/op-log'
import type { GmRequest, GmResponse } from '../src/runtime'

/**
 * 操作日志上送的行为契约（plan §3.3 附注 / DECISIONS）：
 *   - 面板点一下 = 一条结构化 op，走 `/api/notebookOp`，带自定义头；
 *   - 会话名还没定 ⇒ 不写（不许写进假会话）；
 *   - 失败不阻塞、不无限重试、队列有界；detail 拍成单行且截断。
 */
interface Sent {
  url: string
  headers?: Record<string, string>
  entry: OpEntry
}

function fakeRuntime(handler?: (req: GmRequest) => boolean): {
  runtime: { gmRequest: (req: GmRequest) => Promise<GmResponse> }
  sent: Sent[]
} {
  const sent: Sent[] = []
  return {
    sent,
    runtime: {
      gmRequest: async (req: GmRequest): Promise<GmResponse> => {
        const ok = handler ? handler(req) : true
        if (!ok) throw new Error('boom')
        sent.push({
          url: req.url,
          headers: req.headers,
          entry: JSON.parse(req.data ?? '{}') as OpEntry,
        })
        return { status: 200, statusText: 'OK', responseText: '{"ok":true}', responseHeaders: '' }
      },
    },
  }
}

const base = (over: Record<string, unknown> = {}) => {
  const h = fakeRuntime()
  return {
    sent: h.sent,
    ops: createOpLog({
      runtime: h.runtime,
      dashboardBase: 'http://127.0.0.1:8900',
      site: 'kaggle',
      session: () => 'kaggle-owner-slug-edit-ab12',
      page: 'ab12',
      writer: 'host-ab12',
      now: () => 1_790_000_000_000,
      flushMs: 1_000_000, // 手动 flush：测试不碰计时器
      ...over,
    }),
    ...over,
  } as const
}

describe('操作日志：上送形状', () => {
  it('一条 op = 一个 POST /api/notebookOp（自定义头 + 结构化字段）', async () => {
    const { ops, sent } = base()
    ops.log('cmd:flush', 'ok')
    expect(await ops.flush()).toBe(true)
    expect(sent).toHaveLength(1)
    expect(sent[0]!.url).toBe('http://127.0.0.1:8900/api/notebookOp')
    expect(sent[0]!.headers?.['X-Notebook-Companion']).toBe('1')
    expect(sent[0]!.headers?.['Content-Type']).toBe('application/json')
    expect(sent[0]!.entry).toMatchObject({
      v: 1,
      site: 'kaggle',
      session: 'kaggle-owner-slug-edit-ab12',
      page: 'ab12',
      writer: 'host-ab12',
      at: 1_790_000_000_000,
      op: 'cmd:flush',
      detail: 'ok',
    })
  })

  it('会话名还没定（host 未握手）⇒ 不写；定了以后照常写', async () => {
    let session = ''
    const { ops, sent } = base({ session: () => session })
    ops.log('boot')
    expect(ops.pending()).toBe(0)
    session = 'kaggle-owner-slug-edit'
    ops.log('boot')
    await ops.flush()
    expect(sent.map((s) => s.entry.session)).toEqual(['kaggle-owner-slug-edit'])
  })

  it('op 名归一（非法字符 → -）、detail 拍单行并截断', async () => {
    const { ops, sent } = base()
    ops.log('cmd: save&clear / 中文', `${'x'.repeat(OP_MAX_DETAIL + 50)}\n第二行\tTab`)
    await ops.flush()
    const e = sent[0]!.entry
    expect(e.op).toMatch(/^[A-Za-z0-9:_.-]+$/)
    expect(e.detail?.length).toBe(OP_MAX_DETAIL)
    expect(e.detail).not.toContain('\n')
    expect(e.detail).not.toContain('\t')
  })
})

describe('操作日志：失败与队列', () => {
  it('上送失败 ⇒ 条目留在队列（下次再发），不抛', async () => {
    let fail = true
    const h = fakeRuntime(() => !fail)
    const ops = createOpLog({
      runtime: h.runtime,
      dashboardBase: 'http://127.0.0.1:8900',
      site: 'colab',
      session: () => 'colab-abc',
      page: 'p1',
      writer: 'colab-p1',
      flushMs: 1_000_000,
    })
    ops.log('cmd:runCell')
    expect(await ops.flush()).toBe(false)
    expect(ops.pending()).toBe(1)
    fail = false
    expect(await ops.flush()).toBe(true)
    expect(ops.pending()).toBe(0)
    expect(h.sent[0]!.entry.op).toBe('cmd:runCell')
  })

  it('队列有界：超限丢最旧的，并在下一条 detail 里报 dropped（不静默）', async () => {
    const { ops, sent } = base({ maxQueue: 2 })
    ops.log('a')
    ops.log('b')
    ops.log('c') // 丢 a
    await ops.flush()
    expect(sent.map((s) => s.entry.op)).toEqual(['b', 'c'])
    expect(sent.some((s) => (s.entry.detail ?? '').includes('dropped=1'))).toBe(true)
  })
})
