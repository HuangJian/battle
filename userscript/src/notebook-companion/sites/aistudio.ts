/**
 * aistudio.ts — 百度 AI Studio（BML Codelab）适配器。
 *
 * 实测结构（2026-10-31 从 `tmp/aistudio.mhtml` 抽的存档）：notebook 是 **JupyterLab 4 的分支**，
 * cell / 输入区 / 输出区 / 折叠器 / 工具栏命令 id 与 Kaggle 同构 ⇒ 直接继承
 * `KaggleFrameAdapter`，只覆盖真正不同的四件事：
 *
 *   ① cell 没有 `data-uuid` / `data-windowed-list-index`（不虚拟化）⇒ id 用 DOM 序号；
 *   ② 编辑器是 **Monaco**（`.view-line`），不是 CodeMirror 6（`.cm-line`）；
 *   ③ 录不进 DOM 的命令：AI Studio 的「停止」在外层工作台（不在 notebook 帧内）⇒ fail closed；
 *   ④ 会话/notebook 键从 `/user/<uid>/<pid>/lab/tree/<nb>` 解析。
 *
 * 没实测过的动作一律 fail closed（不猜选择器、不假装成功）。
 */
import { normalizeNewlines } from '../../utils'
import { KaggleFrameAdapter } from './kaggle'
import type { CellRef, CmdResult, SessionAdapter, SiteId } from './types'

/** cell id = DOM 序号（页面不虚拟化，序号在一次会话里稳定）。 */
const CELL_PREFIX = 'cell-'

const lineText = (el: Element): string => (el.textContent ?? '').replace(/\u00a0/g, ' ')

/**
 * `/bj-cpu-01/user/<uid>/<pid>/lab/tree/<name>.ipynb` ⇒ `aistudio/<pid>/<name>`。
 * 不是 notebook 页 ⇒ ''（定时任务据此拒绝归属）。
 */
export function parseAistudioNotebookKey(href: string): string {
  try {
    const parts = new URL(href).pathname.split('/').filter(Boolean)
    const ui = parts.indexOf('user')
    if (ui < 0 || parts.length < ui + 4) return ''
    const pid = parts[ui + 2] ?? ''
    if (!/^\d+$/.test(pid)) return ''
    // 只认 notebook 页：/user/<uid>/<pid>/lab/…（外层 /vscode/ 工作台不是 notebook）
    if (parts[ui + 3] !== 'lab') return ''
    // /user/<uid>/<pid>/lab/tree/<nb>：ui+3 = lab，ui+4 = tree，ui+5 起是 notebook 路径
    const name = decodeURIComponent(parts.slice(ui + 5).join('/'))
    return `aistudio/${pid}${name ? `/${name}` : ''}`
  } catch {
    return ''
  }
}

/** 日志键（`[a-z0-9._-]{1,64}`）：拿不到工程 id ⇒ `aistudio-unknown`。 */
export function parseAistudioSession(href: string): string {
  const key = parseAistudioNotebookKey(href)
  if (!key) return 'aistudio-unknown'
  const safe = key
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .slice(0, 64)
  return safe || 'aistudio-unknown'
}

export class AistudioAdapter extends KaggleFrameAdapter {
  override readonly site: SiteId = 'aistudio'

  /** 不虚拟化：`.jp-Cell` 全在 DOM，id 取序号（页码稳定，命令/定时都按它定位）。 */
  override cells(): CellRef[] {
    // 基类的 cells() 负责装折叠按键/样式；这里覆盖了取数逻辑，得自己补上
    this.ensureFoldUi()
    const root = this.root()
    return [...root.querySelectorAll<HTMLElement>('.jp-Cell')].map((el, i) => {
      const kind = el.classList.contains('jp-MarkdownCell')
        ? 'markdown'
        : el.classList.contains('jp-CodeCell')
          ? 'code'
          : 'raw'
      return { id: `${CELL_PREFIX}${i}`, index: i, kind } satisfies CellRef
    })
  }

  protected override cellEl(cell: CellRef): HTMLElement | null {
    const els = [...this.root().querySelectorAll<HTMLElement>('.jp-Cell')]
    const m = /^cell-(\d+)$/.exec(cell.id)
    const index = m ? Number(m[1]) : cell.index
    return els[index] ?? null
  }

  override activeCell(): CellRef | null {
    const els = [...this.root().querySelectorAll<HTMLElement>('.jp-Cell')]
    const at = els.findIndex(
      (el) => el.classList.contains('jp-mod-active') || el.classList.contains('jp-mod-selected'),
    )
    if (at < 0) return null
    return this.cells()[at] ?? null
  }

  /** Monaco：一行一个 `.view-line`（NBSP 是可断行空格，还原成普通空格才对得上源码）。 */
  override sourceText(cell: CellRef): string {
    const el = this.cellEl(cell)
    if (!el) return ''
    const box = el.querySelector<HTMLElement>('.jp-InputArea-editor')
    if (!box) return ''
    const lines = [...box.querySelectorAll<HTMLElement>('.view-line')]
    if (lines.length === 0) return normalizeNewlines(lineText(box))
    return lines.map(lineText).join('\n')
  }

  /**
   * 面板「CFG」：在 Monaco 的 `.view-line` 里找配置行，点一下把光标落上去再滚进视口。
   * 启发式优先（`CFG...{`），再按配置标记顺序；找不到必须带原因。
   */
  override async focusConfig(markers: string[]): Promise<CmdResult> {
    const wanted = markers.filter((m) => typeof m === 'string' && m.trim().length > 0)
    if (wanted.length === 0) return { ok: false, reason: 'configMarkers 为空（没有可定位的标记）' }

    const scan = (
      match: (text: string) => boolean,
    ): { cell: CellRef; line: HTMLElement; lineNo: number } | null => {
      for (const cell of this.cells()) {
        if (cell.kind !== 'code') continue
        const el = this.cellEl(cell)
        const lines = el
          ? [...el.querySelectorAll<HTMLElement>('.jp-InputArea-editor .view-line')]
          : []
        for (let i = 0; i < lines.length; i++) {
          if (match(lineText(lines[i]!))) return { cell, line: lines[i]!, lineNo: i + 1 }
        }
      }
      return null
    }

    let marker = 'CFG 行'
    let hit = scan((text) => /^\s*CFG\b.*\{/.test(text))
    if (!hit) {
      for (const m of wanted) {
        hit = scan((text) => text.includes(m))
        if (hit) {
          marker = m
          break
        }
      }
    }
    if (!hit) return { ok: false, reason: `找不到 CFG 行（标记：${wanted.join(' / ')}）` }

    const { cell, line, lineNo } = hit
    const el = this.cellEl(cell)
    if (el) {
      const all = this.notebook()?.getAttribute('data-bnc-fold') === 'all'
      const own = el.getAttribute('data-bnc-fold')
      if (own === 'on' || (all && own !== 'off')) el.setAttribute('data-bnc-fold', 'off')
    }
    // 只滚动 + 聚焦：**不派发指针事件** —— Monaco 会把合成 mousedown 当成选区起点，
    // 之后鼠标一动就把 CFG 内容选上（选中是多余的副作用，用户明确要求去掉）。
    const editor = line.closest('.jp-InputArea-editor') as HTMLElement | null
    editor?.focus?.()
    if (typeof line.scrollIntoView === 'function') line.scrollIntoView({ block: 'center' })
    const label = marker.startsWith('#') ? `按 ${marker} 命中，可能不是 CFG cell` : marker
    return { ok: true, detail: `第 ${cell.index} 段第 ${lineNo} 行 · ${label}` }
  }
}

export class AistudioSessionAdapter implements SessionAdapter {
  readonly site = 'aistudio' as const

  constructor(
    private readonly doc: Document,
    private readonly href: string,
    /** 关内核后等 Running Sessions 清空的上限（测试注入小值）。 */
    private readonly stopTimeoutMs = 8000,
  ) {}

  sessionId(): string {
    return parseAistudioSession(this.href)
  }

  /**
   * 工具栏的执行指示器：`data-status="busy" | "idle"`；认不出的状态一律 unknown（不猜）。
   * 「内核段在、但一个内核都没有」⇒ stopped（AI Studio 停会话 = 关内核，见 stopSession）。
   */
  sessionState(): 'running' | 'idle' | 'stopped' | 'unknown' {
    const ind = this.doc.querySelector('.jp-Notebook-ExecutionIndicator')
    const status = (ind?.getAttribute('data-status') ?? '').toLowerCase()
    if (status === 'busy' || status === 'running') return 'running'
    const section = this.kernelSection()
    if (section && this.kernelItems(section).length === 0) return 'stopped'
    if (status === 'idle') return 'idle'
    return 'unknown'
  }

  /** Running Sessions 侧栏的「内核」段（存档实测：`h2` 文案是「内核」）。 */
  private kernelSection(): Element | null {
    for (const section of this.doc.querySelectorAll('.jp-RunningSessions-section')) {
      if (/内核|kernels/i.test(section.querySelector('h2')?.textContent ?? '')) return section
    }
    return null
  }

  private kernelItems(section: Element): HTMLElement[] {
    return [...section.querySelectorAll<HTMLElement>('li.jp-RunningSessions-item')]
  }

  /**
   * 停会话 = **关内核**：AI Studio 的机器随内核释放（省配额）。
   *
   * 入口在 Running Sessions 侧栏的「内核」段（存档实测：每行 `button.jp-RunningSessions-itemShutdown`，
   * title「关闭」）。点完之后**必须看到内核条目全部消失**才算成功；还在就带原因失败（可能有确认框挡住）。
   * 侧栏没渲染出来 ⇒ fail closed（不点「看起来像」的东西）。
   */
  async stopSession(): Promise<CmdResult> {
    const section = this.kernelSection()
    if (!section) {
      return {
        ok: false,
        reason: '找不到 Running Sessions 的「内核」段（先打开该侧栏，或 AI Studio 结构变了）',
      }
    }
    const items = this.kernelItems(section)
    if (items.length === 0) return { ok: true, detail: 'already-stopped' }
    for (const item of items) {
      const btn = item.querySelector<HTMLElement>('button.jp-RunningSessions-itemShutdown')
      if (!btn) return { ok: false, reason: '内核条目里找不到「关闭」按钮（AI Studio 结构变了）' }
      btn.click()
    }
    const deadline = Date.now() + this.stopTimeoutMs
    while (Date.now() <= deadline) {
      if (this.kernelItems(section).length === 0) {
        return {
          ok: true,
          detail: `已关闭全部 ${items.length} 个内核（Running Sessions 已无内核）`,
        }
      }
      await this.sleep(100)
    }
    return {
      ok: false,
      reason: '点了内核「关闭」但内核仍在（可能有确认框挡住，或按钮没点中）',
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }
}
