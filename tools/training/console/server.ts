/** server.ts — 神经网络训练控制台（本地 localhost 无鉴权；DECISIONS §348）。
 *
 *  职责：
 *    - GET  /            → 控制台页（page.renderConsolePage，服务端渲染整页）
 *    - GET  /api/state   → 状态快照（api.buildStateView：组件/节点/模式/指标）
 *    - POST /api/<act>   → 动作（api.routeAction → actions：启/停/冒烟/预设/开关/节点编辑）
 *  页面热加载：page.ts mtime 键控动态 import（§341 语义）——改页面即时生效；
 *  api/actions 为有状态逻辑层不热加载（改后重启控制台进程）。
 *
 *  无鉴权边界：仅绑定 127.0.0.1（回环，不可外网访问）；动作面等价 CLI 启动器
 *  （能杀进程/改 rl-config.json），因此**不要**用 0.0.0.0 或端口转发暴露。
 *
 *  运行：bun tools/training/console/server.ts [--port 8900]
 */

import { statSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, REPO_ROOT } from '../paths'
import { buildStateView, routeAction } from './api'

const PAGE_TS = path.join(import.meta.dir, 'page.ts')

const PAGE_TS_HINT = 'tools/training/console/page.ts'

interface ServeOpts {
  port: number
}

function parseArgs(): ServeOpts {
  let port = 8900
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--port') port = Number.parseInt(argv[++i] ?? '', 10) || 8900
  }
  return { port }
}

// page.ts 热加载（mtime 键控动态 import，§341 语义）；坏文件沿用上一版可用模块。
let pageMod: { mtimeMs: number; mod: typeof import('./page') } | null = null
async function loadPage(): Promise<typeof import('./page') | null> {
  try {
    const mtimeMs = statSync(PAGE_TS).mtimeMs
    if (pageMod && pageMod.mtimeMs === mtimeMs) return pageMod.mod
    const mod = await import(`./page.ts?m=${mtimeMs}`)
    pageMod = { mtimeMs, mod }
    console.log(`[console] page.ts hot-reloaded (mtime=${Math.round(mtimeMs)})`)
    return mod
  } catch (e) {
    console.error(
      `[console] page.ts hot-reload FAILED — serving previous version: ${String(e).slice(0, 160)}`,
    )
    return pageMod?.mod ?? null
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

async function main(): Promise<void> {
  const { port } = parseArgs()
  const server = Bun.serve({
    // 无鉴权的前提 = 只听回环；绑 0.0.0.0 会把"杀进程/改配置"暴露给整个局域网。
    hostname: '127.0.0.1',
    port,
    async fetch(req) {
      const url = new URL(req.url)
      try {
        if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/console')) {
          const page = await loadPage()
          if (!page) {
            return new Response(`${PAGE_TS_HINT} unavailable — see console log`, { status: 500 })
          }
          const state = await buildStateView()
          return new Response(page.renderConsolePage(state), {
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
          })
        }
        if (req.method === 'GET' && url.pathname === '/api/state') {
          return json(await buildStateView())
        }
        if (req.method === 'POST' && url.pathname.startsWith('/api/')) {
          const act = url.pathname.slice('/api/'.length)
          let body: Record<string, unknown> = {}
          try {
            body = (await req.json()) as Record<string, unknown>
          } catch {
            /* empty body — actions that need params will 400 */
          }
          const resp = await routeAction(act, body)
          return resp ?? json({ ok: false, message: `未知动作: ${act}` }, 404)
        }
        return new Response('not found', { status: 404 })
      } catch (e) {
        console.error(`[console] ${req.method} ${url.pathname} failed:`, e)
        return json({ ok: false, message: e instanceof Error ? e.message : String(e) }, 500)
      }
    },
  })
  const cfgPath = path.relative(REPO_ROOT, CONFIG_PATH)
  console.log(`[console] NN 训练控制台: http://127.0.0.1:${server.port}/  (仅回环，无鉴权)`)
  console.log(`[console] 配置回写: ${cfgPath} · 停止: Ctrl-C`)
}

void main()
