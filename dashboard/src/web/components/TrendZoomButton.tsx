/** TrendZoomButton.tsx — 走势图左下角的放大入口（单课格与多课格共用）。
 *
 *  为什么是独立按钮：图表主体负责 hover 读数，点击会跟十字准星抢手势（2026-11-10）；
 *  位置固定在 `.tc-tcell__zoom`（图区左下角），顶部留给读数/提示框。
 */

import type { JSX } from 'preact'

export interface TrendZoomButtonProps {
  /** 图名（aria-label 用）。 */
  label: string
  onClick: () => void
  /** 悬停说明（默认「放大走势图（浮窗内可切换统计量）」）。 */
  title?: string
}

export function TrendZoomButton({
  label,
  onClick,
  title = '放大走势图（浮窗内可切换统计量）',
}: TrendZoomButtonProps): JSX.Element {
  return (
    <button
      type="button"
      className="tc-tcell__zoom"
      aria-label={`放大走势图：${label}`}
      title={title}
      onClick={onClick}
    >
      <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
        <path
          d="M7.5 1H11v3.5M4.5 11H1V7.5M11 1 7.5 4.5M1 11l3.5-3.5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>
  )
}
