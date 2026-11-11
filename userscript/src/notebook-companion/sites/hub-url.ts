/**
 * hub-url.ts — 把 dashboard 当前的 cloudflared 隧道 URL 写进 notebook 的 `CFG['hub_url']`。
 *
 * 只做三件可测的事：
 *   ① 在源码里找到 `"hub_url": "…"` 这个**字面量**并只替换值（引号风格、缩进、注释、
 *      其它键逐字不动）；
 *   ② 判断 URL 是否可信（http/https、无空白/尖括号/引号）；
 *   ③ 通过页面暴露的 Monaco model 做整段写回（Colab / AI Studio）。
 * 另有纪律：现值的**主机是 IP 字面量**（手填的直连地址，如 `http://100.64.0.1:8900`）⇒
 * 视为人为钉住，不换成隧道 URL（`pinned`）；否则 colab/kaggle 里手写的直连会被悄悄改掉。
 *
 * 为什么不整段重写 cell：CFG 里还有 token 占位、注释与别的键——整段替换会把仓库/用户
 * 的其它改动一起抹掉。改写范围 = 旧值与新值的**最小差异区间**（CM6 路径按它选 DOM Range）。
 */
import type { CmdResult } from './types'

/** `"hub_url": "…"` / `'hub_url': '…'`；值只认单/双引号字面量（不猜表达式）。 */
const HUB_KEY = /(["'])hub_url\1\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/

export type HubUrlPlan =
  | { ok: true; text: string; changed: boolean; replaced: string; pinned?: boolean }
  | { ok: false; reason: string }

/** 可信的 hub 地址：cloudflared 隧道是 https，tailnet 兜底允许 http；模板占位不算。 */
export function isHubUrl(value: string): boolean {
  const v = value.trim()
  if (!v || /[\s<>"'`\\]/.test(v)) return false
  try {
    const u = new URL(v)
    return (u.protocol === 'https:' || u.protocol === 'http:') && u.hostname.length > 0
  } catch {
    return false
  }
}

/**
 * 主机是不是 IP 字面量：手填的直连地址（tailnet `100.x.y.z`、本机 `127.0.0.1`、IPv6）。
 * `URL.hostname` 会把 IPv6 括成 `[::1]`，两种形态都认。
 */
export function isIpHostUrl(value: string): boolean {
  const v = value.trim()
  if (!v) return false
  try {
    const host = new URL(v).hostname
    return /^\[[0-9a-f:.]+\]$/i.test(host) || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)
  } catch {
    return false
  }
}

/** 去掉尾部斜杠：notebook 自己拼 `/ping`，留下 `//ping` 会 404。 */
export function normalizeHubUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

/**
 * 只替换 `hub_url` 的值。找不到键 ⇒ 失败（绝不新加键——那会改到用户没写的配置）；
 * 值已经相同 ⇒ `changed:false`（调用方据此一次写都不发）。
 */
export function planHubUrlEdit(source: string, url: string): HubUrlPlan {
  const next = normalizeHubUrl(url)
  if (!isHubUrl(next)) return { ok: false, reason: `不是有效的 hub URL：${url || '(空)'}` }
  const m = HUB_KEY.exec(source)
  if (!m) return { ok: false, reason: '这段代码里没有字面量 `hub_url` 键（不改非 CFG cell）' }
  const literal = m[2]!
  const current = literal.slice(1, -1)
  if (current === next) return { ok: true, text: source, changed: false, replaced: current }
  // 现值是 IP 直连 ⇒ 人写的，钉住不动（隧道 URL 换几次都不覆盖）。
  if (isIpHostUrl(current)) {
    return { ok: true, text: source, changed: false, replaced: current, pinned: true }
  }
  // `at` 指向字面量的**第一个内容字符**（跳过开引号）；收尾也跳过闭引号。
  const at = m.index + m[0].length - literal.length + 1
  return {
    ok: true,
    text: `${source.slice(0, at)}${next}${source.slice(at + literal.length - 2)}`,
    changed: true,
    replaced: current,
  }
}

export interface SourceEdit {
  start: number
  end: number
  text: string
}

/**
 * 两段文本的最小差异区间（公共前缀 + 公共后缀）。完全相同 ⇒ null。
 * CM6 路径用它把 DOM Range 收窄到「只有 URL 那几个字符」，而不是整段重写。
 */
export function minimalEdit(before: string, after: string): SourceEdit | null {
  if (before === after) return null
  const max = Math.min(before.length, after.length)
  let start = 0
  while (start < max && before[start] === after[start]) start++
  let endBefore = before.length
  let endAfter = after.length
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) {
    endBefore--
    endAfter--
  }
  return { start, end: endBefore, text: after.slice(start, endAfter) }
}

export interface MonacoModel {
  getValue(): string
  getFullModelRange?: () => unknown
  getLineCount?: () => number
  getLineMaxColumn?: (line: number) => number
  pushEditOperations?: (
    selections: unknown[],
    edits: Array<{ range: unknown; text: string }>,
    cursor: () => unknown,
  ) => unknown
  setValue?: (value: string) => void
}

export interface MonacoGlobal {
  editor?: { getModels?: () => MonacoModel[] }
}

/**
 * 页面里的 Monaco（Colab / AI Studio 都有）。sandbox 里 `document.defaultView.monaco`
 * 可能拿不到 ⇒ 再试 `unsafeWindow.monaco`；都没有就返回 null（调用方 fail closed）。
 */
export function pageMonaco(win: unknown): unknown {
  const w = win as { monaco?: unknown } | null
  if (w?.monaco) return w.monaco
  const g = globalThis as { unsafeWindow?: { monaco?: unknown } }
  return g.unsafeWindow?.monaco ?? null
}

/** 按**源码逐字相等**认 cell 的 model（同内容多份 ⇒ 不猜，返回 null）。 */
export function findMonacoModel(monaco: unknown, source: string): MonacoModel | null {
  const models = (monaco as MonacoGlobal | null)?.editor?.getModels?.()
  if (!Array.isArray(models)) return null
  const hits = models.filter((m) => typeof m?.getValue === 'function' && m.getValue() === source)
  return hits.length === 1 ? hits[0]! : null
}

function fullRange(model: MonacoModel): unknown {
  const full = model.getFullModelRange?.()
  if (full) return full
  const lines = model.getLineCount?.()
  if (!lines || lines < 1) return null
  const col = model.getLineMaxColumn?.(lines)
  if (!col) return null
  return { startLineNumber: 1, startColumn: 1, endLineNumber: lines, endColumn: col }
}

/**
 * 用 Monaco model 写回整段源码（`after` 已由调用方按最小差异算好，内容不丢）。
 * 写完**回读校验**：没落地 ⇒ 带原因失败，绝不假装成功。
 */
export function replaceMonacoSource(monaco: unknown, before: string, after: string): CmdResult {
  const model = findMonacoModel(monaco, before)
  if (!model) {
    return {
      ok: false,
      reason: '找不到这段 cell 对应的 Monaco model（页面未暴露 monaco 或内容已变）',
    }
  }
  if (minimalEdit(before, after) === null) return { ok: true, detail: 'unchanged' }
  const range = fullRange(model)
  if (!range) return { ok: false, reason: 'Monaco model 不支持整段定位（缺 getFullModelRange）' }
  if (typeof model.pushEditOperations === 'function') {
    model.pushEditOperations([], [{ range, text: after }], () => null)
  } else if (typeof model.setValue === 'function') {
    model.setValue(after)
  } else {
    return { ok: false, reason: 'Monaco model 不支持写入（缺 pushEditOperations/setValue）' }
  }
  const now = model.getValue()
  if (now !== after) {
    return {
      ok: false,
      retry: true,
      reason: `Monaco 写入未生效（回读 ${now.length} 字符，期望 ${after.length}）`,
    }
  }
  return { ok: true, detail: 'monaco' }
}
