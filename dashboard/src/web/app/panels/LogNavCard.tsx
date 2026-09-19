/** LogNavCard.tsx — 抽屉「日志」tab：常驻组件各自日志页入口（独立页 /log/<key>）。 */

import type { ConsoleStateView } from '../../view'
import { NODE_FACE_COMPONENTS } from '../../view'
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
  // 节点面组件（worker_server）不在这里列日志入口：它的语义轴是节点 —— 与组件卡行同一份例外声明
  // （`NODE_FACE_COMPONENTS`），不留第二行 filter（两处名单漂开 = 某个组件某处消失）。
  const mains = stateView.components.filter((c) => !NODE_FACE_COMPONENTS.includes(c.key))
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
