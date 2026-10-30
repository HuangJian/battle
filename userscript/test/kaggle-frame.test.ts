import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import {
  KaggleFrameAdapter,
  KaggleSessionAdapter,
  parseKaggleSession,
} from '../src/notebook-companion/sites/kaggle'

const FRAME = readFileSync(join(import.meta.dir, 'fixtures/kaggle-frame.html'), 'utf8')
const OUTER = readFileSync(join(import.meta.dir, 'fixtures/kaggle-outer.html'), 'utf8')
const opened: DomHandle[] = []

/** happy-dom 的 Document 与 lib.dom 的 Document 结构不兼容（v21 类型）；测试边界一次强转。 */
const docOf = (w: DomHandle): Document => w.document as unknown as Document

function dom(
  html: string,
  url = 'https://kkb-production.jupyter-proxy.kaggle.net/x.html',
): DomHandle {
  const w = loadDom(html, url)
  opened.push(w)
  return w
}
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const fast = { wait: async () => {}, nextFrame: async () => {}, maxScanSteps: 3 }

describe('KaggleFrameAdapter：cells / output / source', () => {
  it('cells() 拿到 3 个，index 来自 data-windowed-list-index，kind 正确', () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    expect(a.cells().map((c) => [c.index, c.kind])).toEqual([
      [0, 'markdown'],
      [1, 'code'],
      [2, 'code'],
    ])
    expect(a.ready).toBe(true)
  })

  it('outputText：stdout + stderr 按 DOM 顺序，mime 变化写中性标记，CRLF 归一', () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    const cell = a.cells()[1]!
    const text = a.outputText(cell)
    expect(text).toContain('start rollout\n')
    expect(text).toContain('# [stderr]\n')
    expect(text.indexOf('start rollout')).toBeLessThan(text.indexOf('# [stderr]'))
    expect(text.indexOf('# [stderr]')).toBeLessThan(text.indexOf('RuntimeError: boom'))
    expect(text).not.toContain('\r')
    expect(a.outputText(a.cells()[0]!)).toBe('')
  })

  it('sourceText 读 `.cm-content`（折叠时仍可读，不用 innerText）', () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    expect(a.sourceText(a.cells()[1]!)).toContain('CFG = {')
  })
})

describe('KaggleFrameAdapter：命令阶梯与后置条件', () => {
  it('只有隐藏的 `<li>` 也能执行（命令面板关闭时仍在 DOM）', async () => {
    const w = dom(FRAME)
    let clicked = 0
    w.document
      .querySelector('[data-command="notebook:clear-all-cell-outputs"]')!
      .addEventListener('click', () => {
        clicked += 1
      })
    const a = new KaggleFrameAdapter(docOf(w), { ...fast, isVisible: () => false })
    const r = await a.command('notebook:clear-all-cell-outputs')
    expect(r).toMatchObject({ ok: true, detail: 'clicked:hidden' })
    expect(clicked).toBe(1)
  })

  it('可见按钮优先于隐藏 li', async () => {
    const w = dom(FRAME)
    const hidden = w.document.querySelector('[data-command="toc:toggle-collapse"]')!
    const clicks: string[] = []
    hidden.addEventListener('click', () => clicks.push('hidden'))
    const visible = hidden.cloneNode(true) as typeof hidden
    visible.addEventListener('click', () => clicks.push('visible'))
    hidden.parentElement!.append(visible)
    const a = new KaggleFrameAdapter(docOf(w), {
      ...fast,
      isVisible: (el) => el === (visible as unknown as Element),
    })
    const r = await a.command('toc:toggle-collapse')
    expect(r).toMatchObject({ ok: true, detail: 'clicked:visible' })
    expect(clicks).toEqual(['visible'])
  })

  it('命令不存在 ⇒ {ok:false}（不静默成功）', async () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    const r = await a.command('notebook:no-such-command')
    expect(r.ok).toBe(false)
  })

  it('点击了但后置条件不成立 ⇒ clearCellOutput 返回失败', async () => {
    const w = dom(FRAME)
    // 选中监听：点 InputArea 就加 jp-mod-selected
    for (const cell of w.document.querySelectorAll('.jp-CodeCell')) {
      cell.querySelector('.jp-InputArea')!.addEventListener('click', () => {
        cell.classList.add('jp-mod-selected')
      })
    }
    const a = new KaggleFrameAdapter(docOf(w), fast)
    const r = await a.clearCellOutput(a.cells()[1]!)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('后置条件')
  })

  it('runCell 先选中非 active cell，再点 run（夹具里 run 只有一份且隐藏）', async () => {
    const w = dom(FRAME)
    const order: string[] = []
    const target = w.document.querySelectorAll('.jp-CodeCell')[1]!
    target.querySelector('.jp-InputArea')!.addEventListener('click', () => {
      order.push('select')
      target.classList.add('jp-mod-selected')
    })
    w.document
      .querySelector('[data-command="notebook:run-cell"]')!
      .addEventListener('click', () => order.push('run'))
    const a = new KaggleFrameAdapter(docOf(w), { ...fast, isVisible: () => false })
    // `a.cells()[2]` = index 2 = 第二个 `.jp-CodeCell`（监听器装的就是它）
    const r = await a.runCell(a.cells()[2]!)
    expect(r.ok).toBe(true)
    expect(order.slice(0, 2)).toEqual(['select', 'run'])
    expect(target.classList.contains('jp-mod-selected')).toBe(true)
  })

  it('scroll：容器存在即写 scrollTop 并补发 scroll；不存在 ⇒ 失败', () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    expect(a.scroll('top').ok).toBe(true)
    const empty = new KaggleFrameAdapter(docOf(dom('<html><body></body></html>')), fast)
    expect(empty.scroll('bottom').ok).toBe(false)
  })
})

describe('KaggleSessionAdapter：会话名与停止', () => {
  it('parseKaggleSession：owner/slug/run 归一，非法字符替换、截断 64', () => {
    expect(parseKaggleSession('https://www.kaggle.com/code/Huang/rollout/edit/run/358870479')).toBe(
      'kaggle-huang-rollout-358870479',
    )
    expect(parseKaggleSession('https://www.kaggle.com/code/a/b/edit')).toBe('kaggle-a-b-edit')
    expect(parseKaggleSession('not a url')).toBe('kaggle-unknown-edit')
    expect(
      parseKaggleSession('https://www.kaggle.com/code/a/b/edit/run/1').length,
    ).toBeLessThanOrEqual(64)
  })

  it('sessionState：Draft Session ⇒ idle；Stopped ⇒ stopped；无按钮 ⇒ unknown', () => {
    const href = 'https://www.kaggle.com/code/example/rollout/edit/run/123'
    const idle = new KaggleSessionAdapter(docOf(dom(OUTER, href)), href)
    expect(idle.sessionState()).toBe('idle')

    const stopped = new KaggleSessionAdapter(
      docOf(dom('<html><body>Stopped</body></html>')),
      'https://www.kaggle.com/code/a/b/edit',
    )
    expect(stopped.sessionState()).toBe('stopped')
    expect(
      new KaggleSessionAdapter(docOf(dom('<html><body>hi</body></html>')), 'x').sessionState(),
    ).toBe('unknown')
  })

  it('stopSession：点按钮 → 无对话框 → 状态变 stopped 才 ok；状态不变 ⇒ 失败', async () => {
    const href = 'https://www.kaggle.com/code/example/rollout/edit/run/123'
    const w = dom(OUTER, href)
    const btn = w.document.querySelector('[aria-label="Stop session"]')!
    btn.addEventListener('click', () => {
      w.document.body.append(w.document.createTextNode(' Stopped'))
    })
    const a = new KaggleSessionAdapter(docOf(w), href, { wait: async () => {} })
    expect(await a.stopSession()).toMatchObject({ ok: true })

    const stuck = dom(OUTER, href)
    const b = new KaggleSessionAdapter(docOf(stuck), href, { wait: async () => {} })
    expect((await b.stopSession()).ok).toBe(false)
  })
})
