/**
 * commands.ts — frame 侧命令表（bridge 的 `handle`；面板与 GM 菜单共用同一份）。
 *
 * 命令只做「可观测的副作用」；失败必须带原因（不静默）。`saveAndClear` 的语义完全由
 * ship-loop 决定：drain 不全 ⇒ 拒绝清空。
 */
import type { CmdResult, FrameAdapter, SessionAdapter } from '../sites/types'
import type { ShipLoop } from './ship-loop'

export interface CommandDeps {
  loop: ShipLoop
  adapter: FrameAdapter
  session: SessionAdapter
  openLog: () => void
  openConfig: () => void
}

export interface CommandReply {
  ok: boolean
  value?: unknown
  reason?: string
}

const fromCmd = (r: CmdResult): CommandReply =>
  r.ok
    ? { ok: true, ...(r.detail === undefined ? {} : { value: r.detail }) }
    : { ok: false, reason: r.reason }

export async function runFrameCommand(
  deps: CommandDeps,
  name: string,
  _args?: unknown,
): Promise<CommandReply> {
  switch (name) {
    case 'saveAndClear': {
      const r = await deps.loop.saveAndClear()
      return r.ok
        ? { ok: true, value: { bytes: r.bytes } }
        : { ok: false, reason: r.reason ?? '保存未完成' }
    }
    case 'flush': {
      const r = await deps.loop.flushAll()
      return r.ok
        ? { ok: true, value: { bytes: r.bytes } }
        : { ok: false, reason: r.reason ?? '保存未完成' }
    }
    case 'clearDashboard': {
      const r = await deps.loop.clearDashboardSession()
      return r.ok ? { ok: true } : { ok: false, reason: r.reason ?? '服务端拒绝' }
    }
    case 'status': {
      return {
        ok: true,
        value: {
          session: deps.loop.session,
          dirty: deps.loop.dirty.size,
          lastShipAt: deps.loop.state.lastShipAt,
          lastError: deps.loop.state.lastError,
          stopped: [...deps.loop.state.stopped.keys()],
        },
      }
    }
    case 'collapseAll':
      // 代码 + markdown 都走适配器：命令会把输入整段藏掉，留不下顶部两行（plan §3.4）
      return fromCmd(await deps.adapter.setFoldedAll(true))
    case 'expandAll':
      return fromCmd(await deps.adapter.setFoldedAll(false))
    case 'collapseAllOutputs':
      return fromCmd(await deps.adapter.command('notebook:hide-all-cell-outputs'))
    case 'expandAllOutputs':
      return fromCmd(await deps.adapter.command('notebook:show-all-cell-outputs'))
    case 'collapseCurrent':
    case 'expandCurrent': {
      const cell = deps.adapter.activeCell()
      if (!cell) return { ok: false, reason: '没有选中的 cell' }
      return fromCmd(await deps.adapter.setFolded(cell, name === 'collapseCurrent'))
    }
    case 'scrollTop':
      return fromCmd(deps.adapter.scroll('top'))
    case 'scrollBottom':
      return fromCmd(deps.adapter.scroll('bottom'))
    case 'scrollPageTop':
      return fromCmd(deps.adapter.scrollOuter('top'))
    case 'scrollPageBottom':
      return fromCmd(deps.adapter.scrollOuter('bottom'))
    case 'stopSession':
      return fromCmd(await deps.session.stopSession())
    case 'openLog':
      deps.openLog()
      return { ok: true }
    case 'config':
      deps.openConfig()
      return { ok: true }
    default:
      return { ok: false, reason: `未知命令：${name}` }
  }
}
