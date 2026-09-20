/** SectionHeader.tsx — 区块头：标题 + 计数 + 提示 + 动作区。
 *
 *  为什么抽出来（docs/dashboard-redesign.md §4.2）：面板头此前各写各的——组件卡是
 *  `<span class="tc-comps__glabel">`、节点行是 `<span class="lbl">节点</span>`、
 *  worker 登记是 `<span class="lbl">push worker</span>`、调度器是 `.tc-loopq__head`。
 *  字号/字重/间距/「计数怎么标」四处不同，于是同一页里区块看起来不属于同一个系统。
 *
 *  用法契约：`title` 是名词短语（「服务」），`hint` 是这个区块**是什么/怎么读**
 *  的一句话（悬停），`actions` 放区块级控件（范围档位 / 深链 / 行过滤）。
 */

import type { ComponentChildren } from 'preact'

export interface SectionHeaderProps {
  title: string
  /** 计数（与标题同排、弱化显示）。0 也会显示——「0」是有信息量的读数。 */
  count?: number
  /** 标题悬停说明。 */
  hint?: string
  /** 标题旁的短状态注（已挂载 / 不可用…）。与 actions 分开：它是**读数**，不是控件。 */
  note?: ComponentChildren
  /** 区块级控件（按钮 / 深链 / 过滤器），右对齐。 */
  actions?: ComponentChildren
  /** 更弱的层级（卡内子区块用）。 */
  sub?: boolean
}

export function SectionHeader({ title, count, hint, note, actions, sub }: SectionHeaderProps) {
  return (
    <div className={`tc-sechd${sub ? ' tc-sechd--sub' : ''}`}>
      <span className="tc-sechd__title" title={hint}>
        {title}
      </span>
      {count !== undefined ? <span className="tc-sechd__count">{count}</span> : null}
      {note}
      {actions ? <span className="tc-sechd__acts">{actions}</span> : null}
    </div>
  )
}
