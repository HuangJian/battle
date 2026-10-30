/** selectors.ts — 站点选择器常量与角色判定（Kaggle 实测结构见 plan §2.2）。 */

export const SEL = {
  notebook: '.jp-Notebook',
  scrollOuter: '.jp-WindowedPanel-outer',
  cell: '.jp-Cell',
  codeCell: '.jp-CodeCell',
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

export type Role = 'host' | 'frame'

/**
 * Kaggle 编辑页 = 外层 `www.kaggle.com/code/*`；notebook = `*.jupyter-proxy.kaggle.net`。
 * 其余（数据集页、http、伪域名、非 URL）一律 null —— 角色判定只认这两个入口。
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
  return null
}
