/** CourseOverview.tsx — 并行课程总览（多课程单 hub，2026-09-18）。
 *
 *  一行一门课：在训 / 离线 / iter / 队列深度 / 在飞；表头一行是 hub 自身的调度状态
 *  （应答基址 · 在派发课程数 / 活跃 worker 数 · 竞速 · 停机）。
 *
 *  为什么它必须存在：多课程并行后「某门课为什么在饿着」有四种可能——不在 hub 课程表里、
 *  被标成离线、队列里有活但没有（空闲的）worker、队列是空的（训练侧还没发布）。这四者
 *  在本面板上是四个不同的格子，在日志里要靠猜。
 *
 *  交互：点任意行 = 切到查看该课程（与顶部课程 select 同一条路径 `onSelectCourse`，
 *  故 LAN 只读视图下同样可用——它只改本浏览器的查看目标，不碰任何训练状态）。 */

import { shortUrl, type ParallelOverviewView } from '../../view'

export interface CourseOverviewProps {
  overview: ParallelOverviewView | null
  /** 当前查看课程（高亮）。 */
  course: string
  onSelectCourse: (course: string) => void
  /** 动作通道（R3-2：切离线/在线）。可缺省——只读视图/单测直接渲染时不接动作。
   *  返回类型放 `unknown`：app 的 `doAction` 回 `{ok}`，无关的回传值不该逼调用方套壳。 */
  onAction?: (act: string, body: Record<string, unknown>) => unknown
}

/** 单行状态徽标的（文案 + 修饰类）。 */
function rowBadge(r: ParallelOverviewView['rows'][number]): {
  text: string
  cls: string
  title: string
} {
  if (!r.hubSeen && r.training) {
    return {
      text: '在训·hub 未注册',
      cls: 'tc-cov__badge--warn',
      title: 'hub 的课程表里没有这门课——它的 job 永远不会被派发。以 --course <课> 重启 hub',
    }
  }
  if (r.offline) {
    return {
      text: '离线（只收回传）',
      cls: 'tc-cov__badge--off',
      title: 'hub 把这门课标为离线：不实时派发 PPO，只接收 it 权重/指标回传',
    }
  }
  // 「在训」= 共享 trainer 在跑 ∧ 这门课没被收官（2026-09-19 / R3-5：trainer 是一个进程
  // 服务所有课程，按课查进程存活是共享 trainer 时代的假事实）。
  if (r.training)
    return { text: '在训', cls: 'tc-cov__badge--on', title: '共享 trainer 在跑，且这门课未收官' }
  return { text: '停', cls: 'tc-cov__badge--idle', title: '调度器没在跑，或这门课已收官' }
}

export function CourseOverview({
  overview,
  course,
  onSelectCourse,
  onAction,
}: CourseOverviewProps) {
  if (!overview || overview.rows.length === 0) return null
  const trainingCount = overview.rows.filter((r) => r.training).length
  const recentDispatch = overview.recentDispatch
  return (
    <section className="tc-cov" aria-label="并行课程总览">
      <div className="tc-cov__head">
        <span className="lbl">并行课程</span>
        <b>{trainingCount}</b>
        <span className="tc-cov__sep">·</span>
        {overview.hubOnline && overview.hubUrl ? (
          <span className="tc-cov__hub" title={`队列观测源：${overview.hubUrl}/admin/queue`}>
            hub {shortUrl(overview.hubUrl)}
          </span>
        ) : (
          <span
            className="tc-cov__hub tc-cov__hub--dead"
            title="没有任何 hub 在应答 /admin/queue——队列列显示为 —；先启 hub-server"
          >
            hub 无应答
          </span>
        )}
        <span className="tc-cov__stat" title="在实时派发的课程数 / 窗口内活跃 worker 数">
          在派发 {overview.activeCourses} / worker {overview.activeWorkers}
        </span>
        {recentDispatch ? (
          <span
            className="tc-cov__cursor"
            title="hub 轮转游标：上一份 job 派给了这门课（下一份从它的下一门开始扫）"
          >
            最近派发 {recentDispatch}
          </span>
        ) : null}
        {overview.raceActive ? (
          <span
            className="tc-cov__race"
            title="在派发课程数 < 活跃 worker 数 ⇒ 最新 job 广播给所有 worker，先回传者胜"
          >
            竞速
          </span>
        ) : null}
        {overview.halt ? (
          <span
            className="tc-cov__halt"
            title="已向云机下发停机达令（任务照常分发，云机停不掉就继续干活）"
          >
            停机中
          </span>
        ) : null}
      </div>
      <div className="tc-cov__rows">
        {overview.rows.map((r) => {
          const b = rowBadge(r)
          const viewing = r.course === course
          // 模式开关只在 hub 认识这门课时给（hub 不认识的课程 → 400，按钮就是假承诺）。
          const canToggle = overview.hubOnline && r.hubSeen && Boolean(onAction)
          return (
            <div className="tc-cov__rowwrap" key={r.course}>
              <button
                type="button"
                className={`tc-cov__row${viewing ? ' tc-cov__row--cur' : ''}`}
                aria-current={viewing ? 'true' : undefined}
                title={viewing ? `${r.course}（当前查看）` : `切到查看 ${r.course}`}
                onClick={() => onSelectCourse(r.course)}
              >
                <span className="tc-cov__name">{r.course}</span>
                <span className={`tc-cov__badge ${b.cls}`} title={b.title}>
                  {b.text}
                </span>
                <span className="tc-cov__iter" title="该课账本尾行的 iteration">
                  {r.iter === null ? '—' : `it${r.iter}`}
                </span>
                <span
                  className="tc-cov__q"
                  title={
                    overview.hubOnline
                      ? `队列深度 ${r.queuePending}（可领取 job 数）· 在飞 ${r.inflight}`
                      : 'hub 无应答——队列数据不可得'
                  }
                >
                  {overview.hubOnline ? `队列 ${r.queuePending} · 在飞 ${r.inflight}` : '队列 —'}
                </span>
              </button>
              {canToggle ? (
                <button
                  type="button"
                  className="tc-cov__mode"
                  title={
                    r.offline
                      ? '切回在线：hub 恢复为这门课实时派发 PPO'
                      : '切离线：hub 不再实时派发这门课的 PPO，只接收 it 权重/指标回传（本机训练与账本不动）'
                  }
                  onClick={() =>
                    void onAction?.('setCourseMode', {
                      course: r.course,
                      mode: r.offline ? 'online' : 'offline',
                    })
                  }
                >
                  {r.offline ? '恢复在线' : '切离线'}
                </button>
              ) : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}
