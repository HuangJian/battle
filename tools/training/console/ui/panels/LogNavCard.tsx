/** LogNavCard.tsx — 日志卡（导航）：五组件各自日志页入口（独立页 /log/<key>）。 */

import type { PanelProps } from '../../../ui/view'
import { Pill } from '../../../ui/components/Pill'

const tone = (status: string): 'g' | 'y' | 'r' | 'gray' =>
  status === 'running' ? 'g' : status === 'exited' ? 'r' : 'gray'

export function LogNavCard({ stateView }: PanelProps) {
  if (!stateView) return <div className="tc-loading">加载中…</div>
  return (
    <div className="tc-row" style={{ gap: 10 }}>
      {stateView.components.map((c) => (
        <span
          key={c.key}
          className="tc-preset"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}
        >
          <Pill tone={tone(c.status)}>{c.label}</Pill>
          <a className="tc-btn tc-btn--sm" href={`/log/${c.key}`}>
            日志 →
          </a>
        </span>
      ))}
      <span className="tc-muted tc-small">
        日志页独立轮询（follow 2s / 关 4s），上滚读历史不被拉回，滚回底部自动恢复跟随。
      </span>
    </div>
  )
}
