/** Flash.tsx — 动作结果浮层（固定右下角，ok=绿/失败=红，8s 自动隐藏；鼠标停在上面时暂停计时）。
 *
 *  为什么悬停要停表：浮层里常是「哪一步栽了 / 下一步怎么办」这类需要读完的句子，而 8s 定时器
 *  不看人在不在照样走完——鼠标停着读（或在选字复制）时被抽走，等于把「能读」变成「抢时间」。
 *  口径：悬停期间不计时；移开后**重新给满 8s**（不补剩余那一段）——浮层是通知不是倒计时进度条，
 *  「只剩 0.2s」比「再读 8s」更意外。手动 ✕ 仍是随时可关。
 */

import { useEffect, useState } from 'preact/hooks'

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
  /** 鼠标是否停在浮层上（停了就不倒计时）。 */
  const [paused, setPaused] = useState(false)

  // 浮层消失（✕ / 到点）⇒ 暂停态归零：指针留在原处不会再触发 mouseleave，
  // 不清就会让下一条浮层永远停在暂停里。
  useEffect(() => {
    if (!flash) setPaused(false)
  }, [flash])

  useEffect(() => {
    // 悬停期间不建 timer（不是建了再取消）：读多久都行，移开才重新起算。
    if (!flash || paused) return
    const t = setTimeout(onHide, FLASH_MS)
    return () => clearTimeout(t)
  }, [flash, paused, onHide])

  if (!flash) return null
  return (
    <div
      className={`tc-flash tc-flash--show tc-flash--${flash.ok ? 'ok' : 'bad'}`}
      role="status"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {flash.ok ? '✅ ' : '❌ '}
      {flash.message}
      <button type="button" className="tc-iconbtn" aria-label="关闭" onClick={onHide}>
        ✕
      </button>
    </div>
  )
}
