/** Switch.tsx — 视觉 toggle 开关（role=switch + 内嵌轨道）：节点启停等「一目了然」
 *  的开/关语义用这个；纯文字 checkbox 语义留给表单（ui/components/Toggle.tsx）。
 *  纯客户端交互（SSR 输出静态轨道态，hydrate 后可点）。 */

import type { JSX } from 'preact'

export interface SwitchProps {
  /** 无障碍名（如「停用 a1」「启用 a2」——描述目标动作，不是当前状态）。 */
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  title?: string
}

export function Switch({ label, checked, onChange, disabled, title }: SwitchProps): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={title ?? label}
      className={`tc-switch${checked ? ' tc-switch--on' : ''}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="tc-switch__track" aria-hidden="true">
        <span className="tc-switch__thumb" />
      </span>
    </button>
  )
}
