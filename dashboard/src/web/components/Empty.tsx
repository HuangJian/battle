/** Empty.tsx — 空态三态：无数据 / 加载中 / 错误。
 *
 *  为什么必须区分（docs/dashboard-redesign.md §4.2 / 问题 C9）：此前面板对「没内容」有三种
 *  答法——直接 `return null`（整块静默消失）、一行 `tc-muted` 文案、或一个转圈。操作员无法
 *  分清「这门课还没有迭代记录」「还在读盘」「读失败了」，而这三者的下一步动作完全不同。
 *
 *  纪律：`error` 必须给 `onRetry`；`empty` 必须说明**为什么没有**（`reason`），
 *  不能只写「暂无数据」——那句等于没说。
 */

import type { ComponentChildren } from 'preact'

export type EmptyKind = 'empty' | 'loading' | 'error'

export interface EmptyProps {
  kind: EmptyKind
  /** 一句话说清「为什么没有 / 在读什么 / 为什么失败」。 */
  reason: string
  /** 附加说明（口径、命令提示等，可含 `<code>`）。 */
  children?: ComponentChildren
  /** kind === 'error' 时的重试回调（错误态必须可重试）。 */
  onRetry?: () => void
  retryLabel?: string
}

export function Empty({ kind, reason, children, onRetry, retryLabel = '重试' }: EmptyProps) {
  return (
    <div
      className={`tc-emptybox tc-emptybox--${kind}`}
      role={kind === 'error' ? 'alert' : 'status'}
    >
      <span className="tc-emptybox__icon" aria-hidden="true">
        {kind === 'error' ? '⚠' : kind === 'loading' ? '◌' : '∅'}
      </span>
      <span className="tc-emptybox__reason">{reason}</span>
      {children ? <span className="tc-emptybox__extra">{children}</span> : null}
      {kind === 'error' && onRetry ? (
        <button type="button" className="tc-btn tc-btn--sm" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  )
}
