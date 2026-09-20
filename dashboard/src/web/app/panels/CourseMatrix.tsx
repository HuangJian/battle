/** CourseMatrix.tsx — 课程矩阵：把「并行课程总览」（hub 侧）与「训练调度器」（训练侧）
 *  合并成**一张**表（问题 C5）。合并规则与全部纯函数在 `view/course-matrix.ts`。
 *
 *  ## 为什么合并（而不是并排摆两块）
 *
 *  两块卡回答的是同一个问题——「这门课现在怎么样」——却各写一半，而且**互相看不见**。
 *  于是最该看到的两种局面在两边各自都是「正常」的：
 *
 *  - hub 在给一门**没有任何进程推进**的课派活 ⇒ job 越堆越多，没人消费；
 *  - 一门课**疯狂训练但 hub 没注册它** ⇒ PPO 永远不被派发，算力白烧（rollout 白跑）。
 *
 *  合并之后这两条落在**状态列**上（`hub 已注册 · 无进程` / `在训 · hub 未注册`），也就是
 *  操作员第一眼看的那一列。
 *
 *  ## 为什么这里是一张 `<table>` 而不是 `StatusRow` 芯片行
 *
 *  `StatusRow` 是「一行实体 + 状态 + 指标 + 动作」的**芯片行**原语（组件卡 / 节点 / worker）：
 *  它是 `inline-flex` 且会换行，**跨行不对齐**。矩阵的七列是要**竖着比**的（哪门课队列最深、
 *  哪门课段内停最久）——芯片行拼出来的「表」在同一列上每行宽度都不同，比较只能靠读。
 *  所以矩阵用真表格（`tc-table` 基础样式 + `tabular-nums` 数字列），但**状态词表与语义档
 *  仍走 `matrixStatus` + `StatusDot`**（一个状态只有一个说法、一个颜色），这条才是 §4.2 的实质。
 *
 *  ## 只读语义
 *
 *  LAN 只读时动作按钮**照常渲染且可点**（§7 O1）：只读是服务端边界（403），不是把按钮涂灰。
 *  `onAction` 缺省 = 这个渲染上下文没有动作通道（单测/无动作能力），此时**不假装能控**、
 *  一个都不渲染——与合并前两张卡同一判据。
 */

import {
  kindBadge,
  type CourseMatrixRow,
  type LoopQueueView,
  type ParallelOverviewView,
  matrixBadgeTone,
  matrixDotTone,
  matrixFoot,
  matrixMeta,
  mergeCourseRows,
  pauseOp,
  pendingTitle,
  stepTitle,
  waitingClass,
} from '../../view'
import { Empty } from '../../components/Empty'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusDot } from '../../components/StatusDot'

/** 切课按钮的悬停（导出给用例断言，避免文案与断言两处漂移）。 */
export function pickTitle(course: string, viewing: boolean): string {
  return viewing ? `${course}（当前查看）` : `切到查看 ${course}`
}

export interface CourseMatrixProps {
  /** hub 侧事实（`null` = 没读到）。 */
  overview: ParallelOverviewView | null
  /** 训练侧事实（`null` = 只读视图不可用）。 */
  loopQueue: LoopQueueView | null
  /** 当前查看课程（高亮）。 */
  course: string
  onSelectCourse: (course: string) => void
  /** 动作通道。缺省 = 不渲染动作列（见文件头注）。 */
  onAction?: (act: string, body: Record<string, unknown>) => unknown
}

export function CourseMatrix({
  overview,
  loopQueue,
  course,
  onSelectCourse,
  onAction,
}: CourseMatrixProps) {
  // 「段内多久没动」要当下时刻：读表在这里发生，纯函数只收数字（可单测、可回放）。
  const nowSec = Math.floor(Date.now() / 1000)
  const rows = mergeCourseRows({ overview, queue: loopQueue, viewing: course, nowSec })

  // 两侧都没东西可说时不留空壳；但**读失败必须显因**（不静默）——那是运维唯一能修的线索。
  if (rows.length === 0) {
    const err = loopQueue?.error
    if (!err) return null
    return (
      <section className="tc-mx" aria-label="课程矩阵">
        <SectionHeader title="课程" />
        <Empty kind="error" reason={`只读视图不可用：${err}`} />
      </section>
    )
  }

  const meta = matrixMeta({ overview, queue: loopQueue })
  return (
    <section className="tc-mx" aria-label="课程矩阵">
      <SectionHeader
        title="课程"
        count={rows.length}
        hint="每门课现在怎么样：hub 侧（派活/队列/离线段）与训练侧（指针/卡在哪一步/在等什么）合并成一行"
      />
      <div className="tc-mx__meta">
        {/* 「单例」是训练进程的形态事实（不是读数）：一个进程服务所有并行课程，每课一条队列。 */}
        <span className="tc-mx__singleton" title="一个进程服务所有并行课程（每课一条任务队列）">
          单例
        </span>
        {meta.map((m) => (
          <span
            key={m.text}
            className={`tc-mx__chip${m.tone ? ` tc-mx__chip--${m.tone}` : ''}`}
            title={m.title}
          >
            {m.text}
          </span>
        ))}
      </div>
      <div className="tc-tablewrap">
        <table className="tc-table tc-mx__table">
          <thead>
            <tr>
              <th scope="col">课程</th>
              <th scope="col">状态</th>
              <th scope="col" className="tc-mx__num">
                iter
              </th>
              <th scope="col">本轮</th>
              <th scope="col">在等什么</th>
              <th scope="col" className="tc-mx__num">
                队列 · 在飞
              </th>
              <th scope="col">段内</th>
              {onAction ? <th scope="col">操作</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <MatrixTr
                key={r.course}
                row={r}
                onSelectCourse={onSelectCourse}
                onAction={onAction}
              />
            ))}
          </tbody>
        </table>
      </div>
      <div className="tc-mx__foot">{matrixFoot({ queue: loopQueue, rows })}</div>
    </section>
  )
}

function MatrixTr({
  row: r,
  onSelectCourse,
  onAction,
}: {
  row: CourseMatrixRow
  onSelectCourse: (course: string) => void
  onAction?: (act: string, body: Record<string, unknown>) => unknown
}) {
  const lq = r.lq
  const kind = lq ? kindBadge(lq) : null
  const pause = pauseOp(lq)
  const badgeTone = matrixBadgeTone(r.status.tone)
  return (
    <tr
      className={`tc-mx__row${r.viewing ? ' tc-mx__row--cur' : ''}${
        lq && !lq.training ? ' tc-mx__row--stopped' : ''
      }`}
      // 冲突行整行加底色：它不是某一格的问题，是两半事实在打架。
      data-conflict={r.conflict ?? undefined}
    >
      <th scope="row" className="tc-mx__course">
        {/* 切课按钮只包住课程名（不是整行）：整行可点会让表格里的文字选不中，
            而这一列旁边就有「操作」列的两个开关。 */}
        <button
          type="button"
          className="tc-mx__pick"
          aria-current={r.viewing ? 'true' : undefined}
          title={pickTitle(r.course, r.viewing)}
          onClick={() => onSelectCourse(r.course)}
        >
          <StatusDot tone={matrixDotTone(r.status.tone)} title={r.status.title} />
          <b>{r.course}</b>
        </button>
        {kind ? (
          <span className={`tc-badge tc-badge--${kind.tone}`} title={kind.title}>
            {kind.text}
          </span>
        ) : null}
      </th>
      <td>
        <span className={`tc-badge tc-badge--${badgeTone}`} title={r.status.title}>
          {r.status.text}
        </span>
      </td>
      <td className="tc-mx__num" title={r.iter === null ? iterMissingTitle(r) : iterTitle(r)}>
        {r.iter === null ? '—' : `it${r.iter}`}
      </td>
      <td className="tc-mx__round">
        {lq ? (
          <>
            <span title={stepTitle(lq)}>{lq.current || '—'}</span>
            <span className="tc-mx__todo" title={pendingTitle(lq)}>
              待办 {lq.pending.length}
            </span>
          </>
        ) : (
          <span title={queueMissingTitle()}>—</span>
        )}
      </td>
      <td className={`tc-mx__wait ${waitingClass(lq)}`} title={r.waiting.title}>
        {r.waiting.text}
      </td>
      <td className="tc-mx__num" title={r.queue.title}>
        {r.queue.text}
      </td>
      <td
        className={r.segment?.warn ? 'tc-mx__seg tc-mx__seg--stale' : 'tc-mx__seg'}
        title={r.segment?.title}
      >
        {r.segment?.text ?? '—'}
      </td>
      {onAction ? (
        <td className="tc-mx__ops">
          {/* 两个开关**并列**（§7 O4）：必须在视觉与文案上区分归属，否则会被读成「一个开关
              管两件事」。分隔线 + 各自的 role=group aria-label 前缀（hub：/ 本地：）钉住归属。 */}
          {r.canToggleMode ? (
            <span
              className="tc-mx__opgroup"
              role="group"
              aria-label={`hub：${r.ov?.offline ? '恢复在线' : '切离线'}`}
            >
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                aria-label={`hub：${r.ov?.offline ? `恢复在线 ${r.course}` : `切离线 ${r.course}`}`}
                title={
                  r.ov?.offline
                    ? '切回在线：hub 恢复为这门课实时派发 PPO（写 hub 课程表）'
                    : '切离线：hub 不再实时派发这门课的 PPO，只接收 it 权重/指标回传（本机训练与账本不动）；写 hub 课程表'
                }
                onClick={() =>
                  void onAction('setCourseMode', {
                    course: r.course,
                    mode: r.ov?.offline ? 'online' : 'offline',
                  })
                }
              >
                {r.ov?.offline ? '恢复在线' : '切离线'}
              </button>
            </span>
          ) : null}
          {pause ? (
            <>
              <span className="tc-mx__opsep" aria-hidden="true" />
              <span className="tc-mx__opgroup" role="group" aria-label={`本地：${pause.label}`}>
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  aria-label={`本地：${pause.label} ${r.course}`}
                  title={pause.title}
                  onClick={() =>
                    void onAction('setCoursePaused', {
                      course: r.course,
                      paused: !lq!.pausedIntent,
                    })
                  }
                >
                  {pause.label}
                </button>
              </span>
              {/* 事实徽标：开关改的是**意图文件**，能不能生效由训练进程决定 ⇒ 诚实区分
                  「已暂停」与「待生效」（只看意图会骗人，只看事实则点完没反馈）。 */}
              {pause.badge ? (
                <span
                  className={`tc-mx__pausebadge tc-badge tc-badge--${pause.badge.tone}`}
                  title={pause.title}
                >
                  {pause.badge.text}
                </span>
              ) : null}
            </>
          ) : null}
        </td>
      ) : null}
    </tr>
  )
}

/** 指针悬停：说清它是「下一轮要跑的 it」以及**来自哪一侧**。 */
function iterTitle(r: CourseMatrixRow): string {
  const src = r.iterSource === 'queue' ? '训练侧队列（下一轮要跑）' : 'hub 侧账本尾行'
  const cross = r.iterSource === 'queue' ? '；hub 侧账本尾行另见 /metrics' : ''
  return `账本指针 it${r.iter} —— 来自${src}${cross}`
}

/** 两侧都没给出指针：**说清是不知道**，不是 0。 */
function iterMissingTitle(r: CourseMatrixRow): string {
  return r.lq || r.ov
    ? '没有账本指针：这门课还没有 iteration 事件（未开训 / 账本还没结算过这一轮）'
    : '两侧都读不到——指针不可知'
}

function queueMissingTitle(): string {
  return '训练侧只读视图未读——本轮步骤与待办不可知（不是「无待办」）'
}
