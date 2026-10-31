/** selectors.ts — 站点选择器常量与角色判定（Kaggle 实测结构见 plan §2.2）。 */

export const SEL = {
  notebook: '.jp-Notebook',
  scrollOuter: '.jp-WindowedPanel-outer',
  cell: '.jp-Cell',
  codeCell: '.jp-CodeCell',
  markdownCell: '.jp-MarkdownCell',
  inputArea: '.jp-InputArea',
  cmContent: '.cm-content',
  outputArea: '.jp-OutputArea',
  outputChild: '.jp-OutputArea-child',
  inputCollapser: '.jp-InputCollapser',
  outputCollapser: '.jp-OutputCollapser',
  command: (id: string) => `[data-command="${id}"]`,
} as const

export const MIME = {
  stdout: 'application/vnd.jupyter.stdout',
  stderr: 'application/vnd.jupyter.stderr',
} as const

/** 先直查，再穿 open shadow root（JupyterLab 4 的部分面板在影子树里）。 */
export function deepQuery(root: ParentNode, selector: string): Element | null {
  const direct = root.querySelector(selector)
  if (direct) return direct
  const stack: ParentNode[] = [root]
  while (stack.length > 0) {
    const scope = stack.pop()!
    for (const el of scope.querySelectorAll('*')) {
      if (!el.shadowRoot) continue
      const hit = el.shadowRoot.querySelector(selector)
      if (hit) return hit
      stack.push(el.shadowRoot)
    }
  }
  return null
}

export function deepQueryAll(root: ParentNode, selector: string): Element[] {
  const out = [...root.querySelectorAll(selector)]
  const stack: ParentNode[] = [root]
  while (stack.length > 0) {
    const scope = stack.pop()!
    for (const el of scope.querySelectorAll('*')) {
      if (!el.shadowRoot) continue
      out.push(...el.shadowRoot.querySelectorAll(selector))
      stack.push(el.shadowRoot)
    }
  }
  return out
}

export type Role = 'host' | 'frame' | 'single'

/**
 * Kaggle 编辑页 = 外层 `www.kaggle.com/code/*`；notebook = `*.jupyter-proxy.kaggle.net`；
 * Colab = 单文档（cell 与面板同页、无跨域 iframe ⇒ `single`）。
 * 其余（数据集页、http、伪域名、非 URL）一律 null —— 角色判定只认这三个入口。
 */
export function detectRole(rawUrl: string): Role | null {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return null
  }
  if (url.protocol !== 'https:') return null
  const host = url.hostname.toLowerCase()
  if ((host === 'www.kaggle.com' || host === 'kaggle.com') && url.pathname.startsWith('/code/')) {
    return 'host'
  }
  if (host === 'jupyter-proxy.kaggle.net' || host.endsWith('.jupyter-proxy.kaggle.net')) {
    return 'frame'
  }
  if (host === 'colab.research.google.com') return 'single'
  return null
}
