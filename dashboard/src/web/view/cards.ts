/** cards.ts — 控制台卡片注册表类型。 */
import { ConsoleStateView, StaleState } from './console-types'

// ────────────────────────── 卡片注册表类型（console/ui/cards.ts 消费） ──────────────────────────

export interface CardVisibilityCtx {
  course: string
}

/** 传达一个可调整的卡片状态（App 持有）。 */
export interface CardState {
  collapsed: boolean
  maximized: boolean
}

/** 卡片面板共用 props（App 装配；卡片只需消费自己需要的字段）。
 *  定义在共享层以避免 cards ↔ panels 循环依赖（纯类型，编译期擦除）。 */
export interface PanelProps {
  stateView: ConsoleStateView | null
  onAction: (act: string, body: Record<string, unknown>) => void
  course?: string
  /** 卡未折叠（专属数据源 pool/log 据此刻暂停拉取，DS-U5）。 */
  active?: boolean
  /** 全局暂停（dirty L2 / 后台 tab / 用户暂停）。 */
  paused?: boolean
  maximizedCard?: string | null
  pendingEdits?: ReadonlyMap<string, string>
  onPending?: (key: string, value: string) => void
  onDiscardPending?: () => void
  onCommitConcurrency?: (id: string, value: string) => void
  onPoolState?: (st: StaleState, at: number) => void
  poolFreshNonce?: number
}
