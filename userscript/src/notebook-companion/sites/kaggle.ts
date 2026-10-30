/**
 * kaggle.ts — Kaggle 站点适配器（plan §4.3/§4.4）。第一版唯一真实适配器。
 *
 * 只碰 DOM，不碰 GM_* / 网络：命令阶梯与后置条件里的「等待 / 可见性」都可注入，
 * 于是 happy-dom 用例能验证「选择器找对元素 + 点击顺序对」，而不假装有布局。
 */
import { normalizeNewlines } from '../../utils'
import { deepQuery, deepQueryAll, MIME, SEL } from '../selectors'
import type { CellRef, CmdResult, FrameAdapter, SessionAdapter } from './types'

export interface KaggleHooks {
  /** 默认：元素在布局里可见（happy-dom 无布局 ⇒ 用例注入）。 */
  isVisible?: (el: Element) => boolean
  wait?: (ms: number) => Promise<void>
  nextFrame?: () => Promise<void>
  /** ensureRendered 的步进上界（§3.6：400 步 + 取消）。 */
  maxScanSteps?: number
  /** select 轮询上界（ms）。 */
  selectTimeoutMs?: number
}

/**
 * 折叠开关：代码与 markdown 段都走这里。
 *
 * 不用 Jupyter 的 `hide-*-cell-code`：它会把输入整段藏掉，留不下「顶部两行」。改成
 * `data-bnc-fold` 属性 + 一条注入样式：`.jp-Notebook[data-bnc-fold="all"]` 管全部，
 * 单 cell 用 `on|off`（`off` 在全局折叠时覆盖成展开）。只加脚本私有属性，虚拟化后
 * 重新渲染的 cell 也自动生效。
 */
const FOLD_ATTR = 'data-bnc-fold'
const FOLD_STYLE_ID = 'bnc-cell-fold-style'
const UNFOLD_CLASS = 'bnc-unfold'
const FOLD_STYLE = `
/* 折叠态：每段保留顶部两行（2lh = 两倍行高；代码 = 源码前两行，markdown = 渲染内容前两行） */
.jp-Notebook[${FOLD_ATTR}="all"] .jp-CodeCell .jp-InputArea,
.jp-CodeCell[${FOLD_ATTR}="on"] .jp-InputArea {
  max-height: calc(2lh + 4px) !important;
  overflow: hidden !important;
}
.jp-Notebook[${FOLD_ATTR}="all"] .jp-MarkdownCell .jp-RenderedMarkdown,
.jp-Notebook[${FOLD_ATTR}="all"] .jp-MarkdownCell .jp-MarkdownOutput,
.jp-MarkdownCell[${FOLD_ATTR}="on"] .jp-RenderedMarkdown,
.jp-MarkdownCell[${FOLD_ATTR}="on"] .jp-MarkdownOutput {
  max-height: calc(2lh + 4px) !important;
  overflow: hidden !important;
}
/* 代码段 = 蓝，Markdown 段 = 琥珀；折叠后一眼看出一段是什么 */
.jp-Notebook[${FOLD_ATTR}="all"] .jp-CodeCell,
.jp-CodeCell[${FOLD_ATTR}="on"] {
  position: relative !important;
  border-left: 3px solid #4c8dff !important;
  background: rgba(76, 141, 255, .06) !important;
}
.jp-Notebook[${FOLD_ATTR}="all"] .jp-MarkdownCell,
.jp-MarkdownCell[${FOLD_ATTR}="on"] {
  position: relative !important;
  border-left: 3px solid #d9a441 !important;
  background: rgba(217, 164, 65, .08) !important;
}
/* 折叠后的展开按键（右上角，同时充当 CODE / MD 标签） */
.bnc-unfold {
  position: absolute; top: 2px; right: 6px; z-index: 6;
  font: 10px/1.4 system-ui, -apple-system, sans-serif; letter-spacing: .04em;
  color: #9dbdff; background: rgba(10, 14, 20, .78);
  border: 1px solid currentColor; border-radius: 999px; padding: 0 5px;
  cursor: pointer;
}
.bnc-unfold:hover { background: rgba(10, 14, 20, .95); }
.bnc-unfold::before { content: "CODE "; opacity: .7; }
.jp-MarkdownCell .bnc-unfold { color: #e6c36a; }
.jp-MarkdownCell .bnc-unfold::before { content: "MD "; opacity: .7; }
/* 全局折叠时单独展开一段：cell 上的 off 覆盖根属性 */
.jp-Notebook[${FOLD_ATTR}="all"] :is(.jp-CodeCell, .jp-MarkdownCell)[${FOLD_ATTR}="off"] {
  border-left: none !important;
  background: transparent !important;
}
.jp-Notebook[${FOLD_ATTR}="all"] .jp-CodeCell[${FOLD_ATTR}="off"] .jp-InputArea,
.jp-Notebook[${FOLD_ATTR}="all"] .jp-MarkdownCell[${FOLD_ATTR}="off"] .jp-RenderedMarkdown,
.jp-Notebook[${FOLD_ATTR}="all"] .jp-MarkdownCell[${FOLD_ATTR}="off"] .jp-MarkdownOutput {
  max-height: none !important;
  overflow: visible !important;
}
`

const defaultWait = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const defaultNextFrame = (): Promise<void> =>
  new Promise((r) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => requestAnimationFrame(() => r()))
    } else setTimeout(r, 16)
  })

function defaultVisible(el: Element): boolean {
  if (el.getAttribute('aria-hidden') === 'true') return false
  if (el.closest('.lm-mod-hidden')) return false
  const rect = el.getBoundingClientRect()
  return rect.width > 0 && rect.height > 0
}

function q<T extends Element>(root: ParentNode, sel: string): T | null {
  return root.querySelector<T>(sel)
}

function qa<T extends Element>(root: ParentNode, sel: string): T[] {
  return [...root.querySelectorAll<T>(sel)]
}

/** 点自定义元素时优先点它 shadow 里的 `<button>`（host 的 click 不一定转发）。 */
function clickElement(el: Element): void {
  const shadowButton = (
    el as HTMLElement & { shadowRoot?: ShadowRoot | null }
  ).shadowRoot?.querySelector<HTMLButtonElement>('button')
  ;(shadowButton ?? (el as HTMLElement)).click()
}
export class KaggleFrameAdapter implements FrameAdapter {
  readonly site = 'kaggle' as const
  private notebookEl: Element | null = null
  private readonly hooks: Required<
    Pick<KaggleHooks, 'isVisible' | 'wait' | 'nextFrame' | 'maxScanSteps' | 'selectTimeoutMs'>
  >
  private unfoldObserver: {
    observe(node: Node, init: MutationObserverInit): void
    disconnect(): void
  } | null = null
  private unfoldSyncQueued = false

  constructor(
    private readonly doc: Document,
    hooks: KaggleHooks = {},
  ) {
    this.hooks = {
      isVisible: hooks.isVisible ?? defaultVisible,
      wait: hooks.wait ?? defaultWait,
      nextFrame: hooks.nextFrame ?? defaultNextFrame,
      maxScanSteps: hooks.maxScanSteps ?? 400,
      selectTimeoutMs: hooks.selectTimeoutMs ?? 700,
    }
  }

  /** `.jp-Notebook` 可能晚于 attach 渲染，也可能在 open shadow root 里。 */
  private notebook(): Element | null {
    if (this.notebookEl?.isConnected) return this.notebookEl
    this.notebookEl = deepQuery(this.doc, SEL.notebook)
    return this.notebookEl
  }

  private root(): ParentNode {
    return this.notebook() ?? this.doc
  }

  get ready(): boolean {
    return this.notebook() !== null
  }

  cells(): CellRef[] {
    return qa<HTMLElement>(this.root(), SEL.cell)
      .map((el, i) => {
        const rawIndex = el.getAttribute('data-windowed-list-index')
        const index = rawIndex === null ? i : Number(rawIndex)
        const kind = el.classList.contains('jp-MarkdownCell')
          ? 'markdown'
          : el.classList.contains('jp-CodeCell')
            ? 'code'
            : 'raw'
        return {
          id: el.getAttribute('data-uuid') ?? `index-${index}`,
          index: Number.isFinite(index) ? index : i,
          kind,
        } satisfies CellRef
      })
      .sort((a, b) => a.index - b.index)
  }

  private cellEl(cell: CellRef): HTMLElement | null {
    for (const el of qa<HTMLElement>(this.root(), SEL.cell)) {
      const index = Number(el.getAttribute('data-windowed-list-index') ?? -1)
      if (el.getAttribute('data-uuid') === cell.id) return el
      if (el.getAttribute('data-uuid') === null && index === cell.index) return el
    }
    return null
  }

  /** stdout + stderr 按 DOM 顺序；mime 变化写中性标记（不含 error/warn 关键词）。 */
  outputText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const area = q(el, SEL.outputArea)
    if (!area) return ''
    let out = ''
    let lastMime = ''
    for (const child of qa<HTMLElement>(area, SEL.outputChild)) {
      const node = q<HTMLElement>(child, '[data-mime-type]') ?? child
      const mime = node.getAttribute('data-mime-type') ?? ''
      if (mime === MIME.stderr && lastMime !== MIME.stderr) out += '# [stderr]\n'
      else if (mime === MIME.stdout && lastMime === MIME.stderr) out += '# [stdout]\n'
      lastMime = mime
      const pre = q<HTMLElement>(node, 'pre') ?? node
      out += pre.textContent ?? ''
    }
    return normalizeNewlines(out)
  }

  sourceText(cell: CellRef): string {
    const el = this.cellEl(cell)
    return el ? (q<HTMLElement>(el, SEL.cmContent)?.textContent ?? '') : ''
  }

  /** 事件构造器取自文档窗口（跨 realm 用全局 `Event` 会 TypeError）。 */
  private fireScroll(el: HTMLElement): void {
    const Ctor = (this.doc.defaultView?.Event ?? Event) as typeof Event
    el.dispatchEvent(new Ctor('scroll'))
  }

  /** 当前 Jupyter 选中/激活的 cell；面板的「折叠当前」用它。 */
  activeCell(): CellRef | null {
    const el = q<HTMLElement>(this.root(), '.jp-Cell.jp-mod-active, .jp-Cell.jp-mod-selected')
    if (!el) return null
    const id = el.getAttribute('data-uuid')
    return this.cells().find((c) => (id ? c.id === id : false)) ?? null
  }

  /** 虚拟化：目标不在 DOM ⇒ 按 0.8 视口步进滚动（每步等两帧；400 步上界）。 */
  async ensureRendered(index: number): Promise<CellRef | null> {
    const found = this.cells().find((c) => c.index === index)
    if (found) return found
    const scroller = q<HTMLElement>(this.root(), SEL.scrollOuter)
    if (!scroller) return null
    const viewport = scroller.clientHeight
    if (!viewport || viewport <= 0) return null // 无布局（happy-dom）⇒ 不假装能滚
    for (let step = 0; step < this.hooks.maxScanSteps; step++) {
      scroller.scrollTop += Math.floor(viewport * 0.8)
      this.fireScroll(scroller)
      await this.hooks.nextFrame()
      const hit = this.cells().find((c) => c.index === index)
      if (hit) return hit
      if (scroller.scrollTop + viewport >= scroller.scrollHeight) break
    }
    return null
  }

  /** 命令只作用于 active cell ⇒ 先点 `.jp-InputArea`，等 `jp-mod-selected` 落到目标。 */
  async select(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    const input = el ? q<HTMLElement>(el, SEL.inputArea) : null
    if (!el || !input) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (el.classList.contains('jp-mod-selected') || el.classList.contains('jp-mod-active')) {
      return { ok: true, detail: 'already-active' }
    }
    input.click()
    const step = 25
    for (let waited = 0; waited <= this.hooks.selectTimeoutMs; waited += step) {
      if (el.classList.contains('jp-mod-selected') || el.classList.contains('jp-mod-active')) {
        return { ok: true, detail: 'selected' }
      }
      await this.hooks.wait(step)
    }
    return { ok: false, reason: '点击后未出现 jp-mod-selected（选中失败，命令作用不到目标）' }
  }

  private clickCommand(id: string): 'visible' | 'hidden' | null {
    const els = deepQueryAll(this.doc, SEL.command(id)) as HTMLElement[]
    if (els.length === 0) return null
    const visible = els.find((el) => this.hooks.isVisible(el))
    clickElement(visible ?? els[0]!)
    return visible ? 'visible' : 'hidden'
  }

  /** 命令阶梯：可见元素 → 任意隐藏元素 → 命令面板 → 失败（绝不静默）。 */
  async command(id: string, args: { cell?: CellRef } = {}): Promise<CmdResult> {
    if (args.cell) {
      const sel = await this.select(args.cell)
      if (!sel.ok) return sel
    }
    const first = this.clickCommand(id)
    if (first) return { ok: true, detail: `clicked:${first}` }
    const palette = deepQuery(this.doc, '[aria-label="Open command palette"]') as HTMLElement | null
    if (palette) {
      palette.click()
      await this.hooks.wait(50)
      const retry = this.clickCommand(id)
      if (retry) return { ok: true, detail: `palette:${retry}` }
    }
    return { ok: false, reason: `command ${id} 不在 DOM（工具栏/命令面板都没有）` }
  }

  private outputChildren(cell: CellRef): number {
    const el = this.cellEl(cell)
    return el ? qa(el, `${SEL.outputArea} ${SEL.outputChild}`).length : 0
  }

  private async commandChecked(
    id: string,
    cell: CellRef,
    check: () => boolean,
  ): Promise<CmdResult> {
    const r = await this.command(id, { cell })
    if (!r.ok) return r
    for (let waited = 0; waited < 2000; waited += 100) {
      if (check()) return { ok: true, detail: `${id}:postcondition` }
      await this.hooks.wait(100)
    }
    return { ok: false, reason: `${id} 点击了但后置条件不成立（无效果）` }
  }

  async runCell(cell: CellRef): Promise<CmdResult> {
    const sel = await this.select(cell)
    if (!sel.ok) return sel
    const before = this.outputText(cell)
    const r = await this.command('notebook:run-cell')
    const alt = r.ok ? r : await this.command('runmenu:run') // 工具栏/溢出菜单里的那份
    if (!alt.ok) return alt
    await this.hooks.wait(100)
    if (this.outputText(cell) !== before) return { ok: true, detail: 'running' }
    return { ok: true, detail: 'queued?' } // 运行有可见效果；无 busy 类也不当失败
  }

  async clearCellOutput(cell: CellRef): Promise<CmdResult> {
    return this.commandChecked(
      'notebook:clear-cell-output',
      cell,
      () => this.outputChildren(cell) === 0,
    )
  }

  async toggleCell(cell: CellRef, part: 'input' | 'output'): Promise<CmdResult> {
    const el = this.cellEl(cell)
    const collapser = el
      ? q<HTMLElement>(el, part === 'input' ? SEL.inputCollapser : SEL.outputCollapser)
      : null
    if (collapser) {
      const before = el?.className ?? ''
      collapser.click()
      for (let waited = 0; waited < 500; waited += 50) {
        if ((el?.className ?? '') !== before) return { ok: true, detail: 'collapser' }
        await this.hooks.wait(50)
      }
    }
    const hide = part === 'input' ? 'notebook:hide-cell-code' : 'notebook:hide-cell-outputs'
    const show = part === 'input' ? 'notebook:show-cell-code' : 'notebook:show-cell-outputs'
    const hidden = el?.querySelector(part === 'input' ? SEL.inputArea : SEL.outputArea)
    return this.command(hidden ? hide : show, { cell })
  }

  private ensureFoldStyle(): boolean {
    if (!this.doc.getElementById(FOLD_STYLE_ID)) {
      const parent = this.doc.head ?? this.doc.documentElement
      if (!parent) return false
      const style = this.doc.createElement('style')
      style.id = FOLD_STYLE_ID
      style.textContent = FOLD_STYLE
      parent.append(style)
    }
    this.watchFold()
    return true
  }

  /** 观察 cell 进出 DOM（虚拟化/重渲染）⇒ 补回「展开」按钮；输出/输入区的高频变化跳过。 */
  private watchFold(): void {
    if (this.unfoldObserver) return
    const view = this.doc.defaultView as unknown as {
      MutationObserver?: new (cb: (records: MutationRecord[]) => void) => {
        observe(node: Node, init: MutationObserverInit): void
        disconnect(): void
      }
    } | null
    const Ctor = view?.MutationObserver
    if (!Ctor || !this.doc.documentElement) return
    this.unfoldObserver = new Ctor((records) => {
      for (const r of records) {
        const target = r.target as unknown as Element
        if (typeof target.closest !== 'function') {
          this.queueFoldSync()
          return
        }
        if (target.closest(SEL.outputArea) || target.closest(SEL.inputArea)) continue
        this.queueFoldSync()
        return
      }
    })
    this.unfoldObserver.observe(this.doc.documentElement, { childList: true, subtree: true })
  }

  private queueFoldSync(): void {
    if (this.unfoldSyncQueued) return
    this.unfoldSyncQueued = true
    const run = (): void => {
      this.unfoldSyncQueued = false
      this.syncUnfoldButtons()
    }
    const view = this.doc.defaultView as unknown as {
      queueMicrotask?: (fn: () => void) => void
    } | null
    if (view?.queueMicrotask) view.queueMicrotask(run)
    else setTimeout(run, 0)
  }

  /** 折叠态的 cell 各带一个「展开」按钮（点一下只展开这一段）；展开态的清掉。 */
  private syncUnfoldButtons(): void {
    const nb = this.notebook()
    if (!nb) return
    const all = nb.getAttribute(FOLD_ATTR) === 'all'
    for (const el of qa<HTMLElement>(nb, SEL.cell)) {
      const own = el.getAttribute(FOLD_ATTR)
      const folded = own === 'on' || (all && own !== 'off')
      const btn = q<HTMLElement>(el, `.${UNFOLD_CLASS}`)
      if (folded && !btn) this.addUnfoldButton(el)
      else if (!folded && btn) btn.remove()
    }
  }

  private addUnfoldButton(el: HTMLElement): void {
    const btn = this.doc.createElement('button')
    btn.type = 'button'
    btn.className = UNFOLD_CLASS
    btn.textContent = '展开'
    btn.title = '展开这个 cell（其他 cell 保持折叠）'
    btn.addEventListener('click', (ev) => {
      ev.preventDefault()
      ev.stopPropagation()
      el.setAttribute(FOLD_ATTR, 'off')
      this.syncUnfoldButtons()
    })
    el.append(btn)
  }

  /**
   * 全部折叠/展开。先 `notebook:show-all-cell-code` 还原 Jupyter 原生折叠（原生折叠会把
   * 输入整段藏掉，留不下顶部两行），再由根属性 + 注入样式接管。
   */
  async setFoldedAll(collapsed: boolean): Promise<CmdResult> {
    const nb = this.notebook()
    if (!nb) return { ok: false, reason: '找不到 .jp-Notebook（笔记本未就绪）' }
    if (!this.ensureFoldStyle()) return { ok: false, reason: '无法注入折叠样式（没有 head）' }
    await this.command('notebook:show-all-cell-code')
    if (collapsed) nb.setAttribute(FOLD_ATTR, 'all')
    else nb.removeAttribute(FOLD_ATTR)
    // 全部切换时清掉单 cell 覆盖，否则「展开全部」会被之前的 off/on 拦住
    for (const el of qa<HTMLElement>(nb, SEL.cell)) el.removeAttribute(FOLD_ATTR)
    this.syncUnfoldButtons()
    return { ok: true, detail: collapsed ? 'folded:all' : 'unfolded:all' }
  }

  /** 单个 cell：代码 / markdown 同一套属性；全局折叠时 `off` 覆盖成展开。 */
  async setFolded(cell: CellRef, collapsed: boolean): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (!this.ensureFoldStyle()) return { ok: false, reason: '无法注入折叠样式（没有 head）' }
    // 原生折叠只能整段隐藏 ⇒ 先还原，再由属性接管「顶部两行」
    await this.command('notebook:show-cell-code', { cell })
    el.setAttribute(FOLD_ATTR, collapsed ? 'on' : 'off')
    this.syncUnfoldButtons()
    return { ok: true, detail: collapsed ? 'folded' : 'unfolded' }
  }

  /** 候选滚动容器：JL4 通常是 .jp-WindowedPanel-outer，不同布局/版本也可能是 notebook 或文档本身。 */
  private scrollCandidates(): HTMLElement[] {
    const out: HTMLElement[] = []
    const push = (el: Element | null | undefined): void => {
      if (el && !out.includes(el as HTMLElement)) out.push(el as HTMLElement)
    }
    const nb = this.notebook()
    push(nb ? q(nb, SEL.scrollOuter) : null)
    push(nb)
    push(q(this.doc, '.jp-NotebookPanel'))
    push(this.doc.scrollingElement)
    push(this.doc.documentElement)
    push(this.doc.body)
    return out
  }

  private pinScroll(el: HTMLElement, top: number): void {
    const view = this.doc.defaultView as unknown as {
      requestAnimationFrame?: (cb: () => void) => number
    } | null
    const pin = (): void => {
      el.scrollTop = top
    }
    if (view?.requestAnimationFrame) view.requestAnimationFrame(pin)
    else setTimeout(pin, 16)
  }

  /**
   * 顶/底：按候选容器逐个试，读回 `scrollTop` 验证（overflow 不是 auto/scroll 的容器赋值无效）；
   * 全都不行再让首/末个已渲染 cell 自己滚进视口。失败必须带原因，不静默报成功。
   */
  scroll(to: 'top' | 'bottom'): CmdResult {
    const tried: string[] = []
    for (const el of this.scrollCandidates()) {
      const max = Math.max(0, el.scrollHeight - el.clientHeight)
      if (max <= 1) continue
      const want = to === 'top' ? 0 : max
      const before = el.scrollTop
      el.scrollTop = want
      this.fireScroll(el)
      const label = el.classList[0] ?? el.tagName
      const atTarget = Math.abs(el.scrollTop - want) <= 1
      const overflowY = this.doc.defaultView?.getComputedStyle(el).overflowY
      const scrollable = overflowY === 'auto' || overflowY === 'scroll'
      if (atTarget && (before !== want || scrollable)) {
        this.pinScroll(el, want) // 防虚拟化/重渲染在下一帧回写旧位置
        return { ok: true, detail: `${to} → ${label}@${el.scrollTop}` }
      }
      if (scrollable) {
        // 平滑滚动/布局后回写：值可能下一帧才落地，先认这个容器并补钉一次
        this.pinScroll(el, want)
        return { ok: true, detail: `${to} → ${label}@pin` }
      }
      tried.push(`${label}:${el.scrollTop}/${want}`)
    }
    const cells = qa<HTMLElement>(this.root(), SEL.cell)
    const anchor = to === 'top' ? cells[0] : cells[cells.length - 1]
    if (anchor && typeof anchor.scrollIntoView === 'function') {
      anchor.scrollIntoView({ block: to === 'top' ? 'start' : 'end' })
      return { ok: true, detail: `scrollIntoView ${to}` }
    }
    return { ok: false, reason: `找不到可滚动容器（试过 ${tried.join(', ') || '无'}）` }
  }

  scrollOuter(to: 'top' | 'bottom'): CmdResult {
    const win = this.doc.defaultView
    if (!win) return { ok: false, reason: '没有 window' }
    win.scrollTo(0, to === 'top' ? 0 : win.document.body.scrollHeight)
    return { ok: true }
  }

  flash(cell: CellRef): void {
    const el = this.cellEl(cell)
    if (!el) return
    el.classList.add('bnc-hit')
    setTimeout(() => el.classList.remove('bnc-hit'), 1500)
  }
}

// ────────────────────────── host / 会话 ──────────────────────────

/** `kaggle-<owner>-<slug>-<runId>`（无 runId ⇒ `-edit`）；非法字符归一成 `-`，截断 64。 */
export function parseKaggleSession(href: string): string {
  let raw = ''
  try {
    const url = new URL(href)
    const parts = url.pathname.split('/').filter(Boolean) // code/<owner>/<slug>/edit[/run/<id>]
    if (parts[0] === 'code' && parts.length >= 3) {
      const owner = parts[1]!
      const slug = parts[2]!
      const runAt = parts.indexOf('run')
      const runId = runAt >= 0 ? parts[runAt + 1] : 'edit'
      raw = `kaggle-${owner}-${slug}-${runId ?? 'edit'}`
    }
  } catch {
    /* 非 URL ⇒ 交给下面兜底 */
  }
  const safe = raw
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .slice(0, 64)
  return safe || 'kaggle-unknown-edit'
}

export class KaggleSessionAdapter implements SessionAdapter {
  readonly site = 'kaggle' as const
  private readonly wait: (ms: number) => Promise<void>

  constructor(
    private readonly doc: Document,
    private readonly href: string,
    hooks: { wait?: (ms: number) => Promise<void> } = {},
  ) {
    this.wait = hooks.wait ?? defaultWait
  }

  sessionId(): string {
    return parseKaggleSession(this.href)
  }

  private statusText(): string {
    return this.doc.body?.textContent ?? ''
  }

  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown' {
    const text = this.statusText()
    if (/\bStopped\b|\bStarting\b/i.test(text)) return 'stopped'
    if (/Stop session/i.test(text) && /Running\b/i.test(text)) return 'running'
    if (/Stop session/i.test(text) && /Draft Session/i.test(text)) return 'idle'
    if (/Stop session/i.test(text)) return 'running'
    return 'unknown'
  }

  async stopSession(): Promise<CmdResult> {
    const button = q<HTMLElement>(this.doc, '[aria-label="Stop session"]')
    if (!button) return { ok: false, reason: '找不到 Stop session 按钮（外层页结构变了）' }
    button.click()
    await this.wait(300)
    const dialog = q<HTMLElement>(this.doc, '[role="dialog"]')
    if (dialog) {
      const confirm = qa<HTMLElement>(dialog, 'button').find(
        (b) =>
          /stop|shut down|confirm|ok|确定|停止/i.test(b.textContent ?? '') &&
          !/cancel|取消/i.test(b.textContent ?? ''),
      )
      if (confirm) confirm.click()
      else return { ok: false, reason: '出现确认框但找不到确认按钮（未假装成功）' }
    }
    for (let waited = 0; waited < 15_000; waited += 500) {
      if (this.sessionState() === 'stopped') return { ok: true, detail: 'stopped' }
      await this.wait(500)
    }
    return { ok: false, reason: '已点击 Stop session，但会话状态 15s 内未变 Stopped/Starting' }
  }
}
