/** ComponentCards.tsx — 组件 chips 行（样式对齐节点行：一行内 pill，点击 pill 在**下方**
 *  展开整行全宽日志详情）。主按钮随状态换身。
 *  - 未启动：唯一「启动」（品牌色）；运行中：「停止」+ 冒烟/日志 小图标。
 *  - cloudflared：url / key 复制钮（文案即 url/key，不展示完整字符串；复制点击不展开详情）。
 *  - TrainingLoop 的「启动」→ 打开 TrainLaunchModal（App 层），选模式后再预设。
 *  - 启/停 pending 锁（§367）：点击先本地 disable（不依赖下一轮轮询），等状态切换完成
 *    或动作失败后再 enable——防双连击把组件状态打乱。 */

import { useEffect, useState } from 'preact/hooks'
import type { ComponentView, ConsoleStateView, PushFleetProbe } from '../../view'
import { cardFamilies, pendingLockReleases, scopeBadge } from '../../view'
import { CopyButton } from '../../components/CopyButton'

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

/** push 执行面徽章文案（机群级）：hub 派发 N 台 / 直推 N 台 / 等待拉取（+ 探活汇总）。
 *
 *  2026-09-19 起执行面不再按课程配（课程与 worker 节点正交）：它就是「这轮 PPO 会去哪」
 *  的一句话，数据源 = `stateView.pushFleet`（部署事实推出来，见 `stack/push-config.ts`）。 */
function pushBadgeText(f: PushFleetProbe): string {
  const up = f.probes.filter((p) => p.healthy === true).length
  const down = f.probes.filter((p) => p.healthy === false).length
  const n = f.nodes
  if (f.mode === 'pull') return 'dispatch→拉取'
  const head = f.mode === 'hub-dispatch' ? `hub→${n} 台` : `直推→${n} 台`
  return down > 0 ? `${head}·${down} 台不通` : up > 0 ? head : `${head}·未探`
}

/** 徽章悬停详情：模式理由 / hub / 逐节点探活。 */
function pushBadgeTitle(f: PushFleetProbe): string {
  const lines = [`执行面：${f.text}`, f.detail]
  for (const p of f.probes) {
    const probe = p.healthy === true ? '通' : p.healthy === false ? '不通' : '未探（无鉴权键）'
    lines.push(`· ${p.id || '(未命名)'} ${p.url} — ${probe}`)
  }
  if (f.mode === 'pull') {
    lines.push('没有登记节点时的必然结果：谁来领谁就跑（本机 worker 与云机同权）')
  }
  return lines.join('\n')
}

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
  // 两族（R3-3）：服务面（单例角色，与课程无关）vs 课程面（卡片对象 = 当前查看的那门课）。
  // 分组与顺序都出自 view 层的 `cardFamilies`（成员资格 = 服务端给的 scope，不在这里按 key 猜）。
  const families = cardFamilies(stateView.components)
  const openCard = families.flatMap((f) => f.rows).find((c) => c.key === open) ?? null

  return (
    <>
      <div className="tc-comps" aria-label="组件">
        {families.map((f) => (
          <div className={`tc-comps__group tc-comps__group--${f.id}`} key={f.id} data-family={f.id}>
            <span className="tc-comps__glabel" title={f.hint}>
              {f.title}
            </span>
            {f.rows.map((c) => {
              const isOpen = open === c.key
              const isRunning = c.status === 'running'
              const locked = c.busy || pending[c.key] !== undefined
              // 只读视图不禁用按钮（与其它动作键同哲学：可点、服务端 403 + flash 提示）。
              const roDisabledCls = readOnly ? ' tc-npill--ro' : ''
              const toggle = (): void => setOpen(isOpen ? null : c.key)
              const logHref = `/log/${c.key}${course ? `?course=${encodeURIComponent(course)}` : ''}`
              const scope = scopeBadge(c)
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
                  {scope ? (
                    // 作用域徽章（R3-3）：只说 scope 说不出来的那件事——「共享」= 一个进程服务
                    // 所有课程（不标就会有人去给这门课再起一个 hub，第二个实例抢同一端口）；
                    // 「单例」= 全机一份。按课程是默认语义（组标题已说），不给每行挂噪声标签。
                    <b className={`tc-cc__scope ${scope.cls}`} title={scope.title}>
                      {scope.text}
                    </b>
                  ) : null}
                  {(() => {
                    // 执行面徽章：贴在 trainer 卡上（「这轮 PPO 会去哪」只有这一个卡问得出口）。
                    // 数据源是**机群级**事实（登记节点 + rl.hub_push + 探活），与当前查看的课程
                    // 无关——课程与 worker 节点正交（2026-09-19）。
                    const f = c.key === 'trainingLoop' ? (stateView.pushFleet ?? null) : null
                    return (
                      <>
                        {c.mode ? <b className="tc-cc__mode">{c.mode}</b> : null}
                        {f ? (
                          <b
                            className={`tc-cc__push tc-cc__push--${f.mode}`}
                            title={pushBadgeTitle(f)}
                          >
                            {pushBadgeText(f)}
                          </b>
                        ) : null}
                      </>
                    )
                  })()}
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
                        onClick={
                          c.key === 'trainingLoop' ? onLaunchTrainer : () => fire(c, 'start')
                        }
                      >
                        启动
                      </button>
                    )}
                  </span>
                </span>
              )
            })}
          </div>
        ))}
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
