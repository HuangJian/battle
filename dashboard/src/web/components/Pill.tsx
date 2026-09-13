/** Pill.tsx / Badge.tsx — 状态徽章基元（tone 取 g/y/r/gray/a）。 */

import type { ComponentChildren } from 'preact'

export type Tone = 'g' | 'y' | 'r' | 'gray' | 'a'

export function pillClass(kind: 'pill' | 'badge', tone: Tone): string {
  return `tc-${kind} tc-${kind}--${tone}`
}

export interface PillProps {
  tone?: Tone
  title?: string
  children: ComponentChildren
}

export function Pill({ tone = 'gray', title, children }: PillProps) {
  return (
    <span className={pillClass('pill', tone)} title={title}>
      {children}
    </span>
  )
}

export function Badge({ tone = 'gray', title, children }: PillProps) {
  return (
    <span className={pillClass('badge', tone)} title={title}>
      {children}
    </span>
  )
}
