import { describe, expect, it } from 'bun:test'
import { runFrameCommand, type CommandDeps } from '../src/notebook-companion/app/commands'
import type { ShipLoop } from '../src/notebook-companion/app/ship-loop'
import type { FrameAdapter, SessionAdapter } from '../src/notebook-companion/sites/types'

function deps(over: Partial<CommandDeps> = {}): CommandDeps {
  const loop = {
    session: 'kaggle-a-b-1',
    dirty: new Set(['c1']),
    state: { lastShipAt: 123, lastError: null, stopped: new Map([['c2', 'too big']]) },
    saveAndClear: async () => ({ ok: false, reason: '保存未完成，拒绝清空', bytes: 9 }),
    flushAll: async () => ({ ok: true, bytes: 42 }),
    clearDashboardSession: async () => ({ ok: true }),
  } as unknown as ShipLoop
  const adapter = {
    command: async (id: string) => ({ ok: true, detail: `clicked:${id}` }),
    activeCell: () => ({ id: 'c1', index: 1, kind: 'code' as const }),
    scroll: (to: string) => ({ ok: true, detail: to }),
    scrollOuter: (to: string) => ({ ok: true, detail: to }),
  } as unknown as FrameAdapter
  const session = {
    stopSession: async () => ({ ok: true, detail: 'stopped' }),
  } as unknown as SessionAdapter
  return { loop, adapter, session, openLog: () => {}, openConfig: () => {}, ...over }
}

describe('frame 命令表', () => {
  it('saveAndClear 失败 ⇒ 命令失败且带原因（不报成功）', async () => {
    expect(await runFrameCommand(deps(), 'saveAndClear')).toEqual({
      ok: false,
      reason: '保存未完成，拒绝清空',
    })
  })

  it('status 带 dirty/lastShipAt/stopped；都是可序列化值', async () => {
    const r = await runFrameCommand(deps(), 'status')
    expect(r.ok).toBe(true)
    expect(r.value).toEqual({
      session: 'kaggle-a-b-1',
      dirty: 1,
      lastShipAt: 123,
      lastError: null,
      stopped: ['c2'],
    })
    expect(() => JSON.stringify(r)).not.toThrow()
  })

  it('折叠当前 ⇒ 作用到 activeCell；没有 active cell ⇒ 失败', async () => {
    expect(await runFrameCommand(deps(), 'collapseCurrent')).toMatchObject({
      ok: true,
      value: 'clicked:notebook:hide-cell-code',
    })
    const none = deps()
    ;(none.adapter as { activeCell: () => null }).activeCell = () => null
    expect(await runFrameCommand(none, 'expandCurrent')).toEqual({
      ok: false,
      reason: '没有选中的 cell',
    })
  })

  it('脚本/页面菜单共用的打开动作有副作用；未知命令失败', async () => {
    let opened = 0
    const d = deps({ openLog: () => (opened += 1) })
    expect(await runFrameCommand(d, 'openLog')).toEqual({ ok: true })
    expect(opened).toBe(1)
    expect((await runFrameCommand(d, 'nope')).ok).toBe(false)
  })
})
