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
import {
  hubOfflineProgress,
  hubPushWorkers,
  liveHub,
  withWorkerProbes,
} from '../../stack/hub-admin'
import { hubPushEnabled } from '../../stack/push-config'
import {
  type HubQueueView,
  type OfflineRunView,
  type ParallelOverviewView,
  type PushWorkerView,
  type PushWorkerRegistryView,
  buildCourseRows,
  latestIterFromLedgerTail,
  overviewCourseNames,
} from '../../web/view'
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
  /** worker 直探（id → online/busy；停用/无 key = 缺席）。 */
  workerPing: Map<string, { online: boolean | null; busy: boolean | null }>
}

/** rl-config 里的 push worker 行（未探活）。 */
function workerRows(cfg: RlConfig): Array<Omit<PushWorkerView, 'online' | 'busy'>> {
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

/** hub 观测面 = **结构**（cfg 的 worker 行，现算）⊕ 探测（队列/登记表/逐 worker 探活，缓存）。
 *
 *  `course` 只作为**探测入口**的参数（`liveHub` 的候选清单来自账本，与课程无关）——
 *  它**不是**缓存键：共享单 hub 下按课程各探一遍纯浪费，且切课会撞上 1.2s 探测超时。
 *  结构现算的理由：刚登记/停用的 worker（与 `rl.hub_push` 开关）必须在**第一帧**就上屏。 */
export async function getHubAdmin(cfg: RlConfig, course: string): Promise<HubAdmin> {
  const p = await hubCache.get(() => probeHubAdmin(cfg, course))
  return {
    url: p.url,
    queue: p.queue,
    pushMap: p.pushMap,
    offline: p.offline,
    workers: workerRows(cfg).map((w) => ({
      ...w,
      online: p.workerPing.get(w.id)?.online ?? null,
      busy: p.workerPing.get(w.id)?.busy ?? null,
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
  const [pushMap, offline] = live
    ? await Promise.all([hubPushWorkers(live.url, token), hubOfflineProgress(live.url, token)])
    : [null, null]
  return {
    url: live?.url ?? null,
    queue: live?.queue ?? null,
    pushMap,
    offline,
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
 *  夹具时用。与 `refreshHubAdmin()` 的分工即 swr-cache 文件头的 ③/④。 */
export function invalidateHubAdmin(): void {
  hubCache.clear()
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
    raceActive: admin.queue?.raceActive ?? false,
    activeCourses: admin.queue?.activeCourses ?? 0,
    activeWorkers: admin.queue?.activeWorkers ?? 0,
    halt: admin.queue?.halt ?? false,
    recentDispatch: admin.queue?.cursor ?? null,
    offlineProgress: admin.offline,
    rows: buildCourseRows({
      courses: names,
      training,
      queue: admin.queue,
      iters,
      offline: admin.offline,
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
