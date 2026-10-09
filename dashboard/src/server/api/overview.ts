/** overview.ts — 并行课程总览 + push worker 登记的组装（唯一事实源 = hub 观测面 + 磁盘账本）。
 *
 *  两个面共用一次 hub 探测（`getHubAdmin`，5s TTL + 单飞）：
 *    · **总览**：每门课一行（在训 / iter / 队列深度 / 在飞 / 离线）——在训判据取
 *      registry 的 trainingLoop 条目存活，iter 取该课账本尾行，队列取 hub `/admin/queue`；
 *    · **worker 登记**：rl-config `nodes[].gpu_push` 为条目来源（写回即配置，hub 按 mtime
 *      热重载），hub 侧登记表只提供「它认为这台在不在线」这一列。
 *
 *  为什么不是慢快照的一部分：慢快照的**课程级**部分按课程键控（每个查看者各算一份），
 *  而 hub 观测面是**进程级全局**的——按课程各探一遍纯浪费。故独立一层 5s 缓存，与慢快照同节奏。
 *
 *  ★ 2026-09-22：这个缓存的**键也要是全局的**（此前按课程键控）。共享单 hub 之后
 *  `hubCandidates` 根本不看课程（只按账本里活着的 hub 条目挑基址），push worker 表也住在
 *  `rl-config`（机群级）——按课程键控 = 切到没看过的课就把 hub（1.2s）+ 逐 worker 探活
 *  重做一遍，正是「切课程要等几秒」的另一半。用 SWR 语义：陈旧先给旧值 + 后台重算，
 *  动作后 `invalidateHubAdmin()` 硬作废（登记/移除 worker 必须即时上屏）。
 */

import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { pidAlive } from '../../core/net'
import { createSwrCache } from '../../core/swr-cache'
import { entryForCourse, loadRegistry, scopeOf } from '../../core/registry'
import type { RlConfig } from '../../core/types'
import { hubOfflineAdmin, hubPushWorkers, liveHub, withWorkerProbes } from '../../stack/hub-admin'
import { hubPushEnabled } from '../../stack/push-config'
import {
  type HubHoldView,
  type HubPendingExportView,
  type HubQueueView,
  type OfflineLeaseView,
  type OfflineResultView,
  type OfflineRunView,
  type OfflineStalledView,
  type ParallelOverviewView,
  type PushWorkerView,
  type PushWorkerRegistryView,
  type ReadStaleView,
  buildCourseRows,
  latestIterFromLedgerTail,
  overviewCourseNames,
} from '../../web/view'
import { writeHeldCache } from '../actions/loop-control'
import { readLedgerTail } from './logs'

// ────────────────────────── 共享 trainer 存活（「在训」的进程事实） ──────────────────────────

/** **共享 trainer 进程存活**：registry 里 `trainingLoop` 的**无课程槽**（`''`）条目。
 *
 *  一个进程服务所有课程（2026-09-19 / R3-5）⇒ 账本里只有 `['']` 一个槽，按课查存活
 *  只会得到「一门课都没在训」这个假事实。故存活是**进程级**一个布尔，而「这一课有没有活」
 *  来自 python 的队列状态（`loop-queue.trainingFromQueue`）——两半各取自它能回答的那一半。
 *
 *  为什么不问 hub：hub 只知道「谁派过活」，训练循环停在两轮之间时它一无所知。
 *  也不问 console-state：那是「操作员在看哪门课」，与「哪几门课在跑」是两件事
 *  （多课程并行下二者必然不同）。 */
export function sharedTrainerAlive(): boolean {
  try {
    const ent = entryForCourse(loadRegistry(), 'trainingLoop', scopeOf('trainingLoop'))
    return pidAlive(ent?.pid)
  } catch {
    /* 账本不可读 → 视为没在跑（面板显示空态，不编） */
    return false
  }
}

// ────────────────────────── hub 观测面（5s 缓存 + 单飞） ──────────────────────────

export const OVERVIEW_TTL_MS = 5000

interface HubAdmin {
  url: string | null
  queue: HubQueueView | null
  /** hub 派发器登记表：worker id → 探活结论；null = hub 未启用 push 派发 / 不可达。 */
  pushMap: Map<string, boolean> | null
  /** 逐课程离线段进度（`/admin/offline`）；null = hub 不可达 / 端点不存在（旧版 hub）。 */
  offline: Record<string, Record<string, OfflineRunView>> | null
  /** 逐课程离线租约（`/admin/offline.leases`；stale/墓碑徽标用）；null = hub 不可达 / 旧版 hub。 */
  leases: Record<string, OfflineLeaseView> | null
  /** 逐课程各 run 的**段末摘要**（`/admin/offline.results`）——离线补评的收官判据
   *  （plan/offline-eval-backfill）；null = hub 不可达 / 旧版 hub。 */
  offlineResults: Record<string, Record<string, OfflineResultView>> | null
  /** 停滞告警（`/admin/offline.stalled`；T8）；null = hub 不可达 / 旧版 hub。 */
  offlineStalled: OfflineStalledView[] | null
  /** **逐课接管**（★M4：hub `/admin/queue` 每课行的 `hold`；从 `queue` 读取，零新增探测）。
   *  空表 = 本拍没人被接管（含 hub 不可达——那一种由 `url===null`/`stale` 另行标注）。 */
  holds: Record<string, HubHoldView>
  /** **逐课导包软态**（★M4：`pending_export`；同上）。 */
  pendingExports: Record<string, HubPendingExportView>
  /** 近期报过到的自主盘（★P2-5；hub `offline_disk.recent`）；`null` = 旧 hub 未上报。 */
  offlineDisks: string[] | null
  /** 读面新鲜度（P1-11）：非 null = 这是**上一拍**的 hub 事实（探测失败但还在保值窗口内）。
   *  `null` = 本拍探测成功，或已经超窗退化（那时 `url === null`，显式未知）。 */
  stale: ReadStaleView | null
  /** worker 行 = **当下 cfg** ⊕ 探活列（探活取自下面的探测缓存）。 */
  workers: PushWorkerView[]
}

/** **hub 探测**结果（贵：hub 不可达时每个候选 1.2s + 逐 worker 探活 1.5s）。
 *  共享单 hub ⇒ 与课程无关，故全局单条目缓存（见 `getHubAdmin`）。 */
interface HubProbe {
  url: string | null
  queue: HubQueueView | null
  pushMap: Map<string, boolean> | null
  offline: Record<string, Record<string, OfflineRunView>> | null
  leases: Record<string, OfflineLeaseView> | null
  offlineResults: Record<string, Record<string, OfflineResultView>> | null
  offlineStalled: OfflineStalledView[] | null
  /** 从 `/admin/queue` 逐课行抽出的接管/导包/露面面（★M4；同一份应答，零新增网络）。 */
  holds: Record<string, HubHoldView>
  pendingExports: Record<string, HubPendingExportView>
  offlineDisks: string[] | null
  /** worker 直探（id → online/busy；停用/无 key = 缺席）。 */
  workerPing: Map<string, { online: boolean | null; busy: boolean | null }>
}

/** 从队列视图抽逐课接管面（纯函数；hub 不可达 ⇒ 两张空表）。 */
function holdFacts(queue: HubQueueView | null): {
  holds: Record<string, HubHoldView>
  pendingExports: Record<string, HubPendingExportView>
  offlineDisks: string[] | null
} {
  const holds: Record<string, HubHoldView> = {}
  const pendingExports: Record<string, HubPendingExportView> = {}
  for (const [course, row] of Object.entries(queue?.courses ?? {})) {
    if (row.hold) holds[course] = row.hold
    if (row.pendingExport) pendingExports[course] = row.pendingExport
  }
  return { holds, pendingExports, offlineDisks: queue?.offlineDisks ?? null }
}

/** **worker 类型**（★P2-5；纯函数，便于单测）：持 hold / 自主盘报过到 ⇒ autonomous；
 *  否则只在队列在飞或 hub 登记表里出现 ⇒ collaborative；两个面都没有 ⇒ null（不猜）。 */
export function workerKindOf(
  id: string,
  facts: { holds: Record<string, HubHoldView>; offlineDisks: string[] | null },
  inflightHolders: Set<string>,
  hubRegistered: boolean,
): PushWorkerView['kind'] {
  if (!id) return null
  for (const h of Object.values(facts.holds))
    if (h.workerId && h.workerId === id) return 'autonomous'
  if (facts.offlineDisks?.includes(id)) return 'autonomous'
  if (inflightHolders.has(id)) return 'collaborative'
  return hubRegistered ? 'collaborative' : null
}

/** rl-config 里的 push worker 行（未探活；`kind` 由 `getHubAdmin` 按 hub 观测面补）。 */
function workerRows(cfg: RlConfig): Array<Omit<PushWorkerView, 'online' | 'busy' | 'kind'>> {
  return (cfg.nodes ?? [])
    .filter((n) => n.gpu_push === true)
    .map((n) => ({
      id: String(n.id ?? ''),
      url: String(n.url ?? ''),
      enabled: n.enabled !== false,
      concurrency: Number(n.concurrency ?? 1) || 1,
      hubOnline: null,
    }))
}

/** hub 探测的全局缓存（单条目：共享单 hub ⇒ 结果与课程无关）。 */
const hubCache = createSwrCache<HubProbe>(OVERVIEW_TTL_MS)

/** **last-known-good**（P1-11）：最近一次成功的完整探测（失败时回它，不把读数清空）。 */
let hubLastGood: HubProbe | null = null
/** 连续失败的起点（epoch ms；0 = 当前没有在失败）。 */
let hubFailSince = 0
/** 最近一次失败原因（诊断用；成功即清）。 */
let hubLastError = ''

/** 陈旧保值窗口（P1-11）：连续失败超它就不再保旧值。取一个刷新周期（TTL）。 */
export const HUB_KEEP_MS = OVERVIEW_TTL_MS

/** 保值窗口判定（P1-11 的纯决策；导出让用例不必等 5s）：超窗 ⇒ 不再保旧值，显示「未知」。 */
export function hubKeepWithin(failSince: number, now: number = Date.now()): boolean {
  return failSince > 0 && now - failSince <= HUB_KEEP_MS
}

/** 探测失败的空形状（超窗后用它：`url=null` ⇒ 面板显示「未知」，不把旧值无限当真）。 */
function emptyProbe(): HubProbe {
  return {
    url: null,
    queue: null,
    pushMap: null,
    offline: null,
    leases: null,
    offlineResults: null,
    offlineStalled: null,
    holds: {},
    pendingExports: {},
    offlineDisks: null,
    workerPing: new Map(),
  }
}

/** 带 last-known-good 的探测：失败时**不抛**（除非连旧值都没有）——缓存里也放旧值，
 *  这样其它读路径（`peekHubAdmin`、总览）看到的都是同一份“上一拍的真实”。 */
async function probeWithKeep(cfg: RlConfig, course: string): Promise<HubProbe> {
  try {
    const p = await probeHubAdmin(cfg, course)
    // 「一个候选都没应答」= 失败（部分端点失败不算：offline=null 是旧 hub 的合法缺省）。
    if (p.url === null) throw new Error('没有 hub 在应答 /admin/queue（候选全试完）')
    hubLastGood = p
    hubFailSince = 0
    hubLastError = ''
    return p
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (!hubFailSince) hubFailSince = Date.now()
    hubLastError = msg
    // 有旧值回旧值；连旧值都没有 → 回空形状（**而不是抛**）：空结果也进缓存，
    // 否则「没有 hub」这种稳态会变成每请求重探一遍（旧行为是缓存 5s），
    // 而重探代价 = 候选数 × 1.2s（切课程同步等探测就是从这里来的）。
    return hubLastGood ?? emptyProbe()
  }
}

/** hub 观测面 = **结构**（cfg 的 worker 行，现算）⊕ 探测（队列/登记表/逐 worker 探活，缓存）。
 *
 *  `course` 只作为**探测入口**的参数（`liveHub` 的候选清单来自账本，与课程无关）——
 *  它**不是**缓存键：共享单 hub 下按课程各探一遍纯浪费，且切课会撞上 1.2s 探测超时。
 *  结构现算的理由：刚登记/停用的 worker（与 `rl.hub_push` 开关）必须在**第一帧**就上屏。
 *
 *  ★P1-11（2026-10-05，R4-b/g）：探测失败**不立刻清空读数**——失败在保值窗口内 ⇒ 返回上一拍
 *  完整探测 + `stale` 标注（词不变，只降级标注：探针抖一下不再把离线课跌回训练侧词）；
 *  超窗 ⇒ 退化成空探测（`url=null`，显示「未知」，不无限保旧）。 */
export async function getHubAdmin(cfg: RlConfig, course: string): Promise<HubAdmin> {
  let p: HubProbe
  try {
    p = await hubCache.get(() => probeWithKeep(cfg, course))
  } catch {
    p = emptyProbe()
  }
  const failed = hubFailSince > 0
  const withinWindow = hubKeepWithin(hubFailSince)
  if (failed && !withinWindow) p = emptyProbe()
  const stale: ReadStaleView | null = failed ? { since: hubFailSince, reason: hubLastError } : null
  // ★M4：接管面**就在这份探测里**（`/admin/queue` 每课行）——与面板同席写训练侧的
  // `held` 缓存（那个文件的写侧契约见 `actions/loop-control.ts` 末段：同一份事实两次读 =
  // 两个时刻的真相，所以必须与面板读的是同一拍）。写失败不抛（纯缓存，训练侧还有直问 hub 的主通道）。
  writeHeldCache(
    Object.entries(p.holds).map(([course, h]) => ({ course, lastProgressAt: h.lastProgressAt })),
  )
  const inflightHolders = new Set<string>()
  for (const row of Object.values(p.queue?.courses ?? {})) {
    for (const d of row.inflightDetail) if (d.worker) inflightHolders.add(d.worker)
  }
  return {
    url: p.url,
    queue: p.queue,
    pushMap: p.pushMap,
    offline: p.offline,
    leases: p.leases,
    offlineResults: p.offlineResults,
    offlineStalled: p.offlineStalled,
    holds: p.holds,
    pendingExports: p.pendingExports,
    offlineDisks: p.offlineDisks,
    stale,
    workers: workerRows(cfg).map((w) => ({
      ...w,
      online: p.workerPing.get(w.id)?.online ?? null,
      busy: p.workerPing.get(w.id)?.busy ?? null,
      kind: workerKindOf(w.id, p, inflightHolders, p.pushMap?.has(w.id) ?? false),
    })),
  }
}

/** 真正探一次（不落缓存；落缓存由 getHubAdmin 负责）。 */
async function probeHubAdmin(cfg: RlConfig, course: string): Promise<HubProbe> {
  const token = String(cfg.rl?.remote_token ?? '')
  // 两段并行：worker 直探（与 hub 无关）与 hub 探测同时开跑——串行会把冷算拉
  // 到 3×超时（hub → 登记表 → 工人），而它们之间无依赖。
  const [probed, live] = await Promise.all([
    withWorkerProbes(workerRows(cfg), cfg),
    liveHub(cfg, course),
  ])
  // 两个 hub 端点**并行**探（登记表 + 离线进度）：串行会把冷算再拉一个超时窗口，
  // 而它们互不依赖（同一个 hub 基址，各自独立问答）。
  const [pushMap, offlineAdmin] = live
    ? await Promise.all([hubPushWorkers(live.url, token), hubOfflineAdmin(live.url, token)])
    : [null, null]
  const facts = holdFacts(live?.queue ?? null)
  return {
    url: live?.url ?? null,
    queue: live?.queue ?? null,
    pushMap,
    ...facts,
    offline: offlineAdmin?.progress ?? null,
    // ★ 2026-10-05（P0-11）：租约一览（beat_at/stale/revoked）与进度同一次 `/admin/offline`
    // 应答；旧 hub 无 leases 键 ⇒ null（parseOfflineLeases 兜底）。
    leases: offlineAdmin?.leases ?? null,
    // 同一次 `/admin/offline` 应答里已有 results（`parseOfflineAdmin` 三段同源）——补评
    // 的收官判据零新增网络、零新增探测；旧 hub 无 results 时 `parseOfflineAdmin` 已兜成 {}。
    offlineResults: offlineAdmin?.results ?? null,
    offlineStalled: offlineAdmin?.stalled ?? null,
    workerPing: new Map(probed.map((w) => [w.id, { online: w.online, busy: w.busy }])),
  }
}

/** **动作后**的 hub 观测面处置（软作废）：worker 行本身（id/url/enabled/并发）与
 *  `rl.hub_push` 是现算的 → 刚登记/停用的 worker **第一帧**就上屏；hub 侧的探活列
 *  （`hubOnline`/`online`）本来就要一次 1.2–1.5s 探测，读路径先给旧值、重算丢后台
 *  —— 否则「登记完要等几秒才看到它」又回来了（2026-09-22）。
 *
 *  生产调用点是 `snapshot-refresher.invalidateAfterAction()`（与慢快照同一时机）。 */
export function refreshHubAdmin(): void {
  hubCache.refresh()
}

/** **硬作废**（下一次读**必须**重探）：改了输入（rl-config / 账本 / registry）或要隔离测试
 *  夹具时用。与 `refreshHubAdmin()` 的分工即 swr-cache 文件头的 ③/④。
 *
 *  ★P1-11：last-known-good 的模块态一并清——测试夹具隔离时上一个用例的旧探测不得被
 *  下一个用例的失败当缓存供出来（与 `invalidateLoopQueue` 同规）。 */
export function invalidateHubAdmin(): void {
  hubCache.clear()
  hubLastGood = null
  hubFailSince = 0
  hubLastError = ''
}

/** **只读窥视**（`hubCache.peek()` 包装，**不触发探测**）——给「顺手要用上一拍的 hub 观测、
 *  但**不许**为它 await 一次 1.2–1.5s 探测」的调用方（plan/dashboard-reload-perf R1/A6：
 *  贡献度缩略的 inflight 来源）。
 *
 *  新鲜度 = 后台刷新器每拍暖一次的上一拍值（≤1 个刷新周期）；这与「inflight 本来就是 5s 陈旧的
 *  观测」口径一致。冷启动首拍（缓存空）返回 null —— 缩略里暂时没有「只在飞」的 worker，
 *  第二拍起一致（守卫用例钉死这条）。
 *
 *  ★ 2026-10-09（plan/dashboard-ppo-live-rows）：两个键**同一次窥视**给出——`queue` 里现在
 *  带着 inflight 的 `course`/`it` 与 worker 上报的预取状态，`offline` 是自主盘那一行
 *  「跑到第几轮」的唯一来源（`HubProbe` 早就探了它，只是以前没透出去）。 */
export function peekHubAdmin(): {
  queue: HubQueueView | null
  offline: Record<string, Record<string, OfflineRunView>> | null
} | null {
  const p = hubCache.peek()
  return p ? { queue: p.queue, offline: p.offline } : null
}

// ────────────────────────── 组装 ──────────────────────────

/** 该课账本尾行的最新轮次（**只认 iteration 事件**；多写者账本见 `latestIterFromLedgerTail`）。
 *  读取量 = 尾部 600 行（约 120 轮），在 5s 快照节奏下可忽略。
 *  必须用 `readLedgerTail`（不截断长行）：`iteration` 事件单行 >500 字符，经
 *  `readLogTail` 的展示截断后 JSON 不可解析 ⇒ 总览「轮次」列对每门课都恒显 `—`
 *  （2026-09-20 实测 x20-steady / c6-chip 均为 null）。 */
export function courseIter(course: string): number | null {
  if (!course) return null
  try {
    const lines = readLedgerTail(path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'), 600)
    return latestIterFromLedgerTail(lines)
  } catch {
    return null
  }
}

/** 并行总览视图（在训 ∪ hub 课程表 ∪ 课程清单 ∪ 查看课程）。 */
export async function buildOverview(
  cfg: RlConfig,
  courses: string[],
  viewing: string,
  /** 在训课程（由 `loop-queue.trainingFromQueue` 推出：调度器存活 ∧ 该课未收官）。
   *  调用方传进来而不是在这里重算：那是**第二份真相**，而两份一定会以不同的速度漂开。 */
  training: string[] = [],
): Promise<ParallelOverviewView> {
  const admin = await getHubAdmin(cfg, viewing)
  const names = overviewCourseNames({
    courses,
    training,
    hubOrder: admin.queue?.order ?? [],
    viewing,
  })
  const iters: Record<string, number | null> = {}
  for (const c of names) iters[c] = courseIter(c)
  return {
    hubUrl: admin.url,
    hubOnline: admin.url !== null,
    activeCourses: admin.queue?.activeCourses ?? 0,
    activeWorkers: admin.queue?.activeWorkers ?? 0,
    halt: admin.queue?.halt ?? false,
    recentDispatch: admin.queue?.cursor ?? null,
    offlineProgress: admin.offline,
    offlineStalled: admin.offlineStalled,
    // P2-1：租约一览透到读面（告警坞文案补 revoked/stale-holder 两态用；矩阵徽标也读它）。
    offlineLeases: admin.leases,
    stale: admin.stale,
    rows: buildCourseRows({
      courses: names,
      training,
      queue: admin.queue,
      iters,
      offline: admin.offline,
      leases: admin.leases,
    }),
  }
}

/** push worker 登记视图：rl-config 为条目来源，hub 登记表只补「hub 认为它在不在线」。 */
export async function buildWorkerRegistry(
  cfg: RlConfig,
  viewing: string,
): Promise<PushWorkerRegistryView> {
  const admin = await getHubAdmin(cfg, viewing)
  return {
    hubUrl: admin.url,
    mounted: admin.pushMap !== null,
    // 派发开关（机群级，与课程无关）：缺省 true = 配了节点就走 hub 中介派发。
    hubPush: hubPushEnabled(cfg),
    workers: admin.workers.map((w) => ({
      ...w,
      hubOnline: admin.pushMap ? (admin.pushMap.get(w.id) ?? null) : null,
    })),
  }
}
