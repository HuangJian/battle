import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { createOutputObserver } from '../src/notebook-companion/observer'

const FRAME = readFileSync(join(import.meta.dir, 'fixtures/kaggle-frame.html'), 'utf8')
const opened: DomHandle[] = []

/** happy-dom 的 Document 与 lib.dom 的 Document 结构不兼容（v21 类型）；测试边界一次强转。 */
const docOf = (w: DomHandle): Document => w.document as unknown as Document

function dom(html: string): DomHandle {
  const w = loadDom(html)
  opened.push(w)
  return w
}

afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

describe('输出 observer：只把输出区标脏', () => {
  it('往 stdout <pre> 追加文本 ⇒ 该 cell 的 uuid 被标脏', async () => {
    const w = dom(FRAME)
    const dirty: string[] = []
    const o = createOutputObserver(docOf(w), (id) => dirty.push(id))
    const pre = w.document.querySelector('[data-mime-type="application/vnd.jupyter.stdout"] pre')!
    pre.append('tail')
    await new Promise((r) => setTimeout(r, 20))
    o.disconnect()
    expect(dirty).toEqual(['22222222-2222-4222-8222-222222222222'])
  })

  it('在 `.cm-content` 打字 / 点折叠器 ⇒ 不标脏（否则每拍读全文）', async () => {
    const w = dom(FRAME)
    const dirty: string[] = []
    const o = createOutputObserver(docOf(w), (id) => dirty.push(id))
    const cm = w.document.querySelector('.jp-CodeCell .cm-content')!
    cm.append(w.document.createTextNode('x'))
    const collapser = w.document.querySelector('.jp-InputCollapser') as unknown as {
      dispatchEvent(e: unknown): void
    }
    const EventCtor = (w as unknown as { Event: typeof Event }).Event
    collapser.dispatchEvent(new EventCtor('click', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 20))
    o.disconnect()
    expect(dirty).toEqual([])
  })

  it('cell 列表变化 ⇒ 只刷渲染集合回调（不标某个 cell 脏）', async () => {
    const w = dom(FRAME)
    const dirty: string[] = []
    let cellsChanged = 0
    const o = createOutputObserver(
      docOf(w),
      (id) => dirty.push(id),
      () => {
        cellsChanged += 1
      },
    )
    const notebook = w.document.querySelector('.jp-WindowedPanel-viewport')!
    const extra = w.document.createElement('div')
    extra.className = 'jp-Cell'
    extra.setAttribute('data-uuid', '44444444-4444-4444-8444-444444444444')
    extra.setAttribute('data-windowed-list-index', '3')
    notebook.append(extra)
    await new Promise((r) => setTimeout(r, 20))
    o.disconnect()
    expect(cellsChanged).toBeGreaterThan(0)
    expect(dirty).toEqual([])
  })
})
