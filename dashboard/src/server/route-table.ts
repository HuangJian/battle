/**
 * route-table.ts — 控制台 HTTP 路由表（**唯一事实源**；server.ts 据此生成 `Bun.serve({ routes })`）。
 *
 *  为什么单独成模块（2026-10-25，Bun 1.4 原生 router）：
 *    ① `server.ts` 一 import 就 `void main()`（起进程），路由表没法在单测里被**执行**；
 *       抽到这里后 `tests/server-route-table.test.ts` 能用**同一张表**起一个桩服务器，
 *       直接验证「哪个方法 + 哪条路径落到哪个 handler」（Bun 的优先级：静态 > 参数 > 通配）；
 *    ② 「表里有、handler 缺席」从**静默 404** 变成**启动期抛错**（`buildRouteTable`）——
 *       这正是 if 链时代最难发现的一类退化（漏一个 `if` 分支 = 那页/那接口永远 404，
 *       只有用户跑到才发现）。
 *
 *  设计约束（与旧 if 链逐条等价，见 server.ts 的接线）：
 *    · 只登记**规范路径**。`/Metrics`、`/metrics/` 这类宽松形态不在这里——它们由 `/*`
 *      兜底按 `pageForPath` 归一化（Bun 的匹配是字面/大小写敏感的，而旧的
 *      `pageForPath` 接受大小写与尾斜杠；直接不登记才不用维护两份归一化）；
 *    · 方法按旧语义**逐条声明**：页面/GET 接口只登记 GET（POST `/` 仍是 404），
 *      动作面是 `/api/*` 的 POST 兜底（任何 `/api/<act>` 都进 `routeAction`）；
 *    · `/*` 兜底同时接 GET 与 POST：GET 做宽松页面解析与 404，POST 做非 API 的 404；
 *    · **HEAD 与 GET 同路由**（Bun 原生语义：HEAD = GET 的无体形态）。旧 if 链把 HEAD
 *      当未知方法 ⇒ 404；现在是标准 HTTP 行为，属**有意收下的偏差**（路由测试钉住）。
 *
 *  排序纪律：静态路径在前、通配在后（Bun 自己按优先级排序，这张表的顺序是给**人**读的；
 *  但 `buildRouteTable` 会拒绝同一 method 的重复登记，避免「先注册者赢」的隐式语义）。
 */

/** 控制台用到的两种方法（没有 PUT/DELETE/PATCH；动作面一律 POST）。 */
export type RouteMethod = 'GET' | 'POST'

export interface ConsoleRoute {
  /** Bun 路由模式（支持 `/*` 通配；精确路径区分大小写）。 */
  path: string
  /** 该路径允许的方法（未列出的方法 ⇒ 不命中 ⇒ 404）。 */
  methods: readonly RouteMethod[]
}

/**
 * 全部路由（顺序 = 文档顺序：页面族 → 页面级独立页 → GET API → 通配 API → 动作兜底 → 兜底）。
 *
 * ⚠ 新增端点时**只改这里 + server.ts 的 handler 表**（键必须与 `path` 逐字一致，否则
 *   `buildRouteTable` 在启动期抛「没有 handler」）。
 */
export const CONSOLE_ROUTES: readonly ConsoleRoute[] = [
  // ── 控制台页面族（同一 App + 服务端 SSR，见 web/view/routes.ts）──
  { path: '/', methods: ['GET'] },
  { path: '/console', methods: ['GET'] }, // `/` 的历史别名
  { path: '/metrics', methods: ['GET'] },
  { path: '/nodes', methods: ['GET'] },
  { path: '/wire', methods: ['GET'] },
  { path: '/courses', methods: ['GET'] },
  // ── 独立页（各自 bundle，整页 SSR）──
  { path: '/eval', methods: ['GET'] },
  { path: '/errors', methods: ['GET'] },
  // ── GET 数据面（顺序与旧 if 链一致）──
  { path: '/api/state', methods: ['GET'] },
  { path: '/api/pool', methods: ['GET'] },
  { path: '/api/poolCounters', methods: ['GET'] },
  { path: '/api/mem', methods: ['GET'] },
  { path: '/api/evalboard', methods: ['GET'] },
  { path: '/api/bcEpochs', methods: ['GET'] },
  { path: '/api/courseMetrics', methods: ['GET'] },
  { path: '/api/evalCkpts', methods: ['GET'] },
  { path: '/api/curriculumLadder', methods: ['GET'] },
  { path: '/api/evalGames', methods: ['GET'] },
  { path: '/api/evalRounds', methods: ['GET'] },
  { path: '/api/compareTrends', methods: ['GET'] },
  { path: '/api/evalReplayJob', methods: ['GET'] },
  { path: '/api/evalReplayFile', methods: ['GET'] },
  { path: '/api/errors', methods: ['GET'] },
  { path: '/api/taskBundleInfo', methods: ['GET'] },
  { path: '/api/taskBundle', methods: ['GET'] },
  // 产物导入（multipart）：静态路径，仍是 POST——排在通配之前（静态 > 通配）。
  { path: '/api/deliverUpload', methods: ['POST'] },
  // notebook 伴生脚本（plan/notebook-userscript §4.5）：第二个 typed POST（自定义头 + 有界 body）；
  // 会话清单仍是 GET。两条都必须排在 `/api/log/*` 之前（静态先于通配）。
  { path: '/api/notebookLogs', methods: ['GET'] },
  { path: '/api/notebookLog', methods: ['POST'] },
  // ── 通配（日志载荷 + 整页；`*` 段由 handler 从 pathname 里切）──
  { path: '/api/log/*', methods: ['GET'] },
  { path: '/log/*', methods: ['GET'] },
  // 一切 `/api/<act>`：动作兜底（静态 `/api/*` 端点已在上面登记，优先级由 Bun 保证）
  { path: '/api/*', methods: ['POST'] },
  // ── 兜底（宽松页面解析 / 非 API 的 404）──
  { path: '/*', methods: ['GET', 'POST'] },
]

/** handler 表：键 = `ConsoleRoute.path`，值 = 该方法对应的处理函数。 */
export type RouteHandlers<H> = Record<string, Partial<Record<RouteMethod, H>> | undefined>

/**
 * 按表生成 Bun 的 `routes` 结构，并在**启动期**校验「表里有 ⇒ handler 必有」。
 *
 * 抛错是刻意的：漏接一个端点属于配置错误（旧 if 链时代它会静默变成 404），
 * 宁可控制台起不来，也不要带着一个永远 404 的入口跑。
 */
export function buildRouteTable<H>(
  routes: readonly ConsoleRoute[] = CONSOLE_ROUTES,
  handlers: RouteHandlers<H>,
): Record<string, Partial<Record<RouteMethod, H>>> {
  const out: Record<string, Partial<Record<RouteMethod, H>>> = {}
  for (const r of routes) {
    const byMethod = handlers[r.path]
    if (!byMethod) throw new Error(`[routes] ${r.path} 没有 handler（路由表与 handler 表漂移）`)
    if (out[r.path]) throw new Error(`[routes] ${r.path} 重复登记`)
    const entry: Partial<Record<RouteMethod, H>> = {}
    for (const m of r.methods) {
      const fn = byMethod[m]
      if (!fn) throw new Error(`[routes] ${r.path} ${m} 没有 handler`)
      entry[m] = fn
    }
    out[r.path] = entry
  }
  return out
}
