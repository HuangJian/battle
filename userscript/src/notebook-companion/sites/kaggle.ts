/**
 * kaggle.ts — Kaggle 站点适配器（plan §4.3/§4.4）。第一版唯一真实适配器。
 *
 * 只碰 DOM，不碰 GM_* / 网络：命令阶梯与后置条件里的「等待 / 可见性」都可注入，
 * 于是 happy-dom 用例能验证「选择器找对元素 + 点击顺序对」，而不假装有布局。
 */
import { normalizeNewlines } from '../../utils'
import { MIME, SEL } from '../selectors'
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
  private readonly hooks: Required<
    Pick<KaggleHooks, 'isVisible' | 'wait' | 'nextFrame' | 'maxScanSteps' | 'selectTimeoutMs'>
  >

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

  get ready(): boolean {
    return q(this.doc, SEL.notebook) !== null
  }

  cells(): CellRef[] {
    return qa<HTMLElement>(this.doc, SEL.cell)
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
    for (const el of qa<HTMLElement>(this.doc, SEL.cell)) {
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
    const el = q<HTMLElement>(this.doc, '.jp-Cell.jp-mod-active, .jp-Cell.jp-mod-selected')
    if (!el) return null
    const id = el.getAttribute('data-uuid')
    return this.cells().find((c) => (id ? c.id === id : false)) ?? null
  }

  /** 虚拟化：目标不在 DOM ⇒ 按 0.8 视口步进滚动（每步等两帧；400 步上界）。 */
  async ensureRendered(index: number): Promise<CellRef | null> {
    const found = this.cells().find((c) => c.index === index)
    if (found) return found
    const scroller = q<HTMLElement>(this.doc, SEL.scrollOuter)
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
    const els = qa<HTMLElement>(this.doc, SEL.command(id))
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
    const palette = q<HTMLElement>(this.doc, '[aria-label="Open command palette"]')
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

  scroll(to: 'top' | 'bottom'): CmdResult {
    const scroller = q<HTMLElement>(this.doc, SEL.scrollOuter)
    if (!scroller) return { ok: false, reason: '找不到滚动容器 .jp-WindowedPanel-outer' }
    scroller.scrollTop = to === 'top' ? 0 : scroller.scrollHeight
    this.fireScroll(scroller)
    return { ok: true, detail: `scrollTop=${scroller.scrollTop}` }
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
