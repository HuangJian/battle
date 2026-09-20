/** StatusRow.tsx — 统一行原语：「一行实体 + 状态 + 指标 + 动作」的**唯一实现**。
 *
 *  为什么必须有它（docs/dashboard-redesign.md §4.2 / 问题 C6）：这个语义此前有 4 套手写实现
 *  ——组件卡（`tc-comps` 的 chip）、节点行（`tc-npill`）、worker 登记行（`tc-npill tc-wreg__pill`）、
 *  课程矩阵/调度器行（`tc-cov__row` / `tc-loopq__row`）。四者对「徽章长什么样」「动作键放哪」
 *  「能不能展开、展开在哪」各自作答，结果是**操作员要为每一块面板重新学一遍行**。
 *
 *  统一后的契约（顺序固定，不可由调用方重排）：
 *    `● 名称 │ 值 │ 徽章… │ 元信息… │ ──────── 动作区`
 *    展开时详情贴在行下方、占整行宽，并自带**显式折叠头**。
 *
 *  为什么详情必须有折叠头（原 `tc-cc__detail` 的教训）：旧实现是「展开后点详情任意处收起」
 *  ——于是想在日志尾部**选段复制**，一按下鼠标就把详情收了，详情越长越点不着。
 *  现在展开后详情内**没有任何隐藏点击区**（可以正常选字、滚动），收起只有两个入口：
 *  折叠头这颗按钮，或再点一次行本身。
 *
 *  三条纪律：
 *  1. **领域状态 → 语义档的映射留在调用方**（`tone` 由调用方算）——原语不认识组件/节点/worker。
 *  2. **只读不等于禁用**（§7 O1 已决）：只读时行不渲染交互角色（无 `role`/`tabIndex`/`onClick`），
 *     但按钮保持可点，误点由服务端 403 兜底；行本身给 `roTitle` 说明。
 */

import type { ComponentChildren } from 'preact'
import { StatusDot, type StatusTone } from './StatusDot'

/** 行内徽章（作用域 / 模式 / 执行面 / 忙…）。 */
export interface RowBadge {
  text: string
  /** 语义色（不给 = 中性灰）。 */
  tone?: 'g' | 'y' | 'r' | 'a' | 'gray'
  /** 覆盖默认类名——面板自带徽章词表时用（如 `tc-cc__scope tc-cc__scope--shared`）。 */
  cls?: string
  title?: string
}

/** 行内元信息（url / 并发 / 上轮贡献…）。 */
export interface RowMeta {
  text: string
  title?: string
  /** 等宽字体（url / 路径 / pid）。 */
  mono?: boolean
}

export interface StatusRowProps {
  tone: StatusTone
  /** 状态点的悬停解释。 */
  dotTitle?: string
  /** 行主标识（组件 key / 节点 id / worker id / 课程名）。 */
  name: string
  /** 名称后的高亮短值（节点的 `✓4`、本机槽位的 `3槽`）。 */
  value?: string
  valueTitle?: string
  badges?: RowBadge[]
  meta?: RowMeta[]
  /** 点击行 = 切换展开（给出且非只读时可交互）。 */
  onToggle?: () => void
  expanded?: boolean
  /** 只读视图：去掉交互角色（动作键仍在，服务端 403 兜底）。 */
  readOnly?: boolean
  /** 只读时的行悬停提示。 */
  roTitle?: string
  /** 行的无障碍名（描述「这是什么 + 点它会怎样」）。 */
  ariaLabel?: string
  /** 行尾动作区（按钮 / 开关 / 复制钮）。点击不冒泡到行本身。 */
  actions?: ComponentChildren
  /** 展开后贴在行下方的详情（整行宽）。 */
  detail?: ComponentChildren
  /** 折叠头左侧的标题（如「日志详情」）；不给则折叠头只留右侧按钮。 */
  detailTitle?: string
  /** 额外的行 modifier（如 `tc-row--local`），供面板做局部样式。 */
  className?: string
}

export function StatusRow({
  tone,
  dotTitle,
  name,
  value,
  valueTitle,
  badges,
  meta,
  onToggle,
  expanded = false,
  readOnly = false,
  roTitle,
  ariaLabel,
  actions,
  detail,
  detailTitle,
  className,
}: StatusRowProps) {
  const interactive = onToggle !== undefined && !readOnly
  const cls = [
    'tc-row',
    interactive ? 'tc-row--act' : '',
    expanded ? 'tc-row--open' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="tc-row-wrap">
      <span
        className={cls}
        role={interactive ? 'button' : undefined}
        tabIndex={interactive ? 0 : undefined}
        aria-expanded={onToggle ? expanded : undefined}
        aria-label={ariaLabel}
        title={readOnly ? roTitle : undefined}
        onClick={interactive ? onToggle : undefined}
        onKeyDown={
          interactive
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onToggle?.()
                }
              }
            : undefined
        }
      >
        <StatusDot tone={tone} title={dotTitle} />
        <b>{name}</b>
        {value !== undefined ? (
          <span className="tc-row__val" title={valueTitle}>
            {value}
          </span>
        ) : null}
        {badges?.map((b, i) => (
          <b
            key={`${b.text}-${i}`}
            className={b.cls ?? `tc-badge tc-badge--${b.tone ?? 'gray'}`}
            title={b.title}
          >
            {b.text}
          </b>
        ))}
        {meta?.map((m, i) => (
          <span
            key={`${m.text}-${i}`}
            className={`tc-row__meta${m.mono ? ' tc-mono' : ''}`}
            title={m.title}
          >
            {m.text}
          </span>
        ))}
        {actions ? (
          // 动作区是行的**兄弟语义**：点按钮不该顺带展开/收起这一行。
          <span className="tc-row__acts" onClick={(e) => e.stopPropagation()}>
            {actions}
          </span>
        ) : null}
      </span>
      {expanded && detail ? (
        <div className="tc-row__detail">
          <div className="tc-row__detailhd">
            {detailTitle !== undefined ? (
              <span className="tc-row__detailttl">{detailTitle}</span>
            ) : null}
            {onToggle && !readOnly ? (
              <button
                type="button"
                className="tc-row__collapse"
                aria-label={`收起 ${name} 的详情`}
                onClick={onToggle}
              >
                收起 ▴
              </button>
            ) : null}
          </div>
          {detail}
        </div>
      ) : null}
    </div>
  )
}
