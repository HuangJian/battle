import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import {
  AistudioAdapter,
  AistudioSessionAdapter,
  parseAistudioNotebookKey,
  parseAistudioSession,
} from '../src/notebook-companion/sites/aistudio'

/**
 * 夹具来自真实 AI Studio（BML Codelab）页面存档（2026-10-31）：`tmp/aistudio.mhtml` 的
 * notebook 段抽出 `.jp-NotebookPanel`，命令项从同一存档的命令面板抽出来（data-command 原样），
 * 源码与输出换成合成文本（原存档输出里有训练日志，不入库）。
 */
const FIXTURE = readFileSync(join(import.meta.dir, 'fixtures/aistudio.html'), 'utf8')
const HREF = 'https://aistudio.baidu.com/bj-cpu-01/user/20267332/10818538/lab/tree/main.ipynb'

const opened: DomHandle[] = []
/** happy-dom 的 Document 与 lib.dom 的 Document 结构不兼容（v21 类型）；测试边界一次强转。 */
const docOf = (w: DomHandle): Document => w.document as unknown as Document

function dom(): DomHandle {
  const w = loadDom(FIXTURE, HREF)
  opened.push(w)
  return w
}

afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const fast = {
  wait: async (): Promise<void> => {},
  nextFrame: async (): Promise<void> => {},
  isVisible: () => true,
  selectTimeoutMs: 60,
}

const adapter = (w: DomHandle): AistudioAdapter => new AistudioAdapter(docOf(w), fast)
const cellOf = (w: DomHandle, index: number): HTMLElement =>
  docOf(w).querySelectorAll<HTMLElement>('.jp-Cell')[index]!

describe('AI Studio：URL → 会话 / notebook 键', () => {
  it('`/user/<uid>/<pid>/lab/tree/<nb>` 解析出工程 id 与文件名', () => {
    expect(parseAistudioNotebookKey(HREF)).toBe('aistudio/10818538/main.ipynb')
    expect(parseAistudioSession(HREF)).toBe('aistudio-10818538-main.ipynb')
  })

  it('外层工作台 / 非数字工程 id ⇒ 空串（定时任务据此拒绝归属）', () => {
    expect(
      parseAistudioNotebookKey('https://aistudio.baidu.com/bj-cpu-01/user/1/abc/lab/tree/x.ipynb'),
    ).toBe('')
    expect(parseAistudioNotebookKey('https://aistudio.baidu.com/bj-cpu-01/user/1/2/vscode/')).toBe(
      '',
    )
    expect(parseAistudioNotebookKey('not a url')).toBe('')
    expect(parseAistudioSession('https://aistudio.baidu.com/studio/abc')).toBe('aistudio-unknown')
  })
})

describe('AI Studio 适配器：结构来自真实页面存档', () => {
  it('cells()：不虚拟化 ⇒ 全在 DOM；id 用序号，code/markdown 分类对', () => {
    const a = adapter(dom())
    const cells = a.cells()
    expect(cells.map((c) => c.id)).toEqual([
      'cell-0',
      'cell-1',
      'cell-2',
      'cell-3',
      'cell-4',
      'cell-5',
    ])
    expect(cells.filter((c) => c.kind === 'code').length).toBe(5)
    expect(cells[5]?.kind).toBe('markdown')
    // 每次取都从 DOM 现算（页面上 cell 增删后序号跟着变，不会留旧 id）
    expect(a.cells()).toHaveLength(cells.length)
  })

  it('sourceText：Monaco 逐行 + NBSP 还原成空格', () => {
    const a = adapter(dom())
    const src = a.sourceText(a.cells()[0]!)
    expect(src.split('\n')).toHaveLength(8)
    expect(src).toContain('import time') // 夹具里存的是 `import&nbsp;time`
    expect(src).not.toContain('\u00a0')
    expect(src).toContain('CFG = {')
  })

  it('outputText：stdout 按 DOM 顺序（有输出的只有两段）', () => {
    const a = adapter(dom())
    const cells = a.cells()
    const withOut = cells.filter((c) => a.outputText(c) !== '')
    expect(withOut).toHaveLength(2)
    const out = a.outputText(withOut[0]!)
    expect(out).toContain('[rollout-node] build ok')
    expect(out).toContain('RuntimeError: boom')
  })

  it('activeCell：`.jp-mod-selected` / `.jp-mod-active` 那个', () => {
    const w = dom()
    const a = adapter(w)
    cellOf(w, 2).classList.add('jp-mod-selected')
    expect(a.activeCell()).toMatchObject({ id: 'cell-2', index: 2 })
    docOf(w).querySelectorAll<HTMLElement>('.jp-Cell')[2]!.classList.remove('jp-mod-selected')
    cellOf(w, 0).classList.add('jp-mod-active')
    expect(a.activeCell()).toMatchObject({ id: 'cell-0', index: 0 })
  })

  it('focusConfig：启发式命中 CFG 行；没标记 / 没源码 ⇒ 带原因失败', async () => {
    const w = dom()
    const a = adapter(w)
    // 不派发指针事件：Monaco 会把合成 mousedown 当选区起点，鼠标一动就选上内容
    const line = docOf(w).querySelectorAll('.jp-InputArea-editor .view-line')[2]!
    const seen: string[] = []
    for (const t of ['mousedown', 'mouseup', 'click']) line.addEventListener(t, () => seen.push(t))
    const hit = await a.focusConfig(['CFG = {'])
    expect(hit.ok).toBe(true)
    expect(seen).toEqual([])
    if (hit.ok) {
      expect(hit.detail).toContain('第 3 行') // 合成源码第 3 行是 `CFG = {`
      expect(hit.detail).toContain('CFG 行')
    }
    // 标记表为空 ⇒ 直接拒绝（没有可定位的东西）
    expect((await a.focusConfig([])).ok).toBe(false)
    // 编辑器还没挂（一行源码都没有）⇒ 必须带原因失败，不能假装跳到了
    docOf(w)
      .querySelectorAll('.jp-InputArea-editor .view-lines')
      .forEach((el) => el.remove())
    const miss = await a.focusConfig(['CFG = {'])
    expect(miss.ok).toBe(false)
    if (!miss.ok) expect(miss.reason).toContain('找不到 CFG 行')
  })

  it('runCell：先选中再点 notebook:run-cell，点了才算数', async () => {
    const w = dom()
    const a = adapter(w)
    const cell = a.cells()[0]!
    cellOf(w, 0).classList.add('jp-mod-selected')
    const li = docOf(w).querySelector('[data-command="notebook:run-cell"]')!
    let clicks = 0
    li.addEventListener('click', () => (clicks += 1))
    expect(await a.runCell(cell)).toMatchObject({ ok: true })
    expect(clicks).toBe(1)
  })

  it('command：实测过的命令点得到；没实测的 id 一律带原因失败', async () => {
    const w = dom()
    const a = adapter(w)
    expect(await a.command('notebook:run-all-cells')).toMatchObject({ ok: true })
    const miss = await a.command('notebook:shutdown-kernel')
    expect(miss.ok).toBe(false)
    if (!miss.ok) expect(miss.reason).toContain('不在 DOM')
  })

  it('clearCellOutput：清空必须有后置条件；没变就是失败', async () => {
    const w = dom()
    const a = adapter(w)
    const cell = a.cells().find((c) => a.outputText(c) !== '')!
    cellOf(w, cell.index).classList.add('jp-mod-selected')

    const r = await a.clearCellOutput(cell)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('后置条件不成立')

    // 模拟真清空：点命令后输出子节点消失 ⇒ 成功
    docOf(w)
      .querySelector('[data-command="notebook:clear-cell-output"]')!
      .addEventListener('click', () => {
        cellOf(w, cell.index)
          .querySelectorAll('.jp-OutputArea-child')
          .forEach((child) => child.remove())
      })
    expect(await a.clearCellOutput(cell)).toMatchObject({ ok: true })
  })

  it('定时徽标：run-cell 目标按序号挂到 cell 顶部（AI Studio 没有 data-uuid）', () => {
    const w = dom()
    const a = adapter(w)
    // 宿主下发的是 captureTarget 的结果：uuid = cell.id（AI Studio 是序号 id），index = 序号
    expect(
      a.setScheduledMarks([
        { id: 't1', label: '还剩 10s', uuid: 'cell-2', index: 2 },
        { id: 't2', label: '运行全部 · 还剩 5s', action: 'run-all' },
      ]),
    ).toMatchObject({ ok: true, detail: 'marks:1+1' })

    const el = cellOf(w, 2)
    expect(el.getAttribute('data-bnc-sched')).toBe('1')
    expect(el.querySelector('.bnc-sched')?.textContent).toContain('还剩 10s')
    // 居中挂在 cell 顶部，并给左上角的 CODE/MD 折叠按键让位（不遮挡）
    const css = docOf(w).getElementById('bnc-sched-style')?.textContent ?? ''
    expect(css).toContain('width: max-content')
    expect(css).toContain('margin: 16px auto')
    expect(css).toContain('max-width: min(calc(100% - 96px), 520px)')
    expect(docOf(w).getElementById('bnc-sched-banner')?.textContent).toContain('运行全部')

    // 清空 ⇒ cell 上的标记与徽标都移除，顶部横幅一起消失
    expect(a.setScheduledMarks([])).toMatchObject({ ok: true, detail: 'marks:0+0' })
    expect(el.hasAttribute('data-bnc-sched')).toBe(false)
    expect(el.querySelector('.bnc-sched')).toBeNull()
    expect(docOf(w).getElementById('bnc-sched-banner')).toBeNull()
  })

  it('折叠：根属性 + 注入样式；每个 cell 顶边常驻 CODE/MD 按键', async () => {
    const w = dom()
    const a = adapter(w)
    a.cells() // 第一次取 cells 就装上按键与样式
    expect(docOf(w).querySelectorAll('.bnc-fold').length).toBe(6)

    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true })
    const nb = docOf(w).querySelector('.jp-Notebook')!
    expect(nb.getAttribute('data-bnc-fold')).toBe('all')
    const css = docOf(w).getElementById('bnc-cell-fold-style')?.textContent ?? ''
    expect(css).toContain('max-height: calc(4lh + 4px)')
    expect(css).toContain('transform: translateY(-50%)')

    const label = (): string =>
      (docOf(w).querySelector('.jp-Cell .bnc-fold') as HTMLButtonElement).textContent ?? ''
    expect(label()).toBe('展开')
    ;(docOf(w).querySelector('.jp-Cell .bnc-fold') as HTMLButtonElement).click()
    expect(cellOf(w, 0).getAttribute('data-bnc-fold')).toBe('off')
    expect(label()).toBe('折叠')

    expect(await a.setFoldedAll(false)).toMatchObject({ ok: true })
    expect(nb.hasAttribute('data-bnc-fold')).toBe(false)
  })

  it('顶/底：读到可滚容器就设 scrollTop（并把位置钉住）', () => {
    const w = dom()
    const a = adapter(w)
    const nb = docOf(w).querySelector('.jp-Notebook') as HTMLElement
    // happy-dom 没有布局 ⇒ 手工给一个可滚容器的读数
    let top = 0
    Object.defineProperty(nb, 'scrollHeight', { configurable: true, get: () => 3000 })
    Object.defineProperty(nb, 'clientHeight', { configurable: true, get: () => 600 })
    Object.defineProperty(nb, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (v: number) => {
        top = v
      },
    })

    expect(a.scroll('bottom')).toMatchObject({ ok: true })
    expect(top).toBe(2400)
    expect(a.scroll('top')).toMatchObject({ ok: true })
    expect(top).toBe(0)
  })

  it('顶/底：没有可滚容器也没有 cell ⇒ 带原因失败（不静默报成功）', () => {
    const w = dom()
    const a = adapter(w)
    docOf(w)
      .querySelectorAll('.jp-Cell')
      .forEach((el) => el.remove())
    const r = a.scroll('bottom')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('找不到可滚动容器')
  })
})

describe('AI Studio 会话', () => {
  it('执行指示器 busy ⇒ running；idle ⇒ idle；没有指示器 ⇒ unknown', () => {
    const w = dom()
    const s = new AistudioSessionAdapter(docOf(w), HREF)
    expect(s.sessionId()).toBe('aistudio-10818538-main.ipynb')
    expect(s.sessionState()).toBe('idle')

    const ind = docOf(w).querySelector('.jp-Notebook-ExecutionIndicator')!
    ind.setAttribute('data-status', 'busy')
    expect(s.sessionState()).toBe('running')

    ind.remove()
    expect(s.sessionState()).toBe('unknown')
  })

  it('停会话 = 关内核：点 Running Sessions 的「关闭」，内核条目消失才算成功', async () => {
    const w = dom()
    const s = new AistudioSessionAdapter(docOf(w), HREF, 400)
    expect(s.sessionState()).toBe('idle')
    const item = docOf(w).querySelector('li.jp-RunningSessions-item')!
    const btn = item.querySelector('button.jp-RunningSessions-itemShutdown') as HTMLElement
    let clicked = 0
    btn.addEventListener('click', () => {
      clicked += 1
      item.remove()
    })

    const r = await s.stopSession()
    expect(r).toMatchObject({ ok: true })
    if (r.ok) expect(r.detail).toContain('已关闭全部 1 个内核')
    expect(clicked).toBe(1)
    expect(s.sessionState()).toBe('stopped')
  })

  it('内核已经关了 ⇒ already-stopped，不点按钮', async () => {
    const w = dom()
    docOf(w).querySelector('li.jp-RunningSessions-item')!.remove()
    const s = new AistudioSessionAdapter(docOf(w), HREF, 100)
    expect(s.sessionState()).toBe('stopped')
    expect(await s.stopSession()).toMatchObject({ ok: true, detail: 'already-stopped' })
  })

  it('找不到 Running Sessions 的内核段 ⇒ 带原因失败（不假装停机）', async () => {
    const w = dom()
    docOf(w).querySelector('.jp-RunningSessions')!.remove()
    const r = await new AistudioSessionAdapter(docOf(w), HREF, 100).stopSession()
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('内核')
  })
})
