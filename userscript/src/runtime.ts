/** runtime.ts — 平台 API 的唯一边界（Tampermonkey 的 GM_* 只在这里出现）。 */

export interface GmResponse {
  status: number
  statusText: string
  responseText: string
  responseHeaders: string
  finalUrl?: string
}

export interface GmRequest {
  method?: 'GET' | 'POST'
  url: string
  headers?: Record<string, string>
  data?: string
  timeout?: number
}

interface GmScope {
  GM_xmlhttpRequest?: (
    details: GmRequest & {
      onload?: (r: GmResponse) => void
      onerror?: (e: unknown) => void
      ontimeout?: () => void
    },
  ) => void
  GM_getValue?: <T>(key: string, fallback: T) => T
  GM_setValue?: (key: string, value: unknown) => void
  GM_deleteValue?: (key: string) => void
  GM_registerMenuCommand?: (label: string, fn: () => void) => void
  GM_setClipboard?: (text: string) => void
}

export interface Runtime {
  gmRequest(details: GmRequest): Promise<GmResponse>
  getValue<T>(key: string, fallback: T): T
  setValue(key: string, value: unknown): void
  deleteValue(key: string): void
  registerMenuCommand(label: string, fn: () => void): void
  setClipboard(text: string): void
}

/**
 * 从注入作用域（`globalThis`）取出 GM 能力。没有 `GM_xmlhttpRequest` ⇒ null：
 * 页面脚本环境 / 测试里不假装可用（调用方必须显式处理）。
 */
export function createRuntime(scope: Record<string, unknown>): Runtime | null {
  const gm = scope as GmScope
  const request = gm.GM_xmlhttpRequest
  if (typeof request !== 'function') return null
  return {
    gmRequest: (details) =>
      new Promise<GmResponse>((resolve, reject) => {
        request({
          ...details,
          onload: resolve,
          onerror: reject,
          ontimeout: () => reject(new Error('GM_xmlhttpRequest timeout')),
        })
      }),
    getValue: <T>(key: string, fallback: T): T =>
      typeof gm.GM_getValue === 'function' ? gm.GM_getValue(key, fallback) : fallback,
    setValue: (key, value) => gm.GM_setValue?.(key, value),
    deleteValue: (key) => gm.GM_deleteValue?.(key),
    registerMenuCommand: (label, fn) => gm.GM_registerMenuCommand?.(label, fn),
    setClipboard: (text) => gm.GM_setClipboard?.(text),
  }
}
