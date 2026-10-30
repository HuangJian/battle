/** server.ts — 神经网络训练控制台（局域网只读 + localhost 控制；DECISIONS §348 延伸）。
 *
 *  职责：
 *    - GET  /            → SSR 首屏（render.tsx renderConsolePage，renderToString + hydrate）
 *    - GET  /metrics     → 指标页（同一 App + page=metrics；单 bundle，服务端路由 SSR，
 *    - GET  /nodes         docs/dashboard-redesign.md §3.2 选型）
 *    - GET  /wire
 *    - GET  /courses     → 课程管理页（全部课程 + 封存区；行内开课/停课/暂停/切模式/封存。
 *                          2026-09-27）
 *    - GET  /app.js      → 客户端 bundle（build.ts ensureBundle：mtime 失效自动重建；禁词/体积断言）
 *    - GET  /log/<key>   → 日志页 SSR（renderLogPage）+ /app-log.js
 *    - GET  /api/state   → 状态快照（api.buildStateView：组件/节点/模式/指标，3s 全局节奏）
 *    - GET  /api/pool    → 池数据端点（api.buildPoolView：节点历史/selfStatus/localHash；?days= 本地日窗口，
 *                          独立慢节奏 + 课程键控 30s TTL 缓存，?fresh=1 强制）
 *    - GET  /api/log/<key> → 日志载荷（日志页 2s/4s 轮询）
 *    - GET  /api/evalGames            → 一轮 in-loop eval 逐局视图（`?iter=`；缺省最新轮；导出 replay 弹窗）
 *    - GET  /api/evalRounds           → 该课程全部可导出 eval 轮（弹窗轮次选择器；summary-only 扫描）
 *    - GET  /api/compareTrends        → 多课程同图对比（`?courses=a,b&metric=&source=&from=&to=`；
 *                                       比较弹窗打开时自持轮询，不进 /api/state）
 *    - GET  /api/evalReplayJob        → replay 导出任务态（busy + manifest + 日志尾）
 *    - GET  /api/evalReplayFile       → 单局 .replay 下载（manifest 白名单）
 *    - POST /api/<act>   → 动作（api.routeAction → actions：启/停/冒烟/预设/开关/节点编辑）
 *
 *  课程只读覆盖：/api/state /api/log/<key> /log/<key> 均接受 ?course=<name>
 *  （★ 2026-09-26：/api/pool 已移除 ?course= —— 池视图与课程无关，改为 ?days= 窗口）
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
import { CONFIG_PATH, notebookLogsDir, REPO_ROOT, tmpLogsDir } from '../core/paths'
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
  buildCompareTrendsView,
  buildEvalCkptsView,
  buildEvalGamesView,
  buildEvalReplayJobView,
  buildEvalRoundsView,
  buildPoolView,
  buildStateView,
  componentLogPayload,
  curriculumLadderView,
  discoverCourses,
  evalboardApiPayload,
  evalboardPageDecision,
  evalboardRouteCounters,
  evalReplayFileResponse,
  parseEvalIterParam,
  parseCompareQuery,
  getLoopQueueView,
  poolCountersView,
  invalidatesSnapshot,
  invalidateAfterAction,
  ladderTickAll,
  markConsoleActivity,
  appendNotebookChunk,
  isNotebookLogKey,
  listNotebookSessions,
  notebookLogPayload,
  notebookNavEntry,
  parseNotebookChunk,
  readBoundedJson,
  routeAction,
  sanitizeViewCourse,
  shouldStartLadderTicker,
  startSnapshotRefresher,
} from './api'
import { courseMetricsStatusPayload, startCourseMetricsMirror } from './course-metrics-mirror'
import { restartSpecFor } from './actions'
import { runningStaleCode } from '../core/reload'
import {
  exportStatusLine,
  handleDeliverUpload,
  readTaskBundleStatus,
  taskBundleDownloadResponse,
  taskBundleInfo,
} from './bundles'
import { runExitCheck } from './exit-watchdog'
import { OFFLINE_EVAL_BACKFILL_INTERVAL_MS, runOfflineEvalBackfill } from './offline-eval-backfill'
import { ensureBundle, type BundleTarget } from './build'
import { CONSOLE_ROUTES, type RouteHandlers, buildRouteTable } from './route-table'
import { livePageCss } from './theme-css'
import {
  renderConsolePage,
  renderErrorsPage,
  renderEvalNoticePage,
  renderEvalPage,
  renderLogPage,
} from '../web/render'
import { pageForPath } from '../web/view'
import {
  buildErrorSummary,
  errorRegistryScannedAt,
  findErrorEvent,
  getErrorSummary,
  getResolvedErrors,
} from './api'
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
    // ★ 2026-10-25：**首帧语义**——监督只用**结构**（账本 pid + registry），探测列（ping /
    //   健康 / hub）在这里没有任何用处。以前这一路走完整快照 ⇒ 冷启动时、以及空闲超时后
    //   每 15s 一拍都要等 2.3s 的探测预算（实测 Bun 1.4.4：冷 buildStateView 2299ms，其中
    //   结构只有 28–33ms），而且会把「没人在看就不刷」的空闲闸从旁绕开（见
    //   snapshot-refresher.refreshSnapshotTick）。
    const state = await buildStateView(undefined, { firstPaint: true })
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
  // ★ 2026-10-03（plan/dashboard-reload-perf W4/A4）：这条 **await** 链是「监督先到位、
  //   再开始接收请求」的**唯一保证** —— 判据全在结构（registry/账本）；★ 2026-10-25 起它是
  //   **首帧图**（`firstPaint`），不再为探测预算（节点 ping 1.5s ∥ hub 1.2s ∥ 隧道 2.5s）
  //   把「开始监听」往后推——探测在后台自己落地，页面首帧拿到的是结构 + `probesPending`。
  //   下面 `startSnapshotRefresher()` 的首拍是 `void run()`（fire-and-forget），**不得**把
  //   它当冷启动保证（它只负责后续每拍续命）；两者分工见 W4 的用例断言。
  await reconcileWatch()
  const reconcileTimer = setInterval(() => void reconcileWatch(), 15000)
  reconcileTimer.unref?.()
  // R4 自动爬梯 ticker（A3：console 服务端常驻；无状态推导，重启不丢进度；
  // 无 ladder 请求时 ladderTickAll 直接返回，零重扫描）。
  // ★ 2026-10-05（plan/dashboard-memory-evalboard-off R4/接线 c）：evalBoard 缺省停用 ⇒
  //   **不起 ticker** —— 否则只要有一门课挂过 ladder_start，它每 30s 就把整本账拉进内存。
  if (shouldStartLadderTicker()) {
    evalboardRouteCounters.ladderStarted += 1
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
  }
  // 慢部件快照后台刷新（§366：节点 ping/组件探测/池历史移出请求路径，页面加载 <1s）。
  // reconcileWatch 已冷算一次暖缓存；此后每 5s 后台重算，请求只读缓存。
  startSnapshotRefresher()
  // 课程指标备份库（plan/course-metrics-persistence §3.4）：启动首拍 + 60s 独立节拍。
  // 只读 tmp、写 dashboard/data/course-metrics；读侧回退不依赖它（备份存在就能读）。
  startCourseMetricsMirror()
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
  // 离线课补评（plan/offline-eval-backfill）：云机没开/没回传 eval 时，评估点 N 等该 run
  // 回传推进到 +3 轮仍无读数（收官轮立即）⇒ 用现成 evalA 链（LAN 节点 + 本机份额）补评；
  // 每拍至多一个（evalA 单槽互斥），启动先跑一拍。内部绝不抛，unref 不挡进程退出。
  const offlineBackfillTimer = setInterval(
    () => void runOfflineEvalBackfill(),
    OFFLINE_EVAL_BACKFILL_INTERVAL_MS,
  )
  offlineBackfillTimer.unref?.()
  void runOfflineEvalBackfill()

  const { BUNDLES } = await import('./build')

  // ────────────────────────── 路由表（Bun 原生 router，2026-10-25） ──────────────────────────
  // ★ 把三十多个 `url.pathname === ...` 的比较换成 `Bun.serve({ routes })`（Bun 1.4 原生
  //   router）：① 匹配在原生侧完成，静态路径不再逐条过 JS 判断链（每请求省的是微秒级，
  //   但控制台是 3s 一拍的轮询面，CPU 是白给的）；② 路径/方法/入口变成**声明式**
  //   （`route-table.ts` 是唯一事实源）——以前漏写一个 `if` 分支 = 该页/该接口静默 404，
  //   现在「表里有、handler 缺席」在启动期就抛（`buildRouteTable`）。
  //
  //   语义与旧 if 链逐条对齐：
  //     · 每条 handler 都过同一个 `guard`（markConsoleActivity → 只读门控 → 课程 sanitize
  //       → 异常 500），顺序与旧 `fetch` 头部一致；
  //     · 只登记**规范路径**；`/Metrics` `/metrics/` 这类宽松形态由 `/*` 兜底按 `pageForPath`
  //       归一化（Bun 的匹配是字面/大小写敏感的，而旧的 `pageForPath` 接受两者）；
  //     · 方法不匹配（例：POST `/`）不命中任何 GET handler ⇒ 落到 `/*` POST ⇒ 404「not found」，
  //       与旧实现一致；`/api/*` POST 兜底 = 旧的 `routeAction` 分支。
  type Ctx = { url: URL; viewCourse: string; loopback: boolean }
  type RouteFn = (req: Request, ctx: Ctx) => Response | Promise<Response>
  type RouteHandler = (req: Request, srv: Bun.Server<undefined>) => Promise<Response>

  const guard =
    (fn: RouteFn, opts: { activity?: boolean } = {}): RouteHandler =>
    async (req, srv) => {
      // 有人在看（2026-10-25）：刷新器的空闲闸判据就是这一行——没请求 = 没人开页，
      // 后台不该每 5s 出去探一轮（探针是主动出网，不是空转）。见 snapshot-refresher。
      // `activity:false` 只给「后台搬运」用（notebook 日志 POST）：5s 一条若点亮活动，
      // 会把「没人看就停刷」永久变成「有人看」（plan/notebook-userscript §4.6 / 评审 B8）。
      if (opts.activity !== false) markConsoleActivity()
      const url = new URL(req.url)
      try {
        // 只读门控：一切 POST 动作仅限回环来源（本机）；LAN 只能 GET 查看。
        // fail closed——requestIP 不可得（null）时视为非回环，动作被拒。
        // 判定抽在 net.ts::isReadonlyAction（纯函数，回归测试见 tests/server-lan-gate.test.ts）。
        if (isReadonlyAction(req.method, srv.requestIP(req)?.address)) {
          return json(
            {
              ok: false,
              message:
                '只读模式：动作仅限本机 localhost 执行（局域网可查看课程/日志/节点，不可启停/改配置）',
            },
            403,
          )
        }
        const loopback = isLoopbackAddress(srv.requestIP(req)?.address)
        // GET 只读课程覆盖（已 sanitize：真实课程 + 防路径穿越；空 = 自动/操作员课程）。
        const viewCourse = sanitizeViewCourse(url.searchParams.get('course'))
        return await fn(req, { url, viewCourse, loopback })
      } catch (e) {
        // 单行（把 stack 拆进文件会把「一行一决策」打散；定位靠 message + 请求行）
        error(
          `[console] ${req.method} ${url.pathname} failed: ` +
            (e instanceof Error ? e.message : String(e)),
        )
        return json({ ok: false, message: e instanceof Error ? e.message : String(e) }, 500)
      }
    }

  /** 只读视图标记：服务端按来源判定（客户端无权自封）——LAN 首屏 SSR 即渲染只读角标。 */
  const stampState = (
    s: Awaited<ReturnType<typeof buildStateView>>,
    loopback: boolean,
  ): typeof s => {
    s.readOnly = !loopback
    return s
  }

  // ── 页面族（同一 App + 服务端 SSR；路由真相只有一份 web/view/routes.ts）──
  const pageRoute: RouteFn = async (_req, { url, viewCourse, loopback }) => {
    const page = pageForPath(url.pathname)
    if (!page) return new Response('not found', { status: 404 })
    // 页面首帧：结构先上屏，探测背景补（首帧不等 1.2–2.5s 的探测；带 probesPending
    // ⇒ UI 画「探测中」而不是红「离线」）。见 state-view.BuildStateOptions。
    const state = stampState(
      await buildStateView(viewCourse || undefined, { firstPaint: true }),
      loopback,
    )
    // `css` = 现读的 theme.css（改样式不必重启；见 theme-css.ts）。
    return new Response(renderConsolePage(state, { page, css: livePageCss() }), {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
  }

  // ── /eval 独立评估页（?courses=a,b 可分享 URL；?course= 兼容）──
  const evalRoute: RouteFn = async (_req, { url, viewCourse, loopback }) => {
    const all = discoverCourses()
    const raw = url.searchParams.get('courses') ?? url.searchParams.get('course') ?? ''
    const selected = raw
      .split(',')
      .map((s) => s.trim())
      .filter((c) => c && all.includes(c))
    const eff = selected.length > 0 ? selected : viewCourse ? [viewCourse] : all.slice(0, 1)
    // R4（接线 b/d + W2′）：开关关 ⇒ 说明页（**不注入 /eval.js**，客户端不再打
    // /api/evalboard）；文案与 API 的 { disabled } 同源。
    const decision = evalboardPageDecision(eff)
    if (decision.kind === 'notice') {
      return new Response(renderEvalNoticePage(decision.message, { css: livePageCss() }), {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      })
    }
    const payload = {
      views: decision.views,
      options: { courses: eff, allCourses: all, readOnly: !loopback },
    }
    return new Response(renderEvalPage(payload, { css: livePageCss() }), {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
  }

  // ── /errors 组件错误登记表（独立页，第 4 份 bundle，照 /log 范式）──
  const errorsPageRoute: RouteFn = async (_req, { loopback }) => {
    const summary = getErrorSummary()
    const scannedAt = errorRegistryScannedAt()
    return new Response(
      renderErrorsPage(
        {
          items: summary.items,
          resolvedItems: buildErrorSummary(getResolvedErrors()).items,
          scannedAt,
        },
        { readOnly: !loopback, scannedAt },
        { css: livePageCss() },
      ),
      { headers: { 'Content-Type': 'text/html; charset=utf-8' } },
    )
  }

  // ── /log/<key> 日志页 SSR + 客户端 bundle（?lines= / ?follow=0）──
  const logPageRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const key = url.pathname.slice('/log/'.length) as Component
    const lines = parseLogLines(url.searchParams.get('lines'))
    // 虚拟键必须在 `as Component` / `componentLogPayload` **之前**分支（后者的 key 是联合类型）。
    const payload = isNotebookLogKey(key)
      ? notebookLogPayload(notebookLogsDir(), key, lines)
      : await componentLogPayload(key, lines, viewCourse || undefined)
    if (!payload) return new Response(`unknown component: ${key}`, { status: 404 })
    const state = await buildStateView(viewCourse || undefined, { firstPaint: true })
    const follow = url.searchParams.get('follow') !== '0'
    const components = state.components.map((c) => ({
      key: c.key,
      label: c.label,
      status: c.status,
    }))
    if (isNotebookLogKey(key)) {
      const entry = notebookNavEntry(notebookLogsDir(), key)
      if (entry) components.push(entry)
    }
    return new Response(
      renderLogPage(
        payload,
        { components, follow, lines, course: viewCourse || undefined },
        { css: livePageCss() },
      ),
      { headers: { 'Content-Type': 'text/html; charset=utf-8' } },
    )
  }

  // ── GET 数据面 ──
  const stateRoute: RouteFn = async (_req, { viewCourse, loopback }) => {
    return json(stampState(await buildStateView(viewCourse || undefined), loopback))
  }

  const poolRoute: RouteFn = async (_req, { url }) => {
    const fresh = url.searchParams.get('fresh') === '1'
    // ★ 2026-09-26：`?days=` = 本地日窗口（today/yesterday/7/all/N，缺省 today）；池视图与
    // 课程无关，`?course=` 已移除（plan/nodes-decouple-from-course.plan.md）。
    const days = url.searchParams.get('days') ?? 'today'
    return json(await buildPoolView(fresh, days))
  }

  // 逐调用计数器只读视图（plan/dashboard-pool-history-idle-cost P1-3）：空置内存取证
  // ⇒ 隔一段取两次，`computes`/`fullRescans`/`bytesRead` 不得持续增长。纯读，不触发聚合。
  const poolCountersRoute: RouteFn = async () => json(poolCountersView())

  // 内存读数（2026-10-17）：控制台 RSS 锯齿时的**取证口**——「1.5GB 的提交里有多少是
  // 活对象」没有它只能靠任务管理器猜。只读；`?gc=1` 额外跑一次**强制 GC**；
  // `?snapshot=1` 写一份 V8 格式堆快照到 tmp/。
  const memRoute: RouteFn = async (_req, { url }) => {
    const forced = url.searchParams.get('gc') === '1'
    if (forced) Bun.gc(true)
    if (url.searchParams.get('snapshot') === '1') {
      const snapPath = path.join(tmpLogsDir(), `bun-heap-${Date.now()}.heapsnapshot`)
      await Bun.write(snapPath, Bun.generateHeapSnapshot('v8'))
      const mm = process.memoryUsage()
      return json({ snapshot: snapPath, rss: mm.rss, heapUsed: mm.heapUsed })
    }
    const m = process.memoryUsage()
    return json({
      rss: m.rss,
      heapUsed: m.heapUsed,
      heapTotal: m.heapTotal,
      external: m.external,
      gcForced: forced,
      pool: poolCountersView(),
    })
  }

  const evalboardRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const fresh = url.searchParams.get('fresh') === '1'
    // R4（接线 b）：开关关 ⇒ routes 直接给 disabled 形状，不调 buildEvalBoardView。
    return json(evalboardApiPayload(viewCourse || undefined, fresh))
  }

  // BC epoch 指标 / 多地图 eval（2026-09-13；bcRowsFromLedgerTail 解析，只读）。
  const bcEpochsRoute: RouteFn = async (_req, { viewCourse }) =>
    json(await buildBcEpochsView(viewCourse || undefined))

  // 备份库状态（plan/course-metrics-persistence §3.6）：热/冷逐课 + 冷存 memo 占用。
  const courseMetricsRoute: RouteFn = async () => json(courseMetricsStatusPayload())

  // R7：ckpt/iter 发现（只 stat 不读内容；?leg= 懒加载单腿明细）。
  const evalCkptsRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const leg = url.searchParams.get('leg') ?? ''
    return json(buildEvalCkptsView(viewCourse || undefined, leg))
  }

  // I5（roadmap v2.0）：阶梯统一 identity 台账 LAN 只读渲染（与 God-AI evalboard ladder 无关）。
  const curriculumLadderRoute: RouteFn = async () => json(curriculumLadderView())

  // 导出 replay：最新 in-loop eval 逐局视图 / 轮选择器 / 导出任务态 / 单局 .replay 下载。
  const evalGamesRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const iter = parseEvalIterParam(url.searchParams.get('iter'))
    if (iter === null) return json({ ok: false, message: '?iter= 须为整数' }, 400)
    return json(buildEvalGamesView(viewCourse || '', iter))
  }

  const evalRoundsRoute: RouteFn = async (_req, { viewCourse }) =>
    json(buildEvalRoundsView(viewCourse || ''))

  // 比较课程（plan/dashboard-compare-trends.plan.md）：跨课程指标对比的数据面。
  // 弹窗打开期间自持轮询（节奏 = 顶栏全局间隔）——不进 /api/state 的 3s 路径。
  const compareTrendsRoute: RouteFn = async (_req, { url }) => {
    const parsed = parseCompareQuery({
      courses: url.searchParams.get('courses'),
      metric: url.searchParams.get('metric'),
      source: url.searchParams.get('source'),
      from: url.searchParams.get('from'),
      to: url.searchParams.get('to'),
    })
    if (!parsed.ok) return json({ ok: false, message: parsed.message }, 400)
    return json(await buildCompareTrendsView(parsed.params))
  }

  const evalReplayJobRoute: RouteFn = async (_req, { viewCourse }) => {
    // F4 闭合（串话归因，服务端路由）：此接口返回的 manifest = POST /api/replayExport 受理成功后
    // 落盘的 manifest，而不再是「最近一次导出的 manifest」。旧弹窗用法（此接口做轮选择）已废除
    // ——轮选择走 GET /api/evalRounds。
    return json(buildEvalReplayJobView(viewCourse || ''))
  }

  const evalReplayFileRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const file = url.searchParams.get('file') ?? ''
    return (
      (await evalReplayFileResponse(viewCourse || '', file)) ??
      json({ ok: false, message: '缺少 course' }, 400)
    )
  }

  // 日志载荷（日志页 2s/4s 轮询）。
  const apiLogRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const key = url.pathname.slice('/api/log/'.length) as Component
    const lines = parseLogLines(url.searchParams.get('lines'))
    const payload = isNotebookLogKey(key)
      ? notebookLogPayload(notebookLogsDir(), key, lines)
      : await componentLogPayload(key, lines, viewCourse || undefined)
    return payload ? json(payload) : json({ ok: false, message: `未知组件: ${key}` }, 404)
  }

  // ── notebook 伴生脚本（plan/notebook-userscript §4.5）──
  // 搬运不算「有人在看」：guard activity:false（否则 5s 一条永久点亮空闲闸）。
  // body 在 JSON.parse 前有界读（413 零副作用），自定义头挡跨源网页（无 CORS 头）。
  const notebookLogRoute: RouteFn = async (req) => {
    if (req.headers.get('X-Notebook-Companion') !== '1') {
      return json(
        { ok: false, reason: '缺少 X-Notebook-Companion: 1（跨源网页发不了这个头）' },
        403,
      )
    }
    if (!(req.headers.get('content-type') ?? '').toLowerCase().includes('application/json')) {
      return json({ ok: false, reason: 'Content-Type 必须是 application/json' }, 400)
    }
    const bounded = await readBoundedJson(req)
    if (!bounded.ok) return json({ ok: false, reason: bounded.reason }, bounded.status)
    const parsed = parseNotebookChunk(bounded.value)
    if (!parsed.ok) return json({ ok: false, reason: parsed.reason }, parsed.status)
    const result = appendNotebookChunk(notebookLogsDir(), parsed.chunk, Date.now())
    return json(result.body, result.status)
  }

  const notebookLogsRoute: RouteFn = async () =>
    json({ ok: true, sessions: listNotebookSessions(notebookLogsDir()) })

  // 组件错误登记表（plan/component-error-registry §4.6）：列表投影（每 4s 轮询）+ `?fp=` 单条 detail。
  // ★ 两条都只读登记表的 **SWR 缓存值**——探测只在 `snapshot-refresher` 的 10s 节拍上，
  //   请求路径零网络（N9 / R1 结构闸）。
  const errorsApiRoute: RouteFn = async (_req, { url }) => {
    const fp = url.searchParams.get('fp')
    if (fp) {
      const ev = findErrorEvent(fp)
      if (!ev) return json({ ok: false, message: '未知 fp（已被清理或不在本拍登记表里）' }, 404)
      return json({
        fp: ev.fp,
        component: ev.component,
        message: ev.message,
        detail: ev.detail,
        ref: ev.ref,
        classification: ev.classification,
        activity: ev.activity,
      })
    }
    const summary = getErrorSummary()
    return json({
      items: summary.items,
      resolvedItems: buildErrorSummary(getResolvedErrors()).items,
      cursor: errorRegistryScannedAt(),
    })
  }

  // 任务包信息（5s 轮询）：把上次导出的**成败**一起递出去（四态分开，见下）。
  const taskBundleInfoRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const course = url.searchParams.get('course') || viewCourse || ''
    if (!course) return json({ ok: false, message: '缺少 course' }, 400)
    const info = taskBundleInfo(course)
    const st = readTaskBundleStatus(course)
    return json({
      ok: true,
      exists: info.exists,
      name: info.name,
      bytes: info.bytes,
      mtimeMs: info.mtimeMs,
      log: path.relative(REPO_ROOT, info.log).replace(/\\/g, '/'),
      // ★ 2026-10-15（J3）：四态必须分开：`status === null` = 不可知（还没跑完 / 被 kill），
      //   **不是**成功；`running/stale/expired` 由 `readTaskBundleStatus` 统一收口（同一读面）。
      statusLine: exportStatusLine(course),
      status: st && {
        ok: st.ok,
        running: st.running,
        stale: st.stale,
        expired: st.expired,
        reason: st.reason,
        at: st.at,
        pid: st.pid,
        elapsedSec: st.elapsedSec,
      },
    })
  }

  // 任务包下载（`content-disposition: task-<课程>.zip`）
  const taskBundleRoute: RouteFn = async (_req, { url, viewCourse }) => {
    const course = url.searchParams.get('course') || viewCourse || ''
    const resp = course ? taskBundleDownloadResponse(course) : null
    return resp ?? json({ ok: false, message: '还没有导出过任务包（先点「导出任务包」）' }, 404)
  }

  // ── POST ──
  // 产物导入（multipart 上传 + 自动评估）：体积是 zip，不走 JSON 动作层。
  const deliverUploadRoute: RouteFn = async (req, { viewCourse }) => {
    const resp = await handleDeliverUpload(req, viewCourse || '')
    // 与其它动作同一个处置：**课程级硬清 + 机群级软作废**。
    // ★ 曾经这里是硬清（理由写的是「换了盘上的东西，先给旧值没有必要」）——但硬清会连
    //   **机群级探测**（节点 ping 1.5s / 共享 hub 2.5s）一起丢掉，而导入一个 zip 一点都
    //   改不了「哪些节点在线」，于是导入后的下一次 /api/state 反而要冷算这几笔（本机实测
    //   1575ms 冷 vs 6ms 暖）——「动作后首帧又卡几秒」的第三个出入口。
    invalidateAfterAction()
    return resp
  }

  // 动作兜底：`/api/<act>` 一律进 actions 层（busy 互斥在那一层）；本层只做 HTTP 语义映射。
  const actionRoute: RouteFn = async (req, { url }) => {
    const act = url.pathname.slice('/api/'.length)
    let body: Record<string, unknown> = {}
    try {
      body = (await req.json()) as Record<string, unknown>
    } catch {
      /* empty body — actions that need params will 400 */
    }
    const resp = await routeAction(act, body)
    // 动作改动组件/节点/课程 → 一次缓存处置（`api.invalidateAfterAction`）。
    // **课程级硬清、机群级软作废**——动作后的第一帧也不冷算：结构（组件 running/stopped、
    // 节点行的 enabled/url、worker 行、执行面 mode、暂停意图/生效回执）是现算的，动作结果
    // 照样即时上屏；被软作废的只是探测列与调度器视图（ping / hub 应答 / 每课在等什么），
    // 读路径先给旧值、重算丢后台（分工与理由见 snapshot-refresher.invalidateAfterAction）。
    //
    // ★ 视图态动作（setCourse / getGateHaltMode）**不作废**：它们不碰任何组件/节点事实，
    //   而 setCourse 正是「切课」自己发的动作——一律作废 = 每切一次课都把机群级探测
    //   （节点 ping 1.5s + 共享 hub 1.2s）丢掉重做，「切课要等几秒」有一半是它自己造的
    //   （判据 = route.ts 的 VIEW_ONLY_ACTIONS/invalidatesSnapshot，门禁用例钉住）。
    if (resp && invalidatesSnapshot(act)) invalidateAfterAction()
    return resp ?? json({ ok: false, message: `未知动作: ${act}` }, 404)
  }

  // 兜底：① 非规范路径的宽松页面解析（`/Metrics` `/metrics/` ⇒ 归一化后仍是那一页，
  // 与旧 `pageForPath` 语义一致——Bun 的路由匹配是字面/大小写敏感的）；
  // ② 其余一律 404（含 POST 到非 `/api/*` 的路径，与旧实现的最后一行一致）。
  const fallbackRoute: RouteFn = async (req, ctx) => {
    if (req.method === 'GET' && pageForPath(ctx.url.pathname)) return pageRoute(req, ctx)
    return new Response('not found', { status: 404 })
  }

  const routeHandlers: RouteHandlers<RouteHandler> = {
    '/': { GET: guard(pageRoute) },
    '/console': { GET: guard(pageRoute) },
    '/metrics': { GET: guard(pageRoute) },
    '/nodes': { GET: guard(pageRoute) },
    '/wire': { GET: guard(pageRoute) },
    '/courses': { GET: guard(pageRoute) },
    '/eval': { GET: guard(evalRoute) },
    '/errors': { GET: guard(errorsPageRoute) },
    '/api/state': { GET: guard(stateRoute) },
    '/api/pool': { GET: guard(poolRoute) },
    '/api/poolCounters': { GET: guard(poolCountersRoute) },
    '/api/mem': { GET: guard(memRoute) },
    '/api/evalboard': { GET: guard(evalboardRoute) },
    '/api/bcEpochs': { GET: guard(bcEpochsRoute) },
    '/api/courseMetrics': { GET: guard(courseMetricsRoute) },
    '/api/evalCkpts': { GET: guard(evalCkptsRoute) },
    '/api/curriculumLadder': { GET: guard(curriculumLadderRoute) },
    '/api/evalGames': { GET: guard(evalGamesRoute) },
    '/api/evalRounds': { GET: guard(evalRoundsRoute) },
    '/api/compareTrends': { GET: guard(compareTrendsRoute) },
    '/api/evalReplayJob': { GET: guard(evalReplayJobRoute) },
    '/api/evalReplayFile': { GET: guard(evalReplayFileRoute) },
    '/api/errors': { GET: guard(errorsApiRoute) },
    '/api/taskBundleInfo': { GET: guard(taskBundleInfoRoute) },
    '/api/taskBundle': { GET: guard(taskBundleRoute) },
    // 静态先于通配：两条都必须在 `/api/log/*` 之前（且 `/api/notebookLog` 是第二个 typed POST）。
    '/api/notebookLogs': { GET: guard(notebookLogsRoute) },
    '/api/notebookLog': { POST: guard(notebookLogRoute, { activity: false }) },
    '/api/log/*': { GET: guard(apiLogRoute) },
    '/log/*': { GET: guard(logPageRoute) },
    '/api/deliverUpload': { POST: guard(deliverUploadRoute) },
    '/api/*': { POST: guard(actionRoute) },
    '/*': { GET: guard(fallbackRoute), POST: guard(fallbackRoute) },
  }

  // 客户端 bundle（app.js / log.js；mtime 失效自动重建）——路径由构建产物决定，
  // 不进静态路由表（表是给人读的契约；这些是构建期才知道的动态路径）。
  const bundleRoutes: Record<string, Partial<Record<'GET', RouteHandler>>> = {}
  for (const b of BUNDLES) {
    bundleRoutes[`/${b.key}.js`] = { GET: guard(async () => serveBundle(b)) }
  }

  const server = Bun.serve({
    // 局域网只读边界：绑 0.0.0.0 让局域网可访问，但动作（杀进程/改配置）经 `guard` 的
    // 回环门控只放行本机 localhost；无鉴权的前提是动作面绝不暴露给局域网。
    hostname: '0.0.0.0',
    port,
    routes: { ...buildRouteTable(CONSOLE_ROUTES, routeHandlers), ...bundleRoutes },
    // 兜底（正常情况下 `/*` 已经吃掉全部请求；这里只防「路由表外的协议形态」，
    // 例如带绝对形式的 request-target）。语义 = 与 `/*` 相同的宽松解析 + 404。
    fetch: guard(fallbackRoute),
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
