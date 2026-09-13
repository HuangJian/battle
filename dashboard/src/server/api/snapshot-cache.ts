/** snapshot-cache.ts — 慢部件快照：类型定义与冷算实现（节点 ping / 组件探测 / 池历史）。 */
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { RlConfig } from '../../core/types'
import {
  type ComponentView,
  type CourseEdit,
  type LoopComplete,
  type NodeLocalView,
  type NodeView,
  type PhaseInfo,
  parsePhaseFromLog,
} from '../../web/view'
import { type NodeHistory, aggregateNodeHistory, isSlowNode } from '../pool-history'
import { courseEditFromLedgerTail } from './ledger'
import { readLogTail } from './logs'
import { loopCompleteFromLedgerTail } from './loop-complete'
import { componentViews, nodeViews } from './views'

// ────────────────────────── 慢部件快照缓存（§366：页面加载 <1s） ──────────────────────────
// 节点 ping（超时 1.5s）、组件健康探测（1.5-2.5s）、池历史聚合（磁盘）全部移出请求路径：
// 后台刷新器每 SNAPSHOT_REFRESH_MS 重算一次，buildStateView 只读缓存。请求侧开销只剩
// 配置/课程/指标（实测 ~150ms）；新鲜度 ≤1 个刷新周期（5s），对监控面板不可见。
// 动作（启/停/切课）经 invalidateSlowSnapshot 置空缓存 → 下一次 buildStateView 冷算即时反映。
// 局域网只读（§…）：缓存按课程键控——LAN 切换查看课程时按课程各自冷算/刷新，互不串数据。
// 组件/节点视图本身全局（进程状态），但 trainingLoop 日志尾与阶段是课程相关的，故整体键控。

export interface SlowSnapshot {
  components: ComponentView[]
  nodes: NodeView[]
  localNode: NodeLocalView | null
  phase: PhaseInfo
  /** 训练正常完成停车态（账本尾行 run_complete + trainingLoop 存活时派生；
   *  resume 后新事件自然顶掉 → null）。 */
  loopComplete: LoopComplete | null
  /** 课程热加载最新判决（§2026-09-13-hot-reload；账本最近一条 course_edit 事件。
   *  rejected = 语料身份编辑被拒 → 错误横幅；restored/applied 不上横幅）。 */
  courseEdit: CourseEdit | null
}

export const SNAPSHOT_REFRESH_MS = 5000
/** 非操作员课程（LAN 查看者切到的课程）快照的存活窗口：超过该时长未被查看即丢弃，
 *  避免后台刷新器为无人看的课程持续做节点 ping（§366 慢探测预算）。 */
export const SNAPSHOT_VIEW_TTL_MS = 60_000

interface SlowSnapEntry {
  snap: SlowSnapshot
  at: number
  lastAccess: number
}

export const slowSnapshots = new Map<string, SlowSnapEntry>()
export const snapshotInFlight = new Map<string, Promise<SlowSnapshot>>()

/** 重算指定课程的慢部件快照（不落缓存；落缓存由 getSlowSnapshot 负责）。 */
export async function computeSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  // 先聚合池历史（慢节点判定输入），再并行探测组件/节点——isSlowNode 依据「近期仍在
  // 成功结算且单局耗时高」把 ping 超时的慢节点与真离线区分开。
  let histById = new Map<string, NodeHistory>()
  try {
    histById = aggregateNodeHistory().hist
  } catch {
    /* 池历史不可用 → 慢节点判定退化为全 false（节点一律按 ping 口径展示） */
  }
  const slowById = new Map<string, boolean>()
  for (const [id, h] of histById) slowById.set(id, isSlowNode(h))
  const [components, nodes] = await Promise.all([
    componentViews(cfg, course),
    nodeViews(cfg, slowById),
  ])
  // 节点上一轮贡献数（池历史聚合；无数据 = -1）。
  const contribById = new Map<string, number>()
  // local 节点：只由配置决定（配置缺失/非法才缺省）。池历史不可用只是贡献数拿不到（-1），
  // 不能因此把 local 节点整块吞掉——此前它在 try 里，aggregateNodeHistory() 一抛就丢了。
  const slots = Number(cfg.rl.local_slots)
  let localNode: NodeLocalView | null =
    Number.isInteger(slots) && slots >= 0 ? { id: 'local', slots, lastContrib: -1 } : null
  try {
    const { hist, globalMaxIt } = aggregateNodeHistory()
    for (const [id, h] of hist) contribById.set(id, globalMaxIt >= 0 ? h.lastIterOk : -1)
    const localH = hist.get('local')
    if (localNode) localNode.lastContrib = globalMaxIt >= 0 ? (localH?.lastIterOk ?? 0) : -1
  } catch {
    /* 池历史不可用 → 贡献数保持 -1（local 节点仍按配置显示） */
  }
  for (const n of nodes) n.lastContrib = contribById.has(n.id) ? contribById.get(n.id)! : -1
  // 当前训练阶段（训练循环日志尾解析）。
  const logTail = components.find((c) => c.key === 'trainingLoop')?.logTail ?? []
  const phase = parsePhaseFromLog(logTail)
  // 正常完成停车态（2026-09-12）：账本尾行是 run_complete 且进程仍存活（停车
  // 等待重启）→ 横幅派生源；进程已死走 exit-watchdog 路径；resume 后新事件
  // 顶掉 → 自动消失。账本小文件 + 尾部窗口读，5s 快照周期内可忽略。
  let loopComplete: LoopComplete | null = null
  let courseEdit: CourseEdit | null = null
  const loopAlive = components.some((c) => c.key === 'trainingLoop' && c.status === 'running')
  if (course && loopAlive) {
    try {
      const ledgerTail = readLogTail(
        path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'),
        1000,
      ).lines
      loopComplete = loopCompleteFromLedgerTail(ledgerTail)
      courseEdit = courseEditFromLedgerTail(ledgerTail)
    } catch {
      loopComplete = null
      courseEdit = null
    }
  }
  return { components, nodes, localNode, phase, loopComplete, courseEdit }
}

/** 账本中最近一条 course_edit 事件 → 热加载判决；无则 null（纯函数，可单测）。
 *
 * 事件是**状态**不是瞬时告警：rejected 横幅要跨后续 iteration 事件持久（用户改回
 * 文件后 trainer 写 restored → 覆盖为 restored → 横幅自然消失），所以取尾部窗口内
 * 最后一条，而非只看尾行。窗口 1000 行（每 iter ~5 事件 ≈ 200 iter 覆盖）。 */
