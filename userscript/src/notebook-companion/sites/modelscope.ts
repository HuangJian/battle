/**
 * modelscope.ts — 魔搭（ModelScope）代码工作区 / Web IDE 的 notebook 适配器。
 *
 * 实测来源：`D:\github\modelscope.mhtml`（2026-11-09 抓取 `https://modelscope.cn/code/workspace`）。
 * 结构：外层 React 外壳 + **同源** iframe `https://modelscope.cn/notebook/main.html?t=…&lang=zh-CN`，
 * 里面是魔搭自己的 VS Code 工作台（hermes-ide），不是 JupyterLab：
 *   - cell = `.cell-list-container` 里 `.monaco-list-row.code-cell-row` / `.markdown-cell-row`，
 *     序号在 `data-index`；**虚拟化**（只有视口内的 cell 在 DOM），总数看 `#notebook.activeCellStatus`
 *     的「单元格 2/3」；
 *   - 执行态 = 行上的 `aria-label="code cell, executing"` + `.cell-statusbar-container` 里的
 *     `codicon-notebook-state-executing`（运行按钮同时变成 `codicon-notebook-stop`）；
 *   - 源码 = cell 里 Monaco 的 `.view-line`（`&nbsp;` 还原成空格；只渲染可视行）；
 *   - 工具栏实测按钮：`aria-label="中断"` / `"Restart Kernel"` / `"清除所有输出"`（在
 *     `.notebook-toolbar-container`）。
 *
 * **输出读不到**：cell 输出（含 stdout）只渲染在跨域 webview iframe 里
 * （`g.alicdn.com/modelscope-fe/…/webviews/index.html?purpose=notebookRenderer`
 * → `*.vscode-cdn.net`），
 * 主文档的 `.rendered-output` 是空的（存档里训练日志只出现在那个 iframe）。所以
 * `outputUnavailable` 非空 ⇒ 保存/搬运 fail closed，绝不「0 字节成功然后清空」。
 *
 * 没实测过的入口（只清空单个 cell、运行全部、折叠输入/输出、停止实例）一律带原因失败，不猜选择器。
 */
import { normalizeNewlines } from '../../utils'
import { pageMonaco, replaceMonacoSource } from './hub-url'
import type { CellRef, CmdResult, FrameAdapter, SessionAdapter, SiteId } from './types'

export const MS_SEL = {
  notebook: '.notebookOverlay',
  list: '.cell-list-container .monaco-list',
  scroller: '.cell-list-container .monaco-list .monaco-scrollable-element',
  cell:
    '.cell-list-container .monaco-list-row.code-cell-row, ' +
    '.cell-list-container .monaco-list-row.markdown-cell-row',
  viewLine: '.cell-editor-container .view-line',
  runButton: '.run-button-container',
  statusbar: '.cell-statusbar-container',
  output: '.output',
  rendered: '.rendered-output',
  toolbar: '.notebook-toolbar-container',
  tab: '.tabs-container .tab[data-resource-name]',
  activeStatus: '#notebook.activeCellStatus',
} as const

/** 执行态的三个证据（任一成立；都是存档里出现过的类名/文案）。 */
const EXEC_CLASS = 'codicon-notebook-state-executing'
const STOP_ICON = 'codicon-notebook-stop'

const FOLD_ATTR = 'data-bnc-fold'
const FOLD_CLASS = 'bnc-ms-fold'
const SCHED_ATTR = 'data-bnc-sched'
const SCHED_CLASS = 'bnc-sched'
const SCHED_BANNER_ID = 'bnc-sched-banner'
const SCHED_BANNER_ROW = 'bnc-sched-brow'
const HIT_CLASS = 'bnc-hit'
const STYLE_ID = 'bnc-ms-style'

/**
 * 折叠是**脚本自己的层**（不改 VS Code 状态）：行上 `data-bnc-fold="on"` ⇒ 裁到顶部四行；
 * 行是 `position: absolute` 的列表项 ⇒ 绝对定位的按键/徽标不会挤动 cell 布局。
 */
const STYLE = `
.monaco-list-row[${FOLD_ATTR}='on'] .cell-inner-container {
  max-height: calc(4lh + 40px) !important;
  overflow: hidden !important;
}
.${FOLD_CLASS} {
  position: absolute; top: 2px; right: 12px; z-index: 6;
  font: 10px/1.4 system-ui, -apple-system, sans-serif; letter-spacing: .04em;
  color: #9dbdff; background: rgba(10, 14, 20, .78);
  border: 1px solid currentColor; border-radius: 999px; padding: 0 5px;
  cursor: pointer; white-space: nowrap;
}
.monaco-list-row[${SCHED_ATTR}='1'] .cell-inner-container {
  outline: 2px solid #ff9f43 !important;
  outline-offset: -2px;
}
.${SCHED_CLASS} {
  position: absolute; top: 2px; left: 50%; transform: translateX(-50%); z-index: 7;
  padding: 3px 12px; border-radius: 999px;
  background: linear-gradient(90deg, #ffb020, #ff7a45);
  color: #1a1206; font: 700 11px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  white-space: nowrap;
}
.${HIT_CLASS} .cell-inner-container {
  outline: 2px solid #4c8dff !important;
  outline-offset: -2px;
}
#${SCHED_BANNER_ID} {
  position: fixed; top: 6px; left: 50%; transform: translateX(-50%);
  z-index: 2147483645; display: flex; flex-direction: column; gap: 3px;
  max-width: min(92vw, 560px); pointer-events: none;
}
#${SCHED_BANNER_ID} .${SCHED_BANNER_ROW} {
  padding: 4px 12px; border-radius: 999px; text-align: center;
  background: linear-gradient(90deg, #ffb020, #ff7a45);
  color: #1a1206; font: 700 12px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
`

export const MS_OUTPUT_UNAVAILABLE =
  '魔搭 Web IDE 的 cell 输出渲染在跨域 webview（vscode-cdn.net）里，页面脚本读不到文本：保存/清空一律不执行（日志请走 notebook 里的 hub 回传）'

export interface ModelscopeHooks {
  wait?: (ms: number) => Promise<void>
  nextFrame?: () => Promise<void>
  maxScanSteps?: number
  selectTimeoutMs?: number
  runStartTimeoutMs?: number
}

const defaultWait = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
const defaultNextFrame = (): Promise<void> =>
  new Promise((r) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => requestAnimationFrame(() => r()))
    } else setTimeout(r, 16)
  })

const CELL_PREFIX = 'cell-'

function lineText(el: Element): string {
  return (el.textContent ?? '').replace(/\u00a0/g, ' ')
}

function q<T extends Element>(root: ParentNode, sel: string): T | null {
  return root.querySelector<T>(sel)
}

function qa<T extends Element>(root: ParentNode, sel: string): T[] {
  return [...root.querySelectorAll<T>(sel)]
}

/** 真点一下（VS Code 的 cell 选区挂在 mousedown 上，只发 click 选不中）。 */
function pointerClick(el: Element): void {
  const view = el.ownerDocument.defaultView
  const rect = typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null
  const init: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    clientX: rect ? rect.left + rect.width / 2 : 0,
    clientY: rect ? rect.top + rect.height / 2 : 0,
  }
  const Ctor = (view?.MouseEvent ?? MouseEvent) as typeof MouseEvent
  for (const type of ['mousedown', 'mouseup', 'click'] as const) {
    el.dispatchEvent(new Ctor(type, init))
  }
}

/**
 * 活动标签的 notebook 名：优先 `data-resource-name`（存档实测 `rollout.ipynb`），
 * 再退到 `\mnt\workspace\rollout.ipynb` 的 aria-label，最后才看标题（`<name>.ipynb - … - ModelScope Code IDE`）。
 * 非 Document（测试里传 `{}`）或还没渲染 ⇒ 空串。
 */
export function modelscopeNotebookName(doc: unknown): string {
  const d = doc as Document | null | undefined
  if (!d || typeof d.querySelector !== 'function') return ''
  try {
    const tab = q<HTMLElement>(d, `${MS_SEL.tab}.active`) ?? q<HTMLElement>(d, MS_SEL.tab)
    const name = (tab?.getAttribute('data-resource-name') ?? '').trim()
    if (name) return name
    const aria =
      tab?.querySelector('.monaco-icon-label[aria-label]')?.getAttribute('aria-label') ?? ''
    const base = aria.split(/[\\/]/).filter(Boolean).pop() ?? ''
    if (base) return base
    const title = (d.title ?? '').split(' - ')[0]?.trim() ?? ''
    return /\.ipynb$/i.test(title) ? title : ''
  } catch {
    return ''
  }
}

/** 日志键（`[a-z0-9._-]{1,64}`）：魔搭的 notebook 身份不在 URL 里，只能从工作台标签读。 */
export function parseModelscopeSession(doc: unknown): string {
  const name = modelscopeNotebookName(doc)
  if (!name) return 'modelscope-unknown'
  const safe = name
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .slice(0, 56)
  return safe ? `modelscope-${safe}` : 'modelscope-unknown'
}

/** `/mnt/workspace/rollout.ipynb` 这种路径给定时任务看：`modelscope/rollout.ipynb`；没有 ⇒ ''。 */
export function parseModelscopeNotebookKey(doc: unknown): string {
  const name = modelscopeNotebookName(doc)
  return name ? `modelscope/${name}` : ''
}

export class ModelscopeAdapter implements FrameAdapter {
  readonly site: SiteId = 'modelscope'
  readonly outputUnavailable = MS_OUTPUT_UNAVAILABLE
  private readonly hooks: Required<ModelscopeHooks>
  private foldAll = false
  /** 单 cell 折叠覆盖（按 list 序号；行会被回收，状态必须住在这里而不是 DOM 属性）。 */
  private readonly foldOverrides = new Map<number, boolean>()
  private foldUiReady = false
  private foldObserver: {
    observe(node: Node, init: MutationObserverInit): void
    disconnect(): void
  } | null = null
  private foldSyncQueued = false
  private schedMarks: Array<{ id: string; label: string; uuid?: string; index?: number }> = []
  private schedBanners: Array<{ id: string; label: string }> = []

  constructor(
    protected readonly doc: Document,
    hooks: ModelscopeHooks = {},
  ) {
    this.hooks = {
      wait: hooks.wait ?? defaultWait,
      nextFrame: hooks.nextFrame ?? defaultNextFrame,
      maxScanSteps: hooks.maxScanSteps ?? 400,
      selectTimeoutMs: hooks.selectTimeoutMs ?? 700,
      runStartTimeoutMs: hooks.runStartTimeoutMs ?? 2500,
    }
  }

  /** `.notebookOverlay` 由 VS Code 工作台在打开 ipynb 后挂上（晚于 document-start）。 */
  protected notebook(): Element | null {
    return q(this.doc, MS_SEL.notebook)
  }

  protected root(): ParentNode {
    return this.notebook() ?? this.doc
  }

  get ready(): boolean {
    return this.notebook() !== null && q(this.root(), '.cell-list-container') !== null
  }

  /** 虚拟化：只有视口内的行在 DOM；序号取 `data-index`（与 `#notebook.activeCellStatus` 同一口径）。 */
  cells(): CellRef[] {
    this.ensureFoldUi()
    return qa<HTMLElement>(this.root(), MS_SEL.cell)
      .map((el, i) => {
        const raw = el.getAttribute('data-index')
        const index = raw === null ? i : Number(raw)
        const kind = el.classList.contains('code-cell-row')
          ? 'code'
          : el.classList.contains('markdown-cell-row')
            ? 'markdown'
            : 'raw'
        return {
          id: `${CELL_PREFIX}${index}`,
          index: Number.isFinite(index) ? index : i,
          kind,
        } satisfies CellRef
      })
      .sort((a, b) => a.index - b.index)
  }

  protected cellEl(cell: CellRef): HTMLElement | null {
    for (const el of qa<HTMLElement>(this.root(), MS_SEL.cell)) {
      if (Number(el.getAttribute('data-index') ?? -1) === cell.index) return el
    }
    return null
  }

  /**
   * 输出文本：**实测读不到**（输出在跨域 webview）。这里只读主文档里可能存在的
   * `.rendered-output`（某些轻量输出/未来版本会内联），读不到就是空串；
   * `outputUnavailable` 保证调用方不会把空串当「已保存」。
   */
  outputText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const out = q<HTMLElement>(el, MS_SEL.output)
    if (!out) return ''
    const rendered = qa<HTMLElement>(out, MS_SEL.rendered)
    if (rendered.length > 0) {
      return normalizeNewlines(rendered.map((n) => n.textContent ?? '').join('\n'))
    }
    return ''
  }

  /** 源码 = Monaco 逐行 `.view-line`（NBSP 还原成空格；只渲染可视行 ⇒ 超长 cell 只能读到窗口）。 */
  sourceText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const lines = qa<HTMLElement>(el, MS_SEL.viewLine)
    if (lines.length === 0) return ''
    return normalizeNewlines(lines.map(lineText).join('\n'))
  }

  /** 执行证据：行 aria-label / 状态栏/序号上的执行图标 / 运行按钮已变成「停止」。 */
  private rowExecuting(el: HTMLElement): boolean {
    const label = (el.getAttribute('aria-label') ?? '').toLowerCase()
    if (/\bexecuting\b|\brunning\b/.test(label)) return true
    if (el.querySelector(`.${EXEC_CLASS}`)) return true
    return el.querySelector(`${MS_SEL.runButton} .${STOP_ICON}`) !== null
  }

  cellExecuting(cell: CellRef): boolean {
    const el = this.cellEl(cell)
    return el !== null && this.rowExecuting(el)
  }

  /**
   * 内核状态：魔搭工作台的执行指示器就是工具栏/单元格上的旋转图标（没有独立 status item），
   * 所以「有 cell 在执行 ⇒ busy，notebook 在 ⇒ idle」，认不出 ⇒ unknown。
   */
  kernelStatus(): string {
    if (this.notebook() === null) return 'unknown'
    const busy =
      qa<HTMLElement>(this.root(), '.code-cell-row').some((el) => this.rowExecuting(el)) ||
      q(this.root(), `${MS_SEL.toolbar} .${EXEC_CLASS}`) !== null
    return busy ? 'busy' : 'idle'
  }

  /** 写回 cell：工作台不一定把 monaco 挂到 window；拿不到 model ⇒ 带原因失败（绝不假装写了）。 */
  async replaceCellSource(cell: CellRef, source: string): Promise<CmdResult> {
    const before = this.sourceText(cell)
    if (before === '') return { ok: false, reason: `cell 没有可读的编辑器文本：${cell.id}` }
    return replaceMonacoSource(pageMonaco(this.doc.defaultView), before, source)
  }

  private findConfigLine(
    markers: string[],
  ): { cell: CellRef; line: HTMLElement; marker: string; lineNo: number } | null {
    const scan = (match: (text: string) => boolean) => {
      for (const cell of this.cells()) {
        if (cell.kind !== 'code') continue
        const el = this.cellEl(cell)
        if (!el) continue
        const lines = qa<HTMLElement>(el, MS_SEL.viewLine)
        for (let i = 0; i < lines.length; i++) {
          if (match(lineText(lines[i]!))) return { cell, line: lines[i]!, lineNo: i + 1 }
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

  /** 面板「CFG」：滚到并聚焦配置行（不复制）；找不到必须带原因失败。 */
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
    const { cell, line, marker, lineNo } = hit
    const own = this.foldOverrides.get(cell.index) ?? this.foldAll
    if (own) {
      this.foldOverrides.set(cell.index, false)
      this.syncFoldButtons()
    }
    // 不派发指针事件：Monaco 会把合成 mousedown 当选区起点，鼠标一动就把 CFG 选上。
    ;(line.closest('.monaco-editor') as HTMLElement | null)?.focus?.()
    if (typeof line.scrollIntoView === 'function') line.scrollIntoView({ block: 'center' })
    const where = lineNo > 0 ? `第 ${cell.index} 段第 ${lineNo} 行` : `第 ${cell.index} 段`
    // `# @title` 这类弱标记必须自曝（plan §3.6 / S10）
    const label = marker.startsWith('#') ? `按 ${marker} 命中，可能不是 CFG cell` : marker
    return { ok: true, detail: `${where} · ${label}` }
  }

  /** 当前选中的 cell：行上的 `selected`（存档实测）或 `aria-selected="true"`。 */
  activeCell(): CellRef | null {
    const el = q<HTMLElement>(
      this.root(),
      '.code-cell-row.selected, .code-cell-row[aria-selected="true"]',
    )
    if (!el) return null
    const index = Number(el.getAttribute('data-index') ?? NaN)
    if (!Number.isFinite(index)) return null
    return this.cells().find((c) => c.index === index) ?? null
  }

  /** 虚拟化：目标不在 DOM 就按 0.8 视口步进滚动（无布局 ⇒ 直接 null，不假装能滚）。 */
  async ensureRendered(index: number): Promise<CellRef | null> {
    const found = this.cells().find((c) => c.index === index)
    if (found) return found
    const scroller = q<HTMLElement>(this.root(), MS_SEL.scroller)
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

  /** 选中：点 cell 的编辑区（真发 mousedown/mouseup/click），等行上出现 selected。 */
  async select(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    if (el.classList.contains('selected') || el.getAttribute('aria-selected') === 'true') {
      return { ok: true, detail: 'already-active' }
    }
    const target =
      q<HTMLElement>(el, '.cell-editor-container') ??
      q<HTMLElement>(el, '.cell-inner-container') ??
      el
    pointerClick(target)
    const step = 25
    for (let waited = 0; waited <= this.hooks.selectTimeoutMs; waited += step) {
      if (el.classList.contains('selected') || el.getAttribute('aria-selected') === 'true') {
        return { ok: true, detail: 'selected' }
      }
      await this.hooks.wait(step)
    }
    return { ok: false, reason: '点击后 cell 未获得 selected（选中失败，命令作用不到目标）' }
  }

  /** cell 左侧运行按钮里的**主操作**（排除下拉箭头）。执行中它变成「停止」，绝不能点。 */
  private runAction(el: HTMLElement): HTMLElement | null {
    const box = q<HTMLElement>(el, MS_SEL.runButton)
    if (!box) return null
    const actions = qa<HTMLElement>(box, 'a.action-label').filter(
      (a) =>
        !a.closest('.dropdown-action-container') &&
        !a.classList.contains('notebook-cell-run-toolbar'),
    )
    return actions[0] ?? null
  }

  async runCell(cell: CellRef): Promise<CmdResult> {
    const el = this.cellEl(cell)
    if (!el) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    // 执行中再点就是「停止」⇒ 直接认账，绝不误触
    if (this.rowExecuting(el)) return { ok: true, detail: 'already-running' }
    if (this.kernelStatus() === 'unknown') {
      return { ok: false, retry: true, reason: '工作台还没就绪（没有 notebookOverlay）' }
    }
    const btn = this.runAction(el)
    if (!btn)
      return { ok: false, reason: '找不到该 cell 的运行按钮（.run-button-container 主操作）' }
    btn.click()
    const step = 100
    const holdMs = 250
    for (let waited = 0; waited <= this.hooks.runStartTimeoutMs; waited += step) {
      if (this.rowExecuting(el)) {
        await this.hooks.wait(holdMs)
        if (this.rowExecuting(el)) return { ok: true, detail: 'running' }
        continue // 证据在沉淀窗里消失 ⇒ 当作没执行，继续等
      }
      await this.hooks.wait(step)
    }
    return {
      ok: false,
      retry: true,
      reason: '点击了运行，但 cell 没进入执行状态（命令可能空点）',
    }
  }

  /** 只清空这个 cell：实测只有工具栏的「清除所有输出」（清全部），单 cell 入口没样本 ⇒ fail closed。 */
  async clearCellOutput(cell: CellRef): Promise<CmdResult> {
    return {
      ok: false,
      reason: `魔搭没有实测过「只清空这个 cell」的入口（${cell.id}）；不猜菜单`,
    }
  }

  /** 折叠输入/输出：VS Code 的折叠开关没实测过 ⇒ fail closed（面板折叠走脚本自己的层）。 */
  async toggleCell(cell: CellRef, part: 'input' | 'output'): Promise<CmdResult> {
    return {
      ok: false,
      reason: `魔搭没有实测过「折叠这个 cell 的 ${part === 'input' ? '输入' : '输出'}」入口（${cell.id}）`,
    }
  }

  async setFoldedAll(collapsed: boolean): Promise<CmdResult> {
    if (!this.notebook()) return { ok: false, reason: '找不到 .notebookOverlay（笔记本未就绪）' }
    this.foldAll = collapsed
    this.foldOverrides.clear()
    this.ensureFoldUi()
    this.syncFoldButtons()
    return { ok: true, detail: collapsed ? 'folded:all' : 'unfolded:all' }
  }

  async setFolded(cell: CellRef, collapsed: boolean): Promise<CmdResult> {
    if (!this.cellEl(cell)) return { ok: false, reason: `cell 未渲染：${cell.id}` }
    this.foldOverrides.set(cell.index, collapsed)
    this.ensureFoldUi()
    this.syncFoldButtons()
    return { ok: true, detail: collapsed ? 'folded' : 'unfolded' }
  }

  /** 工具栏实测按钮：按 aria-label 找（存档里就是这几个中文/英文文案）。 */
  private toolbarAction(labels: string[]): HTMLElement | null {
    const bar = q<HTMLElement>(this.root(), MS_SEL.toolbar)
    if (!bar) return null
    for (const a of qa<HTMLElement>(bar, 'a.action-label[aria-label]')) {
      if (labels.includes(a.getAttribute('aria-label') ?? '')) return a
    }
    return null
  }

  private outputContainers(): number {
    return qa<HTMLElement>(this.root(), `${MS_SEL.output} .output-inner-container`).length
  }

  async command(id: string, args: { cell?: CellRef } = {}): Promise<CmdResult> {
    if (args.cell) {
      const sel = await this.select(args.cell)
      if (!sel.ok) return sel
    }
    switch (id) {
      case 'notebook:interrupt-kernel': {
        const btn = this.toolbarAction(['中断'])
        if (!btn) return { ok: false, reason: '工具栏里找不到「中断」按钮' }
        btn.click()
        return { ok: true, detail: 'clicked:中断' }
      }
      case 'notebook:restart-kernel': {
        const btn = this.toolbarAction(['Restart Kernel'])
        if (!btn) return { ok: false, reason: '工具栏里找不到「Restart Kernel」按钮' }
        btn.click()
        return { ok: true, detail: 'clicked:Restart Kernel' }
      }
      case 'notebook:clear-all-cell-outputs': {
        const btn = this.toolbarAction(['清除所有输出'])
        if (!btn) return { ok: false, reason: '工具栏里找不到「清除所有输出」按钮' }
        const before = this.outputContainers()
        btn.click()
        for (let waited = 0; waited < 2000; waited += 100) {
          if (this.outputContainers() === 0) return { ok: true, detail: 'cleared:all' }
          await this.hooks.wait(100)
        }
        return {
          ok: false,
          reason: `点了「清除所有输出」但输出容器仍在（${before} → ${this.outputContainers()}，可能有确认框）`,
        }
      }
      case 'notebook:run-cell': {
        const cell = this.activeCell()
        if (!cell) return { ok: false, reason: '没有选中的 cell' }
        return this.runCell(cell)
      }
      case 'notebook:run-all-cells':
        return {
          ok: false,
          reason: '魔搭 Web IDE 的「运行全部单元格」入口没有实测样本（菜单未抓取）',
        }
      default:
        return { ok: false, reason: `modelscope 不支持命令 ${id}（未实测，不猜）` }
    }
  }

  // ── 滚动 / 折叠 / 高亮 / 徽标 ──

  private fireScroll(el: HTMLElement): void {
    const Ctor = (this.doc.defaultView?.Event ?? Event) as typeof Event
    el.dispatchEvent(new Ctor('scroll'))
  }

  /** 候选滚动容器：VS Code 的列表真实滚动量在 `.monaco-scrollable-element` 上。 */
  private scrollTargets(): HTMLElement[] {
    const out: HTMLElement[] = []
    const push = (el: Element | null | undefined): void => {
      if (el && !out.includes(el as HTMLElement)) out.push(el as HTMLElement)
    }
    push(q(this.root(), MS_SEL.scroller))
    push(q(this.root(), '.cell-list-container'))
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
      el.scrollTop = want
      this.fireScroll(el)
      const label = el.id || el.classList[0] || el.tagName
      if (Math.abs(el.scrollTop - want) <= 1) {
        return { ok: true, detail: `${to} → ${label}@${el.scrollTop}` }
      }
      tried.push(`${label}:${el.scrollTop}/${want}`)
    }
    return { ok: false, reason: `没有可滚动容器（${tried.join(' ') || '无候选'}）` }
  }

  scrollOuter(to: 'top' | 'bottom'): CmdResult {
    return this.scroll(to)
  }

  flash(cell: CellRef): void {
    const el = this.cellEl(cell)
    if (!el) return
    this.ensureStyle()
    el.classList.add(HIT_CLASS)
    setTimeout(() => el.classList.remove(HIT_CLASS), 1500)
  }

  private ensureStyle(): boolean {
    if (!this.doc.getElementById(STYLE_ID)) {
      const parent = this.doc.head ?? this.doc.documentElement
      if (!parent) return false
      const style = this.doc.createElement('style')
      style.id = STYLE_ID
      style.textContent = STYLE
      parent.append(style)
    }
    return true
  }

  private ensureFoldUi(): void {
    if (this.foldUiReady) return
    if (!this.ensureStyle()) return
    this.foldUiReady = true
    this.watchFold()
    this.syncFoldButtons()
  }

  /** 观察 cell 进出 DOM（虚拟化/重渲染会换行节点）⇒ 补回按键与折叠态；输出区变化跳过（读不到）。 */
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
        if (typeof target.closest === 'function' && target.closest(MS_SEL.output)) continue
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
      this.syncBadges()
      this.syncBanner()
    }
    const view = this.doc.defaultView as unknown as {
      queueMicrotask?: (fn: () => void) => void
    } | null
    if (view?.queueMicrotask) view.queueMicrotask(run)
    else setTimeout(run, 0)
  }

  private isFolded(index: number): boolean {
    return this.foldOverrides.get(index) ?? this.foldAll
  }

  /** 每个 code/markdown 行顶边常驻一个折叠按键（折叠态点=只展开这段，展开态点=折叠这段）。 */
  private syncFoldButtons(): void {
    if (!this.notebook()) return
    for (const el of qa<HTMLElement>(this.root(), MS_SEL.cell)) {
      const index = Number(el.getAttribute('data-index') ?? NaN)
      if (!Number.isFinite(index)) continue
      const folded = this.isFolded(index)
      if (folded) el.setAttribute(FOLD_ATTR, 'on')
      else el.removeAttribute(FOLD_ATTR)
      const btn = q<HTMLElement>(el, `.${FOLD_CLASS}`) ?? this.addFoldButton(el, index)
      const label = folded ? '展开' : '折叠'
      if (btn.textContent !== label) btn.textContent = label
      const title = folded ? '展开这个 cell（其余保持原样）' : '折叠这个 cell（只留顶部四行）'
      if (btn.title !== title) btn.title = title
    }
  }

  private addFoldButton(el: HTMLElement, index: number): HTMLElement {
    const btn = this.doc.createElement('button')
    btn.type = 'button'
    btn.className = FOLD_CLASS
    btn.addEventListener('click', (ev) => {
      ev.preventDefault()
      ev.stopPropagation()
      this.foldOverrides.set(index, !this.isFolded(index))
      this.syncFoldButtons()
    })
    el.append(btn)
    return btn
  }

  private markForCell(index: number, id: string): { id: string; label: string } | null {
    for (const m of this.schedMarks) {
      if (m.uuid && m.uuid === id) return m
      if (m.index !== undefined && Number.isInteger(m.index) && m.index === index) return m
    }
    return null
  }

  /** 目标 cell 徽标：每拍贴回（列表回收行会把徽标冲掉，宿主 1s 调一次即可自愈）。 */
  private syncBadges(): void {
    if (!this.notebook()) return
    for (const el of qa<HTMLElement>(this.root(), MS_SEL.cell)) {
      const index = Number(el.getAttribute('data-index') ?? NaN)
      if (!Number.isFinite(index)) continue
      const id = `${CELL_PREFIX}${index}`
      const mark = this.markForCell(index, id)
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
      el.append(b)
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
   * 幂等：每次调用都按当前 marks 重贴。
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
    if (!this.ensureStyle()) return { ok: false, reason: '无法注入徽标样式（没有 head）' }
    this.syncBadges()
    this.syncBanner()
    return { ok: true, detail: `marks:${cells.length}+${banners.length}` }
  }
}

// ────────────────────────── 会话（single 角色：面板与 cell 同页） ──────────────────────────

export class ModelscopeSessionAdapter implements SessionAdapter {
  readonly site = 'modelscope' as const

  constructor(private readonly doc: Document) {}

  sessionId(): string {
    return parseModelscopeSession(this.doc)
  }

  /**
   * 只读执行态：有 code cell 在跑 ⇒ running；notebook 在 ⇒ idle；认不出 ⇒ unknown。
   * （魔搭没有可读的「实例运行中」指示器——那个在外层 React 外壳里。）
   */
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown' {
    const overlay = this.doc.querySelector(MS_SEL.notebook)
    if (!overlay) return 'unknown'
    const busy = [...this.doc.querySelectorAll<HTMLElement>('.code-cell-row')].some(
      (el) =>
        /\bexecuting\b|\brunning\b/i.test(el.getAttribute('aria-label') ?? '') ||
        el.querySelector(`.${EXEC_CLASS}`) !== null ||
        el.querySelector(`${MS_SEL.runButton} .${STOP_ICON}`) !== null,
    )
    const toolbarBusy = this.doc.querySelector(`${MS_SEL.toolbar} .${EXEC_CLASS}`) !== null
    return busy || toolbarBusy ? 'running' : 'idle'
  }

  /**
   * 停会话 = 停实例，入口在外层 `/code/workspace` 的「实例运行中」下拉里（不在 notebook 帧内）。
   * fail closed：不点「看起来像」的东西。
   */
  async stopSession(): Promise<CmdResult> {
    return {
      ok: false,
      reason:
        '魔搭的实例停止在外层工作台（notebook-ide 帧里没有入口）；请在「实例运行中」下拉里停止，或只用「中断」',
    }
  }
}
