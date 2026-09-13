/** ComponentCards.tsx — 组件 chips 行（样式对齐节点行：一行内 pill，点击 pill 在**下方**
 *  展开整行全宽日志详情）。主按钮随状态换身。
 *  - 未启动：唯一「启动」（品牌色）；运行中：「停止」+ 冒烟/日志 小图标。
 *  - cloudflared：url / key 复制钮（文案即 url/key，不展示完整字符串；复制点击不展开详情）。
 *  - TrainingLoop 的「启动」→ 打开 TrainLaunchModal（App 层），选模式后再预设。
 *  - 启/停 pending 锁（§367）：点击先本地 disable（不依赖下一轮轮询），等状态切换完成
 *    或动作失败后再 enable——防双连击把组件状态打乱。 */

import { useEffect, useState } from 'preact/hooks'
import type { ComponentView, ConsoleStateView } from '../../../ui/view'
import { pendingLockReleases } from '../../../ui/view'
import { CopyButton } from '../../../ui/components/CopyButton'

export interface ComponentCardsProps {
  stateView: ConsoleStateView | null
  /** 返回 POST 结果——失败时卡立即解锁（状态不会切换，效果判定会永远等下去）。 */
  onAction: (act: string, body: Record<string, unknown>) => Promise<{ ok: boolean }>
  /** TrainingLoop 卡「启动」回调（App 打开模式弹窗）。 */
  onLaunchTrainer: () => void
  /** 当前查看课程（日志页链接带 ?course=，保持同课程查看）。 */
  course?: string
  /** 局域网只读视图：按钮保持正常外观，悬停给只读提示；真点击由服务端 403 + flash 兜底
   *  （物理禁用会让整个组件区看起来灰败破碎——只读是动作边界，不是按钮状态）。 */
  readOnly?: boolean
}

/** 只读视图的动作按钮悬停提示（局域网用户误点前的说明）。 */
const RO_TITLE = '只读模式：操作仅限本机 localhost'

function dotClass(c: ComponentView): string {
  if (c.busy) return 'tc-dot--warn'
  if (c.status === 'running') return c.healthy === false ? 'tc-dot--warn' : 'tc-dot--on'
  if (c.status === 'exited') return 'tc-dot--dead'
  return 'tc-dot--empty'
}

export function ComponentCards({
  stateView,
  onAction,
  onLaunchTrainer,
  course,
  readOnly,
}: ComponentCardsProps) {
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
  // 组件卡片固定顺序：hubServer > trainingLoop > selfNode > cloudflared
  const ORDER: string[] = ['hubServer', 'trainingLoop', 'selfNode', 'cloudflared']
  mains.sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key))
  const openCard = mains.find((c) => c.key === open) ?? null

  return (
    <>
      <div className="tc-comps" aria-label="组件">
        <span className="lbl">组件</span>
        {mains.map((c) => {
          const isOpen = open === c.key
          const isRunning = c.status === 'running'
          const locked = c.busy || pending[c.key] !== undefined
          // 只读视图不禁用按钮（与其它动作键同哲学：可点、服务端 403 + flash 提示）。
          const roDisabledCls = readOnly ? ' tc-npill--ro' : ''
          const toggle = (): void => setOpen(isOpen ? null : c.key)
          const logHref = `/log/${c.key}${course ? `?course=${encodeURIComponent(course)}` : ''}`
          return (
            <span
              key={c.key}
              className={`tc-npill${roDisabledCls}${isOpen ? ' tc-npill--collapse' : ''}`}
              role={readOnly ? undefined : 'button'}
              tabIndex={readOnly ? undefined : 0}
              aria-label={c.label}
              aria-expanded={isOpen}
              title={readOnly ? '（只读）' : `${c.label}·点击展开日志详情`}
              onClick={readOnly ? undefined : toggle}
              onKeyDown={
                readOnly
                  ? undefined
                  : (e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        toggle()
                      }
                    }
              }
            >
              <span className={`tc-dot ${dotClass(c)}`} />
              <b>{c.key}</b>
              {c.mode ? <b className="tc-cc__mode">{c.mode}</b> : null}
              {c.key === 'cloudflared' && (c.url || c.secret) ? (
                // 截断展示 + 全量复制（§361：title 留全量，复制钮拿全量）；复制点击不展开日志详情。
                <span
                  role="group"
                  aria-label={
                    c.url && c.secret
                      ? '隧道 + auth key 复制钮'
                      : c.url
                        ? '隧道复制钮'
                        : 'auth key复制钮'
                  }
                  onClick={(e) => e.stopPropagation()}
                >
                  {c.url ? <CopyButton text={c.url} label="url" small /> : null}
                  {c.secret ? <CopyButton text={c.secret} label="key" small /> : null}
                </span>
              ) : null}
              {c.status === 'exited' && c.error ? (
                // §380：退出原因放在日志详情里展示全文；chip 内只放 ⚠ 入口。
                <a
                  className="tc-cc__err-link"
                  href={logHref}
                  title={c.error}
                  aria-label={`${c.label} 退出原因：${c.error}`}
                  onClick={(e) => e.stopPropagation()}
                >
                  ⚠
                </a>
              ) : null}
              <span className="tc-cc__acts" onClick={(e) => e.stopPropagation()}>
                {isRunning ? (
                  <>
                    <button
                      type="button"
                      className="tc-btn tc-btn--sm"
                      disabled={locked}
                      aria-label={`停止 ${c.label}`}
                      title={roDisabledCls ? undefined : '动作进行中…'}
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
                        title={readOnly ? RO_TITLE : '冒烟'}
                        onClick={() => void onAction('smoke', { component: c.key })}
                      >
                        ◎
                      </button>
                    ) : null}
                    <a
                      className="tc-iconbtn"
                      aria-label={`日志 ${c.label}`}
                      title="日志"
                      href={logHref}
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
                    title={roDisabledCls ? undefined : '动作进行中…'}
                    onClick={c.key === 'trainingLoop' ? onLaunchTrainer : () => fire(c, 'start')}
                  >
                    启动
                  </button>
                )}
              </span>
            </span>
          )
        })}
      </div>
      {openCard ? (
        // 点击展开 chip：详情带**整行全宽**贴在 chips 行下方（点击日志区域再次收起）。
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
