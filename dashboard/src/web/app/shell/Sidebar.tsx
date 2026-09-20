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
 *  导航本体在 `NavSidebar.tsx`（/eval 与 /log 两个独立 bundle 复用同一组件，见该文件头注）；
 *  本文件只负责「控制台这一页多出来的东西」——课程、门禁、刷新间隔。
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
  /** 在训课程（课程 select 内标 🔥；与顶栏「在训 n」同源）。 */
  trainingCourses: string[]
  onCourseChange: (course: string) => void
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
  onNavigate,
  readOnly,
  gate,
  refresh,
}: SidebarProps) {
  const trainingSet = new Set(trainingCourses)
  const otherTraining = trainingCourses.filter((c) => c !== course)

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
                  {trainingSet.has(c) ? '（正在训练）' : ''}
                </option>
              ))}
            </select>
            {otherTraining.length > 0 ? (
              <span
                className="tc-training-tag"
                title={
                  `在训课程共 ${trainingCourses.length} 门：${trainingCourses.join('、')}` +
                  (course && trainingSet.has(course)
                    ? '（含当前查看的这门）'
                    : '——切换查看不影响训练')
                }
              >
                <span className="tc-dot tc-dot--on" />
                还有在训：{otherTraining.join('、')}
              </span>
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
