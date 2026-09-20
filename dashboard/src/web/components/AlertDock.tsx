/** AlertDock.tsx — 告警坞：把首页所有的告警/提示收成**一个**容器（问题 C4）。
 *
 *  条目怎么来的、怎么排序、怎么折叠，全在 `view/alerts.ts`（纯函数，可单测）；本组件只管两件
 *  本层才能做的事：① 折叠态的开关（`useState`）；② 把「动作」绑到回调上。
 *
 *  折叠态是**客户端状态、且初值确定**（默认收起）——SSR 与 hydrate 首帧都渲染前 2 条，
 *  不会水合错配（这正是此前「横幅关闭后样式崩」那类事故的成因，见 `web-ssr-readonly.test.ts`）。
 *
 *  空的时候整坞**不出现**：不留空壳，也不再有一条「暂无告警」的占位横幅（§5.5 空态口径 ——
 *  这里「没有告警」就是字面意义的没有，不需要解释）。
 */

import { useState } from 'preact/hooks'
import { ALERT_DOCK_DEFAULT_VISIBLE, alertDockSplit, type AlertItem } from '../view'

export interface AlertDockProps {
  items: AlertItem[]
  /** 执行条目上的 `resume` 动作（真去改训练状态）。 */
  onAct: (act: string, body: Record<string, unknown>) => unknown
  /** 记录条目上的 `ack`（只写本地，不碰服务端）。 */
  onAck: (ackKey: string) => void
  /** 默认可见条数（测试/特例可调）。 */
  cap?: number
}

/** 严重度 → 坞内修饰类（颜色只在这里映射一次，条目本身只带语义）。 */
function severityClass(s: AlertItem['severity']): string {
  return `tc-dock__item tc-dock__item--${s}`
}

export function AlertDock({
  items,
  onAct,
  onAck,
  cap = ALERT_DOCK_DEFAULT_VISIBLE,
}: AlertDockProps) {
  const [expanded, setExpanded] = useState(false)
  const { visible, hidden } = alertDockSplit(items, expanded, cap)
  if (visible.length === 0) return null
  return (
    <section className="tc-dock" aria-label="告警">
      {visible.map((a) => (
        <div key={a.id} className={severityClass(a.severity)} role={a.role}>
          <span className="tc-dock__icon" aria-hidden="true">
            {a.icon}
          </span>
          <div className="tc-dock__body">
            <div className="tc-dock__title">{a.title}</div>
            <div className="tc-dock__detail">{a.detail}</div>
          </div>
          {a.actions.length > 0 ? (
            <span className="tc-dock__acts">
              {a.actions.map((act) => (
                <button
                  key={act.label}
                  type="button"
                  className={`tc-btn tc-btn--sm${act.primary ? ' tc-btn--primary' : ''}`}
                  title={act.title}
                  onClick={() => {
                    if (act.kind === 'ack') onAck(act.ackKey ?? '')
                    else if (act.act) void onAct(act.act, act.body ?? {})
                  }}
                >
                  {act.label}
                </button>
              ))}
            </span>
          ) : null}
        </div>
      ))}
      {hidden.length > 0 ? (
        <button
          type="button"
          className="tc-dock__more"
          aria-expanded={false}
          onClick={() => setExpanded(true)}
        >
          还有 {hidden.length} 条 ▸
        </button>
      ) : null}
      {expanded && items.length > cap ? (
        <button
          type="button"
          className="tc-dock__more"
          aria-expanded={true}
          onClick={() => setExpanded(false)}
        >
          收起 ▴
        </button>
      ) : null}
    </section>
  )
}
