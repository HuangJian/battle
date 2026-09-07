/** ComponentsPanel.tsx — 组件卡（控制视图）：五组件启/停/冒烟/日志 + 状态 + 端点 + 日志尾行。 */

import type { ComponentView, PanelProps } from '../../../ui/view'
import { Pill } from '../../../ui/components/Pill'

function statusPill(c: ComponentView) {
  if (c.status === 'running') {
    return c.healthy === false ? (
      <Pill tone="y">运行中·未就绪</Pill>
    ) : (
      <Pill tone="g">运行中{c.healthy ? '·就绪' : ''}</Pill>
    )
  }
  if (c.status === 'exited') return <Pill tone="r">已退出</Pill>
  return <Pill tone="gray">未启动</Pill>
}

export function ComponentsPanel({ stateView, onAction }: PanelProps) {
  if (!stateView) return <div className="tc-loading">加载中…</div>
  return (
    <table className="tc-table">
      <thead>
        <tr>
          <th>组件</th>
          <th>状态</th>
          <th>端点</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        {stateView.components.map((c) => {
          const urlCell = c.url ? (
            <span className="tc-mono tc-muted tc-small">{c.url}</span>
          ) : c.key === 'selfNode' ? (
            <span className="tc-mono tc-muted">:8443</span>
          ) : (
            <span className="tc-muted">—</span>
          )
          const meta = [
            c.pid ? `PID ${c.pid}` : null,
            c.course ? `course=${c.course}` : null,
            c.mode ? `mode=${c.mode}` : null,
          ]
            .filter(Boolean)
            .join(' · ')
          return (
            <tr key={c.key}>
              <td>
                <b>{c.label}</b>
                <div className="tc-muted tc-small">{meta || '\u00a0'}</div>
                {c.logTail.length > 0 ? (
                  <details>
                    <summary className="tc-muted tc-small">日志尾行 ({c.logTail.length})</summary>
                    <pre className="tc-logtail">{c.logTail.join('\n')}</pre>
                  </details>
                ) : null}
              </td>
              <td>{statusPill(c)}</td>
              <td>{urlCell}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  disabled={c.busy}
                  aria-label={`启动 ${c.label}`}
                  onClick={() => onAction('start', { component: c.key })}
                >
                  启动
                </button>{' '}
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  disabled={c.busy}
                  aria-label={`停止 ${c.label}`}
                  onClick={() => onAction('stop', { component: c.key })}
                >
                  停止
                </button>{' '}
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  disabled={c.busy}
                  aria-label={`冒烟 ${c.label}`}
                  onClick={() => onAction('smoke', { component: c.key })}
                >
                  冒烟
                </button>{' '}
                <a className="tc-btn tc-btn--sm" href={`/log/${c.key}`}>
                  日志
                </a>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
