/** overview.ts — 并行课程总览 + push worker 登记的组装（唯一事实源 = hub 观测面 + 磁盘账本）。
 *
 *  两个面共用一次 hub 探测（`getHubAdmin`，5s TTL + 单飞）：
 *    · **总览**：每门课一行（在训 / iter / 队列深度 / 在飞 / 离线）——在训判据取
 *      registry 的 trainingLoop 条目存活，iter 取该课账本尾行，队列取 hub `/admin/queue`；
 *    · **worker 登记**：rl-config `nodes[].gpu_push` 为条目来源（写回即配置，hub 按 mtime
 *      热重载），hub 侧登记表只提供「它认为这台在不在线」这一列。
 *
 *  为什么不是慢快照的一部分：慢快照按课程键控（每个查看者各算一份），而 hub 观测面是
 *  **进程级全局**的——按课程各探一遍纯浪费。故独立一层 5s 缓存，与慢快照同节奏。
 */

import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { pidAlive } from '../../core/net'
import { entryForCourse, loadRegistry, scopeOf } from '../../core/registry'
import type { RlConfig } from '../../core/types'
import { hubPushWorkers, liveHub, withWorkerProbes } from '../../stack/hub-admin'
import {
  type HubQueueView,
  type ParallelOverviewView,
  type PushWorkerView,
  type PushWorkerRegistryView,
  buildCourseRows,
  latestIterFromLedgerTail,
  overviewCourseNames,
} from '../../web/view'
import { readLogTail } from './logs'

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
  /** 已直探过的 push worker 行（**探活也在本缓存里**，不在请求路径上）。 */
  workers: PushWorkerView[]
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
      local: n.local_push === true,
      hubOnline: null,
    }))
}

const hubCache = new Map<string, { at: number; val: HubAdmin }>()
const hubInFlight = new Map<string, Promise<HubAdmin>>()

/** 探测 hub 观测面（队列 + push 登记表）；5s 内命中缓存，并发共享同一次探测。 */
export async function getHubAdmin(cfg: RlConfig, course: string): Promise<HubAdmin> {
  const now = Date.now()
  const ent = hubCache.get(course)
  if (ent && now - ent.at < OVERVIEW_TTL_MS) return ent.val
  const inflight = hubInFlight.get(course)
  if (inflight) return inflight
  const p = (async (): Promise<HubAdmin> => {
    const token = String(cfg.rl?.remote_token ?? '')
    // 两段并行：worker 直探（与 hub 无关）与 hub 探测同时开跑——串行会把冷算拉
    // 到 3×超时（hub → 登记表 → 工人），而它们之间无依赖。
    const [probed, live] = await Promise.all([
      withWorkerProbes(workerRows(cfg), cfg),
      liveHub(cfg, course),
    ])
    const pushMap = live ? await hubPushWorkers(live.url, token) : null
    return { url: live?.url ?? null, queue: live?.queue ?? null, pushMap, workers: probed }
  })().then(
    (val) => {
      hubCache.set(course, { at: Date.now(), val })
      hubInFlight.delete(course)
      return val
    },
    (e) => {
      hubInFlight.delete(course)
      throw e
    },
  )
  hubInFlight.set(course, p)
  return p
}

/** 动作后置空（与 invalidateSlowSnapshot 同规）：登记/移除 worker 后下一拍不读旧登记表。 */
export function invalidateHubAdmin(): void {
  hubCache.clear()
}

// ────────────────────────── 组装 ──────────────────────────

/** 该课账本尾行的最新轮次（**只认 iteration 事件**；多写者账本见 `latestIterFromLedgerTail`）。
 *  读取量 = 尾部 600 行（约 120 轮），在 5s 快照节奏下可忽略。 */
export function courseIter(course: string): number | null {
  if (!course) return null
  try {
    const lines = readLogTail(path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'), 600).lines
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
    rows: buildCourseRows({ courses: names, training, queue: admin.queue, iters }),
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
    workers: admin.workers.map((w) => ({
      ...w,
      hubOnline: admin.pushMap ? (admin.pushMap.get(w.id) ?? null) : null,
    })),
  }
}
