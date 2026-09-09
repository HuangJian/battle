/** ComponentCards.tsx — 组件 4 小卡（一屏行）：点击卡展开详情，详情带在卡片网格**下方**
 *  整行全宽显示（不复用卡宽）。主按钮随状态换身。
 *  - 未启动：唯一「启动」（品牌色）；运行中：「停止」+ 冒烟/日志 小图标 + 详情。
 *  - cloudflared 常态缩略 endpoint + auth key，各带复制（CopyButton）——复制点击不展开卡片。
 *  - TrainingLoop 的「启动」→ 打开 TrainLaunchModal（App 层），选模式后再预设。
 *  - 启/停 pending 锁（§367）：点击先本地 disable（不依赖下一轮轮询），等状态切换完成
 *    或动作失败后再 enable——防双连击把组件状态打乱。 */

import { useEffect, useState } from 'preact/hooks'
import type { ComponentView, ConsoleStateView } from '../../../ui/view'
import { pendingLockReleases, shortUrl } from '../../../ui/view'
import { CopyButton } from '../../../ui/components/CopyButton'

export interface ComponentCardsProps {
  stateView: ConsoleStateView | null
  /** 返回 POST 结果——失败时卡立即解锁（状态不会切换，效果判定会永远等下去）。 */
  onAction: (act: string, body: Record<string, unknown>) => Promise<{ ok: boolean }>
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
  // §367：pending[key] = 点击时的 status；效果层在状态切换完成后移除（解锁）。
  const [pending, setPending] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!stateView || Object.keys(pending).length === 0) return
    const statusOf = (key: string): string | undefined =>
      stateView.components.find((c) => c.key === key)?.status
    const released = pendingLockReleases(pending, statusOf)
    if (released.length === 0) return
    setPending((p) => {
      const next = { ...p }
      for (const k of released) delete next[k]
      return next
    })
  }, [stateView, pending])

  const release = (key: string): void =>
    setPending((p) => {
      if (p[key] === undefined) return p
      const next = { ...p }
      delete next[key]
      return next
    })

  /** 启/停 统一入口：立即本地锁定（按钮随即 disable），等状态切换完成后由效果层解锁；
   *  POST 失败直接解锁（状态不会切换，效果层判定会永远等不到差异）；另设 60s 安全网——
   *  覆盖「幂等成功但状态不变」（registry/进程不一致的"已在运行"）等永不切换的角落。
   *  60s 大于任何组件动作耗时（hubServer 就绪等待上限 45s），不会在动作中途误解锁。 */
  const fire = (c: ComponentView, act: string): void => {
    setPending((p) => (p[c.key] !== undefined ? p : { ...p, [c.key]: c.status }))
    void onAction(act, { component: c.key }).then((r) => {
      if (!r.ok) release(c.key)
    })
    setTimeout(() => release(c.key), 60_000)
  }

  if (!stateView) return null
  const mains = stateView.components.filter((c) => c.key !== 'workerServe')
  const openCard = mains.find((c) => c.key === open) ?? null

  return (
    <>
      <div className="tc-comps" aria-label="组件">
        {mains.map((c) => {
          const isOpen = open === c.key
          const isRunning = c.status === 'running'
          const locked = c.busy || pending[c.key] !== undefined
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
                // 复制按键不展开卡片：meta 区（隧道/auth key + 复制）整体吞掉冒泡。
                <div className="tc-cc__meta" onClick={(e) => e.stopPropagation()}>
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
                      disabled={locked}
                      aria-label={`停止 ${c.label}`}
                      title={locked ? '动作进行中…' : undefined}
                      onClick={() => fire(c, 'stop')}
                    >
                      停止
                    </button>
                    {c.key !== 'trainingLoop' ? (
                      <button
                        type="button"
                        className="tc-iconbtn"
                        disabled={locked}
                        aria-label={`冒烟 ${c.label}`}
                        title="冒烟"
                        onClick={() => void onAction('smoke', { component: c.key })}
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
                    disabled={locked}
                    aria-label={`启动 ${c.label}`}
                    title={locked ? '动作进行中…' : undefined}
                    onClick={c.key === 'trainingLoop' ? onLaunchTrainer : () => fire(c, 'start')}
                  >
                    启动
                  </button>
                )}
              </div>
              {c.status === 'exited' && c.error ? (
                // §380：非正常退出原因直面展示（不再只有空洞的"已退出"）+ 一键进日志页
                <div className="tc-cc__err" role="alert">
                  <a className="tc-cc__err-link" href={`/log/${c.key}`}>
                    ⚠ {c.error} · 日志
                  </a>
                </div>
              ) : null}
            </section>
          )
        })}
      </div>
      {openCard ? (
        // 点击展开卡：详情带**整行全宽**贴在卡网格下方（点击日志区域再次收起）。
        <pre className="tc-cc__detail" onClick={() => setOpen(null)}>
          {[
            openCard.error ? `exit-error: ${openCard.error}` : null,
            openCard.log ? `log: ${openCard.log}` : null,
            openCard.course ? `course: ${openCard.course}` : null,
            openCard.mode ? `mode: ${openCard.mode}` : null,
            openCard.url ? `endpoint: ${openCard.url}` : null,
            openCard.pid ? `pid: ${openCard.pid}` : null,
            openCard.logTail.length > 0 ? `tail:\n${openCard.logTail.join('\n')}` : null,
          ]
            .filter(Boolean)
            .join('\n')}
        </pre>
      ) : null}
    </>
  )
}
