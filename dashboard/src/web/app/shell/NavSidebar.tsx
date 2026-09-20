/** NavSidebar.tsx — 侧栏的**导航部分**（品牌 + 分组导航 + 底部插槽）：全部页面共用。
 *
 *  为什么单独抽出来（docs/dashboard-redesign.md §6 P3）：控制台、/eval、/log 是三个**独立
 *  bundle**，但它们是同一个产品的三页——导航必须在三处长得一模一样、包含同一组条目、用同一
 *  套激活判定。此前的做法是 /eval 与 /log 头部各放一个「返回控制台」链接（进去就出不来，
 *  想去节点页得先回总览），那是**导航断层**：从侧栏点进「评估」后，侧栏连同它的六个入口一起
 *  消失了。
 *
 *  分工（与 Sidebar.tsx 的边界）：
 *    - 本组件只说**去哪**（导航 + 当前查看课程带来的 `?course=` 透传 + 只读标识）。
 *    - 控制台专有的「当前课程选择器 / 触发门禁 / 刷新间隔」留在 `Sidebar.tsx`，由 `footer` 插槽注入。
 *    独立页没有这些全局设置的所有权（它们各自的轮询节奏是页面自己的），所以不传 footer。
 *
 *  零依赖图标：`NavItem.icon` 是单字符几何符号（不引图标库，§9 非目标）。
 */

import type { ComponentChildren, JSX } from 'preact'
import { isNavActive, NAV_GROUPS, NAV_ITEMS, withCourse, type PageKey } from '../../view'

export interface NavSidebarProps {
  /** 导航激活判定的路径 = 当前页面的规范路径（`canonicalPath(page)`）。
   *
   *  为什么不读 `location.pathname`：SSR 期不存在 `location`，首帧读它会与客户端不一致
   *  → hydrate 错配。由页面键推出规范路径后，两侧首帧同为 `/` → `/metrics`。 */
  activePath: string
  /** 当前查看课程（缺省/空串 = 不附加 `?course=`）。 */
  course?: string
  /** 局域网只读（服务端 stamp）：锁徽标常驻。独立页暂无此 stamp → 缺省不显示。 */
  readOnly?: boolean
  /** 侧栏底部区块（控制台：当前课程 + 全局设置）。独立页不传。 */
  footer?: ComponentChildren
  /** `route` 类导航项的点击（拦截为 pushState）。**不传 = 原生跳转**。
   *
   *  为什么这是可选的：客户端 pushState 只能在**同一个 bundle** 的路由表内切页。独立页
   *  （/eval · /log）点击控制台四页必须走浏览器真跳转，否则 hydrate 后的路由表里根本没有
   *  那个页面键，拦截等于白屏。 */
  onNavigate?: (page: PageKey, e: JSX.TargetedMouseEvent<HTMLAnchorElement>) => void
}

export function NavSidebar({
  activePath,
  course = '',
  readOnly = false,
  footer,
  onNavigate,
}: NavSidebarProps) {
  return (
    <aside className="tc-side" aria-label="控制台导航">
      <div className="tc-side__brand">
        <span className="tc-side__logo" aria-hidden="true" />
        <span className="tc-side__name">炼丹炉</span>
        {readOnly ? (
          <span
            className="tc-lock"
            title="局域网只读：可查看任意课程/日志/节点统计；启停、冒烟、模式开关与节点编辑仅在本机 localhost 打开控制台时可用"
          >
            🔒 只读
          </span>
        ) : null}
      </div>

      <nav className="tc-side__nav" aria-label="主导航">
        {NAV_GROUPS.map((g) => (
          <div className="tc-side__group" key={g.id}>
            <span className="tc-side__glabel">{g.title}</span>
            {NAV_ITEMS.filter((n) => n.group === g.id).map((n) => {
              const active = isNavActive(n, activePath)
              const href = withCourse(n.href, course)
              return (
                <a
                  key={n.id}
                  className="tc-nav"
                  href={href}
                  title={n.title}
                  aria-current={active ? 'page' : undefined}
                  onClick={
                    onNavigate && n.kind === 'route' && n.page
                      ? (e) => onNavigate(n.page as PageKey, e)
                      : undefined
                  }
                >
                  <span className="tc-nav__icon" aria-hidden="true">
                    {n.icon}
                  </span>
                  <span className="tc-nav__label">{n.label}</span>
                </a>
              )
            })}
          </div>
        ))}
      </nav>

      <div className="tc-side__spacer" />

      {footer}
    </aside>
  )
}
