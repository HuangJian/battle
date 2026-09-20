/** Shell.tsx — 应用外壳（侧栏 + 顶栏 + 主内容）：全部页面共用的三区骨架。
 *
 *  依据 docs/dashboard-redesign.md §3.1。三档响应式的实现全在 CSS 里
 *  （`theme.css` 的「应用外壳」段），本组件只负责 DOM 结构——布局不允许在这里分叉。
 *
 *  为什么用 props 注入 Sidebar/Topbar 的配置而不是在这里读 state：外壳是**纯布局**，
 *  状态与动作的所有权留在 App（单一数据源），外壳只做摆放。这样 /eval 与 /log
 *  将来复用外壳时也不需要拿到 ConsoleStateView。
 */

import type { ComponentChildren } from 'preact'
import { Sidebar, type SidebarProps } from './Sidebar'
import { Topbar, type TopbarProps } from './Topbar'

export interface ShellProps {
  sidebar: SidebarProps
  topbar: TopbarProps
  children: ComponentChildren
}

export function Shell({ sidebar, topbar, children }: ShellProps) {
  return (
    <div className="tc-shell">
      <Sidebar {...sidebar} />
      <div className="tc-main">
        <Topbar {...topbar} />
        <div className="tc-main__body">{children}</div>
      </div>
    </div>
  )
}
