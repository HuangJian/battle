/** InlineNotice.tsx — 面板内「刚做了什么」的一行反馈（`role="status"`）。
 *
 *  为什么需要它（docs/dashboard-redesign.md §4.2 / 问题 C6 的第二个实例）：同一个语义此前有
 *  **五处手写实现**，各写各的样式——
 *    `tc-small`（eval-app，连 `tc-muted` 都没加，于是它是全站唯一不灰的那条）、
 *    `tc-muted tc-small` + 内联 `marginBottom: 6`（MetricsTable）、
 *    `tc-eval-summary__note`（EvalSummary，实际只是 padding）、
 *    `tc-caption`（TaskBundlePanel —— 那是**卡片页脚**样式，会画一条上边框，被当成消息用）、
 *    `tc-muted tc-small` + 内联 `marginBottom: 4`（Hero）。
 *  五条消息同页不同貌，纯粹因为写它们的人不同。收敛到这里，样式只有一处。
 *
 *  与 `Flash` 的分工（两层，不是重复）：`Flash` 是**全局**动作的右上角浮层（跨面板、带 ok/bad
 *  配色、8s 自动消失）；`InlineNotice` 是**面板内**就地反馈，留在触发它的控件旁边——
 *  分页/入队/导出这类局部动作不该把用户的目光拽到屏幕角落。
 *
 *  只读视图也照常渲染：它是读数（服务端 403 之类的结果也得让只读者看见），不是控件。
 */

import type { ComponentChildren } from 'preact'

export interface InlineNoticeProps {
  /** 保留换行（如任务包的 `\n` 结尾多行提示）。 */
  wrap?: boolean
  children?: ComponentChildren
}

export function InlineNotice({ wrap = false, children }: InlineNoticeProps) {
  return (
    <div className={`tc-notice${wrap ? ' tc-notice--wrap' : ''}`} role="status">
      {children}
    </div>
  )
}
