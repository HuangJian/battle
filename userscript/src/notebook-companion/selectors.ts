/** selectors.ts — 站点选择器常量与角色判定（Kaggle 实测结构见 plan §2.2）。 */

export const SEL = {
  notebook: '.jp-Notebook',
  scrollOuter: '.jp-WindowedPanel-outer',
  cell: '.jp-Cell',
  codeCell: '.jp-CodeCell',
  markdownCell: '.jp-MarkdownCell',
  inputArea: '.jp-InputArea',
  /** 运行/排队时提示符是 `[*]`（或 `[12*]`）；比只看输出更早出现。 */
  inputPrompt: '.jp-InputPrompt',
  /** JupyterLab 的内核状态指示器（`data-status=idle|busy|connecting`）。 */
  kernelStatus: '.jp-Notebook-ExecutionIndicator, .jp-StatusBar-ExecutionIndicator',
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
/** 同文档站点（cell 与面板同页，不建 iframe 桥）。 */
export type SingleSite = 'colab' | 'aistudio' | 'modelscope'

/**
 * 同文档站点判定：Colab、AI Studio（BML Codelab 的 notebook 帧）、魔搭（ModelScope）Web IDE 的
 * notebook 帧。
 * AI Studio 只认 `/user/<uid>/<pid>/lab/…` 这层——外层 `/vscode/` 工作台不接（面板无处安放）。
 * 魔搭只认 `/notebook/main.html`：那是 hermes-ide（VS Code 工作台）帧，cell 与输出都在里面；
 * 外层 `/code/workspace` 只是 React 外壳（没有 notebook DOM），不接。
 */
export function singleSite(rawUrl: string): SingleSite | null {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return null
  }
  if (url.protocol !== 'https:') return null
  const host = url.hostname.toLowerCase()
  if (host === 'colab.research.google.com') return 'colab'
  if (host === 'aistudio.baidu.com' && /\/user\/[^/]+\/\d+\/lab(\/|$)/.test(url.pathname)) {
    return 'aistudio'
  }
  if (
    (host === 'modelscope.cn' || host === 'www.modelscope.cn') &&
    url.pathname === '/notebook/main.html'
  ) {
    return 'modelscope'
  }
  return null
}

/**
 * Kaggle 编辑页 = 外层 `www.kaggle.com/code/*`；notebook = `*.jupyter-proxy.kaggle.net`；
 * Colab / AI Studio / 魔搭 = 单文档（cell 与面板同页 ⇒ `single`）。
 * 其余（数据集页、http、伪域名、非 URL）一律 null —— 角色判定只认这些入口。
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
  if (singleSite(rawUrl) !== null) return 'single'
  return null
}
