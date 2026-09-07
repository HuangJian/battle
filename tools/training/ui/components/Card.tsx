/** Card.tsx — 卡片容器：header（标题 + 摘要徽章 + 卡级动作 + 折叠/最大化图标）+ 折叠体 + footer。
 *
 *  紧凑化契约（用户指令 2026-09-07）：卡片默认只露 header——header 内 title + summary
 *  （状态徽章 = 基本信息）常驻；点击 header 主体任意处展开/收起完整内容（角色=button，
 *  aria-expanded），右上角 ⌄ 按钮同语义。摘要徽章始终可见（折叠态信息不丢）。
 *  折叠/最大化状态由 App 层持有（localStorage 持久化折叠；最大化不持久化），
 *  图标按钮必带 aria-label（GLM-U7），卡级动作统一在此渲染（DS-E2）。
 */

import type { ComponentChildren } from 'preact'
import { Collapsible } from './Collapsible'
import { StaleDot } from './StaleDot'
import type { StaleState } from '../view'

export interface CardAction {
  key: string
  label: string
  icon?: string
  run: () => void | Promise<void>
}

export interface CardError {
  message: string
  onRetry: () => void
}

export interface CardProps {
  /** 元素 id（锚点滚动目标）。 */
  id?: string
  title: string
  sub?: ComponentChildren
  /** 摘要徽章（基本信息，折叠/展开均可见）：组件运行数 / 模式 / 节点在线 / 最新迭代状态等。 */
  summary?: ComponentChildren
  /** 陈旧度（数据源最近拉到的时间点）。 */
  stale?: { state: StaleState; title?: string } | null
  collapsed: boolean
  maximized: boolean
  onToggleCollapsed: () => void
  onToggleMaximized: () => void
  actions?: CardAction[]
  footer?: ComponentChildren
  error?: CardError | null
  children: ComponentChildren
}

export function Card({
  id,
  title,
  sub,
  summary,
  stale,
  collapsed,
  maximized,
  onToggleCollapsed,
  onToggleMaximized,
  actions = [],
  footer,
  error,
  children,
}: CardProps) {
  return (
    <section
      id={id}
      className={`tc-card${maximized ? ' tc-card--max' : ''}`}
      aria-label={title}
      data-card={title}
    >
      <header className="tc-card__hd">
        <div
          className="tc-card__main"
          role="button"
          tabIndex={0}
          aria-expanded={!collapsed}
          aria-label={collapsed ? `展开 ${title}` : `折叠 ${title}`}
          title={collapsed ? '点击展开完整内容' : '点击折叠'}
          onClick={onToggleCollapsed}
        >
          <h2 className="tc-card__title">
            {title}
            {sub ? <span className="tc-card__sub"> — {sub}</span> : null}
          </h2>
          {summary ? <div className="tc-card__summary">{summary}</div> : null}
        </div>
        <div className="tc-card__actions">
          {stale ? <StaleDot state={stale.state} title={stale.title} /> : null}
          {actions.map((a) => (
            <button
              key={a.key}
              type="button"
              className="tc-iconbtn"
              aria-label={a.label}
              title={a.label}
              onClick={() => void a.run()}
            >
              {a.icon ?? a.label}
            </button>
          ))}
          <button
            type="button"
            className="tc-iconbtn"
            aria-label={maximized ? '退出最大化' : '最大化'}
            title={maximized ? '退出最大化 (Esc)' : '最大化'}
            onClick={onToggleMaximized}
          >
            {maximized ? '✕' : '⤢'}
          </button>
          <button
            type="button"
            className="tc-iconbtn"
            aria-label={collapsed ? '展开卡片' : '折叠卡片'}
            title={collapsed ? '展开' : '折叠'}
            onClick={onToggleCollapsed}
          >
            {collapsed ? '▸' : '⌄'}
          </button>
        </div>
      </header>
      {error ? (
        <div className="tc-card__err">
          <span>该卡片加载失败：{error.message}</span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={error.onRetry}>
            重试
          </button>
        </div>
      ) : null}
      <Collapsible collapsed={collapsed}>{children}</Collapsible>
      {footer ? <div className="tc-card__foot">{footer}</div> : null}
    </section>
  )
}
