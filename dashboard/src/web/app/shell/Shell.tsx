/** Shell.tsx — 应用外壳（侧栏 + 顶栏 + 主内容）：全部页面共用的三区骨架。
 *
 *  依据 docs/dashboard-redesign.md §3.1。三档响应式的实现全在 CSS 里
 *  （`theme.css` 的「应用外壳」段），本组件只负责 DOM 结构——布局不允许在这里分叉。
 *
 *  为什么收 **节点**（`ComponentChildren`）而不是 `SidebarProps`/`TopbarProps`：外壳是
 *  **纯布局**，状态与动作的所有权留在各页 App（单一数据源）。三个 bundle 的侧栏/顶栏
 *  数据形状本来就不同——控制台有课程/门禁/刷新，/eval 只有课程集与只读位，/log 只有当前
 *  组件：外壳一旦声明具体 props 类型，就必须为每种页面开一条分支（正是 §2 要避免的分叉）。
 *
 *  实际注入：
 *    - 控制台（`app/app.tsx`）：`<Sidebar .../>` + `<Topbar page=... stateView=.../>`
 *    - 独立页（/eval · /log）：`<NavSidebar .../>` + `<Topbar page="eval"|"log"/>`
 */

import type { ComponentChildren } from 'preact'

export interface ShellProps {
  /** 侧栏节点（控制台传 `Sidebar`，独立页传 `NavSidebar`）。 */
  sidebar: ComponentChildren
  /** 顶栏节点（全部页面都是 `Topbar`，只是状态读数不同）。 */
  topbar: ComponentChildren
  children: ComponentChildren
}

export function Shell({ sidebar, topbar, children }: ShellProps) {
  return (
    <div className="tc-shell">
      {sidebar}
      <div className="tc-main">
        {topbar}
        <div className="tc-main__body">{children}</div>
      </div>
    </div>
  )
}
