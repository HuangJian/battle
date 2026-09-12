/** MultiCourseOverview.tsx — 同屏多课总览（P5-W2）。
 *
 *  为什么独立成行：单课时「组件卡 + 指标表」已足够；多课时操作员需要**一眼看到全部
 *  课程**谁在跑、跑到哪、有没有停机/排队超时——否则只能靠课程下拉逐课点开，多课并行
 *  的可观测性等于不存在。
 *
 *  边界：本面板**只读展示**（进程状态取自账本，指标取各课最近一轮），不发任何动作、
 *  不写 console-state；启停/冒烟/节点编辑仍在下方「选中课程」的组件卡与节点行里。
 *  点课程名 = 切换查看（与顶栏下拉同一语义）。selfNode 是全局单例，故不出现在任何行。
 */

import type {
  ConsoleStateView,
  CourseOverview,
  CourseOverviewComponent,
} from '../../../ui/view'
import { fmtPct } from '../../../ui/view'

export interface MultiCourseOverviewProps {
  stateView: ConsoleStateView | null
  /** 切换查看某课（本机同时同步操作员课程；只读仅改本浏览器 URL）。 */
  onSelectCourse: (course: string) => void
}

const PHASE_LABEL: Record<string, string> = { rollout: 'rollout', ppo: 'PPO', idle: 'idle' }

/** 组件短名（卡片区太长，总览行只放得下缩写）。 */
const COMP_SHORT: Record<string, string> = {
  hubServer: 'hub',
  cloudflared: 'cf',
  workerServe: 'worker',
  trainingLoop: 'trainer',
}

function dotClass(c: CourseOverviewComponent): string {
  if (c.status === 'running') return 'tc-dot tc-dot--on'
  if (c.status === 'exited') return 'tc-dot tc-dot--dead'
  return 'tc-dot tc-dot--empty'
}

function fmtSec(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return '-'
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`
}

export function MultiCourseOverview({ stateView, onSelectCourse }: MultiCourseOverviewProps) {
  const rows: CourseOverview[] = stateView?.courseOverviews ?? []
  // 单课不渲染：不制造一个只有一行、与下方组件卡重复的面板。
  if (rows.length < 2) return null
  const active = stateView?.course ?? ''
  return (
    <section className="tc-cov" aria-label="多课程总览">
      <div className="tc-cov__hd">
        多课程总览（{rows.length} 课 · 只读；启停/冒烟在下方选中课程的组件卡）
      </div>
      <div className="tc-cov__rows">
        {rows.map((r) => (
          <div
            key={r.course}
            className={`tc-cov__row${r.course === active ? ' tc-cov__row--on' : ''}`}
          >
            <button
              type="button"
              className="tc-cov__name"
              title={`切换查看 ${r.course}`}
              aria-label={`查看课程 ${r.course}`}
              onClick={() => onSelectCourse(r.course)}
            >
              {r.course === active ? '▸ ' : ''}
              {r.course}
            </button>
            <span className="tc-cov__phase" title={`阶段：${PHASE_LABEL[r.phase.phase] ?? r.phase.phase}`}>
              {PHASE_LABEL[r.phase.phase] ?? r.phase.phase}
            </span>
            <span className="tc-cov__comps">
              {r.components.map((c) => (
                <span
                  key={c.key}
                  className="tc-cov__comp"
                  title={`${c.key}: ${c.status}${c.pid ? ` (PID ${c.pid})` : ''}`}
                >
                  <span className={dotClass(c)} />
                  <span className="tc-cov__compname">{COMP_SHORT[c.key] ?? c.key}</span>
                </span>
              ))}
            </span>
            <span className="tc-cov__metrics">
              {r.last ? (
                <>
                  <b>it{r.last.iter}</b>
                  <span title="winRate">胜 {fmtPct(r.last.winRate)}</span>
                  <span title="rollout_sec">采 {fmtSec(r.last.rolloutSec)}</span>
                  <span title="ppo_sec">训 {fmtSec(r.last.ppoSec)}</span>
                  {r.last.halted ? <span className="tc-cov__halted">本轮停车</span> : null}
                </>
              ) : (
                <span className="tc-muted">未训练</span>
              )}
              <span className="tc-muted" title="训练日志轮数">
                {r.iters} 轮
              </span>
            </span>
            <span className="tc-cov__badges">
              {r.cloudHalt ? (
                <span
                  className={`tc-badge ${r.cloudHalt.status === 'halted' ? 'tc-badge--r' : 'tc-badge--gray'}`}
                  title={r.cloudHalt.reason}
                >
                  {r.cloudHalt.status === 'halted' ? '停机中' : '曾停机'}
                </span>
              ) : null}
              {r.ppoQueueStall ? (
                <span
                  className="tc-badge tc-badge--y"
                  title={`job ${r.ppoQueueStall.jobId} 等待 ${r.ppoQueueStall.waitedSec}s 无 worker 领取`}
                >
                  排队超时
                </span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
