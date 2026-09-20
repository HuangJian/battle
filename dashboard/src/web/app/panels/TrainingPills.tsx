/** TrainingPills.tsx — 顶栏里的**在训课程** pill 组（用户 2026-09-20 口径）。
 *
 *  交互（用户原话：「顶部课程 select 选择某课程，点击「训练」按键；正在训练的所有课程，
 *  都在顶部显示为一个 pill，概览显示 it 数和状态（参考节点 pill），有停止按键，点击 pill 后
 *  切换显示其趋势图和指标表，并高亮 pill；点击停止按键后，停止课程，从顶部区域移除」）：
 *    · **点 pill** = 切查看目标（`selectCourse`）：Hero 趋势 + 抽屉里的指标表随查看课程走，
 *      当前查看的那门 pill 高亮（`--active`）；
 *    · **■ 停课** = 非破坏停课（`stopCourse`：删开课标记 + 暂停意图 + 该课 hub 置 offline），
 *      服务端下一拍 stamp 里不再有它 ⇒ pill 自行从顶部消失（不做本地乐观删除：队列/账本
 *      一个字没动这件事，只能由服务端事实说话）；
 *    · **只读视图**（局域网）：pill 仍可点（切查看目标只是「本浏览器看哪门课」，与课程 select
 *      同权），停课按钮点了由服务端 403 + flash 兜底（与其它动作键同哲学：只读是动作边界，
 *      不是按钮状态）。
 *
 *  为什么在顶栏（而不是再塞进课程 select 里）：多课程并行时「哪几门在训」是操作员每分钟要看
 *  几十次的事实，而 select 只显示**选中的一门**——旧版把其余课程堆成一串文字
 *  （「正在训练：a、b、c」），既读不出各自进度，也没有停课入口。
 *
 *  ★ 为什么**不单开一行**（用户 2026-09-20：「要和课程 select 挤进同一行，避免占用宝贵的纵向
 *  页面空间」）：pill 数是零到几（0 时整个组件不渲染），而每一行都稳定吃掉 ~34px 纵向——那种
 *  「有时有内容、有时空白」的行最亏。故它是顶栏行内的一个 flex 项；pill 多时**本组内部横向滚动**，
 *  不换行（换行 = 又占回纵向空间）。
 *
 *  ★ 组前的「在训」文字标签与整组**靠左**（用户 2026-09-20）：标签已删——pill 自己带课名/
 *  it/状态，而顶栏右半是全局读数（阶段 / 节点 / 刷新），左半是「我在看什么」；本组 `flex: 1`
 *  接在标题之后，把全局读数顶到最右（课程选择器 2026-09-20 已在侧栏，与本组不再同行）。
 */

import { coursePills, type CoursePillTone, type LoopQueueRow } from '../../view'

export interface TrainingPillsProps {
  /** 服务端 stamp 的已开课课程（`trainingCourses`，事实源 = 开课标记）。 */
  courses: string[]
  /** 调度器队列行（`loopQueue.rows`；读面不可用时空数组）——逐课的 it / 状态来源。 */
  rows: LoopQueueRow[]
  /** 共享 trainer 是否在跑（「已开课」与「进程在跑」是两件事，状态文案要说清）。 */
  trainerRunning: boolean
  /** 当前查看课程（高亮 + 「正在看」提示）。 */
  viewCourse: string
  onSelect: (course: string) => void
  onStop: (course: string) => void
  readOnly?: boolean
}

/** 只读视图的停课按钮提示（与节点 pill 同款口径）。 */
const RO_STOP_TITLE = '只读模式：停课仅限本机 localhost（点击会被服务端拒绝）'

/** 色调 → 圆点 / 状态字色的 class（与节点 pill 的 tc-dot--* 同一套色板）。 */
const DOT: Record<CoursePillTone, string> = {
  g: 'tc-dot tc-dot--on',
  y: 'tc-dot tc-dot--warn',
  r: 'tc-dot tc-dot--dead',
  gray: 'tc-dot tc-dot--empty',
}

export function TrainingPills({
  courses,
  rows,
  trainerRunning,
  viewCourse,
  onSelect,
  onStop,
  readOnly,
}: TrainingPillsProps) {
  const pills = coursePills({ courses, rows, trainerRunning })
  // 一门课都没开 ⇒ 整个组件不渲染（顶部保持干净：空块/空行会被读成「有东西没加载出来」）。
  if (pills.length === 0) return null
  // 组前的文字标签「在训」已删（2026-09-20 用户指令）——`aria-label` 保留：屏幕阅读器
  // 需要一个组名，而它在页面上不可见（不吃视觉噪声）。
  return (
    <div className="tc-tpills" aria-label="在训课程">
      {pills.map((p) => {
        const active = p.course === viewCourse
        return (
          <span
            key={p.course}
            className={`tc-tpill${active ? ' tc-tpill--active' : ''}`}
            role="button"
            tabIndex={0}
            aria-label={
              `${p.course}，${p.status}，it${p.it ?? '—'}` +
              (active ? '（正在查看）' : '，点击查看其趋势与指标')
            }
            title={
              (active ? '正在查看这门课。' : `点击查看 ${p.course} 的趋势与指标表。`) +
              `${p.title}\n停课 = 非破坏：删开课标记 + 暂停调度 + 该课 hub 置离线；队列与账本一个字不动。`
            }
            onClick={() => onSelect(p.course)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(p.course)
              }
            }}
          >
            <span className={DOT[p.tone]} />
            {p.kind === 'bc' ? <span className="tc-tpill__kind">BC</span> : null}
            <b>{p.course}</b>
            <span className="v">it{p.it ?? '—'}</span>
            <span className="tc-tpill__state">{p.status}</span>
            <button
              type="button"
              className="tc-tpill__stop"
              aria-label={`停课 ${p.course}`}
              title={readOnly ? RO_STOP_TITLE : `停课 ${p.course}（非破坏）`}
              // 停课不能同时把查看目标切过去（否则点个停止键就看走眼了）。
              onClick={(e) => {
                e.stopPropagation()
                onStop(p.course)
              }}
            >
              ■
            </button>
          </span>
        )
      })}
    </div>
  )
}
