/** snapshot-cache.ts — 慢部件快照：类型定义与冷算实现（节点 ping / 组件探测 / 池历史）。 */
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { Component, RlConfig } from '../../core/types'
import { assemblePushFleet, probePushFleetHealth } from '../../stack/push-config'
import {
  type ComponentView,
  type CourseEdit,
  type LoopComplete,
  type NodeLocalView,
  type NodeView,
  type PhaseInfo,
  type PushFleetProbe,
  parsePhaseFromLog,
} from '../../web/view'
import { type HistoryAggregate, aggregateNodeHistory, isSlowNode } from '../pool-history'
import { courseEditFromLedgerTail } from './ledger'
import { readLogTail } from './logs'
import { loopCompleteFromLedgerTail } from './loop-complete'
import {
  type NodeProbeResult,
  componentViews,
  computeComponentHealth,
  mergeNodeProbes,
  nodeProbeResults,
  nodeStructure,
} from './views'

// ────────────────────────── 慢部件快照缓存（§366：页面加载 <1s） ──────────────────────────
// 节点 ping（超时 1.5s）、组件健康探测（1.5-2.5s）、池历史聚合（磁盘）全部移出请求路径：
// 后台刷新器每 SNAPSHOT_REFRESH_MS 重算一次，buildStateView 只读缓存。请求侧开销只剩
// 配置/课程/指标（实测 ~150ms）；新鲜度 ≤1 个刷新周期（5s），对监控面板不可见。
// 动作（启/停/切课）经 invalidateSlowSnapshot 置空缓存 → 下一次 buildStateView 冷算即时反映。
//
// ★ 两层（2026-09-22，多课程并行切课卡顿的根治 **+ 动作后首帧不冷算**）：快照里的字段分两类，
//   **键控粒度不同**，且**只有贵的那一类进缓存**：
//   · **结构**（cfg / 账本 / registry / 内存派生）——节点行（enabled/url/并发）、worker 行、
//     push 执行面（mode/台数）、组件存活与日志尾、阶段、账本尾：**便宜**，随请求现算。
//     两个理由：切课程不必重算（它本来就不贵），而**动作后的第一帧也必须是新的**
//     （否则刚拨下的节点开关会「自己弹回去」）；
//   · **探测**（`FleetProbes`）——节点 ping / 共享组件健康 / push 机群探活 / 池历史聚合：
//     与「在看哪门课」无关（同一份 rl-config、同一个机群），却各带 1.2–2.5s 超时预算。
//     全局算一份（`getFleetProbes`，跨课程共用）+ SWR（陈旧先给旧值、重算丢后台）。
//   此前整体按课程键控 ⇒ 切到一门没看过的课要把节点 ping（1.5s）+ 共享 hub 探测（1.2s）
//   原地重做一遍，请求被压住 2-3s（2026-09-22 实测：冷 2883ms → 暖 2ms；
//   而动作后的首帧同样会被这几笔压住——即使刚切过一次课）。
//   把课程无关的探测放进课程键控的缓存里 = 每门课各探一遍，是同一类错误的两次出现。
// （局域网只读不受影响：课程级部分仍按课程各自冷算/刷新，查看者之间不串数据。）

/** **机群级探测**结果（与查看哪门课无关）：节点 ping / 共享组件健康 / push 机群探活 /
 *  池历史聚合（慢节点判定 + 上轮贡献）。全局算一份、跨课程共用（见文件头「两层」）。 */
export interface FleetProbes {
  nodes: Map<string, NodeProbeResult>
  slowById: Map<string, boolean>
  /** 最近完成轮的逐节点贡献数（rollout+eval 合计；无池数据不在表里 → 合并层补 -1）。 */
  contribById: Map<string, number>
  /** 本机直跑槽的最近完成轮贡献数（-1 = 无池数据）。 */
  localContrib: number
  /** push worker 逐台探活（id → 通不通；null = 探不了）。 */
  pushProbes: Map<string, boolean | null>
  /** 共享/单例组件健康（hub / 隧道 / 采集 agent）；缺席 = 探不了/没在跑。 */
  componentHealth: Map<Component, boolean | null>
}

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
  /** push 执行面（2026-09-19 起是**机群级**事实，不再按课程）：登记节点 + hub_push + 逐节点探活。 */
  pushFleet: PushFleetProbe
}

export const SNAPSHOT_REFRESH_MS = 5000
/** 非操作员课程（LAN 查看者切到的课程）快照的存活窗口：超过该时长未被查看即丢弃，
 *  避免后台刷新器为无人看的课程持续做组件探测（§366 慢探测预算）。
 *  注：机群级探测（节点 ping / push 群）不在此列——那是全局一份（见文件头「两层」）。 */
export const SNAPSHOT_VIEW_TTL_MS = 60_000

interface SlowSnapEntry {
  snap: SlowSnapshot
  at: number
  lastAccess: number
}

export const slowSnapshots = new Map<string, SlowSnapEntry>()
export const snapshotInFlight = new Map<string, Promise<SlowSnapshot>>()

/** 重算**机群级探测**（不落缓存，落缓存由 `getFleetProbes` 负责）。
 *
 *  四笔慢活儿并行：池历史聚合（读活跃流 meta 账本）、节点 ping（超时 1.5s）、
 *  push 机群探活（超时 1.5s）、共享组件健康（hub 1.5s / 隧道 2.5s）。
 *  它们与「在看哪门课」一个字都没关系，故全局只算一份；且**只有它们进缓存**。 */
export async function computeFleetProbes(cfg: RlConfig): Promise<FleetProbes> {
  // 池历史聚合（慢节点判定 + 上一轮贡献数**同源**）——同步、读磁盘，只做一次
  // （它要全读一份实打实的 meta 账本，实测数 MB，此前这里调了两遍）。
  let agg: HistoryAggregate | null = null
  try {
    agg = aggregateNodeHistory()
  } catch {
    /* 池历史不可用 → 慢节点判定退化为全 false、贡献数保持 -1 */
  }
  const slowById = new Map<string, boolean>()
  if (agg) for (const [id, h] of agg.hist) slowById.set(id, isSlowNode(h))
  // 最近完成轮贡献数（无池数据 = -1）。local 节点是否出、以及它的 slots，是**结构**（cfg
  // 现算，见 computeSlowSnapshot）；这里只给它的贡献数——池历史不可用只是拿不到它（-1），
  // 不能因此把 local 节点整块吞掉（此前它在 try 里，aggregateNodeHistory() 一抛就丢了）。
  const contribById = new Map<string, number>()
  let localContrib = -1
  if (agg) {
    for (const [id, h] of agg.hist) contribById.set(id, agg.globalMaxIt >= 0 ? h.lastIterOk : -1)
    localContrib = agg.globalMaxIt >= 0 ? (agg.hist.get('local')?.lastIterOk ?? 0) : -1
  }
  const [nodes, pushProbes, componentHealth] = await Promise.all([
    nodeProbeResults(cfg),
    probePushFleetHealth(cfg),
    computeComponentHealth(cfg),
  ])
  return { nodes, slowById, contribById, localContrib, pushProbes, componentHealth }
}

/** 重算指定课程的慢部件快照（不落缓存；落缓存由 getSlowSnapshot 负责）。
 *
 *  `probes` 由调用方注入（`getFleetProbes` 的全局 SWR 缓存），**结构部分在此现算**：
 *  节点行与 push 执行面看当下 cfg、组件存活与日志尾看当下 registry、阶段看当下日志——
 *  于是动作后的第一帧就是新的，而贵的那几笔探测永远不会把它压住。 */
export async function computeSlowSnapshot(
  cfg: RlConfig,
  course: string,
  probes: FleetProbes,
): Promise<SlowSnapshot> {
  const components = await componentViews(cfg, course, probes.componentHealth)
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
  // ── 结构现算 ⊕ 探测取自缓存（见文件头「两层」） ──
  const nodes = mergeNodeProbes(nodeStructure(cfg), probes.nodes, probes.slowById)
  for (const n of nodes) n.lastContrib = probes.contribById.get(n.id) ?? -1
  // local 节点：出不出、几槽，只由配置决定（配置缺失/非法才缺省）。
  const slots = Number(cfg.rl.local_slots)
  const localNode: NodeLocalView | null =
    Number.isInteger(slots) && slots >= 0
      ? { id: 'local', slots, lastContrib: probes.localContrib }
      : null
  return {
    components,
    nodes,
    localNode,
    phase,
    loopComplete,
    courseEdit,
    pushFleet: assemblePushFleet(cfg, probes.pushProbes),
  }
}

/** 账本中最近一条 course_edit 事件 → 热加载判决；无则 null（纯函数，可单测）。
 *
 * 事件是**状态**不是瞬时告警：rejected 横幅要跨后续 iteration 事件持久（用户改回
 * 文件后 trainer 写 restored → 覆盖为 restored → 横幅自然消失），所以取尾部窗口内
 * 最后一条，而非只看尾行。窗口 1000 行（每 iter ~5 事件 ≈ 200 iter 覆盖）。 */
