/** routes.ts — 控制台路由真相（**唯一事实源**）：路径 ↔ 页面键、导航项、页面元信息。
 *
 *  为什么放在 view 层：服务端 SSR（`server.ts` 决定渲染哪一页）与浏览器（pushState
 *  导航）必须用同一份映射。两处各写一份路由表迟早漂移，而症状是「直接输 URL / 刷新后
 *  看到的不是同一页」——最难在测试里被发现的一类不一致。本模块只含类型与纯函数，
 *  客户端安全（可在浏览器 bundle 内使用，见 `src/server/build.ts` 禁词门禁）。
 *
 *  设计依据：`docs/dashboard-redesign.md` §3.2 路由表 / §5.1 导航与 URL。
 *
 *  形态分两类（`NavItem.kind`）：
 *    - `route` — 本 bundle 内可切换的页面（/ · /metrics · /nodes · /wire），
 *      点击拦截为 pushState，不整页 reload。
 *    - `link`  — 独立成页的入口（/eval · /log/<key>，各自 bundle），真链接，
 *      可中键/右键开新标签。
 */

import type { ConsoleStateView } from './console-types'

// ────────────────────────── 类型 ──────────────────────────

/** 本 bundle 内的页面键（/eval 与 /log 是独立页，不在此列）。 */
export type PageKey = 'overview' | 'metrics' | 'nodes' | 'wire'

/** 侧栏分组 id（渲染顺序 = 数组顺序）。 */
export type NavGroupId = 'monitor' | 'infra'

export interface PageMeta {
  key: PageKey
  /** 页面标题（Topbar 主标题 + 侧栏标签的完整形态）。 */
  title: string
  /** 这一页回答的问题（Topbar 副标题；"一页一个问题"的可视化契约）。 */
  desc: string
  /** 规范路径（canonical URL，导航跳转与 URL 归一化都用它）。 */
  path: string
}

export interface NavItem {
  /** 唯一 id（页面键，或独立页的固定名：`eval` / `log`）。 */
  id: string
  label: string
  /** 跳转目标（route 项为规范路径；link 项为独立页路径）。 */
  href: string
  /** 悬停说明（比 label 多一句「这里能做什么」）。 */
  title: string
  /** 单字符图标（零依赖纪律：不引图标库，用几何符号）。 */
  icon: string
  group: NavGroupId
  kind: 'route' | 'link'
  /** kind === 'route' 时为对应的页面键。 */
  page?: PageKey
}

// ────────────────────────── 页面表 ──────────────────────────

/** 页面元信息（顺序 = Topbar 无关；渲染顺序由 NAV_GROUPS/NAV_ITEMS 决定）。 */
export const PAGES: Readonly<Record<PageKey, PageMeta>> = {
  overview: {
    key: 'overview',
    title: '总览',
    desc: '现在能不能跑？这轮跑到哪了？',
    path: '/',
  },
  metrics: {
    key: 'metrics',
    title: '指标',
    desc: '练得好不好？趋势如何？',
    path: '/metrics',
  },
  nodes: {
    key: 'nodes',
    title: '节点',
    desc: '算力在哪、通不通、贡献多少？',
    path: '/nodes',
  },
  wire: {
    key: 'wire',
    title: '传输',
    desc: '每轮实发/实收多少？隧道哪条快？',
    path: '/wire',
  },
}

/** 默认页面（未知/空路径的落点）。 */
export const DEFAULT_PAGE: PageKey = 'overview'

/** 侧栏「日志」项的默认落点组件（trainer 是日常最需要看的一个）。 */
export const DEFAULT_LOG_COMPONENT = 'trainingLoop'

export const NAV_GROUPS: ReadonlyArray<{ id: NavGroupId; title: string }> = [
  { id: 'monitor', title: '监控' },
  { id: 'infra', title: '基础设施' },
]

/** 侧栏导航项（分两组）。 */
export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: 'overview',
    label: '总览',
    href: '/',
    title: '状态与控制：告警 · KPI · 趋势 · 课程 · 最新轮次',
    icon: '▦',
    group: 'monitor',
    kind: 'route',
    page: 'overview',
  },
  {
    id: 'metrics',
    label: '指标',
    href: '/metrics',
    title: '完整指标表（全部轮次，可搜索/排序）+ eval 明细与口径说明',
    icon: '▤',
    group: 'monitor',
    kind: 'route',
    page: 'metrics',
  },
  {
    id: 'eval',
    label: '评估',
    href: '/eval',
    title: 'EvalBoard：阶梯 / iter 矩阵 / 批次台账（独立页）',
    icon: '◎',
    group: 'monitor',
    kind: 'link',
  },
  {
    id: 'nodes',
    label: '节点',
    href: '/nodes',
    title: '算力供给：节点统计 · 贡献历史 · worker 登记',
    icon: '◈',
    group: 'infra',
    kind: 'route',
    page: 'nodes',
  },
  {
    id: 'wire',
    label: '传输',
    href: '/wire',
    title: '每轮实发/实收字节与秒 + 隧道 A/B 探针',
    icon: '↕',
    group: 'infra',
    kind: 'route',
    page: 'wire',
  },
  {
    id: 'log',
    label: '日志',
    href: `/log/${DEFAULT_LOG_COMPONENT}`,
    title: '组件日志（独立页；页内可切其它组件）',
    icon: '≡',
    group: 'infra',
    kind: 'link',
  },
]

/** 控制台首屏引导载荷（SSR 内联到 `window.__INITIAL__`）：状态快照 + **服务端判定的页面**。
 *
 *  为什么 `page` 要服务端给：首帧必须直接渲染正确路由，否则 hydrate 后要再切一次视图
 *  （「先画总览再跳指标」的闪跳），而首帧读 `location` 在 SSR 期不存在。
 *  `/api/state` 的 JSON 不含 `page`（它不是状态）——客户端缺失时回退当前 URL 判定。 */
export interface ConsoleBootstrap extends ConsoleStateView {
  page?: PageKey
}

// ────────────────────────── 纯函数 ──────────────────────────

/** 解析引导载荷里的页面键：非法/缺失 → 按当前 URL 判定 → 兜底总览。
 *
 *  三层回退的理由：`page` 缺失（旧缓存页 / 直接消费 /api/state）、URL 不是本 bundle 内的页
 *  （如 /eval）都要有确定结果——路由解析绝不允许返回 undefined 让 UI 自行决定。 */
export function bootstrapPage(
  init: { page?: unknown } | null | undefined,
  pathname: string,
): PageKey {
  const p = init?.page
  if (p === 'overview' || p === 'metrics' || p === 'nodes' || p === 'wire') return p
  return pageForPath(pathname) ?? DEFAULT_PAGE
}

/** 路径归一化：小写 + 去尾部斜杠（根路径保持 `/`）+ 折叠重复斜杠。
 *
 *  为什么小写：手工输入 `/Metrics` 不该 404；控制台是内部工具，路径大小写不承载语义。 */
export function normalizePath(pathname: string): string {
  if (!pathname) return '/'
  const lower = pathname.toLowerCase()
  const folded = lower.replace(/\/{2,}/g, '/')
  const trimmed = folded.replace(/\/+$/, '')
  return trimmed === '' ? '/' : trimmed
}

/** 路径 → 页面键（不在本 bundle 内的一律 null）。`/metrics/` 与 `/Metrics` 都归一到 metrics。 */
export function pageForPath(pathname: string): PageKey | null {
  const p = normalizePath(pathname)
  // `/console` 是 `/` 的历史别名（server.ts 两个 pathname 都服务控制台首页）。
  if (p === '/' || p === '/console') return 'overview'
  if (p === '/metrics') return 'metrics'
  if (p === '/nodes') return 'nodes'
  if (p === '/wire') return 'wire'
  return null
}

/** 页面键 → 规范路径。 */
export function canonicalPath(page: PageKey): string {
  return PAGES[page].path
}

/** 给内部链接附加当前查看课程（`?course=`）：导航后不丢「我在看哪门课」。 */
export function withCourse(href: string, course: string | null | undefined): string {
  if (!course) return href
  const sep = href.includes('?') ? '&' : '?'
  return `${href}${sep}course=${encodeURIComponent(course)}`
}

/** 导航项在给定路径下是否处于激活态。
 *
 *  - `route` 项：按页面键比较（`/metrics/` 也点亮「指标」）。
 *  - `link` 项：按路径前缀比较（`/log/xxx` 点亮「日志」；`/eval` 只点亮「评估」）。
 */
export function isNavActive(item: NavItem, pathname: string): boolean {
  const p = normalizePath(pathname)
  if (item.kind === 'route') {
    return item.page != null && pageForPath(p) === item.page
  }
  if (item.href.startsWith('/log')) return p === '/log' || p.startsWith('/log/')
  return p === item.href || p.startsWith(`${item.href}/`)
}
