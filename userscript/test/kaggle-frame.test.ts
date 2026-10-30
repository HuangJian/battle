import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import {
  KaggleFrameAdapter,
  KaggleSessionAdapter,
  parseKaggleSession,
  withPageTag,
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

  it('notebook 在 open shadow root 里也能 ready/cells/output（JupyterLab 4）', () => {
    const w = dom('<html><body><div id="host"></div></body></html>')
    const host = w.document.querySelector('#host') as unknown as {
      attachShadow(init: { mode: 'open' }): { innerHTML: string }
    }
    const shadow = host.attachShadow({ mode: 'open' })
    shadow.innerHTML = `<div class="jp-Notebook"><div class="jp-Cell jp-CodeCell" data-uuid="shadow-1" data-windowed-list-index="0"><div class="jp-InputArea"></div><div class="jp-OutputArea"><div class="jp-OutputArea-child" data-mime-type="application/vnd.jupyter.stdout"><pre>shadow out</pre></div></div></div></div>`
    const a = new KaggleFrameAdapter(docOf(w), fast)
    expect(a.ready).toBe(true)
    expect(a.cells()).toEqual([{ id: 'shadow-1', index: 0, kind: 'code' }])
    expect(a.outputText(a.cells()[0]!)).toContain('shadow out')
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

  it('scroll：默认容器无效时兜底 scrollIntoView；完全没有 cell ⇒ 失败', () => {
    const a = new KaggleFrameAdapter(docOf(dom(FRAME)), fast)
    expect(a.scroll('top').ok).toBe(true)
    const empty = new KaggleFrameAdapter(docOf(dom('<html><body></body></html>')), fast)
    expect(empty.scroll('bottom').ok).toBe(false)
  })

  it('scroll：读回 scrollTop 验证；顶 = 0，底 = scrollHeight - clientHeight', () => {
    const w = dom(FRAME)
    const doc = docOf(w)
    const scroller = doc.querySelector<HTMLElement>('.jp-WindowedPanel-outer')!
    let top = 500
    Object.defineProperty(scroller, 'scrollHeight', { value: 2000, configurable: true })
    Object.defineProperty(scroller, 'clientHeight', { value: 400, configurable: true })
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (v: number) => {
        top = v
      },
    })
    const a = new KaggleFrameAdapter(doc, fast)
    const rTop = a.scroll('top')
    expect(rTop).toMatchObject({ ok: true })
    expect(top).toBe(0)
    if (rTop.ok) expect(rTop.detail).toContain('jp-WindowedPanel-outer')
    expect(a.scroll('bottom')).toMatchObject({ ok: true })
    expect(top).toBe(1600)
  })

  it('scroll：赋值无效的容器不算成功（继续找下一个）', () => {
    const w = dom(FRAME)
    const doc = docOf(w)
    const outer = doc.querySelector<HTMLElement>('.jp-WindowedPanel-outer')!
    Object.defineProperty(outer, 'scrollHeight', { value: 2000, configurable: true })
    Object.defineProperty(outer, 'clientHeight', { value: 400, configurable: true })
    Object.defineProperty(outer, 'scrollTop', {
      configurable: true,
      get: () => 300,
      set: () => {},
    })
    const nb = doc.querySelector<HTMLElement>('.jp-Notebook')!
    let nbTop = 100
    Object.defineProperty(nb, 'scrollHeight', { value: 1000, configurable: true })
    Object.defineProperty(nb, 'clientHeight', { value: 200, configurable: true })
    Object.defineProperty(nb, 'scrollTop', {
      configurable: true,
      get: () => nbTop,
      set: (v: number) => {
        nbTop = v
      },
    })
    const r = new KaggleFrameAdapter(doc, fast).scroll('bottom')
    expect(r).toMatchObject({ ok: true })
    expect(nbTop).toBe(800)
    if (r.ok) expect(r.detail).toContain('jp-Notebook')
  })
})

describe('KaggleFrameAdapter：折叠（每段留顶部两行）', () => {
  it('setFoldedAll：先还原原生折叠 → 根属性 + 样式只注入一次；单 cell 的 off 会被清掉', async () => {
    const w = dom(FRAME)
    const a = new KaggleFrameAdapter(docOf(w), fast)
    const nb = w.document.querySelector('.jp-Notebook')!
    let native = 0
    w.document
      .querySelector('[data-command="notebook:show-all-cell-code"]')!
      .addEventListener('click', () => {
        native += 1
      })

    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true })
    expect(native).toBe(1)
    expect(nb.getAttribute('data-bnc-fold')).toBe('all')
    const style = w.document.getElementById('bnc-cell-fold-style')!
    expect(style.textContent).toContain('max-height: calc(2lh + 4px)')
    expect(style.textContent).toContain('.jp-CodeCell')
    expect(style.textContent).toContain('.jp-MarkdownCell')
    expect(style.textContent).toContain('.bnc-unfold')
    expect(style.textContent).toContain('content: "CODE "')
    expect(style.textContent).toContain('content: "MD "')

    expect((await a.setFoldedAll(true)).ok).toBe(true)
    expect(w.document.querySelectorAll('#bnc-cell-fold-style').length).toBe(1)

    // 全局折叠时单独展开一段 ⇒ cell 上的 off 覆盖
    const md = a.cells()[0]!
    expect(await a.setFolded(md, false)).toMatchObject({ ok: true })
    expect(w.document.querySelector(`[data-uuid="${md.id}"]`)!.getAttribute('data-bnc-fold')).toBe(
      'off',
    )

    expect((await a.setFoldedAll(false)).ok).toBe(true)
    expect(nb.hasAttribute('data-bnc-fold')).toBe(false)
    expect(w.document.querySelector(`[data-uuid="${md.id}"]`)!.hasAttribute('data-bnc-fold')).toBe(
      false,
    )
  })

  it('setFolded：code / markdown 同一套属性；未渲染 ⇒ 失败（不假装成功）', async () => {
    const w = dom(FRAME)
    const a = new KaggleFrameAdapter(docOf(w), fast)
    const md = a.cells()[0]!
    const code = a.cells()[1]!
    expect(await a.setFolded(md, true)).toMatchObject({ ok: true })
    expect(w.document.querySelector(`[data-uuid="${md.id}"]`)!.getAttribute('data-bnc-fold')).toBe(
      'on',
    )
    expect(await a.setFolded(code, true)).toMatchObject({ ok: true })
    expect(
      w.document.querySelector(`[data-uuid="${code.id}"]`)!.getAttribute('data-bnc-fold'),
    ).toBe('on')
    expect((await a.setFolded({ id: 'gone', index: 9, kind: 'code' }, true)).ok).toBe(false)
  })

  it('折叠后每个 cell 都有「展开」按钮；点一下只展开这一段', async () => {
    const w = dom(FRAME)
    const a = new KaggleFrameAdapter(docOf(w), fast)
    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true })
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(3)

    const mdEl = docOf(w).querySelector<HTMLElement>(`[data-uuid="${a.cells()[0]!.id}"]`)!
    const btn = mdEl.querySelector('.bnc-unfold') as HTMLElement
    expect(btn.textContent).toBe('展开')
    btn.click()
    expect(mdEl.getAttribute('data-bnc-fold')).toBe('off')
    expect(mdEl.querySelector('.bnc-unfold')).toBeNull()
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(2)

    expect(await a.setFoldedAll(false)).toMatchObject({ ok: true })
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(0)
  })

  it('单段折叠只给该 cell 加展开按钮；面板展开后按钮消失', async () => {
    const w = dom(FRAME)
    const a = new KaggleFrameAdapter(docOf(w), fast)
    const [md, code] = a.cells()
    expect(await a.setFolded(md!, true)).toMatchObject({ ok: true })
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(1)
    expect(await a.setFolded(code!, true)).toMatchObject({ ok: true })
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(2)
    expect(await a.setFolded(md!, false)).toMatchObject({ ok: true })
    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(1)
  })

  it('cell 重新渲染/新进 DOM ⇒ observer 把展开按钮补回来', async () => {
    const w = dom(FRAME)
    const a = new KaggleFrameAdapter(docOf(w), fast)
    await a.setFoldedAll(true)
    const first = docOf(w).querySelector<HTMLElement>('.bnc-unfold')!
    first.remove()

    const extra = w.document.createElement('div')
    extra.className = 'jp-Cell jp-CodeCell'
    extra.setAttribute('data-uuid', 'extra-1')
    extra.setAttribute('data-windowed-list-index', '9')
    extra.innerHTML = '<div class="jp-InputArea"></div>'
    w.document.querySelector('.jp-WindowedPanel-viewport')!.append(extra)
    await new Promise((r) => setTimeout(r, 0))

    expect(w.document.querySelectorAll('.bnc-unfold').length).toBe(4)
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

  it('withPageTag：同一 notebook 多开页签各一份会话名，总长 ≤64', () => {
    const base = parseKaggleSession('https://www.kaggle.com/code/a/b/edit')
    expect(withPageTag(base, 'ab12cd')).toBe('kaggle-a-b-edit-ab12cd')
    expect(withPageTag(base, 'AB-12')).toBe('kaggle-a-b-edit-ab12') // 非法字符先归一
    expect(withPageTag(base, 'ab12cd')).not.toBe(withPageTag(base, 'zz99xx'))
    const long = parseKaggleSession(
      `https://www.kaggle.com/code/${'x'.repeat(80)}/${'y'.repeat(80)}/edit`,
    )
    expect(long.length).toBe(64)
    expect(withPageTag(long, 'abcd1234').length).toBeLessThanOrEqual(64)
    expect(withPageTag(long, 'abcd1234').endsWith('-abcd1234')).toBe(true)
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
