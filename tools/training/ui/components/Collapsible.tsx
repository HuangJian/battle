/** Collapsible.tsx — 卡片折叠体（grid-template-rows 过渡动画 120ms，aria 语义）。 */

import type { ComponentChildren } from 'preact'

export interface CollapsibleProps {
  collapsed: boolean
  children: ComponentChildren
}

export function Collapsible({ collapsed, children }: CollapsibleProps) {
  return (
    <div className={`tc-collapse${collapsed ? ' tc-collapse--on' : ''}`} aria-expanded={!collapsed}>
      <div>{children}</div>
    </div>
  )
}
