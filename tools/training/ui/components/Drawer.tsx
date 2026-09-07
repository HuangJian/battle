/** Drawer.tsx — 全屏详情 modal（指标 | 节点统计 | 日志 三 tab 共用容器）。
 *  Esc / 点遮罩 / ✕ 关闭由 App 全局处理；本组件渲染 open 形态 + 打开时锁 body 滚动。 */

import { useEffect } from 'preact/hooks'
import type { ComponentChildren } from 'preact'

export interface DrawerTab {
  key: string
  label: string
}

export interface DrawerProps {
  open: boolean
  activeTab: string
  tabs: DrawerTab[]
  onTab: (key: string) => void
  onClose: () => void
  children: ComponentChildren
}

export function Drawer({ open, activeTab, tabs, onTab, onClose, children }: DrawerProps) {
  useEffect(() => {
    if (!open) return
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = ''
    }
  }, [open])
  if (!open) return null
  return (
    <div>
      <div className="tc-drawer-mask" aria-hidden="true" onClick={onClose} />
      <section className="tc-drawer" role="dialog" aria-label="详情">
        <header className="tc-drawer__hd">
          <h2 className="tc-card__title" style={{ margin: 0, fontSize: 15 }}>
            详情
          </h2>
          <div
            className="tc-drawer__tabs"
            style={{ marginLeft: 'auto', padding: 0, border: 'none' }}
            role="tablist"
          >
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={activeTab === t.key}
                className={`tc-drawer__tab${activeTab === t.key ? ' tc-drawer__tab--on' : ''}`}
                onClick={() => onTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button type="button" className="tc-iconbtn" aria-label="关闭详情" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="tc-drawer__body">{children}</div>
      </section>
    </div>
  )
}
