/** LogNavCard.tsx — 抽屉「日志」tab：常驻组件各自日志页入口（独立页 /log/<key>）。 */

import type { ConsoleStateView } from '../../../ui/view'
import { Pill } from '../../../ui/components/Pill'

const tone = (status: string): 'g' | 'y' | 'r' | 'gray' =>
  status === 'running' ? 'g' : status === 'exited' ? 'r' : 'gray'

export function LogNavCard({ stateView }: { stateView: ConsoleStateView | null }) {
  if (!stateView) return <div className="tc-loading">加载中…</div>
  const mains = stateView.components.filter((c) => c.key !== 'workerServe')
  return (
    <div>
      <div className="tc-row" style={{ gap: 10 }}>
        {mains.map((c) => (
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
      </div>
      <p className="tc-muted tc-small" style={{ marginTop: 10 }}>
        日志页独立轮询（follow 2s / 关 4s），上滚读历史不被拉回，滚回底部自动恢复跟随。
      </p>
    </div>
  )
}
