/** HomeCompareLegend.tsx — 首页多课对比的全局图例：课色圆点 + 课名，点一下摘/恢复该课曲线。
 *
 *  为什么是**全局**一组图例：首页所有可见走势图共用同一组对比课与色序（见
 *  `DECISIONS §2026-11-20-dashboard-home-multi-course-trends`），所以隐藏状态也按课名走，
 *  一次点击在每张图上同时生效；不按图各存一份（否则同一门课在不同图里一显一隐）。
 *
 *  隐藏是**临时**的（不进 localStorage，与「比较课程」弹窗的眼睛开关同语义）：
 *  只摘图上的线，选课/请求都不动——刷新后恢复全部可见。
 *
 *  布局：图例并进走势控制条（与「走势配置」同排），不再单独占一行；说明文字只进
 *  课程名 `title`（hover 才显示），不在页面上常驻。
 */

import type { JSX } from 'preact'

export interface HomeCompareLegendEntry {
  course: string
  /** 该课在全部对比图里的线色（当前课恒 `colorOf(0)`，额外课按配置顺序取色）。 */
  color: string
}

export interface HomeCompareLegendProps {
  entries: HomeCompareLegendEntry[]
  /** 当前被临时隐藏的课名。 */
  hidden: readonly string[]
  /** 点课程名：隐藏 ⇄ 显示（调用方用 `toggleHiddenCourse` 翻转）。 */
  onToggle: (course: string) => void
}

export function HomeCompareLegend({
  entries,
  hidden,
  onToggle,
}: HomeCompareLegendProps): JSX.Element {
  return (
    <div className="tc-trends__cmp" role="group" aria-label="首页多课对比">
      <span className="tc-muted tc-small tc-trends__cmp-label">多课对比</span>
      {entries.map((e) => {
        const off = hidden.includes(e.course)
        return (
          <button
            type="button"
            className={`tc-chip${off ? ' tc-chip--off' : ''}`}
            key={e.course}
            aria-pressed={off}
            aria-label={off ? `显示 ${e.course} 的曲线` : `隐藏 ${e.course} 的曲线`}
            title={
              off
                ? `悬停任一张图看各课读数 · 点课程名恢复显示`
                : `悬停任一张图看各课读数 · 点课程名隐藏/显示`
            }
            onClick={() => onToggle(e.course)}
          >
            <svg
              className="tc-trends__cmp-dot"
              viewBox="0 0 8 8"
              width="8"
              height="8"
              aria-hidden="true"
            >
              <circle cx="4" cy="4" r="4" fill={e.color} />
            </svg>
            {e.course}
          </button>
        )
      })}
    </div>
  )
}
