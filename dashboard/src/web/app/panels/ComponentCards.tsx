/** ComponentCards.tsx — 组件卡：按族（服务面 · 单例 / 课程面 · 按课程）分组的组件行。
 *
 *  2026-09-20（docs/dashboard-redesign.md P1）：行结构迁移到 `StatusRow` 原语——状态点、
 *  作用域/模式/执行面徽章、动作区、展开详情各归其位，不再手写一套 chip 布局。
 *  行为契约**逐条保留**：
 *  - 未启动：唯一「启动」（品牌色）；运行中：「停止」+ 冒烟/日志 小图标。
 *  - cloudflared：url / key 复制钮（复制点击不展开详情）。
 *  - TrainingLoop 的「启动」→ 打开 TrainLaunchModal（App 层）。
 *  - 启/停 pending 锁（§367）：点击先本地 disable，等状态切换完成或动作失败后解锁。
 *  - 只读视图：行不渲染交互角色，但动作键保持可点（服务端 403 兜底，§7 O1 已决）。
 */

import { useState } from 'preact/hooks'
import type { ComponentView, ConsoleStateView, PushFleetProbe } from '../../view'
import { cardFamilies, pendingLockReleases, scopeBadge } from '../../view'
import { CopyButton } from '../../components/CopyButton'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusRow, type RowBadge } from '../../components/StatusRow'
import { useEffect } from 'preact/hooks'
import type { StatusTone } from '../../components/StatusDot'

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
 *  执行面不再按课程配（课程与 worker 节点正交）：它就是「这轮 PPO 会去哪」的一句话，
 *  数据源 = `stateView.pushFleet`（部署事实推出来，见 `stack/push-config.ts`）。 */
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

/** 组件状态 → 语义档（领域映射留在本面板，原语不认识组件）。
 *
 *  running 且明确不健康 → warn；healthy=null（未探）不冒充绿——只对「无探测语义」的
 *  组件（trainingLoop/localWorker 服务端恒 true）出绿。cloudflared hub 不通时服务端
 *  会写 healthy=false → warn（2026-09-18）。busy 优先（动作进行中）。 */
function dotTone(c: ComponentView): StatusTone {
  if (c.busy) return 'warn'
  if (c.status === 'running') return c.healthy === false ? 'warn' : 'ok'
  if (c.status === 'exited') return 'err'
  return 'off'
}

/** 状态点的悬停解释（颜色/形状不是唯一信息载体）。 */
function dotTitle(c: ComponentView): string {
  if (c.busy) return '动作进行中（启停 pending）'
  if (c.status === 'running')
    return c.healthy === false ? 'running，但健康探测未通过' : 'running（进程存活）'
  if (c.status === 'exited') return 'exited：登记仍在但进程已死'
  return 'stopped：无存活进程'
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

  return (
    <div className="tc-comps" aria-label="组件">
      {families.map((f) => (
        <div className={`tc-comps__group tc-comps__group--${f.id}`} key={f.id} data-family={f.id}>
          <SectionHeader title={f.title} hint={f.hint} sub />
          {f.rows.map((c) => {
            const isOpen = open === c.key
            const isRunning = c.status === 'running'
            const locked = c.busy || pending[c.key] !== undefined
            const toggle = (): void => setOpen(isOpen ? null : c.key)
            const logHref = `/log/${c.key}${course ? `?course=${encodeURIComponent(course)}` : ''}`
            const scope = scopeBadge(c)
            // 执行面徽章：贴在 trainer 卡上（「这轮 PPO 会去哪」只有这一个卡问得出口）。
            // 数据源是**机群级**事实（登记节点 + rl.hub_push + 探活），与当前查看的课程无关。
            const fleet = c.key === 'trainingLoop' ? (stateView.pushFleet ?? null) : null
            const badges: RowBadge[] = []
            // `scope.cls` 只是修饰类（`tc-cc__scope--shared`）；基类在行里补上。
            if (scope)
              badges.push({
                text: scope.text,
                cls: `tc-cc__scope ${scope.cls}`,
                title: scope.title,
              })
            if (c.mode) badges.push({ text: c.mode, cls: 'tc-cc__mode' })
            if (fleet)
              badges.push({
                text: pushBadgeText(fleet),
                cls: `tc-cc__push tc-cc__push--${fleet.mode}`,
                title: pushBadgeTitle(fleet),
              })
            return (
              <StatusRow
                key={c.key}
                tone={dotTone(c)}
                dotTitle={dotTitle(c)}
                name={c.key}
                badges={badges}
                onToggle={toggle}
                expanded={isOpen}
                readOnly={readOnly}
                roTitle={readOnly ? RO_TITLE : undefined}
                ariaLabel={
                  readOnly
                    ? `${c.label}（只读）`
                    : `${c.label}，${isOpen ? '点击收起日志详情' : '点击展开日志详情'}`
                }
                actions={
                  <>
                    {c.key === 'cloudflared' && (c.url || c.secret) ? (
                      // 截断展示 + 全量复制（title 留全量，复制钮拿全量）；复制点击不展开详情
                      // （动作区的点击已被 StatusRow 拦截冒泡）。
                      <span
                        role="group"
                        aria-label={
                          c.url && c.secret
                            ? '隧道 + auth key 复制钮'
                            : c.url
                              ? '隧道复制钮'
                              : 'auth key复制钮'
                        }
                      >
                        {c.url ? <CopyButton text={c.url} label="url" small /> : null}
                        {c.secret ? <CopyButton text={c.secret} label="key" small /> : null}
                      </span>
                    ) : null}
                    {c.status === 'exited' && c.error ? (
                      // §380：退出原因全文在日志详情/日志页；行内只放 ⚠ 入口。
                      <a
                        className="tc-cc__err-link"
                        href={logHref}
                        title={c.error}
                        aria-label={`${c.label} 退出原因：${c.error}`}
                      >
                        ⚠
                      </a>
                    ) : null}
                    {isRunning ? (
                      <>
                        <button
                          type="button"
                          className="tc-btn tc-btn--sm"
                          disabled={locked}
                          aria-label={`停止 ${c.label}`}
                          title={readOnly ? RO_TITLE : '停止该组件'}
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
                        title={readOnly ? RO_TITLE : '启动该组件'}
                        onClick={
                          c.key === 'trainingLoop' ? onLaunchTrainer : () => fire(c, 'start')
                        }
                      >
                        启动
                      </button>
                    )}
                  </>
                }
                detailTitle="日志详情"
                detail={
                  <pre className="tc-cc__detail">
                    {[
                      c.error ? `exit-error: ${c.error}` : null,
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
                }
              />
            )
          })}
        </div>
      ))}
    </div>
  )
}
