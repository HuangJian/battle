/** Sidebar.tsx — 控制台侧栏 = 共用导航（NavSidebar）+ 控制台专有的全局设置。
 *
 *  为什么课程选择器必须在这里（docs/dashboard-redesign.md §3.1 / 问题 C3）：课程是整页的
 *  **主语**，此前被 `flex: 1` 挤在顶栏正中间与状态 chip 抢权重。移到侧栏底部后：
 *  ① 位置固定、全页只有一处；② 顶栏只剩「本页」与「全局状态」，不再混入主控对象；
 *  ③ 侧栏底部天然是「全局设置」的家——连带把触发门禁与刷新间隔也从顶栏收进来。
 *
 *  只读可见性（§5.4）：LAN 只读常驻显示锁徽标（不可关闭），取代此前可关闭横幅的常驻职责；
 *  动作按钮**保持可点**（§7 O1 已决：物理禁用会让组件区看起来灰败破碎），误点由服务端 403 兜底。
 *
 *  开课入口（2026-09-20 合并远端「进程与课程解耦」）：课程级旋钮（训练模式 / rollout 位置 /
 *  降级本机）随「训练」键弹窗下发——它紧凑课程选择器（读序：选课 → 训经 → 看哪几门在训）。
 *  **停课不在这里**：停在**每门课的 pill** 上（按课停、按课消失）；一个键同时做「开这门」
 *  与「停这门」在两门课并存时语义不明。此前 select 后面挂的「还有在训：a、b」串行标签
 *  已由顶栏的 pill 行取代（同一个事实不再上屏两次）。
 *
 *  导航本体在 `NavSidebar.tsx`（/eval 与 /log 两个独立 bundle 复用同一组件，见该文件头注）；
 *  本文件只负责「控制台这一页多出来的东西」——课程、开课、门禁、刷新间隔。
 */

import type { JSX } from 'preact'
import { refreshLabel, REFRESH_INTERVALS, type PageKey, type RefreshSec } from '../../view'
import { NavSidebar } from './NavSidebar'

export interface SidebarProps {
  /** 导航激活判定的路径 = 当前页面的**规范路径**（`canonicalPath(page)`）。 */
  activePath: string
  /** 当前查看课程（空串 = 自动/最近活跃）。 */
  course: string
  courses: string[]
  /** 在训课程（= 已开课；课程 select 内标 🔥，与顶栏 pill 行同源）。 */
  trainingCourses: string[]
  onCourseChange: (course: string) => void
  /** 开课（弹窗）——课程级选项随它下发。 */
  onOpenCourse: () => void
  /** 该课是否已在课程表（`courseLifecycle.enabled`）；**null = 旧视图无此事实 ⇒ 不渲染该键**
   *  （宁可少一个按钮，不可给一个假承诺）。 */
  courseEnabled: boolean | null
  /** route 类导航项的点击（拦截为 pushState；link 类不传此回调，走真链接）。 */
  onNavigate: (page: PageKey, e: JSX.TargetedMouseEvent<HTMLAnchorElement>) => void
  /** 局域网只读（服务端 stamp）：锁徽标常驻。 */
  readOnly: boolean
  /** 触发门禁（仅在有训练时显示；切换即时对下一轮门判定生效）。 */
  gate: {
    visible: boolean
    mode: 'halt' | 'notify'
    /** 只读/非本机时禁用（服务端也会 403 兜底）。 */
    disabled: boolean
    onChange: (m: 'halt' | 'notify') => void
  }
  refresh: { value: RefreshSec; onChange: (v: RefreshSec) => void }
}

const GATE_TITLE =
  '门禁触发时对云端 PPO worker 的动作。\n' +
  '· 停机（默认）：下发停机达令，云机释放。\n' +
  '· 提示：只记录 verdict 并显示告警，不停机——平台期（G4）会每 5 轮复现，' +
  '停机等于反复杀掉 PPO worker。\n' +
  '切换后立即对下一轮门判定生效，无需重启训练。'

const COURSE_TITLE_RO = '局域网只读：切换仅影响当前浏览器的查看课程，不影响训练'
const COURSE_TITLE_LOCAL = '切换查看课程（含历史课程）。不影响任何在训课程'

export function Sidebar({
  activePath,
  course,
  courses,
  trainingCourses,
  onCourseChange,
  onOpenCourse,
  courseEnabled,
  onNavigate,
  readOnly,
  gate,
  refresh,
}: SidebarProps) {
  const trainingSet = new Set(trainingCourses)

  return (
    <NavSidebar
      activePath={activePath}
      course={course}
      readOnly={readOnly}
      onNavigate={onNavigate}
      footer={
        <>
          {/* ── 当前课程（主控对象，全页唯一一处） ── */}
          <div className="tc-side__sec">
            <span className="tc-side__slabel">当前课程</span>
            <select
              id="courseSel"
              className="tc-sel"
              value={course}
              aria-label="选择查看课程"
              title={readOnly ? COURSE_TITLE_RO : COURSE_TITLE_LOCAL}
              onChange={(e) => onCourseChange((e.currentTarget as HTMLSelectElement).value)}
            >
              <option value="">自动（最近活跃课程）</option>
              {courses.map((c) => (
                <option key={c} value={c}>
                  {trainingSet.has(c) ? '🔥 ' : ''}
                  {c}
                  {/* 「已开课」而不是「正在训练」：名单的事实源是**开课标记**（课程表），
                      与「进程在不在跑」正交——开了课但 trainer 没起是合法稳态。 */}
                  {trainingSet.has(c) ? '（已开课）' : ''}
                </option>
              ))}
            </select>
            {/* 开课键：只读视图**不物理禁用**（只读是动作边界，不是按钮状态——禁用会让整条
                工具栏看起来灰败破碎；真点击由服务端 403 + flash 兜底）。 */}
            {course && courseEnabled !== null ? (
              <button
                type="button"
                className={`tc-btn tc-btn--sm${courseEnabled ? '' : ' tc-btn--primary'}`}
                title={
                  readOnly
                    ? '只读模式：开课仅限本机 localhost'
                    : courseEnabled
                      ? `${course} 已在课程表（在训）。再点「训练」= 按当前选项重写课程级旋钮 + 重新置 hub 模式（机器侧旋钮要重开课才生效）`
                      : `${course} 未开课。点「训练」= 开课：写开课标记 + 建账本/权重/remote-jobs + 按所选训练模式置 hub 派发闸（进程没跑也能开）`
                }
                onClick={onOpenCourse}
              >
                训练
              </button>
            ) : null}
          </div>

          {/* ── 全局设置（原来散在顶栏右侧） ── */}
          <div className="tc-side__sec">
            {gate.visible ? (
              <label className="tc-side__row" title={GATE_TITLE}>
                <span className="tc-side__slabel">触发门禁</span>
                <select
                  id="gateHaltSel"
                  className="tc-sel"
                  value={gate.mode}
                  disabled={gate.disabled}
                  aria-label="门禁触发时对云端 PPO worker 的动作"
                  onChange={(e) =>
                    gate.onChange(
                      (e.currentTarget as HTMLSelectElement).value === 'notify' ? 'notify' : 'halt',
                    )
                  }
                >
                  <option value="halt">停机</option>
                  <option value="notify">提示</option>
                </select>
              </label>
            ) : null}
            <label
              className="tc-side__row"
              title="页面数据轮询间隔（后台标签页自动暂停，切回即补拉）"
            >
              <span className="tc-side__slabel">刷新</span>
              <select
                id="refreshSel"
                className="tc-sel"
                aria-label="刷新间隔"
                value={refresh.value}
                onChange={(e) =>
                  refresh.onChange(
                    Number((e.currentTarget as HTMLSelectElement).value) as RefreshSec,
                  )
                }
              >
                {REFRESH_INTERVALS.map((s) => (
                  <option key={s} value={s}>
                    {refreshLabel(s)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </>
      }
    />
  )
}
