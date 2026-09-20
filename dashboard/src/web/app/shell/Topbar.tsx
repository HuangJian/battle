/** Topbar.tsx — 顶栏：本页标题 + 一句话描述 + 全局状态 chips + 刷新。
 *
 *  分工契约（docs/dashboard-redesign.md §3.1）：顶栏只说**本页**与**全局**——
 *    - 「本页」= 标题 + 这一页回答什么问题（`PAGES[page].desc`；"一页一个问题"的可视化契约）
 *    - 「全局」= 阶段 · 在训课程 · 算力 · 连接状态 + 刷新 + 更新时间
 *  课程选择器、触发门禁、刷新间隔**不在这里**（见 Sidebar.tsx 的文件头）。
 *
 *  阶段耗时由 App 的 10s ticker 驱动（轮询间隙不冻结），本组件只负责展示。
 *
 *  **独立页复用（/eval · /log）**：标题/描述取自同一份 `PAGES`（`page` 取 `AnyPageKey`），
 *  而全局状态读数属于「此刻训练集群在干吗」——那是控制台**持有并轮询**的数据。独立页没有
 *  这份数据，所以 `stateView=null` 时顶栏自动退成「本页标题 + 刷新」，不伪造读数
 *  （这是 `stateView` 从一开始就可空的原因）。
 */

import { fmtElapsed, fmtTs, PAGES, type AnyPageKey, type ConsoleStateView } from '../../view'
import { Badge } from '../../components/Pill'

export interface TopbarProps {
  /** 控制台四页或独立页（eval / log）——只用于查 `PAGES` 元信息。 */
  page: AnyPageKey
  stateView: ConsoleStateView | null
  /** 本阶段已耗时（ms）；null = 无阶段（idle）。 */
  phaseElapsedMs: number | null
  /** 在训课程数（App 派生：共享 trainer 在跑 ∧ 该课未收官）。 */
  trainingCount: number
  /** 课程总数。 */
  courseCount: number
  /** 算力摘要（本机槽位计入在线）；null = 无节点可报。 */
  nodeSummary: { online: number; total: number } | null
  /** 连接状态：off = 正常；retry = 单次失败重试中；down = 连续 3 次失败。 */
  connError?: 'off' | 'retry' | 'down'
  /** 断线重试（仅控制台有轮询连接概念）。 */
  onRetry?: () => void
  /** 立即刷新全部（等同于快捷键 r）。独立页传自己的 refresh 函数。 */
  onRefreshNow?: () => void
}

export function Topbar({
  page,
  stateView,
  phaseElapsedMs,
  trainingCount,
  courseCount,
  nodeSummary,
  connError = 'off',
  onRetry,
  onRefreshNow,
}: TopbarProps) {
  const meta = PAGES[page]
  const phase = stateView?.phase ?? null
  const phaseLabel = phase?.phase === 'rollout' ? 'rollout' : 'ppo'

  return (
    <header className="tc-top">
      <div className="tc-top__head">
        <h1 className="tc-top__title">{meta.title}</h1>
        <div className="tc-top__desc" title={meta.desc}>
          {meta.desc}
        </div>
      </div>
      <div className="tc-top__right">
        {phase && phase.phase !== 'idle' ? (
          <span
            className={`tc-phase tc-phase--${phase.phase}`}
            title={
              phase.iter != null
                ? `it${phase.iter} ${phase.phase === 'rollout' ? '采集' : 'PPO'} 阶段`
                : phase.phase === 'rollout'
                  ? '采集阶段'
                  : 'PPO 阶段'
            }
          >
            <span className="tc-phase__icon" aria-hidden="true">
              {phase.phase === 'rollout' ? '◎' : '⬡'}
            </span>
            <span className="tc-phase__label">{phaseLabel}</span>
            <span className="tc-phase__elapsed">{fmtElapsed(phaseElapsedMs)}</span>
          </span>
        ) : null}
        {courseCount > 0 ? (
          <span className="tc-badge--status" title="在训课程数 / 课程总数">
            <span className={trainingCount > 0 ? 'tc-dot tc-dot--on' : 'tc-dot tc-dot--empty'} />
            在训 {trainingCount}/{courseCount}
          </span>
        ) : null}
        {nodeSummary && nodeSummary.total > 0 ? (
          <span
            className="tc-top__ts"
            title="算力节点在线数 / 已登记总数（本机直跑槽位计入在线；详尽统计见「节点」页）"
          >
            节点 {nodeSummary.online}/{nodeSummary.total}
          </span>
        ) : null}
        {connError !== 'off' ? (
          <Badge
            tone={connError === 'down' ? 'r' : 'y'}
            title={
              connError === 'down'
                ? '控制台无响应（服务端可能已退出）——检查 `bun run dashboard` 进程'
                : '上一次刷新失败，正在重试'
            }
          >
            {connError === 'down' ? '控制台无响应' : '刷新失败，重试中'}
          </Badge>
        ) : null}
        {connError === 'down' && onRetry ? (
          <button type="button" className="tc-btn tc-btn--sm" onClick={onRetry}>
            重试
          </button>
        ) : null}
        {onRefreshNow ? (
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            aria-label="立即刷新全部 (r)"
            title="立即刷新全部 (r)"
            onClick={onRefreshNow}
          >
            ⟳
          </button>
        ) : null}
        {stateView ? (
          <span className="tc-top__ts">
            更新于 {fmtTs(new Date(stateView.time).getTime(), Date.now())}
          </span>
        ) : null}
      </div>
    </header>
  )
}
