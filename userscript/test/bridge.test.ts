import { describe, expect, it } from 'bun:test'
import {
  createFrameBridge,
  createHostBridge,
  type BridgeEvent,
  type BridgeWindow,
} from '../src/notebook-companion/app/bridge'

interface FakeWindow {
  win: BridgeWindow
  sent: Array<{ target: unknown; msg: unknown; origin: string }>
  fire: (e: { data: unknown; origin: string; source?: unknown }) => void
}

function fakeWindow(self: unknown = 'frame-self', parent: unknown = 'frame-parent'): FakeWindow {
  const listeners: Array<(e: BridgeEvent) => void> = []
  const sent: Array<{ target: unknown; msg: unknown; origin: string }> = []
  return {
    win: {
      parent,
      addEventListener: (_type, fn) => listeners.push(fn),
      postMessage: (target, msg, origin) => sent.push({ target, msg, origin }),
    },
    sent,
    fire(e) {
      for (const fn of listeners) {
        fn({ ...e, source: e.source ?? parent, self, parent })
      }
    },
  }
}

const KAGGLE = 'https://www.kaggle.com'
const FRAME = 'https://kkb-production.jupyter-proxy.kaggle.net'

describe('bridge：host ⇄ frame', () => {
  it('host：只认当前 nonce 的 ready/result；错 nonce 的结果不 resolve', async () => {
    const f = fakeWindow()
    let readyCount = 0
    const host = createHostBridge(f.win, { timeoutMs: 60, onReady: () => (readyCount += 1) })
    host.attach(FRAME, 'frame-window', 'n1')
    expect(f.sent[0]!.origin).toBe(FRAME)
    expect(f.sent[0]!.target).toBe('frame-window') // 必须给 iframe.contentWindow，不能发给自己
    expect((f.sent[0]!.msg as { nonce?: string }).nonce).toBe('n1')

    f.fire({ data: { __bnc: 1, kind: 'ready', nonce: 'wrong' }, origin: FRAME })
    expect(readyCount).toBe(0)
    f.fire({ data: { __bnc: 1, kind: 'ready', nonce: 'n1' }, origin: FRAME })
    expect(readyCount).toBe(1)

    const pending = host.send('status')
    const cmd = f.sent[1]!.msg as { id?: string }
    f.fire({
      data: { __bnc: 1, kind: 'result', nonce: 'wrong', id: cmd.id, ok: true },
      origin: FRAME,
    })
    f.fire({
      data: { __bnc: 1, kind: 'result', nonce: 'n1', id: cmd.id, ok: true, value: { cells: 3 } },
      origin: FRAME,
    })
    expect(await pending).toEqual({ ok: true, value: { cells: 3 } })
    host.destroy()
  })

  it('host：setSessionBase 后 attach 带上 notebook 基（frame 不必再猜 unknown）', () => {
    const f = fakeWindow()
    const host = createHostBridge(f.win)
    host.setSessionBase('kaggle-Huang-rollout-edit')
    host.attach(FRAME, 'frame-window', 'n1')
    expect(f.sent[0]!.msg).toMatchObject({
      kind: 'attach',
      nonce: 'n1',
      sessionBase: 'kaggle-Huang-rollout-edit',
    })
    host.destroy()
  })

  it('host：frame 不回 ⇒ 超时是失败（不假装成功）', async () => {
    const f = fakeWindow()
    const host = createHostBridge(f.win, { timeoutMs: 20 })
    host.attach(FRAME, 'frame-window', 'n1')
    const r = await host.send('flush')
    expect(r.ok).toBe(false)
    expect(r.reason).toContain('超时')
    host.destroy()
  })

  it('frame：拒绝非白名单 origin / 错 nonce 的命令', async () => {
    const f = fakeWindow('self', 'parent')
    const handled: string[] = []
    const bridge = createFrameBridge(f.win, {
      hostOrigins: [KAGGLE],
      isReady: () => true,
      session: () => 'notebook:kaggle-a-b-edit-ab12cd',
      handle: async (name) => {
        handled.push(name)
        return { ok: true, value: name }
      },
    })

    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: 'https://evil.test' })
    expect(f.sent).toHaveLength(0)

    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: KAGGLE })
    expect(f.sent[0]!.origin).toBe(KAGGLE)
    expect(f.sent[0]!.target).toBe('parent') // ready 必须回给 parent，不是自己
    // 会话键必须随 ready 一起回来：host 的「打开日志」只能指这一页的，不能拿外层 URL 猜
    expect(f.sent[0]!.msg).toMatchObject({
      kind: 'ready',
      nonce: 'n1',
      notebook: true,
      session: 'notebook:kaggle-a-b-edit-ab12cd',
    })
    expect(bridge.nonce).toBe('n1')

    f.fire({
      data: { __bnc: 1, kind: 'cmd', nonce: 'bad', id: 'a', name: 'flush' },
      origin: KAGGLE,
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(handled).toEqual([])

    f.fire({ data: { __bnc: 1, kind: 'cmd', nonce: 'n1', id: 'b', name: 'flush' }, origin: KAGGLE })
    await new Promise((r) => setTimeout(r, 0))
    expect(handled).toEqual(['flush'])
    expect(f.sent[1]!.msg).toMatchObject({ kind: 'result', id: 'b', nonce: 'n1', ok: true })
  })

  it('frame：attach 里的 sessionBase 交给 onAttach（boot 用它改日志键）', () => {
    const f = fakeWindow('self', 'parent')
    const seen: string[] = []
    createFrameBridge(f.win, {
      hostOrigins: [KAGGLE],
      handle: async () => ({ ok: true }),
      onAttach: (m) => {
        if (m.sessionBase) seen.push(m.sessionBase)
      },
    })
    f.fire({
      data: {
        __bnc: 1,
        kind: 'attach',
        nonce: 'n1',
        sessionBase: 'kaggle-Huang-rollout-edit',
      },
      origin: KAGGLE,
    })
    expect(seen).toEqual(['kaggle-Huang-rollout-edit'])
  })

  it('frame：attach 时 notebook 未就绪 ⇒ 出现后补 status（notebook=true）', async () => {
    const f = fakeWindow('self', 'parent')
    let ready = false
    const bridge = createFrameBridge(f.win, {
      hostOrigins: [KAGGLE],
      handle: async () => ({ ok: true }),
      isReady: () => ready,
      statusPollMs: 10,
    })
    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: KAGGLE })
    expect(f.sent[0]!.msg).toMatchObject({ kind: 'ready', notebook: false })
    ready = true
    await new Promise((r) => setTimeout(r, 35))
    const status = f.sent.find((s) => (s.msg as { kind?: string }).kind === 'status')
    expect(status?.msg).toMatchObject({ kind: 'status', nonce: 'n1', notebook: true })
    expect(status?.target).toBe('parent')
    bridge.destroy()
  })

  it('frame：顶层打开（self === parent）不接受 attach', () => {
    const f = fakeWindow('same', 'same')
    const errors: string[] = []
    createFrameBridge(f.win, {
      hostOrigins: [KAGGLE],
      handle: async () => ({ ok: true }),
      onError: (r) => errors.push(r),
    })
    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: KAGGLE })
    expect(f.sent).toHaveLength(0)
    expect(errors.join()).toContain('顶层')
  })

  it('帧内同窗口的伪造消息（source 不是 parent）被拒', () => {
    const f = fakeWindow('self', 'parent')
    const errors: string[] = []
    createFrameBridge(f.win, {
      hostOrigins: [KAGGLE],
      handle: async () => ({ ok: true }),
      onError: (r) => errors.push(r),
    })
    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: KAGGLE, source: 'other' })
    expect(f.sent).toHaveLength(0)
    expect(errors.join()).toContain('parent')
  })
})
