/** Toggle.tsx — 原生 checkbox 的语义化开关（a11y 最低要求自带）。 */

import type { ComponentChildren } from 'preact'

export interface ToggleProps {
  label: ComponentChildren
  /** 小字说明（如 rl.* 键名）。 */
  note?: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  title?: string
}

export function Toggle({ label, note, checked, onChange, disabled, title }: ToggleProps) {
  return (
    <label className="tc-toggle" title={title}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange((e.target as HTMLInputElement).checked)}
      />
      <span>{label}</span>
      {note ? <code className="tc-muted tc-small">{note}</code> : null}
    </label>
  )
}
