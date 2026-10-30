/**
 * commands.ts — frame 侧命令表（bridge 的 `handle`；面板与 GM 菜单共用同一份）。
 *
 * 命令只做「可观测的副作用」；失败必须带原因（不静默）。`saveAndClear` 的语义完全由
 * ship-loop 决定：drain 不全 ⇒ 拒绝清空。
 */
import { hashRange } from '../../utils'
import type { CellRef, CmdResult, FrameAdapter, SessionAdapter } from '../sites/types'
import type { ShipLoop } from './ship-loop'

/** 目标指纹只看**前 200 字符**（plan §3.3：uuid 失效后按 index 兜底时必须验指纹）。 */
const TARGET_FP_CHARS = 200

/** 定时任务的 cell 目标（创建时捕获；执行时验指纹，绝不跑错代码）。 */
interface TargetArgs {
  uuid?: unknown
  index?: unknown
  fingerprint?: unknown
}

function fingerprintOf(source: string): string {
  return hashRange(source, 0, Math.min(TARGET_FP_CHARS, source.length))
}

/**
 * 解析目标 cell：uuid 命中且指纹一致 ⇒ 用；uuid 缺失/不匹配 ⇒ index 命中且指纹一致；
 * 多处命中、指纹不符、找不到 ⇒ 不执行（fail closed）。
 */
async function resolveTarget(
  adapter: FrameAdapter,
  args: TargetArgs,
): Promise<{ ok: true; cell: CellRef } | { ok: false; reason: string }> {
  const uuid = typeof args.uuid === 'string' && args.uuid ? args.uuid : ''
  const index = typeof args.index === 'number' && Number.isInteger(args.index) ? args.index : null
  const fp = typeof args.fingerprint === 'string' && args.fingerprint ? args.fingerprint : ''
  const codeCells = adapter.cells().filter((c) => c.kind === 'code')
  const matches = (cell: CellRef): boolean =>
    fp === '' || fingerprintOf(adapter.sourceText(cell)) === fp
  if (uuid) {
    const hit = codeCells.filter((c) => c.id === uuid)
    if (hit.length === 1) {
      if (!matches(hit[0]!)) return { ok: false, reason: `目标 cell 内容已变（指纹不符）：${uuid}` }
      return { ok: true, cell: hit[0]! }
    }
  }
  if (index !== null) {
    const hit = codeCells.filter((c) => c.index === index)
    if (hit.length === 1) {
      if (!matches(hit[0]!)) return { ok: false, reason: `第 ${index} 段内容已变（指纹不符）` }
      return { ok: true, cell: hit[0]! }
    }
    const rendered = await adapter.ensureRendered(index)
    if (rendered && rendered.kind === 'code' && matches(rendered))
      return { ok: true, cell: rendered }
  }
  return { ok: false, reason: `找不到目标 cell（uuid=${uuid || '∅'} index=${index ?? '∅'}）` }
}

export interface CommandDeps {
  loop: ShipLoop
  adapter: FrameAdapter
  session: SessionAdapter
  openLog: () => void
  openConfig: () => void
  /** 面板「CFG」定位用：按顺序在代码里找配置行的字面标记。 */
  configMarkers: string[]
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
  args?: unknown,
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
    case 'runCell': {
      const r = await resolveTarget(deps.adapter, (args ?? {}) as TargetArgs)
      if (!r.ok) return r
      deps.adapter.flash(r.cell)
      return fromCmd(await deps.adapter.runCell(r.cell))
    }
    case 'runAllCells':
      return fromCmd(await deps.adapter.command('notebook:run-all-cells'))
    case 'interrupt':
      return fromCmd(await deps.adapter.command('notebook:interrupt-kernel'))
    case 'captureTarget': {
      const cell = deps.adapter.activeCell()
      if (!cell || cell.kind !== 'code') return { ok: false, reason: '当前没有选中 code cell' }
      return {
        ok: true,
        value: {
          uuid: cell.id,
          index: cell.index,
          fingerprint: fingerprintOf(deps.adapter.sourceText(cell)),
        },
      }
    }
    case 'setScheduleMarks': {
      const marks = (args ?? {}) as { marks?: unknown }
      const list = Array.isArray(marks.marks)
        ? (marks.marks as Array<{
            id?: string
            label?: string
            uuid?: string
            index?: number
            action?: string
          }>)
        : []
      return fromCmd(deps.adapter.setScheduledMarks(list))
    }
    case 'openLog':
      deps.openLog()
      return { ok: true }
    case 'config':
      deps.openConfig()
      return { ok: true }
    case 'jumpConfig':
      return fromCmd(await deps.adapter.focusConfig(deps.configMarkers))
    default:
      return { ok: false, reason: `未知命令：${name}` }
  }
}
