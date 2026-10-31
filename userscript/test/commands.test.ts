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
    setFoldedAll: async (collapsed: boolean) => ({ ok: true, detail: `folded:${collapsed}` }),
    setFolded: async (cell: { id: string }, collapsed: boolean) => ({
      ok: true,
      detail: `folded:${cell.id}:${collapsed}`,
    }),
    activeCell: () => ({ id: 'c1', index: 1, kind: 'code' as const }),
    scroll: (to: string) => ({ ok: true, detail: to }),
    scrollOuter: (to: string) => ({ ok: true, detail: to }),
    focusConfig: async (markers: string[]) => ({ ok: true, detail: `cfg:${markers.join('|')}` }),
  } as unknown as FrameAdapter
  const session = {
    stopSession: async () => ({ ok: true, detail: 'stopped' }),
  } as unknown as SessionAdapter
  return {
    loop,
    adapter,
    session,
    openLog: () => {},
    openConfig: () => {},
    configMarkers: ['CFG = {'],
    ...over,
  }
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

  it('折叠/展开当前 ⇒ 走 setFolded（代码与 markdown 同一套）；没有 active cell ⇒ 失败', async () => {
    const calls: string[] = []
    const d = deps()
    const adapter = d.adapter as unknown as {
      setFolded: (cell: { id: string }, collapsed: boolean) => { ok: boolean }
    }
    adapter.setFolded = (cell: { id: string }, collapsed: boolean) => {
      calls.push(`fold:${cell.id}:${collapsed}`)
      return { ok: true }
    }
    expect((await runFrameCommand(d, 'collapseCurrent')).ok).toBe(true)
    expect((await runFrameCommand(d, 'expandCurrent')).ok).toBe(true)
    expect(calls).toEqual(['fold:c1:true', 'fold:c1:false'])

    const none = deps()
    ;(none.adapter as { activeCell: () => null }).activeCell = () => null
    expect(await runFrameCommand(none, 'expandCurrent')).toEqual({
      ok: false,
      reason: '没有选中的 cell',
    })
  })

  it('折叠/展开全部 ⇒ setFoldedAll；失败原因原样带回', async () => {
    const calls: string[] = []
    const d = deps()
    const adapter = d.adapter as unknown as {
      setFoldedAll: (collapsed: boolean) => Promise<{ ok: boolean; reason?: string }>
    }
    adapter.setFoldedAll = async (collapsed: boolean) => {
      calls.push(`fold-all:${collapsed}`)
      return { ok: true }
    }
    expect((await runFrameCommand(d, 'collapseAll')).ok).toBe(true)
    expect((await runFrameCommand(d, 'expandAll')).ok).toBe(true)
    expect(calls).toEqual(['fold-all:true', 'fold-all:false'])

    adapter.setFoldedAll = async () => ({ ok: false, reason: 'notebook 未就绪' })
    expect(await runFrameCommand(d, 'collapseAll')).toEqual({
      ok: false,
      reason: 'notebook 未就绪',
    })
  })

  it('jumpConfig ⇒ 把 configMarkers 交给适配器定位 CFG 行；失败原因原样带回', async () => {
    expect(await runFrameCommand(deps(), 'jumpConfig')).toEqual({
      ok: true,
      value: 'cfg:CFG = {',
    })
    const bad = deps()
    ;(
      bad.adapter as unknown as { focusConfig: () => Promise<{ ok: false; reason: string }> }
    ).focusConfig = async () => ({ ok: false, reason: '找不到 CFG 行' })
    expect(await runFrameCommand(bad, 'jumpConfig')).toEqual({
      ok: false,
      reason: '找不到 CFG 行',
    })
  })

  it('setAutoSave ⇒ 交给 loop 立即生效；间隔夹到 0–24h', async () => {
    const calls: Array<{ enabled: boolean; intervalSec: number }> = []
    const d = deps()
    ;(d.loop as unknown as { setAutoSave: (enabled: boolean, sec: number) => void }).setAutoSave = (
      enabled,
      intervalSec,
    ) => calls.push({ enabled, intervalSec })
    expect(await runFrameCommand(d, 'setAutoSave', { enabled: true, intervalSec: 120 })).toEqual({
      ok: true,
      value: { enabled: true, intervalSec: 120 },
    })
    expect(calls).toEqual([{ enabled: true, intervalSec: 120 }])
    expect(
      await runFrameCommand(d, 'setAutoSave', { enabled: false, intervalSec: 999_999 }),
    ).toEqual({ ok: true, value: { enabled: false, intervalSec: 86_400 } })
  })

  it('脚本/页面菜单共用的打开动作有副作用；未知命令失败', async () => {
    let opened = 0
    const d = deps({ openLog: () => (opened += 1) })
    expect(await runFrameCommand(d, 'openLog')).toEqual({ ok: true })
    expect(opened).toBe(1)
    expect((await runFrameCommand(d, 'nope')).ok).toBe(false)
  })
})
