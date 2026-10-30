import { describe, expect, it } from 'bun:test'
import {
  createFrameBridge,
  createHostBridge,
  type BridgeEvent,
  type BridgeWindow,
} from '../src/notebook-companion/app/bridge'

interface FakeWindow {
  win: BridgeWindow
  sent: Array<{ msg: unknown; origin: string }>
  fire: (e: { data: unknown; origin: string; source?: unknown }) => void
}

function fakeWindow(self: unknown = 'frame-self', parent: unknown = 'frame-parent'): FakeWindow {
  const listeners: Array<(e: BridgeEvent) => void> = []
  const sent: Array<{ msg: unknown; origin: string }> = []
  return {
    win: {
      addEventListener: (_type, fn) => listeners.push(fn),
      postMessage: (msg, origin) => sent.push({ msg, origin }),
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
    host.attach(FRAME, 'n1')
    expect(f.sent[0]!.origin).toBe(FRAME)
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

  it('host：frame 不回 ⇒ 超时是失败（不假装成功）', async () => {
    const f = fakeWindow()
    const host = createHostBridge(f.win, { timeoutMs: 20 })
    host.attach(FRAME, 'n1')
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
      handle: async (name) => {
        handled.push(name)
        return { ok: true, value: name }
      },
    })

    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: 'https://evil.test' })
    expect(f.sent).toHaveLength(0)

    f.fire({ data: { __bnc: 1, kind: 'attach', nonce: 'n1' }, origin: KAGGLE })
    expect(f.sent[0]!.origin).toBe(KAGGLE)
    expect(f.sent[0]!.msg).toMatchObject({ kind: 'ready', nonce: 'n1', notebook: true })
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
