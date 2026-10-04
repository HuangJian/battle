/** snapshot-cache.ts — 慢部件快照：类型定义与冷算实现（节点 ping / 组件探测 / 池历史）。 */
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { Component, RlConfig } from '../../core/types'
import { assemblePushFleet, probePushFleetHealth } from '../../stack/push-config'
import {
  BRIEF_ALL,
  type ComponentView,
  type ContributionBrief,
  type CourseEdit,
  type NodeLocalView,
  type NodeView,
  type PhaseInfo,
  type PushFleetProbe,
  compactSummary,
  parsePhaseFromLog,
} from '../../web/view'
import { buildContributionView, inflightByWorkerFromQueue } from '../contribution'
import {
  type HistoryAggregate,
  aggregateNodeHistory,
  isSlowNode,
  projectWindow,
  resolveWindow,
} from '../pool-history'
import { courseEditFromLedgerTail } from './ledger'
import { readLogTail } from './logs'
import { peekHubAdmin } from './overview'
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
  /** 首页贡献度缩略（plan/dashboard-reload-perf R1）：**同一份 `agg` 的裁剪**，
   *  在后台顺手产出 —— 请求路径（`state-view.ts`）只读它，**永不**裸调聚合。
   *  窗口 = `'24h'` 滚动档（2026-10-03 用户口径，取代 `'today'`——自然日凌晨归零且跨日回退）；
   *  两侧 N 分开给（采样 3 / PPO 全列 `BRIEF_ALL`）。窗口档进缓存值不进缓存键（§4 解耦裁决）。
   *  聚合不可用（读盘失败）→ null（缩略不渲染，不伪造 0）。 */
  contributionBrief: ContributionBrief | null
}

export interface SlowSnapshot {
  components: ComponentView[]
  nodes: NodeView[]
  localNode: NodeLocalView | null
  phase: PhaseInfo
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
  // ★ 2026-09-26（plan/nodes-decouple-from-course.plan.md）：agg 现在是**按天分桶**的全量
  // 聚合，展示行由 `projectWindow` 投影得到。
  //  · 慢节点判定（可达性，看「最近」）取**全部**窗口 —— 与「看哪天」无关；且它吃的
  //    `lastOkTsMs`/`lastOkElapsedSec` 是**可达性口径**（含进行中那一轮的行），不是
  //    「已完成轮」的统计口径 —— 详见 pool-history.ts 的 DayBucket 注释（二轮评审补正）。
  //  · 贡献数（pill 的 `lastContrib`）取 `agg.lastContrib`（跨课按完成时刻选的最新完成轮）；
  //    空 map = 无完成信号 = 无池数据（-1）。
  // local 节点是否出、以及它的 slots，是**结构**（cfg 现算，见 computeSlowSnapshot）；
  // 这里只给它的贡献数——池历史不可用只是拿不到它（-1），不能因此把 local 节点整块吞掉。
  const slowById = new Map<string, boolean>()
  const contribById = new Map<string, number>()
  let localContrib = -1
  // 首页贡献度缩略（R1）：**顺手**算（同一份 agg，边际成本 ≈ buildContributionView 的 10ms，
  // 且发生在后台刷新器里）；`inflight` 取 `hubCache.peek()` 的**上一拍**值——**不** await
  // 一次 1.2–1.5s 的 hub 探测（A6 裁决；刷新器每拍本来就把 getHubAdmin 暖在同一缓存里）。
  let contributionBrief: ContributionBrief | null = null
  if (agg) {
    const all = projectWindow(agg, resolveWindow('all', Date.now(), agg.epochMs))
    // 输入是窗口投影，但其中的时刻/耗时字段取**全部行**（含进行中那一轮）——「还在结算吗」
    // 与「这一轮的账结清了吗」是两个问题，混用会把轮前段就交完活的节点误推成离线。
    for (const [id, h] of all.hist) slowById.set(id, isSlowNode(h))
    if (agg.lastContrib.size > 0) {
      for (const [id, v] of agg.lastContrib) contribById.set(id, v)
      localContrib = contribById.get('local') ?? 0
    }
    try {
      // 窗口 = **近 24 小时滚动档**（用户 2026-10-03：首页要「最近 24 小时的贡献度」）。
      // ⚠ 该档吃子日事件环，环的保留时长（`ROLLING_KEEP_MS`）必须 ≥ 24h，否则数字静默偏低。
      const w = resolveWindow('24h', Date.now(), agg.epochMs)
      // 两侧 N 分开给：采样 top-3（节点行已逐个列身份）；PPO **全列**（用户口径：云机就那几台，
      // 「哪几台在干活、各占多少」比只看前三名有用）——两种人群、两种单位（WC-plan §4.1b）。
      contributionBrief = compactSummary(
        buildContributionView(agg, w, inflightByWorkerFromQueue(peekHubAdmin()?.queue ?? null)),
        3,
        BRIEF_ALL,
      )
    } catch {
      contributionBrief = null
    }
  }
  const [nodes, pushProbes, componentHealth] = await Promise.all([
    nodeProbeResults(cfg),
    probePushFleetHealth(cfg),
    computeComponentHealth(cfg),
  ])
  return {
    nodes,
    slowById,
    contribById,
    localContrib,
    pushProbes,
    componentHealth,
    contributionBrief,
  }
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
  // 课程热加载判决（§2026-09-13-hot-reload）：账本尾行派生；账本小文件 + 尾部窗口读。
  // ★ 2026-10-03：单课单值的 `loopComplete` 已移出本快照——收官横幅改为 `state-view.ts` 的
  //   多课聚合 `loopCompletes`（plan/dashboard-banner-global §4.1：本快照按课程键控，全课聚合
  //   放进来会被按「请求课程」各缓存一份，切课即重算、并发各算一遍）。这里只留 courseEdit。
  let courseEdit: CourseEdit | null = null
  const loopAlive = components.some((c) => c.key === 'trainingLoop' && c.status === 'running')
  if (course && loopAlive) {
    try {
      const ledgerTail = readLogTail(
        path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'),
        1000,
      ).lines
      courseEdit = courseEditFromLedgerTail(ledgerTail)
    } catch {
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
    courseEdit,
    pushFleet: assemblePushFleet(cfg, probes.pushProbes),
  }
}

/** 账本中最近一条 course_edit 事件 → 热加载判决；无则 null（纯函数，可单测）。
 *
 * 事件是**状态**不是瞬时告警：rejected 横幅要跨后续 iteration 事件持久（用户改回
 * 文件后 trainer 写 restored → 覆盖为 restored → 横幅自然消失），所以取尾部窗口内
 * 最后一条，而非只看尾行。窗口 1000 行（每 iter ~5 事件 ≈ 200 iter 覆盖）。 */
