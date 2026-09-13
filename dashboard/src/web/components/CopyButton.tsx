/** CopyButton.tsx — 一键复制（clipboard）+「已复制」反馈。回环 localhost 属安全上下文，clipboard API 可用。 */

import { useEffect, useRef, useState } from 'preact/hooks'

export interface CopyButtonProps {
  text: string
  /** 复制按钮的语意（如「隧道」）；非 icon 模式下按钮文案显示该 label。 */
  label?: string
  small?: boolean
  /** 无字图标模式（§361①：只显示 ⧉ 图标，不显示「复制」字样；语义走 title/aria）。 */
  icon?: boolean
}

export function CopyButton({ text, label, small, icon }: CopyButtonProps) {
  const [done, setDone] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )
  const copy = (): void => {
    void navigator.clipboard
      .writeText(text)
      .then(() => {
        setDone(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setDone(false), 1200)
      })
      .catch(() => setDone(false))
  }
  const caption = label && label !== '内容' ? label : '复制'
  return (
    <button
      type="button"
      className={`tc-copy${done ? ' tc-copy--done' : ''}${small ? ' tc-copy--sm' : ''}${icon ? ' tc-copy--icon' : ''}`}
      aria-label={`复制${label ?? '内容'}`}
      title={done ? '已复制' : `复制${label && label !== '内容' ? ` ${label}` : ''}`}
      onClick={copy}
    >
      {icon ? (done ? '✓' : '⧉') : done ? `✓ ${caption}` : `⧉ ${caption}`}
    </button>
  )
}
