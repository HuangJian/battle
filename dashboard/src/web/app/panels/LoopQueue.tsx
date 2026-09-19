/** LoopQueue.tsx — 训练调度器（**单例**）卡片：每课一行 + 「在等什么」（R2c-3）。
 *
 *  为什么必须存在：多课程并行后训练收敛为**一个进程**（plan/r2-loop-task-queue §7），
 *  「某门课这一轮为什么还没走完」今天要翻 N 份日志 + 猜。本卡片把 python 只读侧已经算好的
 *  答案直接上屏：每课 下一步 / 待办深度 / 在飞 job / **在等什么**（等远端回传哪个 job、
 *  采集落了几局、还是根本没人跑）。
 *
 *  与「并行课程总览」的分工：总览回答**hub 侧**「谁在派活、谁离线」（job 队列），本卡片
 *  回答**训练侧**「这一轮卡在哪一步」（任务队列）。两张卡看的是同一条流水线的两段。
 *
 *  两个事实源合起来才是完整的一行：python（盘上事实：指针/在飞/采集）说「卡在哪」，
 *  registry（`training`）说「这门课此刻有没有人在跑」——后者缺了就会把「停了的课」读成
 *  「等外部」。
 *
 *  交互：点行 = 切到查看该课程（与顶部课程 select / 总览卡同一条路径，LAN 只读下同样可用：
 *  它只改本浏览器的查看目标，不碰训练状态）。
 */

import {
  type LoopQueueRow,
  type LoopQueueView,
  pauseBadge,
  pauseLabel,
  pauseTitle,
} from '../../view'

/** 未在训那一行的悬停全文（导出给用例断言，避免文案与断言两处漂移）。 */
export const STOPPED_TITLE =
  '没有存活的 trainingLoop 进程——下面是**盘上事实**推出的队列状态（若交给调度器会怎么做）'

export interface LoopQueueProps {
  loopQueue: LoopQueueView | null
  /** 当前查看课程（高亮）。 */
  course: string
  onSelectCourse: (course: string) => void
  /** 动作派发（写暂停意图）。缺省 = 不渲染暂停按钮（LAN 只读 / 无动作能力时**不假装能控**）。 */
  onAction?: (act: string, body: Record<string, unknown>) => unknown
}

/** 「在等什么」的（修饰类 + 悬停解释）。kind 的语义在 python `waiting_state` 里定死。 */
function waitCls(kind: LoopQueueRow['waiting']['kind']): string {
  switch (kind) {
    // 在飞 = 结果在别的进程/机器上，运维唯一能干预的一类 → 最醒目
    case 'inflight':
      return 'tc-loopq__wait--inflight'
    case 'collect':
      return 'tc-loopq__wait--collect'
    case 'idle':
      return 'tc-loopq__wait--idle'
    default:
      return ''
  }
}

const WAIT_TITLES: Record<LoopQueueRow['waiting']['kind'], string> = {
  inflight: '已发布的 job 还没回传——结果在 GPU worker / 云机上；换节点或检查 worker 日志',
  collect: '本轮采集还在落盘（局数来自 it<N>/ 下的 manifest，配额只有课程计划知道）',
  idle: '本轮没有待办：账本已结算这一轮，或这门课还没开训',
  ready: '盘上事实看不出外部等待——没有在飞 job，采集也没在跑',
}

/** 资源池票（本机 PPO / eval 定案为跨课排队 = 1）。 */
function poolText(pools: LoopQueueView['pools']): string {
  const names = Object.keys(pools).sort()
  if (names.length === 0) return ''
  return names.map((n) => `${n} ${pools[n]!.held}/${pools[n]!.capacity}`).join(' · ')
}

export function LoopQueue({ loopQueue, course, onSelectCourse, onAction }: LoopQueueProps) {
  if (!loopQueue) return null
  const { rows, error } = loopQueue
  if (error && rows.length === 0) {
    // 读失败要显因（不静默）：它不影响训练，只影响这个观测面
    return (
      <section className="tc-loopq" aria-label="训练调度器">
        <div className="tc-loopq__head">
          <span className="lbl">调度器</span>
          <span className="tc-loopq__singleton" title="一个进程服务所有并行课程">
            单例
          </span>
          <span className="tc-loopq__err" title={error}>
            只读视图不可用：{error}
          </span>
        </div>
      </section>
    )
  }
  if (rows.length === 0) return null
  const pools = poolText(loopQueue.pools)
  const blocked = loopQueue.blockedCourses
  const waiting = rows.filter((r) => r.waiting.kind === 'inflight' && r.training).length
  return (
    <section className="tc-loopq" aria-label="训练调度器">
      <div className="tc-loopq__head">
        <span className="lbl">调度器</span>
        <span className="tc-loopq__singleton" title="一个进程服务所有并行课程（每课一条任务队列）">
          单例
        </span>
        <span
          className="tc-loopq__stat"
          title="这些课程有存活的 trainingLoop 进程；总数 = 账本可发现的课程数"
        >
          在训 {loopQueue.trainingCount}/{rows.length}
        </span>
        {waiting > 0 ? (
          <span
            className="tc-loopq__waitcount"
            title="正在等远端 job 回传的在训课程数（结果在 GPU worker / 云机上）"
          >
            {waiting} 课等回传
          </span>
        ) : null}
        {blocked.length > 0 ? (
          <span
            className="tc-loopq__blocked"
            title="本机重资源（PPO / eval）池已满：这些课在排队等票（单进程调度器的正常态）"
          >
            排队等资源：{blocked.join('、')}
          </span>
        ) : null}
        {pools ? (
          <span className="tc-loopq__pools" title="本机重资源池占用/容量（任一时刻可持有票数）">
            {pools}
          </span>
        ) : null}
        {error ? (
          <span className="tc-loopq__err" title={error}>
            （上一拍读失败，显示的是缓存）
          </span>
        ) : null}
      </div>
      <div className="tc-loopq__rows">
        {rows.map((r) => {
          const viewing = r.course === course
          const badge = pauseBadge(r)
          return (
            // 暂停/恢复开关是行按钮的**兄弟节点**（行本身是 <button>，嵌套 button 非法）。
            <div className="tc-loopq__rowwrap" key={r.course}>
              <button
                key={r.course}
                type="button"
                className={`tc-loopq__row${viewing ? ' tc-loopq__row--cur' : ''}${
                  r.training ? '' : ' tc-loopq__row--stopped'
                }`}
                aria-current={viewing ? 'true' : undefined}
                title={viewing ? `${r.course}（当前查看）` : `切到查看 ${r.course}`}
                onClick={() => onSelectCourse(r.course)}
              >
                <span className="tc-loopq__name">{r.course}</span>
                <span
                  className={`tc-loopq__badge ${r.training ? 'tc-loopq__badge--on' : 'tc-loopq__badge--idle'}`}
                  title={r.training ? 'trainingLoop 进程存活（registry）' : STOPPED_TITLE}
                >
                  {r.training ? '在训' : '未在训'}
                </span>
                <span className="tc-loopq__iter" title="账本指针：下一轮要跑的 it">
                  it{r.it}
                </span>
                <span className="tc-loopq__step" title={`下一步：${r.current || '（本轮无待办）'}`}>
                  {r.current || '—'}
                </span>
                <span
                  className="tc-loopq__pending"
                  title={`待办 ${r.pending.length} 步（顺序即依赖顺序）：${r.pending.join(' → ')}`}
                >
                  待办 {r.pending.length}
                </span>
                <span
                  className={`tc-loopq__wait ${waitCls(r.waiting.kind)}`}
                  title={WAIT_TITLES[r.waiting.kind]}
                >
                  {r.waiting.text || '—'}
                </span>
              </button>
              {/* 开关改的是**意图文件**，能不能生效由训练进程决定 ⇒ 旁边再挂一个事实徽标，
                  诚实区分「已暂停」与「待生效」（只显示意图会骗人，只显示事实会点完没反馈）。 */}
              {onAction ? (
                <button
                  type="button"
                  className="tc-loopq__pausebtn"
                  title={pauseTitle(r)}
                  onClick={() =>
                    void onAction('setCoursePaused', {
                      course: r.course,
                      paused: !r.pausedIntent,
                    })
                  }
                >
                  {pauseLabel(r)}
                </button>
              ) : null}
              {badge ? (
                <span className={`tc-loopq__pause ${badge.cls}`} title={pauseTitle(r)}>
                  {badge.text}
                </span>
              ) : null}
            </div>
          )
        })}
      </div>
      {/* 页脚：口径来源 + （有排队时）一句人话——等票不是卡死。 */}
      <div className="tc-loopq__foot">
        {blocked.length > 0
          ? '本机重资源跨课排队（容量 1）：等票的课不是卡死，持票课一步跑完即自动继续。'
          : footerHint(rows, loopQueue.trainingCount)}
      </div>
    </section>
  )
}

/** 页脚提示：有课在等外部时点名它（「其余课照常推进」是单进程调度器的核心承诺），
 *  否则说明「在等什么」的口径来源——避免操作员以为它比真相还权威。 */
function footerHint(rows: LoopQueueRow[], trainingCount: number): string {
  const r = rows.find((x) => x.inflight.length > 0 && x.training)
  if (r) {
    const job = r.inflight[0]!
    const who = job.jid
      ? `（jid=${job.jid.slice(0, 12)}${job.dispatch ? ` via ${job.dispatch}` : ''}）`
      : ''
    return `${r.course} 正在等 ${job.phase}@${job.round}${who} 回传；其余课照常推进（等外部不占执行权）。`
  }
  return `口径：训练侧只读视图 run_rl_cluster.py --json（${rows.length} 课）+ registry 在训事实；${trainingCount}/${rows.length} 课有存活进程。`
}
