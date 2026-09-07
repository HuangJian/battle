/** cards.ts — 卡片注册表（评审 E2 + DS-E2/E5/E6）：新增卡片 = 追加一行 + panel .tsx。
 *
 *  - 锚点列表 / 卡渲染 / 卡级动作全部自此派生（DS-E2），不另设数组。
 *  - 卡级动作只声明描述（DS-E2 的 aria-label 需求），run 闭包由 App 按 source 装配。
 *  - ds: RefreshBySource 是 exhaustive Record（DS-E5）：新增 source 缺实现编译报错。
 */

import type { ComponentType } from 'preact'
import type { CardVisibilityCtx, PanelProps } from '../../ui/view'
import { ComponentsPanel } from './panels/ComponentsPanel'
import { ModesPanel } from './panels/ModesPanel'
import { NodesPanel } from './panels/NodesPanel'
import { MetricsPanel } from './panels/MetricsPanel'
import { LogNavCard } from './panels/LogNavCard'

export type CardSource = 'state' | 'pool' | 'log'

export interface CardActionDesc {
  key: string
  label: string
  icon?: string
}

export interface CardDef {
  /** 唯一 key，同时是 localStorage tc.card.<id> 后缀与锚点 id。 */
  key: string
  title: string
  /** 数据来源决定刷新节奏（§3.2：节奏归属端点）。 */
  source: CardSource
  defaultCollapsed?: boolean
  /** 语义纯函数（纯模型，客户端与服务端共用）。 */
  visible?: (ctx: CardVisibilityCtx) => boolean
  /** 卡级动作：header 统一渲染（自带 aria-label）（DS-E2）。run 由 App 装配。 */
  actions?: CardActionDesc[]
  component: ComponentType<PanelProps>
}

export const CARD_REGISTRY: CardDef[] = [
  {
    key: 'components',
    title: '组件',
    source: 'state',
    actions: [{ key: 'refresh', label: '刷新本卡', icon: '⟳' }],
    component: ComponentsPanel,
  },
  {
    key: 'modes',
    title: '运行模式',
    source: 'state',
    actions: [{ key: 'refresh', label: '刷新本卡', icon: '⟳' }],
    component: ModesPanel,
  },
  {
    key: 'nodes',
    title: '节点池',
    source: 'state',
    defaultCollapsed: false,
    actions: [
      { key: 'refresh', label: '刷新本卡', icon: '⟳' },
      { key: 'poolFresh', label: '立即刷新池统计', icon: '↻' },
    ],
    component: NodesPanel,
  },
  {
    key: 'metrics',
    title: '训练指标',
    source: 'state',
    actions: [{ key: 'refresh', label: '刷新本卡', icon: '⟳' }],
    component: MetricsPanel,
  },
  {
    key: 'lognav',
    title: '日志',
    source: 'state',
    defaultCollapsed: true,
    actions: [{ key: 'refresh', label: '刷新本卡', icon: '⟳' }],
    component: LogNavCard,
  },
]

/** 由 source 派生锚点/可见性。 */
export function visibleCards(course: string): CardDef[] {
  const ctx: CardVisibilityCtx = { course }
  return CARD_REGISTRY.filter((d) => d.visible?.(ctx) ?? true)
}
