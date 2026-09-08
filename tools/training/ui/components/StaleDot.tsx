/** StaleDot.tsx — 陈旧度圆点（颜色+形状双编码：●绿圆 / ◐黄半圆 / ■红方）。 */

import type { StaleState } from '../view'

export interface StaleDotProps {
  state: StaleState
  /** hover 文案（如「最后更新 12s 前」）。 */
  title?: string
}

const cls: Record<StaleState, string> = {
  ok: 'tc-stale--ok',
  refresh: 'tc-stale--refresh',
  err: 'tc-stale--err',
}

export function StaleDot({ state, title }: StaleDotProps) {
  return (
    <span className="tc-stale-wrap" title={title} aria-label={title}>
      <span className={`tc-stale ${cls[state]}`} />
    </span>
  )
}
