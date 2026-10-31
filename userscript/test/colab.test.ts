import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { COLAB_OBSERVER, createOutputObserver } from '../src/notebook-companion/observer'
import { deepQuery } from '../src/notebook-companion/selectors'
import {
  ColabAdapter,
  ColabSessionAdapter,
  parseColabNotebookKey,
  parseColabSession,
} from '../src/notebook-companion/sites/colab'

/**
 * 夹具来自真实 Colab 页面存档（2026-10-31）：`tmp/rollout.ipynb-Colab.mhtml` 抽出
 * `#top-toolbar` 与 `#notebook-main` 两棵子树，只去噪 + 换成合成文本。
 * 存档把 open shadow root 序列化成 `<template shadowmode="open">`，这里还原成真影子根，
 * 于是 deepQuery / 钻影子点击走的是与线上一致的路径。
 */
const FIXTURE = readFileSync(join(import.meta.dir, 'fixtures/colab.html'), 'utf8')
const HREF = 'https://colab.research.google.com/drive/1VUDX_hyHVvzc2kQxrAOSgsPvzwcuXD7n'

const opened: DomHandle[] = []
/** happy-dom 的 Document 与 lib.dom 的 Document 结构不兼容（v21 类型）；测试边界一次强转。 */
const docOf = (w: DomHandle): Document => w.document as unknown as Document

/** 把 MHTML 的 `<template shadowmode="open">` 还原成真 shadow root（含嵌套组件）。 */
function materializeShadows(doc: Document): void {
  const queue: Element[] = [...doc.querySelectorAll('*')]
  const done = new Set<Element>()
  while (queue.length > 0) {
    const el = queue.shift()!
    if (done.has(el)) continue
    done.add(el)
    const tpl = [...el.children].find(
      (c) => c.tagName === 'TEMPLATE' && c.getAttribute('shadowmode') === 'open',
    )
    if (tpl) {
      const sr = (el as HTMLElement).attachShadow({ mode: 'open' })
      sr.append((tpl as HTMLTemplateElement).content)
    }
    const sr = (el as HTMLElement).shadowRoot
    if (sr) queue.push(...sr.querySelectorAll('*'))
    queue.push(...el.children)
  }
}

function dom(): DomHandle {
  const w = loadDom(FIXTURE, HREF)
  materializeShadows(docOf(w))
  opened.push(w)
  return w
}

afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const fast = { wait: async () => {}, nextFrame: async () => {}, selectTimeoutMs: 40 }

const adapter = (w: DomHandle): ColabAdapter => new ColabAdapter(docOf(w), fast)

const codeCell = (a: ColabAdapter) => a.cells().find((c) => c.kind === 'code')!

/** 每一步都先查本层，再查它的影子根，最后才让 deepQuery 扫后代影子（与线上路径一致）。 */
function shadowFind(root: Element | Document, sel: string): Element | null {
  const el = root as Element
  const direct = typeof el.querySelector === 'function' ? el.querySelector(sel) : null
  if (direct) return direct
  const own = el.shadowRoot?.querySelector(sel)
  if (own) return own
  return deepQuery(root as ParentNode, sel)
}

/** 逐层钻影子根，最后拿最里层那个 `<button>`（我们就是点它）。 */
function innerButton(root: Element | Document, ...path: string[]): HTMLButtonElement | null {
  let node: Element | null = root as Element
  for (const sel of path) {
    node = node ? shadowFind(node, sel) : null
  }
  if (!node) return null
  return (node.shadowRoot?.querySelector('button') ?? null) as HTMLButtonElement | null
}

describe('Colab：URL → 会话 / notebook 键', () => {
  it('colab URL 与 referrer（drive 的 file/d）都解析出文件 id', () => {
    expect(parseColabSession('https://colab.research.google.com/drive/1VUDX_hy7Ns')).toBe(
      'colab-1vudx_hy7ns',
    )
    expect(parseColabSession('https://drive.google.com/file/d/ABC123_xyz/view')).toBe(
      'colab-abc123_xyz',
    )
    expect(parseColabSession('not a url')).toBe('colab-unknown')
    expect(
      parseColabSession('https://colab.research.google.com/drive/' + 'x'.repeat(80)).length,
    ).toBeLessThanOrEqual(64)
  })

  it('notebook 键给定时任务归属页面用；拿不到文件 id ⇒ 空串（任务会拒绝）', () => {
    expect(parseColabNotebookKey('https://colab.research.google.com/drive/1VUDX_hy7Ns')).toBe(
      'colab/1VUDX_hy7Ns',
    )
    expect(parseColabNotebookKey('https://colab.research.google.com/')).toBe('')
    expect(parseColabNotebookKey('not a url')).toBe('')
  })
})

describe('Colab 适配器：结构来自真实页面存档', () => {
  it('cells()：序号取 aria-label，id 取 cell-<hex>，code/text 分类对', () => {
    const a = adapter(dom())
    expect(a.ready).toBe(true)
    expect(a.cells().map((c) => [c.index, c.kind, c.id])).toEqual([
      [0, 'markdown', 'cell-b7c10a01'],
      [1, 'code', 'cell-c3f2a19b'],
    ])
  })

  it('outputText：多段输出按 DOM 顺序；sourceText：逐行 + NBSP 还原成空格', () => {
    const a = adapter(dom())
    const code = codeCell(a)
    const out = a.outputText(code)
    expect(out).toContain('[rollout-node] [keepalive] alive (0 min)')
    expect(out).toContain('RuntimeError: boom')
    expect(out.indexOf('[rollout-node]')).toBeLessThan(out.indexOf('RuntimeError'))

    const src = a.sourceText(code)
    expect(src).toContain('import time') // 夹具里存的是 `import&nbsp;time`
    expect(src).not.toContain('\u00a0')
    expect(src).toContain('CFG = {')
    expect(src.split('\n')).toHaveLength(8)
  })

  it('text cell 没有编辑器 ⇒ sourceText 退到渲染出来的 markdown', () => {
    const a = adapter(dom())
    const text = a.cells().find((c) => c.kind === 'markdown')!
    expect(a.outputText(text)).toBe('')
    expect(a.sourceText(text).length).toBeGreaterThan(0)
  })

  it('activeCell：`.focused` 那个', () => {
    const a = adapter(dom())
    expect(a.activeCell()?.id).toBe('cell-c3f2a19b')
    docOf(opened[0]!).querySelector('.cell.code')!.classList.remove('focused')
    expect(a.activeCell()).toBeNull()
  })

  it('focusConfig：启发式命中 CFG 行，只滚动聚焦（不派发指针事件 ⇒ 不会选中内容）', async () => {
    const w = dom()
    const a = adapter(w)
    const line = docOf(w).querySelector('.cell.code .view-line:nth-child(3)')!
    const seen: string[] = []
    for (const t of ['mousedown', 'mouseup', 'click']) line.addEventListener(t, () => seen.push(t))
    const r = await a.focusConfig(['CFG = {'])
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.detail).toBe('第 1 段第 3 行 · CFG 行')
    expect(seen).toEqual([])
  })

  it('focusConfig：标记表为空 ⇒ 不猜、带原因失败', async () => {
    const a = adapter(dom())
    const r = await a.focusConfig(['   '])
    expect(r).toMatchObject({ ok: false, reason: expect.stringContaining('configMarkers') })
  })

  it('runCell：点该 cell 影子根里的 #run-button；running 时不再点（避免误触中断）', async () => {
    const w = dom()
    const a = adapter(w)
    const cell = docOf(w).querySelector('.cell.code') as HTMLElement
    const btn = innerButton(docOf(w), '.cell.code', 'colab-run-button')
    expect(btn).not.toBeNull()
    let clicks = 0
    btn!.addEventListener('click', () => (clicks += 1))

    // 存档抓的是「正在跑」的那一帧 ⇒ 先把它当成跑完的 cell
    cell.classList.remove('running')
    const r = await a.runCell(codeCell(a))
    expect(r.ok).toBe(true)
    expect(clicks).toBe(1)

    cell.classList.add('running')
    const r2 = await a.runCell(codeCell(a))
    expect(r2).toMatchObject({ ok: true, detail: 'already-running' })
    expect(clicks).toBe(1)
  })

  it('command(run-all)：钻两层影子根点到工具栏按钮', async () => {
    const w = dom()
    const a = adapter(w)
    const btn = innerButton(
      docOf(w),
      'colab-notebook-toolbar-run-button',
      'colab-toolbar-button',
      'md-text-button',
    )
    expect(btn).not.toBeNull()
    let clicks = 0
    btn!.addEventListener('click', () => (clicks += 1))
    expect(await a.command('notebook:run-all-cells')).toMatchObject({ ok: true })
    expect(clicks).toBe(1)
  })

  it('command(interrupt)：菜单项认 md-menu-item[command]（实测属性），点了就算', async () => {
    const w = dom()
    const a = adapter(w)
    const item = deepQuery(docOf(w), 'md-menu-item[command="interrupt"]')!
    const li = item.shadowRoot!.querySelector('li')!
    let clicks = 0
    li.addEventListener('click', () => (clicks += 1))
    expect(await a.command('notebook:interrupt-kernel')).toMatchObject({ ok: true })
    expect(clicks).toBe(1)
  })

  it('command(clear-all-outputs)：后置条件不成立必须失败；输出真清空才 ok', async () => {
    const w = dom()
    const a = adapter(w)
    // ① 没人清 ⇒ fail closed（绝不「点了就算成功」）
    const first = await a.command('notebook:clear-all-cell-outputs')
    expect(first).toMatchObject({ ok: false, reason: expect.stringContaining('后置条件') })

    // ② 模拟 Colab：点菜单项后输出被清空 ⇒ ok
    const item = deepQuery(docOf(w), 'md-menu-item[command="clear-outputs"]')!
    item.shadowRoot!.querySelector('li')!.addEventListener('click', () => {
      for (const pre of docOf(w).querySelectorAll('.output pre')) pre.textContent = ''
    })
    expect(await a.command('notebook:clear-all-cell-outputs')).toMatchObject({ ok: true })
    expect(a.outputText(codeCell(a))).toBe('')
  })

  it('command：没实测过的命令一律 fail closed，理由带上命令名', async () => {
    const a = adapter(dom())
    const r = await a.command('notebook:hide-all-cell-outputs')
    expect(r).toMatchObject({
      ok: false,
      reason: expect.stringContaining('notebook:hide-all-cell-outputs'),
    })
  })

  it('clearCellOutput：输出操作菜单没挂载 ⇒ 带原因失败（不假装清了）', async () => {
    const a = adapter(dom())
    const r = await a.clearCellOutput(codeCell(a))
    expect(r).toMatchObject({ ok: false, reason: expect.stringContaining('菜单') })
  })

  it('toggleCell(output)：点 #toggle-visibility-button，可见性没变 ⇒ 失败', async () => {
    const w = dom()
    const a = adapter(w)
    const btn = innerButton(
      docOf(w),
      '.cell.code',
      'colab-output-info',
      '#toggle-visibility-button',
    )
    expect(btn).not.toBeNull()
    let clicks = 0
    btn!.addEventListener('click', () => (clicks += 1))
    const r = await a.toggleCell(codeCell(a), 'output')
    expect(clicks).toBe(1)
    expect(r).toMatchObject({ ok: false, reason: expect.stringContaining('无效果') })
  })

  it('折叠：根属性 + 注入样式；单 cell 的 off 覆盖全局', async () => {
    const w = dom()
    const a = adapter(w)
    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true })
    const list = docOf(w).querySelector('.notebook-cell-list')!
    expect(list.getAttribute('data-bnc-fold')).toBe('all')
    expect(docOf(w).getElementById('bnc-colab-fold-style')).not.toBeNull()

    expect(await a.setFolded(codeCell(a), false)).toMatchObject({ ok: true })
    expect(docOf(w).querySelector('.cell.code')!.getAttribute('data-bnc-fold')).toBe('off')

    expect(await a.setFoldedAll(false)).toMatchObject({ ok: true })
    expect(list.hasAttribute('data-bnc-fold')).toBe(false)
  })

  it('折叠按键常驻：折叠态点「展开」，展开态点「折叠」；折叠态留出上下间距', async () => {
    const w = dom()
    const a = adapter(w)
    a.cells() // 第一次取 cells 就装上按键与样式

    // 折叠后 cell 只有四行高，会与相邻 cell 相压 ⇒ 样式里必须有 margin
    const css = docOf(w).getElementById('bnc-colab-fold-style')?.textContent ?? ''
    expect(css).toContain('margin: 10px 0')
    expect(css).toContain('.bnc-fold')
    expect(css).toContain('.editor.lazy-editor')
    // 与 cell 左对齐、纵向中心压在顶边
    expect(css).toContain('transform: translateY(-50%)')

    const code = docOf(w).querySelector('.cell.code')!
    const text = docOf(w).querySelector('.cell.text')!
    const btnOf = (el: Element): HTMLButtonElement =>
      el.querySelector('.bnc-fold') as HTMLButtonElement
    const labelOf = (el: Element): string => btnOf(el).textContent ?? ''
    expect(labelOf(code)).toBe('折叠')
    expect(labelOf(text)).toBe('折叠')

    // 展开态点一下 = 折叠本 cell
    btnOf(code).click()
    expect(code.getAttribute('data-bnc-fold')).toBe('on')
    expect(labelOf(code)).toBe('展开')

    // 折叠态点一下 = 展开本 cell
    btnOf(code).click()
    expect(code.getAttribute('data-bnc-fold')).toBe('off')
    expect(labelOf(code)).toBe('折叠')

    // 全局折叠：都变「展开」；点文本的只展开它
    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true })
    expect(labelOf(code)).toBe('展开')
    expect(labelOf(text)).toBe('展开')
    btnOf(text).click()
    expect(text.getAttribute('data-bnc-fold')).toBe('off')
    expect(labelOf(text)).toBe('折叠')
    expect(labelOf(code)).toBe('展开')
  })

  it('定时徽标：run-cell 挂 cell，其余动作挂页面顶部；清空后移除', () => {
    const w = dom()
    const a = adapter(w)
    expect(
      a.setScheduledMarks([
        { id: 't1', label: '还剩 10s', uuid: 'cell-c3f2a19b' },
        { id: 't2', label: '运行全部 · 还剩 5s', action: 'run-all' },
      ]),
    ).toMatchObject({ ok: true, detail: 'marks:1+1' })
    const cell = docOf(w).querySelector('.cell.code')!
    expect(cell.getAttribute('data-bnc-sched')).toBe('1')
    expect(cell.querySelector('.bnc-sched')?.textContent).toContain('还剩 10s')
    const banner = docOf(w).getElementById('bnc-sched-banner')!
    expect(banner.textContent).toContain('运行全部')
    // 另一页的任务不会贴到这一页的 cell 上（不带 uuid/index ⇒ 走悬浮条）
    a.setScheduledMarks([])
    expect(cell.querySelector('.bnc-sched')).toBeNull()
    expect(docOf(w).getElementById('bnc-sched-banner')).toBeNull()
  })
})

describe('Colab 会话', () => {
  it('停会话 = 关运行时：mousedown 开菜单 → hover 激活项 → 点中 → 确认框主按钮', async () => {
    const w = dom()
    const s = new ColabSessionAdapter(docOf(w), HREF, 800)
    expect(s.sessionId()).toBe('colab-1vudx_hyhvvzc2kqxraosgspvzwcuxd7n')
    expect(s.sessionState()).toBe('running')

    const doc = docOf(w)
    const btn = doc.querySelector('#runtime-menu-button')!
    const menu = doc.getElementById('runtime-menu')!
    const item = doc.querySelector('[command="powerwash-current-vm"]')!
    // 真实 Colab：菜单初始隐藏，goog-menu-button 靠 mousedown 展开（只派发 click 打不开）
    menu.setAttribute('aria-hidden', 'true')
    let opened = 0
    let hovered = 0
    let clicked = 0
    let cancelled = 0
    let primary = 0
    let decoyClicks = 0
    // 诱饵：文档更早的位置也有一个看不见的 primaryAction（Colab 会预渲染隐藏副本）
    const decoy = doc.createElement('button')
    decoy.setAttribute('data-decoy', '1')
    decoy.setAttribute('slot', 'primaryAction')
    decoy.style.display = 'none'
    decoy.textContent = 'Yes'
    decoy.addEventListener('click', () => (decoyClicks += 1))
    doc.body.prepend(decoy)
    // 真实弹框（存档实抽）：mwc-dialog 宿主 + light DOM 的 md-text-button[slot=primaryAction]
    const dialog = doc.querySelector('mwc-dialog.yes-no-dialog') as HTMLElement
    expect(dialog).not.toBeNull()
    expect(dialog.hasAttribute('hidden')).toBe(true)
    const primaryHost = dialog.querySelector('[slot="primaryAction"]') as HTMLElement
    const secondary = dialog.querySelector('[slot="secondaryAction"]') as HTMLElement
    expect(primaryHost.tagName.toLowerCase()).toBe('md-text-button')
    expect(primaryHost.getAttribute('dialogaction')).toBe('ok')
    // 真正的 <button> 在 md-text-button 的 shadow root 里
    expect(primaryHost.shadowRoot?.querySelector('button#button')).not.toBeNull()
    secondary.addEventListener('click', () => (cancelled += 1))
    // MWC 把动作监听挂在宿主/弹框上：影子里的点击必须 composed 才冒得出来
    primaryHost.addEventListener('click', () => {
      primary += 1
      deepQuery(doc, '#connect')!.setAttribute('tooltiptext', 'Connect to a runtime')
      dialog.remove()
    })
    btn.addEventListener('mousedown', () => {
      opened += 1
      menu.setAttribute('aria-hidden', 'false')
    })
    // Closure：mouseover/mousemove 才把项变成 active；没有 active 时 click 不触发动作
    item.addEventListener('mouseover', () => {
      hovered += 1
      item.setAttribute('data-active', '1')
    })
    item.addEventListener('click', () => {
      if (!item.hasAttribute('data-active')) return
      clicked += 1
      menu.setAttribute('aria-hidden', 'true')
      // 打开夹具里的真实弹框（结构来自存档，不再手写模拟）
      dialog.removeAttribute('hidden')
    })

    const r = await s.stopSession()
    expect(r).toMatchObject({ ok: true })
    if (r.ok) expect(r.detail).toContain('已断开')
    expect(opened).toBe(1)
    expect(hovered).toBeGreaterThanOrEqual(1)
    expect(clicked).toBe(1)
    expect(primary).toBe(1)
    expect(cancelled).toBe(0)
    expect(decoyClicks).toBe(0)
    expect(dialog.isConnected).toBe(false)
  })

  it('菜单打不开 ⇒ 不点隐藏项，带原因快速失败（不烧 8s 假装点过）', async () => {
    const w = dom()
    docOf(w).getElementById('runtime-menu')!.setAttribute('aria-hidden', 'true')
    const item = docOf(w).querySelector('[command="powerwash-current-vm"]')!
    let clicked = 0
    item.addEventListener('click', () => (clicked += 1))

    const r = await new ColabSessionAdapter(docOf(w), HREF, 150).stopSession()
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('没有展开')
    expect(clicked).toBe(0)
  })

  it('确认键不在 slot 上、也不是 button（自定义元素）⇒ 按文案兜底也能点中', async () => {
    const w = dom()
    const doc = docOf(w)
    const s = new ColabSessionAdapter(doc, HREF, 1200)
    const item = doc.querySelector('[command="powerwash-current-vm"]')!
    let primary = 0
    item.addEventListener('click', () => {
      const dialog = doc.createElement('md-dialog')
      dialog.setAttribute('role', 'alertdialog')
      dialog.innerHTML =
        '<div class="mdc-dialog__surface"><h2 class="mdc-dialog__title">Disconnect and delete runtime</h2><footer></footer></div>'
      const btn = doc.createElement('x-yes')
      btn.textContent = 'Yes'
      btn.addEventListener('click', () => {
        primary += 1
        deepQuery(doc, '#connect')!.setAttribute('tooltiptext', 'Connect to a runtime')
        dialog.remove()
      })
      dialog.querySelector('footer')!.append(btn)
      doc.body.append(dialog)
    })

    const r = await s.stopSession()
    expect(r).toMatchObject({ ok: true })
    expect(primary).toBe(1)
  })

  it('点了菜单项但连接没断 ⇒ 带原因失败（不假装停机）', async () => {
    const w = dom()
    const r = await new ColabSessionAdapter(docOf(w), HREF, 150).stopSession()
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('仍显示已连接')
  })

  it('已经断开 ⇒ already-stopped，不点菜单', async () => {
    const w = dom()
    deepQuery(docOf(w), '#connect')!.setAttribute('tooltiptext', 'Connect to a runtime')
    const s = new ColabSessionAdapter(docOf(w), HREF, 100)
    expect(s.sessionState()).toBe('stopped')
    expect(await s.stopSession()).toMatchObject({ ok: true, detail: 'already-stopped' })
  })

  it('断开后的真实文案是「Click to connect Reconnect」⇒ 必须认 stopped（否则成功也报失败）', async () => {
    const w = dom()
    deepQuery(docOf(w), '#connect')!.setAttribute('tooltiptext', 'Click to connect Reconnect')
    const s = new ColabSessionAdapter(docOf(w), HREF, 100)
    expect(s.sessionState()).toBe('stopped')
    expect(await s.stopSession()).toMatchObject({ ok: true, detail: 'already-stopped' })
  })
})

describe('Colab observer：按 site 选择器标脏', () => {
  it('输出区变化 ⇒ 标该 cell；编辑器里打字 ⇒ 不标', async () => {
    const w = dom()
    const dirty: string[] = []
    const o = createOutputObserver(docOf(w), (id) => dirty.push(id), undefined, COLAB_OBSERVER)
    docOf(w).querySelector('.output pre')!.append('tail')
    docOf(w).querySelector('.cell.code .view-line')!.append('x')
    await new Promise((r) => setTimeout(r, 20))
    o.disconnect()
    expect(dirty).toEqual(['cell-c3f2a19b'])
  })
})
