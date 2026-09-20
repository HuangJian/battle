/** LogNavCard.tsx — 抽屉「日志」tab：常驻组件各自日志页入口（独立页 /log/<key>）。 */

import type { ConsoleStateView } from '../../view'
import { Pill } from '../../components/Pill'

const tone = (status: string): 'g' | 'y' | 'r' | 'gray' =>
  status === 'running' ? 'g' : status === 'exited' ? 'r' : 'gray'

export function LogNavCard({
  stateView,
  course,
}: {
  stateView: ConsoleStateView | null
  /** 当前查看课程（日志页链接带 ?course=，保持同课程查看）。 */
  course?: string
}) {
  if (!stateView) return <div className="tc-loading">加载中…</div>
  // 受管组件即日志入口全集（不再有「某组件不在这列」的例外：本机伪节点已退出受管组件，
  // 2026-09-19 —— 它的日志页入口也随之消失，它只服务冒烟预演）。
  const mains = stateView.components
  return (
    <div>
      {/* 2026-09-20：原为 `tc-row` + 内联 `gap:10` —— `.tc-row` 被 StatusRow 的芯片定义覆盖后
          这行成了带边框的胶囊，行布局请用 `.tc-line`。 */}
      <div className="tc-line">
        {mains.map((c) => (
          <span
            key={c.key}
            className="tc-preset"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}
          >
            <Pill tone={tone(c.status)}>{c.label}</Pill>
            <a
              className="tc-btn tc-btn--sm"
              href={`/log/${c.key}${course ? `?course=${encodeURIComponent(course)}` : ''}`}
            >
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
