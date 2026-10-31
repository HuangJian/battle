/**
 * observer.ts — 输出 dirty 追踪（plan §3.1 / 评审 luna P1-5）。
 *
 * 只把「在 `.jp-OutputArea` 里的变化」标成 dirty：输入区每敲一个键都整 cell 标脏会让
 * 5s 一拍变成每拍读全文。cell 增删/虚拟化进出只刷新「已渲染集合」，不动 offset。
 */
export interface OutputObserver {
  disconnect(): void
}

/** 站点结构：输出区/单元格选择器 + cell 的稳定 id 属性（Kaggle 默认值住在下面）。 */
export interface ObserverSelectors {
  /** 这些子树里的变化不算输出变化（打字、工具栏、折叠开关）。 */
  skip: string[]
  output: string
  cell: string
  idAttr: string
}

export const KAGGLE_OBSERVER: ObserverSelectors = {
  skip: ['.jp-InputArea', '.cm-content', '.jp-InputCollapser', '.jp-OutputCollapser'],
  output: '.jp-OutputArea',
  cell: '.jp-Cell',
  idAttr: 'data-uuid',
}

export const COLAB_OBSERVER: ObserverSelectors = {
  skip: ['.inputarea', '.editor', '.cell-gutter', '.cell-toolbar', '.output-info'],
  output: '.output',
  cell: '.cell.notebook-cell',
  idAttr: 'id',
}

type MoCtor = new (cb: (records: MutationRecord[]) => void) => MutationObserver

export function createOutputObserver(
  doc: Document,
  onChange: (uuid: string) => void,
  onCellsChanged?: () => void,
  sel: ObserverSelectors = KAGGLE_OBSERVER,
): OutputObserver {
  // 观察者从文档自己的窗口取：happy-dom 的节点只认它自己的 MutationObserver，
  // 用宿主全局会 `ReferenceError`（Bun 没有浏览器那套全局）。真实 Chrome 两者相同。
  const view = doc.defaultView as unknown as { MutationObserver?: MoCtor } | null
  const Ctor = view?.MutationObserver
  if (!Ctor || !doc.documentElement) return { disconnect() {} }
  const mo = new Ctor((records) => {
    for (const r of records) {
      // 不用 `instanceof Element`：跨 realm 时恒为 false（happy-dom 用例即如此）。
      const target = r.target as unknown as Element
      if (typeof target.closest !== 'function') {
        onCellsChanged?.()
        continue
      }
      if (sel.skip.some((s) => target.closest(s))) continue // 打字/折叠不改输出文本
      const output = target.closest(sel.output)
      const cell = target.closest(sel.cell)
      if (output && cell) {
        const uuid = cell.getAttribute(sel.idAttr)
        if (uuid) onChange(uuid)
        continue
      }
      onCellsChanged?.()
    }
  })
  mo.observe(doc.documentElement, { childList: true, subtree: true, characterData: true })
  return { disconnect: () => mo.disconnect() }
}
