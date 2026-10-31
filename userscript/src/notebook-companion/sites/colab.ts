/**
 * colab.ts — Google Colab 站点适配器（plan §4.3 的 `single` 角色：cell 与面板同文档、无桥）。
 *
 * 结构全部来自 2026-10-31 抓取的真实页面存档（`tmp/rollout.ipynb-Colab.mhtml` →
 * `test/fixtures/colab.html`，只去噪/换文本，标签与 id 原样保留）：
 *   - cell = `.cell.notebook-cell`，分类看 `cell code|text`，稳定 id 是 `id="cell-<hex>"`，
 *     序号在 `aria-label="Cell N: …"` 里；
 *   - 代码是 Monaco：`.editor.flex.monaco > .view-lines > .view-line`，整 cell 不做内滚动
 *     （编辑器高度 = 内容高度）⇒ 逐行都在 DOM 里，可以按行找 CFG；
 *   - 输出 = `.output > .output-content`，流式文本在 `pre` 里；
 *   - 每个 cell 的运行按钮在 `colab-run-button` 的 **open shadow root** 里（`button#run-button`）；
 *   - 「运行全部」= `#toolbar-run-button`；同一 shadow 树里挂着
 *     `md-menu[anchor="toolbar-run-button-more-actions"]`，菜单项带**实测属性**
 *     `md-menu-item[command="interrupt"]` / `[command="clear-outputs"]`（不靠文案猜）。
 *
 * 没实测过的能力一律 fail closed（带原因的 `{ok:false}`），绝不静默假装成功。
 */
import { normalizeNewlines } from '../../utils'
import { deepQuery, deepQueryAll } from '../selectors'
import type { CellRef, CmdResult, FrameAdapter, SessionAdapter } from './types'

export const COL_SEL = {
  list: '.notebook-cell-list',
  cell: '.cell.notebook-cell',
  output: '.output',
  viewLine: '.view-line',
  scroller: '#notebook-main',
} as const

export interface ColabHooks {
  wait?: (ms: number) => Promise<void>
  nextFrame?: () => Promise<void>
  /** ensureRendered 的步进上界（无布局环境里不假装能滚）。 */
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

function q<T extends Element>(root: ParentNode, sel: string): T | null {
  return root.querySelector<T>(sel)
}

function qa<T extends Element>(root: ParentNode, sel: string): T[] {
  return [...root.querySelectorAll<T>(sel)]
}

/** Monaco 一行的文本：`&nbsp;` 是可断行空格，还原成普通空格才对得上源码。 */
function textOf(el: Element): string {
  return (el.textContent ?? '').replace(/\u00a0/g, ' ')
}

/**
 * 一路钻进 open shadow root 找最里层可点的东西（Colab 全是 `md-*` 组件：
 * `md-text-button → md-text-button 的 shadow → <button>`，宿主上的 `click()` 传不进去）。
 */
function innermostClickable(el: Element): Element {
  let cur = el
  for (let i = 0; i < 8; i++) {
    const sr = (cur as HTMLElement).shadowRoot
    if (!sr) break
    const hit = sr.querySelector('button, [role="button"], [role="menuitem"], li')
    if (hit) {
      cur = hit
      continue
    }
    const host = qa(sr, '*').find((n) => (n as HTMLElement).shadowRoot)
    if (!host) break
    cur = host
  }
  return cur
}

/** click 是 composed 事件：点最里层，宿主与祖先上的监听都能收到。 */
function clickDeep(el: Element): void {
  ;(innermostClickable(el) as HTMLElement).click()
}

/**
 * 元素是否被隐藏（顺着 open shadow root 的宿主链往上走）。只看属性 / 内联样式，
 * 不读布局 —— 无头环境里 `offsetParent` 永远为 null，拿它判可见性只会误伤。
 */
function isHidden(el: Element | null): boolean {
  for (let node: Element | null = el; node;) {
    const style = (node as HTMLElement).style
    if ((node as HTMLElement).hidden) return true
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return true
    if (node.getAttribute('aria-hidden') === 'true') return true
    node = node.parentElement ?? (node.getRootNode() as ShadowRoot).host ?? null
  }
  return false
}

/**
 * 只看「显示层面」的隐藏（hidden / 内联样式 / 计算样式）。**不吸收 `aria-hidden`**：
 * 弹框打开时 Colab 会把它背后的内容标成 aria-hidden，但确认键本身是可见的（用户看到的 Yes）。
 */
function displayHidden(el: Element | null): boolean {
  for (let node: Element | null = el; node;) {
    if ((node as HTMLElement).hidden) return true
    const style = (node as HTMLElement).style
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return true
    const view = node.ownerDocument?.defaultView
    if (view && typeof view.getComputedStyle === 'function') {
      try {
        const cs = view.getComputedStyle(node as HTMLElement)
        if (cs.display === 'none' || cs.visibility === 'hidden') return true
      } catch {
        // 游离节点 / 无样式环境：忽略，交给属性判断
      }
    }
    const root = node.getRootNode() as ShadowRoot
    node = node.parentElement ?? ('host' in root ? (root.host as unknown as Element) : null)
  }
  return false
}

/**
 * Closure 菜单项：先 `mouseover`/`mousemove` 把它变成 active，再按下/抬起。
 * 只派发 mousedown/mouseup/click 时菜单认不到 activeItem ⇒ 动作不触发，菜单也一直开着。
 */
function clickMenuEntry(doc: Document, el: Element): void {
  const Ctor = (doc.defaultView?.MouseEvent ?? MouseEvent) as typeof MouseEvent
  const at: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    composed: true,
    button: 0,
    clientX: 8,
    clientY: 8,
  }
  const target = innermostClickable(el) as HTMLElement
  for (const type of ['mouseover', 'mousemove', 'mousedown', 'mouseup', 'click'] as const)
    target.dispatchEvent(new Ctor(type, at))
}

/**
 * 真实按下序列（pointer + mouse + click）—— Material Web 的按钮只派发 click 不一定认；
 * 构造器取自文档窗口（跨 realm 用全局会 TypeError）。
 */
function clickAt(
  doc: Document,
  el: HTMLElement,
  box?: { left: number; top: number; height: number },
): void {
  const view = doc.defaultView
  const Mouse = (view?.MouseEvent ?? MouseEvent) as typeof MouseEvent
  const Pointer = (view?.PointerEvent ?? view?.MouseEvent ?? MouseEvent) as typeof MouseEvent
  const at: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    // 必须 composed：真实按钮在 md-text-button 的影子根里，事件不穿影子边界就到不了宿主/弹框
    composed: true,
    button: 0,
    clientX: box ? box.left + 4 : 0,
    clientY: box ? box.top + Math.max(1, box.height / 2) : 0,
  }
  el.dispatchEvent(new Pointer('pointerdown', at))
  el.dispatchEvent(new Mouse('mousedown', at))
  el.dispatchEvent(new Pointer('pointerup', at))
  el.dispatchEvent(new Mouse('mouseup', at))
  el.dispatchEvent(new Mouse('click', at))
}

// ────────────────────────── 折叠 / 徽标（脚本私有属性 + 注入样式） ──────────────────────────

const FOLD_ATTR = 'data-bnc-fold'
const FOLD_CLASS = 'bnc-fold'
const FOLD_STYLE_ID = 'bnc-colab-fold-style'
const SCHED_STYLE_ID = 'bnc-colab-sched-style'
const SCHED_CLASS = 'bnc-sched'
const SCHED_ATTR = 'data-bnc-sched'
const SCHED_BANNER_ID = 'bnc-sched-banner'
const SCHED_BANNER_ROW = 'bnc-sched-brow'
const HIT_CLASS = 'bnc-hit'

/**
 * 折叠 = 每段只留顶部四行（代码蓝 / 文本琥珀）。Colab 原生的「Hide code」是整段藏掉，
 * 留不下四行 ⇒ 与 Kaggle 同一套做法：私有属性 + 注入样式，`off` 在全局折叠时覆盖成展开。
 */
const FOLD_STYLE = `
.notebook-cell-list[data-bnc-fold="all"] .cell.code .inputarea,
.notebook-cell-list[data-bnc-fold="all"] .cell.code .editor.lazy-editor,
.cell.code[data-bnc-fold="on"] .inputarea,
.cell.code[data-bnc-fold="on"] .editor.lazy-editor,
.notebook-cell-list[data-bnc-fold="all"] .cell.text :is(.editor-container, .text-top-div),
.cell.text[data-bnc-fold="on"] :is(.editor-container, .text-top-div) {
  max-height: calc(4lh + 4px) !important;
  overflow: hidden !important;
}
/* 折叠后只有四行高：往上贴住上一个 cell（工具栏/浮层互相压住）⇒ 上下留出间距。
   代码 = 蓝，文本 = 琥珀，与 Kaggle 同口径；徽标同色，一眼看出这段是什么。 */
.cell.code[data-bnc-fold="on"],
.notebook-cell-list[data-bnc-fold="all"] .cell.code:not([data-bnc-fold="off"]) {
  position: relative !important;
  margin: 10px 0 !important;
  border-left: 3px solid #4c8dff !important;
  background: rgba(76, 141, 255, .06) !important;
}
.cell.text[data-bnc-fold="on"],
.notebook-cell-list[data-bnc-fold="all"] .cell.text:not([data-bnc-fold="off"]) {
  position: relative !important;
  margin: 10px 0 !important;
  border-left: 3px solid #d9a441 !important;
  background: rgba(217, 164, 65, .08) !important;
}
/* 全局折叠时单独展开一段：回到本色（margin 不复位，免得展开瞬间跳动） */
.notebook-cell-list[data-bnc-fold="all"] .cell.notebook-cell[data-bnc-fold="off"] {
  border-left: none !important;
  background: transparent !important;
}
.notebook-cell-list[data-bnc-fold="all"] .cell.notebook-cell[data-bnc-fold="off"] .inputarea,
.notebook-cell-list[data-bnc-fold="all"] .cell.notebook-cell[data-bnc-fold="off"] .editor.lazy-editor,
.notebook-cell-list[data-bnc-fold="all"] .cell.notebook-cell[data-bnc-fold="off"] :is(.editor-container, .text-top-div) {
  max-height: none !important;
  overflow: visible !important;
}
/* 折叠/展开按键（同时充当 CODE / MD 徽标）：左边线对齐 cell，纵向中心压在 cell 顶边上。
   右上角是 Colab 的 cell 工具条（删除/更多…），所以走左上。 */
.cell.notebook-cell { position: relative; }
.${FOLD_CLASS} {
  position: absolute; top: 0; left: 0; transform: translateY(-50%); z-index: 7;
  padding: 0 6px; border-radius: 999px; cursor: pointer; white-space: nowrap;
  font: 10px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; letter-spacing: .04em;
  color: #2f6fd0; background: rgba(255, 255, 255, .92);
  border: 1px solid currentColor;
}
.${FOLD_CLASS}:hover { background: #fff; }
.${FOLD_CLASS}::before { content: "CODE "; opacity: .7; }
.cell.text .${FOLD_CLASS} { color: #9a6a10; }
.cell.text .${FOLD_CLASS}::before { content: "MD "; opacity: .7; }
.${HIT_CLASS} { outline: 3px solid #4c8dff !important; outline-offset: -3px; }
`

const SCHED_STYLE = `
.cell.notebook-cell[${SCHED_ATTR}="1"] { outline: 2px solid #ff9f43 !important; outline-offset: -2px; }
.${SCHED_CLASS} {
  margin: 2px 6px 4px; padding: 3px 10px; border-radius: 6px;
  display: flex; align-items: center; gap: 6px;
  background: linear-gradient(90deg, #ffb020, #ff7a45);
  color: #1a1206; font: 700 12px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  letter-spacing: .02em;
}
.${SCHED_BANNER_ID} {
  position: fixed; top: 6px; left: 50%; transform: translateX(-50%);
  z-index: 2147483645; display: flex; flex-direction: column; gap: 3px;
  max-width: min(92vw, 560px); pointer-events: none;
}
.${SCHED_BANNER_ID} .${SCHED_BANNER_ROW} {
  padding: 4px 12px; border-radius: 999px; text-align: center;
  background: linear-gradient(90deg, #ffb020, #ff7a45);
  color: #1a1206; font: 700 12px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  box-shadow: 0 2px 12px rgba(255, 122, 69, .45);
}
`

// ────────────────────────── URL → 会话 / notebook 键 ──────────────────────────

/** 从任意 Colab 相关 URL 里取文件 id：`/drive/<id>`、referrer 的 `/file/d/<id>`、`?id=`。 */
function colabFileId(href: string): string {
  try {
    const url = new URL(href)
    const parts = url.pathname.split('/').filter(Boolean)
    const at = parts.indexOf('drive')
    if (at >= 0 && parts[at + 1]) return parts[at + 1]!
    const d = parts.indexOf('d')
    if (d > 0 && parts[d - 1] === 'file' && parts[d + 1]) return parts[d + 1]!
    const id = url.searchParams.get('id')
    if (id) return id
  } catch {
    /* 非 URL ⇒ 交给调用方判空 */
  }
  return ''
}

/** `colab-<fileId>`：日志键（`[a-z0-9._-]{1,64}`，全小写）。拿不到文件 id ⇒ `colab-unknown`。 */
export function parseColabSession(href: string): string {
  const id = colabFileId(href)
  if (!id) return 'colab-unknown'
  const safe = `colab-${id}`
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .slice(0, 64)
  return safe || 'colab-unknown'
}

/** notebook 键（跨 run 稳定，定时任务按它归属页面）；拿不到文件 id ⇒ ''（定时任务会拒绝）。 */
export function parseColabNotebookKey(href: string): string {
  const id = colabFileId(href)
  return id ? `colab/${id}` : ''
}

// ────────────────────────── 适配器 ──────────────────────────

export class ColabAdapter implements FrameAdapter {
  readonly site = 'colab' as const
  private listEl: Element | null = null
  private readonly hooks: Required<ColabHooks>
  /** 已设「运行 cell」定时器的目标（徽标由 1s 定时器反复贴回，重渲染不怕丢）。 */
  private schedMarks: Array<{ id: string; label: string; uuid?: string; index?: number }> = []
  private schedBanners: Array<{ id: string; label: string }> = []
  /** 折叠徽标的 observer：cell 重渲染后把 CODE/MD 徽标补回来。 */
  private foldObserver: {
    observe(node: Node, init: MutationObserverInit): void
    disconnect(): void
  } | null = null
  private foldSyncQueued = false
  /** 折叠按键是否已常驻（首次取 cells 时装样式 + observer）。 */
  private foldUiReady = false

  constructor(
    private readonly doc: Document,
    hooks: ColabHooks = {},
  ) {
    this.hooks = {
      wait: hooks.wait ?? defaultWait,
      nextFrame: hooks.nextFrame ?? defaultNextFrame,
      maxScanSteps: hooks.maxScanSteps ?? 400,
      selectTimeoutMs: hooks.selectTimeoutMs ?? 700,
    }
  }

  private list(): Element | null {
    if (this.listEl?.isConnected) return this.listEl
    this.listEl = deepQuery(this.doc, COL_SEL.list)
    return this.listEl
  }

  private root(): ParentNode {
    return this.list() ?? this.doc
  }

  get ready(): boolean {
    return this.list() !== null
  }

  cells(): CellRef[] {
    this.ensureFoldUi()
    return qa<HTMLElement>(this.root(), COL_SEL.cell)
      .map((el, i) => {
        const m = /^Cell (\d+):/.exec(el.getAttribute('aria-label') ?? '')
        const index = m ? Number(m[1]) : i
        const kind = el.classList.contains('code')
          ? 'code'
          : el.classList.contains('text')
            ? 'markdown'
            : 'raw'
        return {
          id: el.id || `index-${index}`,
          index: Number.isFinite(index) ? index : i,
          kind,
        } satisfies CellRef
      })
      .sort((a, b) => a.index - b.index)
  }

  private cellEl(cell: CellRef): HTMLElement | null {
    const all = qa<HTMLElement>(this.root(), COL_SEL.cell)
    const byId = all.find((el) => el.id !== '' && el.id === cell.id)
    return byId ?? all[cell.index] ?? null
  }

  /** 输出全文：优先 `pre`（流式/异常都在里面）；没有 pre（图像/HTML）就取渲染容器的文本。 */
  outputText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const out = q<HTMLElement>(el, COL_SEL.output)
    if (!out) return ''
    const pres = qa<HTMLElement>(out, 'pre')
    if (pres.length > 0) {
      return normalizeNewlines(pres.map((p) => p.textContent ?? '').join('\n'))
    }
    const body = q<HTMLElement>(out, '.output-iframe-container') ?? out
    return normalizeNewlines(body.textContent ?? '')
  }

  /**
   * 源码 = Monaco 逐行 `.view-line` 的文本（`&nbsp;` 还原成空格）。
   * 整 cell 不内滚 ⇒ 行都在 DOM；文本 cell 没有编辑器，退到渲染出来的 markdown。
   */
  sourceText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const lines = qa<HTMLElement>(el, COL_SEL.viewLine)
    if (lines.length > 0) {
      return normalizeNewlines(lines.map((l) => textOf(l)).join('\n'))
    }
    const md = q<HTMLElement>(el, '.markdown') ?? q<HTMLElement>(el, '.text-top-div')
    return normalizeNewlines(md?.textContent ?? '')
  }

  private findConfigLine(
    markers: string[],
  ): { cell: CellRef; line: HTMLElement; marker: string; lineNo: number } | null {
    const scan = (match: (text: string) => boolean) => {
      for (const cell of this.cells()) {
        if (cell.kind !== 'code') continue
        const el = this.cellEl(cell)
        if (!el) continue
        const lines = qa<HTMLElement>(el, COL_SEL.viewLine)
        for (let i = 0; i < lines.length; i++) {
          if (match(textOf(lines[i]!))) return { cell, line: lines[i]!, lineNo: i + 1 }
        }
      }
      return null
    }
    // ① 内置启发式：某行以 CFG 开头且含 `{`（覆盖 `CFG = {` / `CFG={` / 多空格）
    const guess = scan((text) => /^\s*CFG\b.*\{/.test(text))
    if (guess) return { ...guess, marker: 'CFG 行' }
    // ② 配置的字面标记按顺序
    for (const marker of markers) {
      const hit = scan((text) => text.includes(marker))
      if (hit) return { ...hit, marker }
    }
    return null
  }

  /** 面板「CFG」：滚到并聚焦配置行（不复制）。找不到必须带原因失败。 */
  async focusConfig(markers: string[]): Promise<CmdResult> {
    const wanted = markers.filter((m) => typeof m === 'string' && m.trim().length > 0)
    if (wanted.length === 0) return { ok: false, reason: 'configMarkers 为空（没有可定位的标记）' }
    let hit = this.findConfigLine(wanted)
    if (!hit) {
      this.scroll('top')
      for (let i = 0; i < 10 && !hit; i++) {
        await this.hooks.nextFrame()
        hit = this.findConfigLine(wanted)
      }
    }
    if (!hit) return { ok: false, reason: `找不到 CFG 行（标记：${wanted.join(' / ')}）` }
    return this.focusLine(hit.cell, hit.line, hit.marker, hit.lineNo)
  }

  /** 展开（折叠态下这行被裁掉）+ 滚进视口中央并聚焦编辑器 —— **不派发指针事件**。 */
  private focusLine(cell: CellRef, line: HTMLElement, marker: string, lineNo: number): CmdResult {
    const el = this.cellEl(cell)
    if (el) {
      const all = this.list()?.getAttribute(FOLD_ATTR) === 'all'
      const own = el.getAttribute(FOLD_ATTR)
      if (own === 'on' || (all && own !== 'off')) el.setAttribute(FOLD_ATTR, 'off')
    }
    // 不派发 mousedown/up/click：Monaco 会把合成 mousedown 当成选区起点，之后鼠标一动
    // 就把 CFG 内容选上（选中是多余的副作用，用户明确要求去掉）。
    ;(line.closest('.editor') as HTMLElement | null)?.focus()
    if (typeof line.scrollIntoView === 'function') line.scrollIntoView({ block: 'center' })
    const where = lineNo > 0 ? `第 ${cell.index} 段第 ${lineNo} 行` : `第 ${cell.index} 段`
    // `# @title` 这类弱标记必须自曝（plan §3.6 / S10）
    const label = marker.startsWith('#') ? `按 ${marker} 命中，可能不是 CFG cell` : marker
    return { ok: true, detail: `${where} · ${label}` }
  }

  /** 当前聚焦的 cell（`.focused`）。 */
  activeCell(): CellRef | null {
    const el = deepQuery(this.root(), `${COL_SEL.cell}.focused`) as HTMLElement | null
    if (!el) return null
    return this.cells().find((c) => el.id !== '' && c.id === el.id) ?? null
  }

  /** Colab 不做 cell 虚拟化，但保留步进兜底：目标不在 DOM 就滚容器再看（无布局 ⇒ 直接 null）。 */
  async ensureRendered(index: number): Promise<CellRef | null> {
    const found = this.cells().find((c) => c.index === index)
    if (found) return found
    const scroller = this.scrollTargets()[0]
    if (!scroller) return null
    const viewport = scroller.clientHeight
    if (!viewport || viewport <= 0) return null
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

  /** 命令只作用于聚焦 cell ⇒ 先点 cell 主体，等 `.focused` 落到目标。 */
  async select(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (el.classList.contains('focused')) return { ok: true, detail: 'already-active' }
    const target = q<HTMLElement>(el, '.cell-contents') ?? el
    clickAt(this.doc, target)
    for (let waited = 0; waited <= this.hooks.selectTimeoutMs; waited += 25) {
      if (el.classList.contains('focused')) return { ok: true, detail: 'selected' }
      await this.hooks.wait(25)
    }
    return { ok: false, reason: '点击后 cell 未获得 focused（选中失败，命令作用不到目标）' }
  }

  async runCell(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    // 运行中的 cell 再点 = 中断执行（按钮 tooltip 实测就是 Interrupt），绝不误触
    if (el.classList.contains('running')) return { ok: true, detail: 'already-running' }
    const sel = await this.select(cell)
    if (!sel.ok) return sel
    const btn = deepQuery(el, '#run-button') as HTMLElement | null
    if (!btn) return { ok: false, reason: '找不到该 cell 的运行按钮（colab-run-button 影子根）' }
    const before = this.outputText(cell)
    clickDeep(btn)
    await this.hooks.wait(120)
    if (el.classList.contains('running')) return { ok: true, detail: 'running' }
    if (this.outputText(cell) !== before) return { ok: true, detail: 'running' }
    return { ok: true, detail: 'queued?' } // 运行有可见效果，不以 DOM 断言当开关
  }

  async clearCellOutput(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    const trigger = deepQuery(
      el,
      '[data-aria-label="Code cell output actions"], [aria-label="Code cell output actions"]',
    ) as HTMLElement | null
    if (!trigger) return { ok: false, reason: '找不到该 cell 的「输出操作」菜单按钮' }
    const empty = (): boolean => this.outputText(cell) === ''
    if (empty()) return { ok: true, detail: 'already-empty' }
    // 菜单项文案没在存档里出现过 ⇒ 只按 /clear/i 找，命中后必须看到输出真的空了才算数
    const matchClear = (item: Element): boolean =>
      /clear/i.test(item.getAttribute('command') ?? '') || /clear/i.test(item.textContent ?? '')
    return this.pickMenuItem(trigger, matchClear, empty, { cell: cell.id })
  }

  async toggleCell(cell: CellRef, part: 'input' | 'output'): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (part === 'output') {
      const btn = deepQuery(el, '#toggle-visibility-button') as HTMLElement | null
      if (!btn) return { ok: false, reason: '找不到「Show/hide output」按钮' }
      const before = btn.getAttribute('aria-pressed')
      clickDeep(btn)
      await this.hooks.wait(60)
      const after = btn.getAttribute('aria-pressed')
      const out = q<HTMLElement>(el, COL_SEL.output)
      const hiddenNow =
        out !== null &&
        (out.hasAttribute('hidden') ||
          out.getAttribute('aria-hidden') === 'true' ||
          /none/i.test(out.style.display))
      if ((before !== null && after !== null && before !== after) || hiddenNow) {
        return { ok: true, detail: 'output-visibility' }
      }
      return { ok: false, reason: '点了「Show/hide output」但输出可见性没变（无效果）' }
    }
    const btn = deepQuery(
      el,
      '[data-aria-label="Hide code"], [aria-label="Hide code"], [title="Hide code"]',
    ) as HTMLElement | null
    if (!btn) return { ok: false, reason: '找不到该 cell 的「Hide code」按钮' }
    const beforeLabel = btn.getAttribute('data-aria-label') ?? btn.getAttribute('aria-label') ?? ''
    clickDeep(btn)
    await this.hooks.wait(60)
    const input = q<HTMLElement>(el, '.inputarea')
    const hiddenNow =
      input !== null &&
      (input.hasAttribute('hidden') ||
        input.getAttribute('aria-hidden') === 'true' ||
        /none/i.test(input.style.display))
    const afterLabel = btn.getAttribute('data-aria-label') ?? btn.getAttribute('aria-label') ?? ''
    if (hiddenNow || (afterLabel && afterLabel !== beforeLabel))
      return { ok: true, detail: 'input-visibility' }
    return { ok: false, reason: '点了「Hide code」但输入区可见性没变（无效果）' }
  }

  // ── 折叠 ──

  private ensureStyle(id: string, css: string): boolean {
    if (!this.doc.getElementById(id)) {
      const parent = this.doc.head ?? this.doc.documentElement
      if (!parent) return false
      const style = this.doc.createElement('style')
      style.id = id
      style.textContent = css
      parent.append(style)
    }
    return true
  }

  async setFoldedAll(collapsed: boolean): Promise<CmdResult> {
    const list = this.list()
    if (!list) return { ok: false, reason: '找不到 .notebook-cell-list（笔记本未就绪）' }
    if (!this.ensureStyle(FOLD_STYLE_ID, FOLD_STYLE)) {
      return { ok: false, reason: '无法注入折叠样式（没有 head）' }
    }
    if (collapsed) list.setAttribute(FOLD_ATTR, 'all')
    else list.removeAttribute(FOLD_ATTR)
    for (const el of qa<HTMLElement>(list, COL_SEL.cell)) el.removeAttribute(FOLD_ATTR)
    this.watchFold()
    this.syncFoldButtons()
    return { ok: true, detail: collapsed ? 'folded:all' : 'unfolded:all' }
  }

  async setFolded(cell: CellRef, collapsed: boolean): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (!this.ensureStyle(FOLD_STYLE_ID, FOLD_STYLE)) {
      return { ok: false, reason: '无法注入折叠样式（没有 head）' }
    }
    el.setAttribute(FOLD_ATTR, collapsed ? 'on' : 'off')
    this.watchFold()
    this.syncFoldButtons()
    return { ok: true, detail: collapsed ? 'folded' : 'unfolded' }
  }

  /** 观察 cell 进出 DOM（运行状态/重渲染会换节点）⇒ 重新贴回折叠徽标；输出区高频变化跳过。 */
  private watchFold(): void {
    if (this.foldObserver) return
    const view = this.doc.defaultView as unknown as {
      MutationObserver?: new (cb: (records: MutationRecord[]) => void) => {
        observe(node: Node, init: MutationObserverInit): void
        disconnect(): void
      }
    } | null
    const Ctor = view?.MutationObserver
    if (!Ctor || !this.doc.documentElement) return
    this.foldObserver = new Ctor((records) => {
      for (const r of records) {
        const target = r.target as unknown as Element
        if (
          typeof target.closest === 'function' &&
          target.closest(`${COL_SEL.output}, .inputarea`)
        ) {
          continue
        }
        this.queueFoldSync()
        return
      }
    })
    this.foldObserver.observe(this.doc.documentElement, { childList: true, subtree: true })
  }

  private queueFoldSync(): void {
    if (this.foldSyncQueued) return
    this.foldSyncQueued = true
    const run = (): void => {
      this.foldSyncQueued = false
      this.syncFoldButtons()
    }
    const view = this.doc.defaultView as unknown as {
      queueMicrotask?: (fn: () => void) => void
    } | null
    if (view?.queueMicrotask) view.queueMicrotask(run)
    else setTimeout(run, 0)
  }

  private ensureFoldUi(): void {
    if (this.foldUiReady) return
    if (!this.ensureStyle(FOLD_STYLE_ID, FOLD_STYLE)) return
    this.foldUiReady = true
    this.watchFold()
    this.syncFoldButtons()
  }

  /** 这个 cell 现在是折叠态吗（全局 all + 单段 on/off 覆盖）。 */
  private isFolded(el: Element): boolean {
    const own = el.getAttribute(FOLD_ATTR)
    const all = this.list()?.getAttribute(FOLD_ATTR) === 'all'
    return own === 'on' || (all && own !== 'off')
  }

  /**
   * 每个代码 / 文本 cell 顶边常驻一个 CODE/MD 按键：
   * 折叠态点 = 只展开这一段，展开态点 = 折叠这一段（不必回面板）。
   */
  private syncFoldButtons(): void {
    const list = this.list()
    if (!list) return
    for (const el of qa<HTMLElement>(list, COL_SEL.cell)) {
      if (!el.classList.contains('code') && !el.classList.contains('text')) continue
      const btn = q<HTMLElement>(el, `.${FOLD_CLASS}`) ?? this.addFoldButton(el)
      const folded = this.isFolded(el)
      const label = folded ? '展开' : '折叠'
      if (btn.textContent !== label) btn.textContent = label
      const title = folded ? '展开这个 cell（其余保持原样）' : '折叠这个 cell（只留顶部四行）'
      if (btn.title !== title) btn.title = title
    }
  }

  private addFoldButton(el: HTMLElement): HTMLElement {
    const btn = this.doc.createElement('button')
    btn.type = 'button'
    btn.className = FOLD_CLASS
    btn.addEventListener('click', (ev) => {
      ev.preventDefault()
      ev.stopPropagation()
      el.setAttribute(FOLD_ATTR, this.isFolded(el) ? 'off' : 'on')
      this.syncFoldButtons()
    })
    el.append(btn)
    return btn
  }

  // ── 命令 ──

  /** 菜单阶梯：开菜单 → 点目标项 → 后置条件；不行就直接点项再试一次（都不成 ⇒ 带原因失败）。 */
  private async pickMenuItem(
    trigger: HTMLElement,
    match: (item: Element) => boolean,
    check: (() => boolean) | null,
    why: { cell?: string } = {},
  ): Promise<CmdResult> {
    const root = trigger.getRootNode() as ParentNode
    const menu = this.menuFor(trigger, root)
    if (!menu) return { ok: false, reason: '菜单未挂载（找不到 md-menu）' }
    const items = qa<Element>(menu, 'md-menu-item, [role="menuitem"]')
    const item = items.find(match)
    if (!item) return { ok: false, reason: `菜单里没有目标项（${items.length} 项）` }
    const tryOnce = async (open: boolean): Promise<boolean> => {
      if (open) {
        clickDeep(trigger)
        await this.hooks.wait(60)
      }
      clickDeep(item)
      await this.hooks.wait(80)
      if (!check) return true
      return check()
    }
    if (await tryOnce(true)) {
      this.closeMenus(root)
      return { ok: true, detail: `menu${why.cell ? `:${why.cell}` : ''}` }
    }
    if (await tryOnce(false)) {
      this.closeMenus(root)
      return { ok: true, detail: 'menu-direct' }
    }
    this.closeMenus(root)
    return { ok: false, reason: '菜单项点了但后置条件不成立（无效果）' }
  }

  /** 菜单与触发器同处一个 shadow 根（`md-menu[anchor="<触发器 id>"]` 是实测属性）。 */
  private menuFor(trigger: HTMLElement, root: ParentNode): Element | null {
    const id = trigger.id || trigger.getAttribute('id') || ''
    if (id) {
      const hit = q<Element>(root, `md-menu[anchor="${id}"], md-menu[aria-labelledby="${id}"]`)
      if (hit) return hit
    }
    return q<Element>(root, 'md-menu')
  }

  /** 收尾：Escape 关掉刚打开的菜单（免得挡住页面）。 */
  private closeMenus(root: ParentNode): void {
    const Ctor = (this.doc.defaultView?.KeyboardEvent ?? KeyboardEvent) as typeof KeyboardEvent
    for (const menu of qa<Element>(root, 'md-menu')) {
      const holder = menu.getRootNode() as { host?: Element }
      const target = (holder.host as HTMLElement | undefined) ?? (menu as HTMLElement)
      target.dispatchEvent(new Ctor('keydown', { key: 'Escape', bubbles: true }))
    }
  }

  async command(id: string, args: { cell?: CellRef } = {}): Promise<CmdResult> {
    if (args.cell) {
      const sel = await this.select(args.cell)
      if (!sel.ok) return sel
    }
    switch (id) {
      case 'notebook:run-all-cells': {
        const btn = deepQuery(this.doc, '#toolbar-run-button') as HTMLElement | null
        if (!btn) return { ok: false, reason: '找不到工具栏「Run all cells in notebook」按钮' }
        clickDeep(btn)
        return { ok: true, detail: 'clicked:run-all' }
      }
      case 'notebook:interrupt-kernel': {
        const trigger = deepQuery(
          this.doc,
          '#toolbar-run-button-more-actions',
        ) as HTMLElement | null
        if (!trigger) return { ok: false, reason: '找不到工具栏「更多操作」按钮' }
        return this.pickMenuItem(
          trigger,
          (el) =>
            el.getAttribute('command') === 'interrupt' ||
            /\binterrupt\b/i.test(el.textContent ?? ''),
          null,
        )
      }
      case 'notebook:clear-all-cell-outputs': {
        const trigger = deepQuery(
          this.doc,
          '#toolbar-run-button-more-actions',
        ) as HTMLElement | null
        if (!trigger) return { ok: false, reason: '找不到工具栏「更多操作」按钮' }
        const codes = this.cells().filter((c) => c.kind === 'code')
        if (codes.every((c) => this.outputText(c) === ''))
          return { ok: true, detail: 'already-empty' }
        const emptied = (): boolean => codes.every((c) => this.outputText(c) === '')
        return this.pickMenuItem(
          trigger,
          (el) =>
            el.getAttribute('command') === 'clear-outputs' ||
            /clear all outputs/i.test(el.textContent ?? ''),
          emptied,
        )
      }
      case 'notebook:run-cell': {
        const cell = this.activeCell()
        if (!cell) return { ok: false, reason: '没有选中的 cell' }
        return this.runCell(cell)
      }
      default:
        return { ok: false, reason: `colab 不支持命令 ${id}（未实测，不猜）` }
    }
  }

  // ── 滚动 / 高亮 / 徽标 ──

  private fireScroll(el: HTMLElement): void {
    const Ctor = (this.doc.defaultView?.Event ?? Event) as typeof Event
    el.dispatchEvent(new Ctor('scroll'))
  }

  /** 候选滚动容器：Colab 的 notebook 滚动在 `colab-scroller`（`#notebook-main`）上。 */
  private scrollTargets(): HTMLElement[] {
    const out: HTMLElement[] = []
    const push = (el: Element | null | undefined): void => {
      if (el && !out.includes(el as HTMLElement)) out.push(el as HTMLElement)
    }
    push(deepQuery(this.doc, COL_SEL.scroller))
    push(deepQuery(this.doc, '.notebook-scrolling-horizontal'))
    push(this.doc.scrollingElement)
    push(this.doc.documentElement)
    push(this.doc.body)
    return out
  }

  scroll(to: 'top' | 'bottom'): CmdResult {
    const tried: string[] = []
    for (const el of this.scrollTargets()) {
      const max = Math.max(0, el.scrollHeight - el.clientHeight)
      if (max <= 1) continue
      const want = to === 'top' ? 0 : max
      const before = el.scrollTop
      el.scrollTop = want
      this.fireScroll(el)
      const label = el.id || el.classList[0] || el.tagName
      const atTarget = Math.abs(el.scrollTop - want) <= 1
      const overflowY = this.doc.defaultView?.getComputedStyle(el).overflowY
      const scrollable = overflowY === 'auto' || overflowY === 'scroll'
      if (atTarget && (before !== want || scrollable))
        return { ok: true, detail: `${to} → ${label}@${el.scrollTop}` }
      if (scrollable) return { ok: true, detail: `${to} → ${label}@pin` }
      tried.push(`${label}:${el.scrollTop}/${want}`)
    }
    const cells = qa<HTMLElement>(this.root(), COL_SEL.cell)
    const anchor = to === 'top' ? cells[0] : cells[cells.length - 1]
    if (anchor && typeof anchor.scrollIntoView === 'function') {
      anchor.scrollIntoView({ block: to === 'top' ? 'start' : 'end' })
      return { ok: true, detail: `${to} → cell scrollIntoView` }
    }
    return { ok: false, reason: `没有可滚动容器（${tried.join(' ') || '无候选'}）` }
  }

  scrollOuter(to: 'top' | 'bottom'): CmdResult {
    return this.scroll(to)
  }

  flash(cell: CellRef): void {
    const el = this.cellEl(cell)
    if (!el) return
    this.ensureStyle(FOLD_STYLE_ID, FOLD_STYLE)
    el.classList.add(HIT_CLASS)
    setTimeout(() => el.classList.remove(HIT_CLASS), 1500)
  }

  private markForCell(el: HTMLElement, index: number): { id: string; label: string } | null {
    for (const m of this.schedMarks) {
      if (m.uuid && el.id && m.uuid === el.id) return m
      if (m.index !== undefined && Number.isInteger(index) && m.index === index) return m
    }
    return null
  }

  /** 目标 cell 徽标：每拍贴回（Colab 重渲染会把徽标冲掉，宿主 1s 调一次即可自愈）。 */
  private syncBadges(): void {
    const list = this.list()
    if (!list) return
    for (const [i, el] of qa<HTMLElement>(list, COL_SEL.cell).entries()) {
      const label = /^Cell (\d+):/.exec(el.getAttribute('aria-label') ?? '')
      const index = label ? Number(label[1]) : i
      const mark = this.markForCell(el, index)
      const badge = q<HTMLElement>(el, `.${SCHED_CLASS}`)
      if (!mark) {
        badge?.remove()
        el.removeAttribute(SCHED_ATTR)
        continue
      }
      el.setAttribute(SCHED_ATTR, '1')
      const text = `⏰ 已定时 · ${mark.label}`
      if (badge) {
        if (badge.textContent !== text) badge.textContent = text
        continue
      }
      const b = this.doc.createElement('div')
      b.className = SCHED_CLASS
      b.textContent = text
      b.title = '已设置定时执行（只跑一次；在面板「定时」组删除或停用）'
      el.prepend(b)
    }
  }

  /** 顶部悬浮徽标：只列非 cell 任务（运行全部/停会话/中断内核）。 */
  private syncBanner(): void {
    const existing = this.doc.getElementById(SCHED_BANNER_ID)
    if (this.schedBanners.length === 0) {
      existing?.remove()
      return
    }
    const parent = this.doc.body ?? this.doc.documentElement
    if (!parent) return
    let box = existing
    if (!box) {
      box = this.doc.createElement('div')
      box.id = SCHED_BANNER_ID
      box.className = SCHED_BANNER_ID
      parent.append(box)
    }
    const seen = new Set<string>()
    for (const b of this.schedBanners) {
      seen.add(b.id)
      let row = qa<HTMLElement>(box, '[data-bnc-banner]').find(
        (el) => el.getAttribute('data-bnc-banner') === b.id,
      )
      if (!row) {
        row = this.doc.createElement('div')
        row.className = SCHED_BANNER_ROW
        row.setAttribute('data-bnc-banner', b.id)
        box.append(row)
      }
      const text = `⏰ ${b.label}`
      if (row.textContent !== text) row.textContent = text
    }
    for (const row of qa<HTMLElement>(box, '[data-bnc-banner]')) {
      if (!seen.has(row.getAttribute('data-bnc-banner') ?? '')) row.remove()
    }
  }

  /**
   * 定时徽标：`run-cell` 目标挂 cell，其余动作（运行全部/停会话/中断）挂页面顶部悬浮条。
   * 幂等：每次调用都按当前 marks 重贴，cell 重渲染后下一拍自动补回。
   */
  setScheduledMarks(
    marks: Array<{ id?: string; label?: string; uuid?: string; index?: number; action?: string }>,
  ): CmdResult {
    const cells: Array<{ id: string; label: string; uuid?: string; index?: number }> = []
    const banners: Array<{ id: string; label: string }> = []
    for (const m of marks) {
      const id = typeof m.id === 'string' ? m.id : ''
      const label = typeof m.label === 'string' && m.label ? m.label : '已设置'
      if (typeof m.action === 'string' && m.action && m.action !== 'run-cell') {
        banners.push({ id, label })
        continue
      }
      cells.push({
        id,
        label,
        ...(typeof m.uuid === 'string' && m.uuid ? { uuid: m.uuid } : {}),
        ...(typeof m.index === 'number' && Number.isInteger(m.index) ? { index: m.index } : {}),
      })
    }
    this.schedMarks = cells
    this.schedBanners = banners
    if (!this.ensureStyle(SCHED_STYLE_ID, SCHED_STYLE)) {
      return { ok: false, reason: '无法注入定时徽标样式（没有 head）' }
    }
    this.syncBadges()
    this.syncBanner()
    return { ok: true, detail: `marks:${cells.length}+${banners.length}` }
  }
}

// ────────────────────────── 会话（single 角色：面板与 cell 同页） ──────────────────────────

export class ColabSessionAdapter implements SessionAdapter {
  readonly site = 'colab' as const

  constructor(
    private readonly doc: Document,
    private readonly href: string,
    /** 断开后等连接条变「未连接」的上限（测试注入小值）。 */
    private readonly stopTimeoutMs = 8000,
  ) {}

  sessionId(): string {
    return parseColabSession(this.href)
  }

  /** 连接条的状态文案：`tooltiptext` 属性优先（存档实测：可见部分只有资源占用图形）。 */
  private connectText(): string {
    const el = deepQuery(this.doc, '#connect')
    if (!el) return ''
    return `${el.getAttribute('tooltiptext') ?? ''} ${el.textContent ?? ''}`
      .replace(/\s+/g, ' ')
      .trim()
  }

  /** 文案没实测过的状态一律 unknown，不猜。 */
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown' {
    const text = this.connectText()
    // 实测的未连接文案有三种：`Connect to a runtime`、`Click to connect`、`Reconnect`
    // （`\breconnect\b` 不能匹配 `Reconnecting`，否则会把「正在重连」误判成已断开）
    if (
      /click to connect|connect to (a )?runtime|\breconnect\b|not connected|disconnected|no runtime/i.test(
        text,
      )
    ) {
      return 'stopped'
    }
    if (/backend|ram:|disk:|python \d/i.test(text)) return 'running'
    return 'unknown'
  }

  /**
   * 停会话 = **关运行时**（省配额）：菜单栏 Runtime →「Disconnect and delete runtime」
   * （实测 `command="powerwash-current-vm"`，不在工具栏的「更多操作」里）。
   *
   * 它会连 `/content` 里的运行时文件一起删 —— 用户明确要求「关运行时、停机省配额」，这是主路径。
   * 点到之后必须看到连接条变成未连接才算成功；没断就带原因失败（不假装停机）。
   */
  async stopSession(): Promise<CmdResult> {
    if (this.sessionState() === 'stopped') return { ok: true, detail: 'already-stopped' }
    const runtimeBtn = deepQuery(this.doc, '#runtime-menu-button') as HTMLElement | null
    if (!runtimeBtn) return { ok: false, reason: '找不到菜单栏「Runtime」按钮（Colab 结构变了）' }
    // 菜单会重渲染：每次现查，别拿旧引用点
    const findItem = (): HTMLElement | null =>
      deepQuery(this.doc, '[command="powerwash-current-vm"]') as HTMLElement | null
    if (!findItem()) {
      return {
        ok: false,
        reason: 'Runtime 菜单里没有「Disconnect and delete runtime」（结构变了）',
      }
    }

    // Closure 的 `goog-menu-button` 认 **mousedown**：只派发 `.click()` 打不开菜单（实机就是这条），
    // 而菜单没开时点隐藏项不会有任何动作 —— 所以必须是真实三连，并等到菜单真的展开。
    // 展开判据取两者之一：菜单项不再隐藏，或按钮 `aria-expanded="true"`（Closure 自己维护）。
    const isMenuOpen = (): boolean => {
      const it = findItem()
      if (!it) return false
      if (!isHidden(it)) return true
      return deepQuery(this.doc, '#runtime-menu-button')?.getAttribute('aria-expanded') === 'true'
    }
    clickAt(this.doc, runtimeBtn)
    const opened = await this.waitUntil(
      isMenuOpen,
      Math.min(2000, this.stopTimeoutMs),
      () => clickAt(this.doc, runtimeBtn), // 600ms 仍关着 ⇒ 再按一次（不全局重试）
    )
    if (!opened) {
      return {
        ok: false,
        reason:
          'Runtime 菜单没有展开（点不开；Colab 的菜单结构变了），未点「Disconnect and delete runtime」',
      }
    }

    const item = findItem()
    if (!item) return { ok: false, reason: 'Runtime 菜单刚展开又没了（Colab 结构在变）' }
    clickMenuEntry(this.doc, item)
    await this.confirmDangerDialog()
    if (await this.waitUntil(() => this.sessionState() === 'stopped', this.stopTimeoutMs)) {
      return { ok: true, detail: '已断开并释放运行时（Colab 删掉了运行时文件）' }
    }
    const seen = this.connectText()
    const left = this.openDialog()
    if (left) {
      // 把候选数与按钮标签带进原因：下一次失败就能直接判断是「没找到」还是「点了没反应」
      const labels = this.actionLabels(left)
      return {
        ok: false,
        reason: `确认框「Disconnect and delete runtime」还开着（候选 ${this.primaryCandidates(left).length} 个；按钮：${labels.join(' / ') || '没找到'}），连接条仍是「${(seen || '状态为空').slice(0, 40)}」`,
      }
    }
    return {
      ok: false,
      reason: `点了「Disconnect and delete runtime」但连接条仍显示已连接：「${(seen || '状态为空').slice(0, 80)}」（菜单没点中或确认框未处理）`,
    }
  }

  /** 当前打开的「断开并删运行时」确认框（Colab = Material `mdc-dialog`，role 为 alertdialog）。 */
  private openDialog(): Element | null {
    const list = deepQueryAll(
      this.doc,
      '[role="alertdialog"], .mdc-dialog--open, md-dialog, colab-dialog',
    )
    for (const dialog of list) {
      if (displayHidden(dialog)) continue
      // md-dialog 的 open 属性已经是 false ⇒ 已经关了
      if ((dialog as unknown as { open?: unknown }).open === false) continue
      const label = `${dialog.getAttribute('aria-label') ?? ''} ${textOf(dialog)}`
      if (/disconnect|delete runtime|断开|删除/i.test(label)) return dialog
    }
    return null
  }

  /**
   * 确认框里可点的按钮，按可靠度排序：
   *   ① 当前对话框宿主 / 自身里的 `slot="primaryAction"`（Colab 的确认键就在这个 slot 上）；
   *   ② 文档里其它位置的同一个 slot（兜底：组件把按钮挂到别处）；
   *   ③ 按文案认的确认按钮（Yes/OK/Disconnect…，取消键永不入选）。
   * 隐藏") 的副本一律跳过——它们点不出任何效果。
   */
  private primaryCandidates(dialog: Element): Element[] {
    const hosts: Element[] = [dialog]
    const root = dialog.getRootNode() as ShadowRoot
    if ('host' in root && root.host) hosts.push(root.host as Element)
    const out: Element[] = []
    const add = (el: Element): void => {
      if (!out.includes(el) && !displayHidden(el)) out.push(el)
    }
    for (const host of hosts) deepQueryAll(host, '[slot="primaryAction"]').forEach(add)
    deepQueryAll(this.doc, '[slot="primaryAction"]').forEach(add)
    for (const host of hosts) {
      for (const btn of deepQueryAll(
        host,
        'button, [role="button"], md-text-button, md-filled-button, md-elevated-button, md-filled-tonal-button, md-outlined-button, mwc-button, paper-button, colab-button',
      )) {
        const label = `${btn.getAttribute('aria-label') ?? ''} ${textOf(btn)}`.trim()
        if (/cancel|取消|\bno\b/i.test(label)) continue
        if (/(yes|ok|disconnect|delete|确定|是|删除|断开)/i.test(label)) add(btn)
      }
    }
    // ③ 文案兜底：不认标签（Colab 可能把动作包成自定义元素），只看可见、短文案
    for (const host of hosts) {
      for (const el of deepQueryAll(host, '*')) {
        if (displayHidden(el)) continue
        // 只认叶子（容器元素的 textContent 也是「Yes」，但点容器不会冒泡到子元素）
        if (el.children.length > 0) continue
        const own = textOf(el).trim()
        if (own.length === 0 || own.length > 24) continue
        if (/cancel|取消|\bno\b/i.test(own)) continue
        if (!/^(yes|ok|确定|是|delete|disconnect|删除|断开)$/i.test(own)) continue
        add(el)
        if (out.length >= 8) return out
      }
    }
    return out
  }

  /** 诊断用：弹框里看得见的按钮标签（失败原因里带出来）。 */
  private actionLabels(dialog: Element): string[] {
    const hosts: Element[] = [dialog]
    const root = dialog.getRootNode() as ShadowRoot
    if ('host' in root && root.host) hosts.push(root.host as Element)
    const out: string[] = []
    for (const host of hosts) {
      for (const el of deepQueryAll(
        host,
        'button, [role="button"], md-text-button, mwc-button, [slot]',
      )) {
        if (displayHidden(el)) continue
        const label = (textOf(el).trim() || el.getAttribute('aria-label') || '∅').slice(0, 12)
        out.push(`${el.tagName.toLowerCase()}:${label}`)
        if (out.length >= 6) return out
      }
    }
    return out
  }

  /**
   * 终极兜底：按弹框右下角做命中测试（真实浏览器才有布局；无头环境 rect 为 0 ⇒ 返回 null）。
   * 这样即便按钮在 closed shadow root 里，也能拿到用户真正会点到的那一层元素。
   */
  private hitTestPrimary(dialog: Element): Element | null {
    const fromPoint = this.doc.elementFromPoint?.bind(this.doc)
    if (typeof fromPoint !== 'function') return null
    const surface = (deepQueryAll(dialog, '.mdc-dialog__surface')[0] ?? dialog) as HTMLElement
    const rect = surface.getBoundingClientRect?.()
    if (!rect || rect.width < 40 || rect.height < 40) return null
    const hit = fromPoint(rect.right - 24, rect.bottom - 20)
    if (!hit || hit === surface) return null
    return hit
  }

  /**
   * 危险动作的确认框：点确认键并**验证弹框真的关了**；没关就换下一个候选重试。
   * 最多 5s（弹框有动画，断开请求也要时间）；取消键永不点。
   */
  private async confirmDangerDialog(): Promise<boolean> {
    const started = Date.now()
    const deadline = started + 5000
    let tried = 0
    while (Date.now() <= deadline) {
      if (this.sessionState() === 'stopped') return true
      const dialog = this.openDialog()
      if (!dialog) {
        // 前 600ms 一直没弹框 ⇒ 这次动作不需要确认（别烧满 5s）
        if (tried === 0 && Date.now() - started >= 600) return false
        await this.sleep(150)
        continue
      }
      const candidates = this.primaryCandidates(dialog)
      const hit = this.hitTestPrimary(dialog)
      if (hit && !candidates.includes(hit)) candidates.push(hit)
      const target = candidates[tried]
      if (!target) {
        await this.sleep(150)
        continue
      }
      tried += 1
      clickAt(this.doc, innermostClickable(target) as HTMLElement)
      // 点完等：弹框关了，或者已经断开。都没发生 ⇒ 换下一个候选（可能是隐藏副本/错元素）
      const done = await this.waitUntil(
        () => this.openDialog() === null || this.sessionState() === 'stopped',
        2500,
      )
      if (done) return true
    }
    return false
  }

  /** 轮询到条件成立；`onMiss` 只在第 6 拍（约 600ms）调用一次，不用来刷动作。 */
  private async waitUntil(
    check: () => boolean,
    timeoutMs: number,
    onMiss?: () => void,
  ): Promise<boolean> {
    const deadline = Date.now() + timeoutMs
    let miss = 0
    while (Date.now() <= deadline) {
      if (check()) return true
      await this.sleep(100)
      miss += 1
      if (miss === 6) onMiss?.()
    }
    return check()
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }
}
