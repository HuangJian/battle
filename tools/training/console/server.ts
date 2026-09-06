/** server.ts — 神经网络训练控制台（本地 localhost 无鉴权；DECISIONS §348）。
 *
 *  职责：
 *    - GET  /            → 控制台页（page.renderConsolePage，服务端渲染整页）
 *    - GET  /api/state   → 状态快照（api.buildStateView：组件/节点/模式/指标）
 *    - POST /api/<act>   → 动作（api.routeAction → actions：启/停/冒烟/预设/开关/节点编辑）
 *  页面热加载：page.ts mtime 键控动态 import（§341 语义）——改页面即时生效；
 *  api/actions 为有状态逻辑层不热加载（改后重启控制台进程）。
 *
 *  变更检测监督（DECISIONS §349，原 start.ts 职责的归并）：监督循环周期性检查
 *  受管进程的哨兵文件（codehash-files.txt SSOT + 各自入口源码），运行的代码更新后
 *  自动重启该进程应用最新代码——spec 重建经 actions.restartSpecFor（specs.ts）。
 *  控制台进程自身退出 = 监督停止（受管进程是 detached 的，不受影响）。
 *
 *  无鉴权边界：仅绑定 127.0.0.1（回环，不可外网访问）；动作面等价 CLI 启动器
 *  （能杀进程/改 rl-config.json），因此**不要**用 0.0.0.0 或端口转发暴露。
 *
 *  运行：bun run train（= bun tools/training/console/server.ts [--port 8900]）
 */

import { statSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, REPO_ROOT } from '../paths'
import { createSupervisor } from '../reload'
import { killPid, shapeLoopbackNoProxy, waitUntil } from '../net'
import { saveComponent } from '../registry'
import { launchSpec } from '../proc'
import { monitorTouch } from '../reload-touch'
import { buildStateView, componentLogPayload, routeAction } from './api'
import { restartSpecFor } from './actions'
import type { Component } from '../types'

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

/** 变更检测监督器：账本里登记的每个组件都纳入监督（登记 = 长跑语义；哨兵变更
 *  → 杀旧 → 按 specs.ts 最新配置重建 → 回灌账本）。与页面动作共用 busy 互斥，
 *  避免与手动启停同时操作同一组件。 */
function startSupervisor(): ReturnType<typeof createSupervisor> {
  const restart = async (
    spec: Parameters<Parameters<typeof createSupervisor>[0]>[0],
    oldPid: number,
  ): Promise<number> => {
    const key = spec.key
    const fresh = restartSpecFor(key)
    if (!fresh) {
      console.warn(`[supervisor] ${key}: 无法重建 spec（未登记或缺元数据）——跳过重启`)
      return oldPid
    }
    await killPid(oldPid)
    const r = launchSpec(fresh)
    saveComponent(key, { pid: r.pid, entry: fresh.sentinels[fresh.sentinels.length - 1] })
    monitorTouch()
    const ready = await waitUntil(fresh.healthy, 45000, 500)
    console.log(
      `[supervisor] ${key} 已应用最新代码 (PID ${r.pid}${ready ? '' : '，45s 未就绪，继续观察'})`,
    )
    return r.pid
  }
  return createSupervisor(restart, { intervalMs: 5000 })
}

async function main(): Promise<void> {
  const { port } = parseArgs()
  if (shapeLoopbackNoProxy()) console.log('[console] 检测到代理环境变量——已追加 NO_PROXY 直连回环')

  // 变更检测监督：跟踪账本中已登记的全部组件。
  const sup = startSupervisor()
  const watched = new Set<Component>()
  const reconcileWatch = async (): Promise<void> => {
    const state = await buildStateView()
    for (const c of state.components) {
      if (c.status !== 'running' || watched.has(c.key)) continue
      const spec = restartSpecFor(c.key)
      if (!spec) continue
      sup.watch(spec, c.pid ?? 0)
      watched.add(c.key)
      console.log(`[supervisor] 监督 ${c.key} (PID ${c.pid})`)
    }
  }
  await reconcileWatch()
  const reconcileTimer = setInterval(() => void reconcileWatch(), 15000)
  reconcileTimer.unref?.()

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
        if (req.method === 'GET' && url.pathname.startsWith('/api/log/')) {
          const key = url.pathname.slice('/api/log/'.length) as Component
          const lines = Math.min(
            Math.max(Number(url.searchParams.get('lines') ?? 200) || 200, 10),
            2000,
          )
          const payload = await componentLogPayload(key, lines)
          return payload ? json(payload) : json({ ok: false, message: `未知组件: ${key}` }, 404)
        }
        if (req.method === 'GET' && url.pathname.startsWith('/log/')) {
          const key = url.pathname.slice('/log/'.length) as Component
          const lines = Math.min(
            Math.max(Number(url.searchParams.get('lines') ?? 200) || 200, 10),
            2000,
          )
          const payload = await componentLogPayload(key, lines)
          if (!payload) return new Response(`unknown component: ${key}`, { status: 404 })
          const page = await loadPage()
          if (!page)
            return new Response(`${PAGE_TS_HINT} unavailable — see console log`, { status: 500 })
          const state = await buildStateView()
          const follow = url.searchParams.get('follow') !== '0'
          return new Response(
            page.renderLogPage(payload, {
              components: state.components.map((c) => ({
                key: c.key,
                label: c.label,
                status: c.status,
              })),
              follow,
              lines,
            }),
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } },
          )
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
  console.log(`[console] 配置回写: ${cfgPath} · 变更检测监督已启用 · 停止: Ctrl-C`)
}

void main()
