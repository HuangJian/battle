/** StatusDot.tsx — 统一状态点：四个语义档 + 颜色/形状双编码。
 *
 *  为什么抽成原子（docs/dashboard-redesign.md §4.2 / 问题 C6）：同一套状态点此前在四个面板里
 *  各写一遍（组件卡 `dotClass` / 节点行 `offDotCls` / worker 登记 `workerDot` / 日志页 `statusDot`），
 *  四处的取值互不相干 ⇒ 同一个「离线」在不同面板可能是不同颜色。
 *
 *  **本原子只统一呈现，不统一语义**：领域状态 → 语义档的映射留在各面板（那是领域逻辑——
 *  「hub 侧离线」与「ping 不通」都是 err，但只有面板知道它们为什么是 err）。
 *
 *  色 + 形双编码（色盲可辨；与既有 `.tc-stale` 同款约定）：
 *    ok  ● 实心圆（绿） · warn ◆ 实心菱形（琥珀） · err ■ 实心方（红） · off ○ 空心圆（灰）
 */

/** 状态语义档（四档，刻意少于各面板的状态词表——由面板做多对一映射）。 */
export type StatusTone = 'ok' | 'warn' | 'err' | 'off'

/** 语义档 → 类名（纯函数，可单测；类名沿用既有 `.tc-dot--*`，不引入第二套词表）。 */
export function statusDotClass(tone: StatusTone): string {
  switch (tone) {
    case 'ok':
      return 'tc-dot tc-dot--on'
    case 'warn':
      return 'tc-dot tc-dot--warn'
    case 'err':
      return 'tc-dot tc-dot--dead'
    case 'off':
      return 'tc-dot tc-dot--empty'
  }
}

export interface StatusDotProps {
  tone: StatusTone
  /** 悬停解释。**颜色不是唯一信息载体**：给出状态点的判据，否则只靠色/形猜。 */
  title?: string
}

export function StatusDot({ tone, title }: StatusDotProps) {
  return <span className={statusDotClass(tone)} title={title} />
}
