import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { runFrameCommand, type CommandDeps } from '../src/notebook-companion/app/commands'
import type { ShipLoop } from '../src/notebook-companion/app/ship-loop'
import { KaggleFrameAdapter, parseKaggleNotebookKey } from '../src/notebook-companion/sites/kaggle'
import type { SessionAdapter } from '../src/notebook-companion/sites/types'

const FRAME = readFileSync(join(import.meta.dir, 'fixtures/kaggle-frame.html'), 'utf8')
const opened: DomHandle[] = []
const docOf = (w: DomHandle): Document => w.document as unknown as Document
function dom(): DomHandle {
  const w = loadDom(FRAME)
  opened.push(w)
  return w
}
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

/** 只有定时命令会用到 deps；其余命令的依赖给最小假件（这些用例不调用它们）。 */
const loop = {
  session: 's',
  dirty: new Set(),
  state: {},
  saveAndClear: async () => ({ ok: true, bytes: 0 }),
  flushAll: async () => ({ ok: true, bytes: 0 }),
  clearDashboardSession: async () => ({ ok: true }),
} as unknown as ShipLoop

const session = {
  site: 'kaggle',
  sessionId: () => 's',
  sessionState: () => 'idle',
  stopSession: async () => ({ ok: true }),
} as SessionAdapter

function makeDeps(): { w: DomHandle; doc: Document; deps: CommandDeps } {
  const w = dom()
  const doc = docOf(w)
  const adapter = new KaggleFrameAdapter(doc, {
    isVisible: () => true,
    wait: async () => {},
  })
  return {
    w,
    doc,
    deps: { loop, adapter, session, openLog: () => {}, openConfig: () => {} },
  }
}

/** happy-dom 的 Document 与 lib.dom 不同源，查询边界统一走 docOf 后的 Document。 */
function clearActive(doc: Document): void {
  for (const el of doc.querySelectorAll('.jp-Cell')) {
    el.classList.remove('jp-mod-active', 'jp-mod-selected')
  }
}

describe('frame 命令：定时任务的 cell 目标', () => {
  it('captureTarget 抓当前 code cell（uuid/index/前 200 字符指纹）', async () => {
    const { doc, deps } = makeDeps()
    clearActive(doc)
    const cell = doc.querySelector<HTMLElement>('.jp-CodeCell')
    expect(cell).not.toBeNull()
    cell!.classList.add('jp-mod-selected')

    const cap = await runFrameCommand(deps, 'captureTarget')
    expect(cap.ok).toBe(true)
    const target = cap.value as { uuid: string; index: number; fingerprint: string }
    expect(target.uuid).toBeTruthy()
    expect(Number.isInteger(target.index)).toBe(true)
    expect(target.fingerprint).toMatch(/^[0-9a-f]{16}$/)
  })

  it('runCell：指纹一致才跑；uuid 缺失按 index + 指纹兜底；指纹不符/找不到 ⇒ 不跑', async () => {
    const { doc, deps } = makeDeps()
    clearActive(doc)
    const cell = doc.querySelector<HTMLElement>('.jp-CodeCell')!
    cell.classList.add('jp-mod-selected')
    const cap = await runFrameCommand(deps, 'captureTarget')
    const target = cap.value as { uuid: string; index: number; fingerprint: string }

    expect((await runFrameCommand(deps, 'runCell', target)).ok).toBe(true)
    expect(
      (
        await runFrameCommand(deps, 'runCell', {
          index: target.index,
          fingerprint: target.fingerprint,
        })
      ).ok,
    ).toBe(true)

    const changed = await runFrameCommand(deps, 'runCell', {
      ...target,
      fingerprint: '0'.repeat(16),
    })
    expect(changed.ok).toBe(false)
    expect(changed.reason).toContain('指纹')

    const missing = await runFrameCommand(deps, 'runCell', { uuid: 'no-such-cell', index: 999 })
    expect(missing.ok).toBe(false)
    expect(missing.reason).toContain('找不到')
  })

  it('setScheduleMarks：目标 cell 顶部出现醒目标记；清空后移除', async () => {
    const { doc, deps } = makeDeps()
    clearActive(doc)
    const cell = doc.querySelector<HTMLElement>('.jp-CodeCell')!
    cell.classList.add('jp-mod-selected')
    const cap = await runFrameCommand(deps, 'captureTarget')
    const target = cap.value as { uuid: string; index: number }

    const set = await runFrameCommand(deps, 'setScheduleMarks', {
      marks: [{ id: 't1', label: '今天 18:00', uuid: target.uuid, index: target.index }],
    })
    expect(set.ok).toBe(true)
    const badge = cell.querySelector('.bnc-sched')
    expect(badge).not.toBeNull()
    expect(badge!.textContent).toContain('已定时')
    expect(badge!.textContent).toContain('今天 18:00')
    expect(cell.getAttribute('data-bnc-sched')).toBe('1')
    expect(doc.getElementById('bnc-sched-style')).not.toBeNull()

    expect((await runFrameCommand(deps, 'setScheduleMarks', { marks: [] })).ok).toBe(true)
    expect(cell.querySelector('.bnc-sched')).toBeNull()
    expect(cell.getAttribute('data-bnc-sched')).toBeNull()
  })

  it('runAllCells 点得到；interrupt 在夹具里没有 ⇒ 响亮失败（不假装成功）', async () => {
    const { deps } = makeDeps()
    expect((await runFrameCommand(deps, 'runAllCells')).ok).toBe(true)
    const intr = await runFrameCommand(deps, 'interrupt')
    expect(intr.ok).toBe(false)
    expect(intr.reason).toContain('不在 DOM')
  })
})

describe('parseKaggleNotebookKey', () => {
  it('owner/slug 跨 run 稳定；非 notebook 页 ⇒ 空串', () => {
    expect(parseKaggleNotebookKey('https://www.kaggle.com/code/Huang/rollout/edit/run/1')).toBe(
      'Huang/rollout',
    )
    expect(parseKaggleNotebookKey('https://www.kaggle.com/code/a/b/edit')).toBe('a/b')
    expect(parseKaggleNotebookKey('https://www.kaggle.com/datasets/a/b')).toBe('')
    expect(parseKaggleNotebookKey('not a url')).toBe('')
  })
})
