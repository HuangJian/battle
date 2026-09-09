/** render.tsx — 服务端 SSR 渲染包装（tests 的 HTML 断言改指这里）。
 *  每张卡由 PanelErrorBoundary（class componentDidCatch）在 SSR 期也隔离——单卡
 *  render 崩溃只输出该卡占位，其余卡正常（首屏不整页 500，DS-E3）。 */

import { renderToString } from 'preact-render-to-string'
import { options } from 'preact'
import { pageCss } from '../ui/theme'
import { App } from './ui/app'
import { LogApp } from './ui/log-app'
import type { ConsoleStateView, LogPageOptions, LogPayload } from '../ui/view'

// preact-render-to-string v6：SSR 期 ErrorBoundary 默认关闭，需显式开（DS-E3 服务端隔离）。
;(options as { errorBoundaries?: boolean }).errorBoundaries = true

/** 内联 JSON 的安全转义（`</script>` 注入防护）。 */
function escapeJson(s: string): string {
  return s.replace(/</g, '\\u003c')
}

function shell(title: string, bodyHtml: string, initialJson: string, scriptSrc: string): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${title}</title>
<style>${pageCss()}</style>
</head>
<body>
<div id="root">${bodyHtml}</div>
<script>window.__INITIAL__=${escapeJson(initialJson)}</script>
<script src="${scriptSrc}"></script>
</body>
</html>`
}

/** 控制台首屏（只含 /api/state；pool 卡 skeleton + 客户端异步拉，E8/R7）。 */
export function renderConsolePage(state: ConsoleStateView, scriptSrc = '/app.js'): string {
  const html = renderToString(<App initial={state} />)
  return shell('网训战役指挥部', html, JSON.stringify(state), scriptSrc)
}

/** 日志页（/log/<key>，SSR 首帧 + hydrate）。bundle 与 /app.js 同目录：/log.js（§371：旧默认
 *  /app-log.js 与服务端 bundlesByPath（/log.js）不一致 → bundle 404 → 日志页零交互）。 */
export function renderLogPage(
  payload: LogPayload,
  opts: LogPageOptions,
  scriptSrc = '/log.js',
): string {
  const html = renderToString(<LogApp initial={payload} options={opts} />)
  return shell(
    `组件日志 — ${payload.label}`,
    html,
    JSON.stringify({ payload, options: opts }),
    scriptSrc,
  )
}
