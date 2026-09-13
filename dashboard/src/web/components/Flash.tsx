/** Flash.tsx — 动作结果浮层（固定右上角，ok=绿/失败=红，8s 自动隐藏）。 */

import { useEffect } from 'preact/hooks'

export interface FlashState {
  ok: boolean
  message: string
}

export interface FlashProps {
  flash: FlashState | null
  onHide: () => void
}

const FLASH_MS = 8000

export function Flash({ flash, onHide }: FlashProps) {
  useEffect(() => {
    if (!flash) return
    const t = setTimeout(onHide, FLASH_MS)
    return () => clearTimeout(t)
  }, [flash, onHide])

  if (!flash) return null
  return (
    <div className={`tc-flash tc-flash--show tc-flash--${flash.ok ? 'ok' : 'bad'}`} role="status">
      {flash.ok ? '✅ ' : '❌ '}
      {flash.message}
      <button type="button" className="tc-iconbtn" aria-label="关闭" onClick={onHide}>
        ✕
      </button>
    </div>
  )
}
