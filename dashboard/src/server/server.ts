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
 *    - GET  /api/evalGames            → 最新 in-loop eval 逐局视图（导出 replay 弹窗）
 *    - GET  /api/evalReplayJob        → replay 导出任务态（busy + manifest + 日志尾）
 *    - GET  /api/evalReplayFile       → 单局 .replay 下载（manifest 白名单）
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
 *  运行：bun run dashboard（= cd dashboard && bun run start，= bun dashboard/src/server/server.ts [--port 8900]）
 */

import { statSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, REPO_ROOT } from '../core/paths'
import { createSupervisor } from '../core/reload'
import {
  isLoopbackAddress,
  isReadonlyAction,
  killPid,
  killPidTree,
  shapeLoopbackNoProxy,
  waitUntil,
} from '../core/net'
import { COMPONENT_KILL_TREE } from '../core/types'
import {
  entryForCourse,
  loadRegistry,
  registryComponents,
  saveAnyComponent,
} from '../core/registry'
import { launchSpec } from '../core/proc'
import { error, info, initConsoleLog, log, warn } from '../core/log'
import { monitorTouch } from '../core/reload-touch'
import {
  buildBcEpochsView,
  buildEvalBoardView,
  buildEvalCkptsView,
  buildEvalGamesView,
  buildEvalReplayJobView,
  buildPoolView,
  buildStateView,
  componentLogPayload,
  curriculumLadderView,
  discoverCourses,
  evalReplayFileResponse,
  getLoopQueueView,
  invalidateHubAdmin,
  invalidateLoopQueue,
  invalidateSlowSnapshot,
  ladderTickAll,
  routeAction,
  sanitizeViewCourse,
  startSnapshotRefresher,
} from './api'
import { restartSpecFor } from './actions'
import { runningStaleCode } from '../core/reload'
import { handleDeliverUpload, taskBundleDownloadResponse, taskBundleInfo } from './bundles'
import { runExitCheck } from './exit-watchdog'
import { ensureBundle, type BundleTarget } from './build'
import { renderConsolePage, renderEvalPage, renderLogPage } from '../web/render'
import type { Component, ProcSpec, RegistryEntry } from '../core/types'

interface ServeOpts {
  port: number
}

/** 监督器的重启回调（`(key, course)` 精确重建 → 整树杀 → spawn → 回灌账本）。 */
type RestartFn = (spec: ProcSpec, oldPid: number) => Promise<number>

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

/** 变更检测监督器（同 §349；页面动作与监督共用 busy 互斥语义在 actions 层）。
 *
 *  返回监督器本体 + `restart`：启动对账（`reconcileWatch`）要用它接管「跑着旧码」的在跑进程，
 *  而重启逻辑（整树杀、重建 spec、回灌槽位）只此一份。 */
function startSupervisor(): { sup: ReturnType<typeof createSupervisor>; restart: RestartFn } {
  const restart: RestartFn = async (spec, oldPid) => {
    const key = spec.key
    const course = spec.course ?? ''
    const tag = `${key}${course ? `[${course}]` : ''}`
    // fail-closed（M5）：按 (key, course) 精确重建，绝不用 console-state 猜课程。
    const fresh = restartSpecFor(key, course)
    if (!fresh) {
      // 放弃重建必须可见（F-A4）：null 是「放弃」不是「没事发生」。
      warn(`[supervisor] ${tag}: 无法重建 spec（该 (key, course) 未登记或缺元数据）——跳过重启`)
      return oldPid
    }
    // 带子进程监督器的组件（localWorker）必须整树停：只杀父进程会给重启后的新实例
    // 留一个抢同一 hub job 的孤儿（判定唯一来源 types.COMPONENT_KILL_TREE）。
    if (COMPONENT_KILL_TREE.has(key)) await killPidTree(oldPid)
    else await killPid(oldPid)
    const r = launchSpec(fresh)
    // 回灌原槽位（per-course）；无课程走旧扁平键
    saveAnyComponent(key, course, {
      ...entryForCourse(loadRegistry(), key, course),
      pid: r.pid,
      course: course,
      // 启动时刻每次重启都刷新（`runningStaleCode` 的比对面；不刷新 = 每轮对账都
      // 把刚重启的新进程又当成旧码——15s 一次的重启风暴）。
      startedAt: Date.now(),
      entry: fresh.sentinels[fresh.sentinels.length - 1],
      log: fresh.log,
    })
    monitorTouch()
    // 就绪 = 「新 pid 真的持有它要独占的端口」∧ 健康检查（2026-09-17 同族修复）。
    // 只问 healthy 会踩「旧僵尸替新进程答 200」：新进程 bind 失败（EADDRINUSE / python 侧
    // 双监听守卫）后早已退出，而端口上的旧实例照样答 /ping 或 /ready ⇒ 监督器把**僵尸的
    // 200** 记成「重启成功」，账本记新 pid、实际服务的是旧进程（就是 hub-server 重启事故
    // 的相位：账本上的 pid ≠ 真在服务的那一个）。
    const ownsPort = async (): Promise<boolean> => (await fresh.ownsResource?.(r.pid)) !== false // 未声明 = 无独占资源可核 → 不阻塞
    const ready = await waitUntil(
      async () => (await ownsPort()) && (await fresh.healthy()),
      45000,
      500,
    )
    // 未就绪的原因必须写清楚：到底是「没起起来」还是「新实例没拿到它该独占的端口」。
    // false 的两种含义（见 core/proc.ts::portOwnedBy）：端口被别人占着（旧僵尸仍在服务？）、
    // 或端口上根本没人监听（新实例 bind 失败后已退出）。
    const portNote = (await ownsPort())
      ? ''
      : '；新实例未持有该端口（bind 失败，或旧实例仍在服务？）'
    log(
      `[supervisor] ${tag} 已应用最新代码 (PID ${r.pid}` +
        `${ready ? '' : '，45s 未就绪，继续观察'}${portNote})`,
    )
    return r.pid
  }
  return { sup: createSupervisor(restart, { intervalMs: 5000 }), restart }
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
  // ★ 组件级决策落盘（2026-09-20 用户指令）：必须在**任何**决策之前 arm，否则
  //   「谁在何时停了/重启了什么」又只剩下终端滚屏（事故复盘时从盘上证据分不出
  //   「人工停的」与「自己死的」——见 core/log.ts 模块头）。
  const decisionLog = initConsoleLog()
  if (shapeLoopbackNoProxy()) info('[console] 检测到代理环境变量——已追加 NO_PROXY 直连回环')

  // 变更检测监督：跟踪账本中已登记的全部组件。
  const { sup, restart } = startSupervisor()
  // 监督单位 = (key, course)：多课程下同一组件有多份进程，按 key 单键会互相顶掉。
  const watched = new Set<string>()
  const reconcileWatch = async (): Promise<void> => {
    const state = await buildStateView()
    // 账本原件（`startedAt` 在视图里没有，而判「跑的是不是旧码」只看它）。
    const entries = new Map<string, RegistryEntry>(
      registryComponents().map((e) => [`${e.key}|${e.course}`, e.entry]),
    )
    for (const c of state.components) {
      const course = c.course ?? ''
      const id = `${c.key}|${course}`
      if (c.status !== 'running' || watched.has(id)) continue
      const spec = restartSpecFor(c.key as Component, course)
      if (!spec) continue
      let pid = c.pid ?? 0
      // ★ 接管在跑进程之前先判「它跑的是不是磁盘上的这份代码」（2026-09-20 事故）：
      //   watch() 以**当下**指纹为基线，直接接管会把旧码永久冻结在「就绪」上——
      //   于是盘上加的新闸对已在跑的组件永远不生效，而控制台一切显示正常。
      //   判据的真相源是账本 `startedAt`（spawn/重启时写）；缺席 = 旧条目 ⇒ 按旧码处理。
      //
      //   ⚠ 适用范围**不含 cloudflared**：它是第三方二进制，跑的不是我们的代码（哨兵只是共用
      //   SSOT 清单的代理），而重启它的代价是**隧道 URL 变化**：云机手上那个 URL 立刻作废、
      //   正在跑的 job 无法回传（用户得回到 Colab 重新贴）。零收益、高代价 ⇒ 永不由「旧码」触发。
      if (
        pid > 0 &&
        c.key !== 'cloudflared' &&
        runningStaleCode(spec, entries.get(id)?.startedAt)
      ) {
        warn(
          `[supervisor] ${c.key}${course ? `[${course}]` : ''} (PID ${pid}) 跑的是磁盘上更早的代码` +
            '——接管并重启应用最新代码（旧进程的判据/课程表都停在它启动的那一刻）',
        )
        pid = await restart(spec, pid)
      }
      sup.watch(spec, pid)
      watched.add(id)
      log(`[supervisor] 监督 ${c.key}${course ? `[${course}]` : ''} (PID ${pid})`)
    }
  }
  await reconcileWatch()
  const reconcileTimer = setInterval(() => void reconcileWatch(), 15000)
  reconcileTimer.unref?.()
  // R4 自动爬梯 ticker（A3：console 服务端常驻；无状态推导，重启不丢进度；
  // 无 ladder 请求时 ladderTickAll 直接返回，零重扫描）。
  const ladderTimer = setInterval(() => {
    try {
      const r = ladderTickAll(discoverCourses())
      if (r.enqueued.length > 0)
        log(`[ladder] tick tasks=${r.tasks} enqueued=${r.enqueued.join(',')}`)
    } catch {
      /* ticker 永不炸循环 */
    }
  }, 30000)
  ladderTimer.unref?.()
  // 慢部件快照后台刷新（§366：节点 ping/组件探测/池历史移出请求路径，页面加载 <1s）。
  // reconcileWatch 已冷算一次暖缓存；此后每 5s 后台重算，请求只读缓存。
  startSnapshotRefresher()
  // 调度器视图（R2c-3）暖一次缓存：它要起一个只读 python（~sub-second），懒算的话
  // 首次 /api/state（含 SSR 首屏）要为它等一个子进程。之后由 TTL（10s）驱动重算。
  void getLoopQueueView().catch(() => {
    /* 读失败由视图内部转成 error 上屏；这里只需不抛 */
  })
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
        // 判定抽在 net.ts::isReadonlyAction（纯函数，回归测试见 training-console.test.ts）。
        if (isReadonlyAction(req.method, server.requestIP(req)?.address)) {
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
        // BC epoch 指标 / 多地图 eval（2026-09-13；bcRowsFromLedgerTail 解析，只读）。
        if (req.method === 'GET' && url.pathname === '/api/bcEpochs') {
          return json(buildBcEpochsView(viewCourse || undefined))
        }
        // R7：ckpt/iter 发现（只 stat 不读内容；?leg= 懒加载单腿明细）。
        if (req.method === 'GET' && url.pathname === '/api/evalCkpts') {
          const leg = url.searchParams.get('leg') ?? ''
          return json(buildEvalCkptsView(viewCourse || undefined, leg))
        }
        // I5（roadmap v2.0）：阶梯统一 identity 台账 LAN 只读渲染（与 God-AI
        // evalboard ladder 无关）。
        if (req.method === 'GET' && url.pathname === '/api/curriculumLadder') {
          return json(curriculumLadderView())
        }
        // 导出 replay：最新 in-loop eval 逐局视图 / 导出任务态 / tar.gz 下载。
        if (req.method === 'GET' && url.pathname === '/api/evalGames') {
          return json(buildEvalGamesView(viewCourse || ''))
        }
        if (req.method === 'GET' && url.pathname === '/api/evalReplayJob') {
          return json(buildEvalReplayJobView(viewCourse || ''))
        }
        if (req.method === 'GET' && url.pathname === '/api/evalReplayFile') {
          const file = url.searchParams.get('file') ?? ''
          return (
            (await evalReplayFileResponse(viewCourse || '', file)) ??
            json({ ok: false, message: '缺少 course' }, 400)
          )
        }
        // R8：/eval 独立评估页（?courses=a,b 可分享 URL；?course= 兼容）。
        if (req.method === 'GET' && url.pathname === '/eval') {
          const all = discoverCourses()
          const raw = url.searchParams.get('courses') ?? url.searchParams.get('course') ?? ''
          const selected = raw
            .split(',')
            .map((s) => s.trim())
            .filter((c) => c && all.includes(c))
          const eff = selected.length > 0 ? selected : viewCourse ? [viewCourse] : all.slice(0, 1)
          const views = eff.map((c) => buildEvalBoardView(c, false))
          const payload = {
            views,
            options: { courses: eff, allCourses: all, readOnly: !loopback },
          }
          return new Response(renderEvalPage(payload), {
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
          })
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
        // ── 产物导入（multipart 上传 + 自动评估）：体积是 zip，不走 JSON 动作层 ──
        if (req.method === 'POST' && url.pathname === '/api/deliverUpload') {
          const resp = await handleDeliverUpload(req, viewCourse || '')
          invalidateSlowSnapshot()
          invalidateHubAdmin()
          return resp
        }
        if (req.method === 'GET' && url.pathname === '/api/taskBundleInfo') {
          const course = url.searchParams.get('course') || viewCourse || ''
          if (!course) return json({ ok: false, message: '缺少 course' }, 400)
          const info = taskBundleInfo(course)
          return json({
            ok: true,
            exists: info.exists,
            name: info.name,
            bytes: info.bytes,
            mtimeMs: info.mtimeMs,
            log: path.relative(REPO_ROOT, info.log).replace(/\\/g, '/'),
          })
        }
        // 任务包下载（`content-disposition: task-<课程>.zip`）
        if (req.method === 'GET' && url.pathname === '/api/taskBundle') {
          const course = url.searchParams.get('course') || viewCourse || ''
          const resp = course ? taskBundleDownloadResponse(course) : null
          return (
            resp ?? json({ ok: false, message: '还没有导出过任务包（先点「导出任务包」）' }, 404)
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
          // hub 观测面（队列/worker 登记表）同处失效：worker 登记写过 rl-config、启停 hub
          // 都会改它的内容，下一拍不该再读旧观测（与慢快照同一时机 = 一个失效点）。
          if (resp) {
            invalidateSlowSnapshot()
            invalidateHubAdmin()
            // 调度器视图的 TTL 比 hub 观测面长（10s）：暂停/恢复动作后必须显式作废，
            // 否则按钮点下去要到下一个 TTL 才看到意图上屏（回执面同理）。
            invalidateLoopQueue()
          }
          return resp ?? json({ ok: false, message: `未知动作: ${act}` }, 404)
        }
        return new Response('not found', { status: 404 })
      } catch (e) {
        // 单行（把 stack 拆进文件会把「一行一决策」打散；定位靠 message + 请求行）
        error(
          `[console] ${req.method} ${url.pathname} failed: ` +
            (e instanceof Error ? e.message : String(e)),
        )
        return json({ ok: false, message: e instanceof Error ? e.message : String(e) }, 500)
      }
    },
  })
  const cfgPath = path.relative(REPO_ROOT, CONFIG_PATH)
  log(`[console] NN 训练控制台: http://127.0.0.1:${server.port}/  (局域网只读 + localhost 控制)`)
  log(
    `[console] 局域网可查看任意课程/日志/节点统计（?course= 切换）；启停/冒烟/模式/节点编辑仅限本机。`,
  )
  log(`[console] 配置回写: ${cfgPath} · 变更检测监督已启用 · 停止: Ctrl-C`)
  // 组件级决策（启/停/重启/判死/开课/停课/放弃重建）的落盘位置必须让操作员一眼看到
  // ——它就是「盘上证据」那份：`tail -f` 它就能看到谁在何时动了什么。
  log(`[console] 组件决策日志: ${path.relative(REPO_ROOT, decisionLog)}（追加；旋转保留一代 .1）`)
}

void main()
