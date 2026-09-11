/** render.tsx — 服务端 SSR 渲染包装（tests 的 HTML 断言改指这里）。
 *  每张卡由 PanelErrorBoundary（class componentDidCatch）在 SSR 期也隔离——单卡
 *  render 崩溃只输出该卡占位，其余卡正常（首屏不整页 500，DS-E3）。 */

import { renderToString } from 'preact-render-to-string'
import { options } from 'preact'
import { pageCss } from '../ui/theme'
import { App } from './ui/app'
import { EvalApp } from './ui/eval-app'
import { LogApp } from './ui/log-app'
import type { ConsoleStateView, EvalPagePayload, LogPageOptions, LogPayload } from '../ui/view'

// preact-render-to-string v6：SSR 期 ErrorBoundary 默认关闭，需显式开（DS-E3 服务端隔离）。
;(options as { errorBoundaries?: boolean }).errorBoundaries = true

/** 内联 JSON 的安全转义（`</script>` 注入防护）。 */
function escapeJson(s: string): string {
  return s.replace(/</g, '\\u003c')
}

/** 炼丹炉 favicon：内联 SVG data-URI（暗底熔炉 + 炉鼎 + 火焰，简单辨识，16-32px 均清晰）。 */
export const LANTERN_FAVICON =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#1c2333"/>
  <circle cx="16" cy="18" r="12" fill="none" stroke="#e8922e" stroke-width="1.6"/>
  <!-- 炉鼎（圆底锅体 + 缘口） -->
  <path d="M9 14.4 Q12.4 11.2 14.4 9.6 Q16 8.6 14.4 9.6 Q12.4 11.2 9 14.4 Z" fill="#e8922e"/>
  <rect x="10.8" y="12.6" width="10.4" height="2" rx="1" fill="#1c2333"/>
  <path d="M9 14.4 L9 22.4 L23 22.4 L23 14.4 Z" fill="#e8922e"/>
  <!-- 火焰（三束） -->
  <path d="M13 22.4 Q11.6 25 12 27.4 Q12.6 28.8 13 27.4 Q13.4 25 13 22.4 Z" fill="#f4722b"/>
  <path d="M15.5 22.4 Q14.6 24.6 15 26.6 Q15.6 27.8 16 26.6 Q16.6 24.6 15.5 22.4 Z" fill="#ef4444"/>
  <path d="M18.5 22.4 Q17.4 25 18 27.4 Q18.6 28.8 19 27.4 Q19.4 25 18.5 22.4 Z" fill="#f4722b"/>
</svg>`,
  )

function shell(title: string, bodyHtml: string, initialJson: string, scriptSrc: string): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="icon" href="${LANTERN_FAVICON}"/>
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
  return shell('炼丹炉', html, JSON.stringify(state), scriptSrc)
}

/** 评估页（/eval，独立成页 R8）：SSR 首帧 + hydrate，bundle = /eval.js。 */
export function renderEvalPage(payload: EvalPagePayload, scriptSrc = '/eval.js'): string {
  const html = renderToString(<EvalApp initial={payload} options={payload.options} />)
  return shell('评估页 — EvalBoard', html, JSON.stringify(payload), scriptSrc)
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
