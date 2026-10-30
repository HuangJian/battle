/**
 * observer.ts — 输出 dirty 追踪（plan §3.1 / 评审 luna P1-5）。
 *
 * 只把「在 `.jp-OutputArea` 里的变化」标成 dirty：输入区每敲一个键都整 cell 标脏会让
 * 5s 一拍变成每拍读全文。cell 增删/虚拟化进出只刷新「已渲染集合」，不动 offset。
 */
export interface OutputObserver {
  disconnect(): void
}

type MoCtor = new (cb: (records: MutationRecord[]) => void) => MutationObserver

export function createOutputObserver(
  doc: Document,
  onChange: (uuid: string) => void,
  onCellsChanged?: () => void,
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
      if (
        target.closest('.jp-InputArea') ||
        target.closest('.cm-content') ||
        target.closest('.jp-InputCollapser') ||
        target.closest('.jp-OutputCollapser')
      ) {
        continue // 打字/折叠不改输出文本
      }
      const output = target.closest('.jp-OutputArea')
      const cell = target.closest('.jp-Cell')
      if (output && cell) {
        const uuid = cell.getAttribute('data-uuid')
        if (uuid) onChange(uuid)
        continue
      }
      onCellsChanged?.()
    }
  })
  mo.observe(doc.documentElement, { childList: true, subtree: true, characterData: true })
  return { disconnect: () => mo.disconnect() }
}
