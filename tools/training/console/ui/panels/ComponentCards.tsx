/** ComponentCards.tsx — 组件 4 小卡（一屏行）：点击卡展开详情，主按钮随状态换身。
 *  - 未启动：唯一「启动」（品牌色）；运行中：「停止」+ 冒烟/日志 小图标 + 详情。
 *  - cloudflared 常态缩略 endpoint + auth key，各带复制（CopyButton）。
 *  - TrainingLoop 的「启动」→ 打开 TrainLaunchModal（App 层），选模式后再预设。 */

import { useState } from 'preact/hooks'
import type { ComponentView, ConsoleStateView } from '../../../ui/view'
import { shortUrl } from '../../../ui/view'
import { CopyButton } from '../../../ui/components/CopyButton'

export interface ComponentCardsProps {
  stateView: ConsoleStateView | null
  onAction: (act: string, body: Record<string, unknown>) => void
  /** TrainingLoop 卡「启动」回调（App 打开模式弹窗）。 */
  onLaunchTrainer: () => void
}

function dotClass(c: ComponentView): string {
  if (c.busy) return 'tc-dot--warn'
  if (c.status === 'running') return c.healthy === false ? 'tc-dot--warn' : 'tc-dot--on'
  if (c.status === 'exited') return 'tc-dot--dead'
  return 'tc-dot--empty'
}

function statusText(c: ComponentView): string {
  if (c.status === 'running') return c.healthy === false ? '未就绪' : '运行中'
  if (c.status === 'exited') return '已退出'
  return '未启动'
}

export function ComponentCards({ stateView, onAction, onLaunchTrainer }: ComponentCardsProps) {
  const [open, setOpen] = useState<string | null>(null)
  if (!stateView) return null
  const mains = stateView.components.filter((c) => c.key !== 'workerServe')

  return (
    <div className="tc-comps" aria-label="组件">
      {mains.map((c) => {
        const isOpen = open === c.key
        const isRunning = c.status === 'running'
        const meta = c.pid ? `PID ${c.pid} · ${statusText(c)}` : statusText(c)
        return (
          <section
            key={c.key}
            className={`tc-cc${isOpen ? ' tc-cc--open' : ''}`}
            aria-label={c.label}
            onClick={() => setOpen(isOpen ? null : c.key)}
          >
            <div className="tc-cc__hd">
              <span className={`tc-dot ${dotClass(c)}`} />
              <span className="tc-cc__name">{c.key}</span>
            </div>
            {c.key === 'cloudflared' ? (
              <div className="tc-cc__meta">
                <div className="tc-cc__sec">
                  {c.url ? (
                    <>
                      <code title={c.url}>{shortUrl(c.url)}</code>
                      <CopyButton text={c.url} label="隧道" icon small />
                    </>
                  ) : (
                    <span className="tc-muted">未建立隧道</span>
                  )}
                </div>
                <div className="tc-cc__sec">
                  <code>
                    token{' '}
                    {c.secret
                      ? c.secret.length > 14
                        ? `${c.secret.slice(0, 7)}…${c.secret.slice(-4)}`
                        : (c.secret ?? '-')
                      : '-'}
                  </code>
                  {c.secret ? <CopyButton text={c.secret} label="auth key" icon small /> : null}
                </div>
              </div>
            ) : (
              <span className="tc-cc__meta">{meta}</span>
            )}
            <div className="tc-cc__acts" onClick={(e) => e.stopPropagation()}>
              {isRunning ? (
                <>
                  <button
                    type="button"
                    className="tc-btn tc-btn--sm"
                    disabled={c.busy}
                    aria-label={`停止 ${c.label}`}
                    onClick={() => onAction('stop', { component: c.key })}
                  >
                    停止
                  </button>
                  {c.key !== 'trainingLoop' ? (
                    <button
                      type="button"
                      className="tc-iconbtn"
                      disabled={c.busy}
                      aria-label={`冒烟 ${c.label}`}
                      title="冒烟"
                      onClick={() => onAction('smoke', { component: c.key })}
                    >
                      ◎
                    </button>
                  ) : null}
                  <a
                    className="tc-iconbtn"
                    aria-label={`日志 ${c.label}`}
                    title="日志"
                    href={`/log/${c.key}`}
                  >
                    ≡
                  </a>
                </>
              ) : (
                <button
                  type="button"
                  className="tc-btn tc-btn--sm tc-btn--primary"
                  disabled={c.busy}
                  aria-label={`启动 ${c.label}`}
                  onClick={
                    c.key === 'trainingLoop'
                      ? onLaunchTrainer
                      : () => onAction('start', { component: c.key })
                  }
                >
                  启动
                </button>
              )}
            </div>
            {isOpen ? (
              <pre className="tc-cc__detail">
                {[
                  c.log ? `log: ${c.log}` : null,
                  c.course ? `course: ${c.course}` : null,
                  c.mode ? `mode: ${c.mode}` : null,
                  c.url ? `endpoint: ${c.url}` : null,
                  c.pid ? `pid: ${c.pid}` : null,
                  c.logTail.length > 0 ? `tail:\n${c.logTail.join('\n')}` : null,
                ]
                  .filter(Boolean)
                  .join('\n')}
              </pre>
            ) : null}
          </section>
        )
      })}
    </div>
  )
}
