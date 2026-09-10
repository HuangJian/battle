/** server.ts — 神经网络训练控制台（局域网只读 + localhost 控制；DECISIONS §348 延伸）。
 *
 *  职责：
 *    - GET  /            → SSR 首屏（render.tsx renderConsolePage，renderToString + hydrate）
 *    - GET  /app.js      → 客户端 bundle（build.ts ensureBundle：mtime 失效自动重建；禁词/体积断言）
 *    - GET  /log/<key>   → 日志页 SSR（renderLogPage）+ /app-log.js
 *    - GET  /api/state   → 状态快照（api.buildStateView：组件/节点/模式/指标，3s 全局节奏）
 *    - GET  /api/pool    → 池数据端点（api.buildPoolView：节点历史/selfStatus/localHash，
 *                          独立慢节奏 + 课程键控 30s TTL 缓存，?fresh=1 强制）
 *    - GET  /api/log/<key> → 日志载荷（日志页 2s/4s 轮询）
 *    - POST /api/<act>   → 动作（api.routeAction → actions：启/停/冒烟/预设/开关/节点编辑）
 *
 *  课程只读覆盖：/api/state /api/pool /api/log/<key> /log/<key> 均接受 ?course=<name>
 *  （api.sanitizeViewCourse 校验：真实课程 + 防路径穿越）——只影响本次读取的课程数据，
 *  绝不写 console-state，LAN 切换查看课程不影响正在训练的操作员课程。
 *
 *  权限边界（局域网只读）：绑定 0.0.0.0（局域网可访问）；POST /api/* 动作仅接受回环来源
 *  （server.requestIP 判定，api.isLoopbackAddress）——局域网只能 GET 查看，启/停/冒烟/
 *  预设/模式/节点编辑仅本机 localhost 可执行（fail closed：无法判定来源 = 拒绝）。
 *  视图数据（含 cloudflared 隧道 auth key 行与复制）局域网与回环同权——只读是动作边界，
 *  不是数据边界（2026-09-09 用户指令：LAN 照样显示 token 行）。
 *
 *  变更检测监督（DECISIONS §349）：监督循环周期性检查受管进程的哨兵文件，运行代码
 *  更新后自动重启该进程。控制台进程自身退出 = 监督停止（受管进程 detached 不受影响）。
 *  ui/** 与 .tsx 非 api/actions 热加载层：改 .tsx → 下次请求 mtime 检测自动 rebuild
 *  （~300ms，打印一行日志）→ 用户手动 F5 生效（§3.5/5，评审 E4 降级采纳）。
 *
 *  运行：bun run train（= bun tools/training/console/server.ts [--port 8900]）
 */

import { statSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, REPO_ROOT } from '../paths'
import { createSupervisor } from '../reload'
import { isLoopbackAddress, killPid, shapeLoopbackNoProxy, waitUntil } from '../net'
import { saveComponent } from '../registry'
import { launchSpec } from '../proc'
import { monitorTouch } from '../reload-touch'
import {
  buildEvalBoardView,
  buildPoolView,
  buildStateView,
  componentLogPayload,
  invalidateSlowSnapshot,
  routeAction,
  sanitizeViewCourse,
  startSnapshotRefresher,
} from './api'
import { restartSpecFor } from './actions'
import { runExitCheck } from './exit-watchdog'
import { ensureBundle, type BundleTarget } from './build'
import { renderConsolePage, renderLogPage } from './render'
import type { Component } from '../types'

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

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

/** ?lines 解析（§371 优化 1）：'all' → 读全部；数字 clamp [10, 2000]。 */
function parseLogLines(raw: string | null, dft = 200): number | 'all' {
  if (raw === 'all') return 'all'
  return Math.min(Math.max(Number(raw ?? dft) || dft, 10), 2000)
}

/** 变更检测监督器（同 §349；页面动作与监督共用 busy 互斥语义在 actions 层）。 */
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

// ────────────────────────── bundle 服务（mtime 内存缓存 + 自动重建） ──────────────────────────

const bundleMemory = new Map<string, { mtimeMs: number; body: ArrayBuffer }>()

async function serveBundle(target: BundleTarget): Promise<Response> {
  await ensureBundle(target)
  const st = statSync(target.out)
  let ent = bundleMemory.get(target.key)
  if (!ent || ent.mtimeMs !== st.mtimeMs) {
    const ab = await Bun.file(target.out).arrayBuffer()
    ent = { mtimeMs: st.mtimeMs, body: ab }
    bundleMemory.set(target.key, ent)
  }
  return new Response(ent.body, {
    headers: {
      'Content-Type': 'application/javascript; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  })
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
      if (c.status !== 'running' || watched.has(c.key as Component)) continue
      const spec = restartSpecFor(c.key as Component)
      if (!spec) continue
      sup.watch(spec, c.pid ?? 0)
      watched.add(c.key as Component)
      console.log(`[supervisor] 监督 ${c.key} (PID ${c.pid})`)
    }
  }
  await reconcileWatch()
  const reconcileTimer = setInterval(() => void reconcileWatch(), 15000)
  reconcileTimer.unref?.()
  // 慢部件快照后台刷新（§366：节点 ping/组件探测/池历史移出请求路径，页面加载 <1s）。
  // reconcileWatch 已冷算一次暖缓存；此后每 5s 后台重算，请求只读缓存。
  startSnapshotRefresher()
  // 非正常退出看护（§380）：受管进程自行退出/被杀 → 显式写失败日志 + 记录 error，
  // 不再静默（TrainingLoop 曾因缺 BC 参考 boot 崩溃，只有翻日志才知道原因）。4s 一轮，
  // 两帧确认（内部）避免监督器换 pid 的瞬时误报。
  const exitWatchdog = setInterval(() => {
    void runExitCheck() // async：内部已兜底（返回 -1），失败不炸循环
  }, 4000)
  exitWatchdog.unref?.()

  const { BUNDLES } = await import('./build')
  const bundlesByPath = new Map(BUNDLES.map((b) => [`/${b.key}.js`, b]))

  const server = Bun.serve({
    // 局域网只读边界：绑 0.0.0.0 让局域网可访问，但动作（杀进程/改配置）经下方回环门控
    // 只放行本机 localhost；无鉴权的前提是动作面绝不暴露给局域网。
    hostname: '0.0.0.0',
    port,
    async fetch(req) {
      const url = new URL(req.url)
      try {
        // 只读门控：一切 POST 动作仅限回环来源（本机）；LAN 只能 GET 查看。
        // fail closed——requestIP 不可得（null）时视为非回环，动作被拒。
        if (req.method === 'POST' && !isLoopbackAddress(server.requestIP(req)?.address)) {
          return json(
            {
              ok: false,
              message:
                '只读模式：动作仅限本机 localhost 执行（局域网可查看课程/日志/节点，不可启停/改配置）',
            },
            403,
          )
        }
        const loopback = isLoopbackAddress(server.requestIP(req)?.address)
        // GET 只读课程覆盖（已 sanitize：真实课程 + 防路径穿越；空 = 自动/操作员课程）。
        const viewCourse = sanitizeViewCourse(url.searchParams.get('course'))
        // 只读视图标记：服务端按来源判定（客户端无权自封）——LAN 首屏 SSR 即渲染只读角标。
        const stampState = (s: Awaited<ReturnType<typeof buildStateView>>): typeof s => {
          s.readOnly = !loopback
          return s
        }
        if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/console')) {
          const state = stampState(await buildStateView(viewCourse || undefined))
          return new Response(renderConsolePage(state), {
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
          })
        }
        // 客户端 bundle（app.js / log.js；mtime 失效自动重建）
        if (req.method === 'GET' && bundlesByPath.has(url.pathname)) {
          return serveBundle(bundlesByPath.get(url.pathname)!)
        }
        if (req.method === 'GET' && url.pathname === '/api/state') {
          return json(stampState(await buildStateView(viewCourse || undefined)))
        }
        if (req.method === 'GET' && url.pathname === '/api/pool') {
          const fresh = url.searchParams.get('fresh') === '1'
          return json(await buildPoolView(fresh, viewCourse || undefined))
        }
        if (req.method === 'GET' && url.pathname === '/api/evalboard') {
          const fresh = url.searchParams.get('fresh') === '1'
          return json(await buildEvalBoardView(viewCourse || undefined, fresh))
        }
        if (req.method === 'GET' && url.pathname.startsWith('/api/log/')) {
          const key = url.pathname.slice('/api/log/'.length) as Component
          const lines = parseLogLines(url.searchParams.get('lines'))
          const payload = await componentLogPayload(key, lines, viewCourse || undefined)
          return payload ? json(payload) : json({ ok: false, message: `未知组件: ${key}` }, 404)
        }
        if (req.method === 'GET' && url.pathname.startsWith('/log/')) {
          const key = url.pathname.slice('/log/'.length) as Component
          const lines = parseLogLines(url.searchParams.get('lines'))
          const payload = await componentLogPayload(key, lines, viewCourse || undefined)
          if (!payload) return new Response(`unknown component: ${key}`, { status: 404 })
          const state = await buildStateView(viewCourse || undefined)
          const follow = url.searchParams.get('follow') !== '0'
          return new Response(
            renderLogPage(payload, {
              components: state.components.map((c) => ({
                key: c.key,
                label: c.label,
                status: c.status,
              })),
              follow,
              lines,
              course: viewCourse || undefined,
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
          // 动作改动组件/节点/课程 → 失效慢部件缓存，下次 buildStateView 冷算即时上屏（§366）。
          if (resp) invalidateSlowSnapshot()
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
  console.log(
    `[console] NN 训练控制台: http://127.0.0.1:${server.port}/  (局域网只读 + localhost 控制)`,
  )
  console.log(
    `[console] 局域网可查看任意课程/日志/节点统计（?course= 切换）；启停/冒烟/模式/节点编辑仅限本机。`,
  )
  console.log(`[console] 配置回写: ${cfgPath} · 变更检测监督已启用 · 停止: Ctrl-C`)
}

void main()
