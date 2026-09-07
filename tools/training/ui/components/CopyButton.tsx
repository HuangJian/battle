/** CopyButton.tsx — 一键复制（clipboard）+「已复制」反馈。回环 localhost 属安全上下文，clipboard API 可用。 */

import { useEffect, useRef, useState } from 'preact/hooks'

export interface CopyButtonProps {
  text: string
  /** 复制按钮的语意（如「隧道」）。 */
  label?: string
  small?: boolean
}

export function CopyButton({ text, label, small }: CopyButtonProps) {
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
  return (
    <button
      type="button"
      className={`tc-copy${done ? ' tc-copy--done' : ''}${small ? ' tc-copy--sm' : ''}`}
      aria-label={`复制${label ?? '内容'}`}
      title={done ? '已复制' : `复制${label ? ` ${label}` : ''}`}
      onClick={copy}
    >
      {done ? '已复制 ✓' : '⧉ 复制'}
    </button>
  )
}
