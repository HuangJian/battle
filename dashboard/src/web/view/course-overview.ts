/** course-overview.ts — 多课程并行总览 + push worker 登记的视图类型与纯函数。
 *
 *  背景（2026-09-18，单 hub 多课程）：一个 hub-server 进程托管 N 份 job 账本、跨课程轮转派发，
 *  训练循环也允许多门课同时跑。于是控制台需要两个新面：
 *    ① **并行总览**：每门在训课程一行（状态 / iter / 队列深度 / 在飞 / 离线 / 竞速）——
 *       它是「为什么某门课在饿着」的唯一答案面；
 *    ② **worker 登记**：把 GPU push worker 写进 rl-config（hub 按 mtime 热重载）并看它的探活态。
 *
 *  本模块**只放类型与纯函数**（无 IO、无 node: 模块）——解析与合并都是最容易写错、
 *  也最值得单测的部分，故与传输层分开。 */

// ────────────────────────── hub 观测面（GET /admin/queue） ──────────────────────────

/** hub 的毒包熔断阈值（`remote/hub_server.py::FREEZE_AFTER_RECLAIMS` 的**镜像常量**）。
 *
 *  为什么镜像在这里：面板要把「零回传 3 次」说成「到了阈值」才读得懂，而控制台不能 import python。
 *  权威仍在 python —— `tests/poison-unfreeze.test.ts` 对着源码文本核对这一个字面量，
 *  改了阈值不改这里就红（与 `kickstart-receipt` 的镜像常量同规）。 */
export const FROZEN_RECLAIMS = 3

/** 被毒包熔断冻住的一份 job（hub `_frozen` 的一条记录，plan/accident.plan.md §4.1）。
 *
 *  为什么要有它上屏：「已冻的 job 不在 pending 里」 ⇒ 只给队列深度的话，操作员看到的只是
 *  「队列莫名其妙短了」，而真正的事实是「这份 payload 认领 N 次零回传，hub 主动把它拿出了池子」。
 *  冻住 ≠ 死亡：人工确认（`POST /admin/unfreeze`）后回池可重领——这是熔断唯一的可逆口。 */
export interface FrozenJobView {
  jobId: string
  /** 「认领后零回传」次数（熔断判据；hub 侧阈值 3）。 */
  reclaims: number
  /** 最后一次零回传的认领者身份（空串 = 无身份）。 */
  worker: string
  /** 落冻时刻（epoch 秒；0 = 缺失）。 */
  ts: number
}

/** 单课程队列行（hub `queue_state()` 的一行）。 */
export interface HubQueueCourseView {
  /** `online` = 参与实时派发；`offline` = 只收回传，不派活。 */
  mode: 'online' | 'offline'
  /** 可领取 job 数（= 队列深度）。 */
  pending: number
  /** 在飞（租约未过期）条数。 */
  inflight: number
  /** 队首 job_id（无 → null）。 */
  nextJob: string | null
  /** 在飞持有人（worker 身份）；空串 = 无身份（旧 worker / 手写 curl）。 */
  holders: string[]
  /** 毒包熔断冻结的 job（§4.1）；空数组 = 没有冻的（不是「不可知」）。 */
  frozen: FrozenJobView[]
}

/** hub 调度面观测（`/admin/queue` 归一化后的视图）。 */
export interface HubQueueView {
  courses: Record<string, HubQueueCourseView>
  /** 轮转序（hub 启动时的课程表）。 */
  order: string[]
  /** 上一份派发到的课程（轮转游标；null = 还没派过）——「最近派发」那一列。 */
  cursor: string | null
  /** 离线课程名（只收回传、不实时派发）。 */
  offline: string[]
  /** 在实时派发的课程数。 */
  activeCourses: number
  /** 窗口内活跃 worker 数（避让链/观测用）。 */
  activeWorkers: number
  /** 云端停机达令（随任务同发；不停任务）。 */
  halt: boolean
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** 解析 `/admin/queue` 每课的 `frozen` 块（`{job_id: {reclaims, worker, ts}}`）→ 列表。
 *
 *  宽容解析（与整个 `/admin/queue` 同规）：形状不符 → 空数组（缺这一块 = 这个 hub 版本
 *  还没有熔断，而不是「没有冻的 job」——但两者对操作员都是「无需处理」，故不另设不可知态）。 */
export function parseFrozenBlock(raw: unknown): FrozenJobView[] {
  if (!raw || typeof raw !== 'object') return []
  const out: FrozenJobView[] = []
  for (const [jobId, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!jobId) continue
    const rec = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
    out.push({
      jobId,
      reclaims: num(rec.reclaims),
      worker: typeof rec.worker === 'string' ? rec.worker : '',
      ts: num(rec.ts),
    })
  }
  return out.sort((a, b) => b.reclaims - a.reclaims || a.jobId.localeCompare(b.jobId))
}

/** 解析 hub `/admin/queue` 的响应体 → 视图；形状不符 → null（UI 显示空态，不炸整页）。
 *
 *  宽容解析是刻意的：hub 是独立进程、可能比控制台新/旧一个版本；缺字段退化为 0/空
 *  比让整个 /api/state 500 好得多（与 console 其它只读面的容错口径一致）。 */
export function parseHubQueue(body: unknown): HubQueueView | null {
  if (!body || typeof body !== 'object') return null
  const raw = body as Record<string, unknown>
  if (!raw.courses || typeof raw.courses !== 'object') return null
  const courses: Record<string, HubQueueCourseView> = {}
  for (const [name, v] of Object.entries(raw.courses as Record<string, unknown>)) {
    if (!v || typeof v !== 'object') continue
    const c = v as Record<string, unknown>
    const inflightRaw = Array.isArray(c.inflight) ? (c.inflight as unknown[]) : []
    const holders: string[] = []
    for (const it of inflightRaw) {
      if (it && typeof it === 'object') {
        const w = (it as Record<string, unknown>).worker
        holders.push(typeof w === 'string' ? w : '')
      }
    }
    courses[name] = {
      mode: c.mode === 'offline' ? 'offline' : 'online',
      pending: num(c.pending_n),
      inflight: inflightRaw.length,
      nextJob: typeof c.next_job === 'string' ? c.next_job : null,
      holders,
      frozen: parseFrozenBlock(c.frozen),
    }
  }
  return {
    courses,
    order: Array.isArray(raw.order)
      ? raw.order.filter((x): x is string => typeof x === 'string')
      : [],
    cursor: typeof raw.cursor === 'string' && raw.cursor ? raw.cursor : null,
    offline: Array.isArray(raw.offline)
      ? raw.offline.filter((x): x is string => typeof x === 'string')
      : [],
    activeCourses: num(raw.active_courses),
    activeWorkers: num(raw.active_workers),
    halt: raw.halt === true,
  }
}

// ────────────────────────── 离线段进度（GET /admin/offline） ──────────────────────────

/** 一次离线段（一个 run_id 手）已回传到 hub 的逐轮产物统计。
 *
 *  为什么需要它：离线课的段内几轮**不在课程账本里**（hub 不跑那些轮，训练侧也没参与），
 *  唯一能回答「它在跑还是挂了」的事实就是产物目录里最近一轮的时间戳。 */
export interface OfflineRunView {
  /** 已收到的轮次（升序）。 */
  its: number[]
  /** 已收到的轮数（= `its.length`；hub 也单独给，两者对不上时以 `its` 为准）。 */
  count: number
  /** 最近一件产物的 mtime（epoch 秒）；0 = 没读到。 */
  lastMtime: number
}

/** 解析 hub `/admin/offline` 的响应体 → `{课: {runId: 段内进度}}`；形状不符 → null。
 *
 *  宽容解析（与 `parseHubQueue` 同规）：hub 可能比控制台新/旧一个版本，缺字段退化成
 *  空表比让整页 /api/state 500 好。 */
export function parseOfflineProgress(
  body: unknown,
): Record<string, Record<string, OfflineRunView>> | null {
  if (!body || typeof body !== 'object') return null
  const raw = (body as Record<string, unknown>).progress
  if (!raw || typeof raw !== 'object') return null
  const out: Record<string, Record<string, OfflineRunView>> = {}
  for (const [course, runsRaw] of Object.entries(raw as Record<string, unknown>)) {
    if (!course || !runsRaw || typeof runsRaw !== 'object') continue
    const runs: Record<string, OfflineRunView> = {}
    for (const [runId, v] of Object.entries(runsRaw as Record<string, unknown>)) {
      if (!runId || !v || typeof v !== 'object') continue
      const r = v as Record<string, unknown>
      const its = Array.isArray(r.its)
        ? (r.its as unknown[]).filter(
            (x): x is number => typeof x === 'number' && Number.isFinite(x),
          )
        : []
      its.sort((a, b) => a - b)
      runs[runId] = {
        its,
        // count 缺/不实（与 its 长度不符）时以 its 为准：轮次数是能数出来的事实。
        count: typeof r.count === 'number' && Number.isFinite(r.count) ? r.count : its.length,
        lastMtime: num(r.last_mtime),
      }
    }
    if (Object.keys(runs).length) out[course] = runs
  }
  return out
}

/** 一门课的全部离线段：已收到的总轮数 + 最后一轮号 + 最近时间戳（跨 run 取最大）。 */
export function offlineSummary(runs: Record<string, OfflineRunView> | undefined): {
  rounds: number
  lastIter: number | null
  lastMtime: number
} {
  if (!runs) return { rounds: 0, lastIter: null, lastMtime: 0 }
  let rounds = 0
  let lastIter: number | null = null
  let lastMtime = 0
  for (const r of Object.values(runs)) {
    rounds += r.count
    for (const it of r.its) lastIter = lastIter === null ? it : Math.max(lastIter, it)
    lastMtime = Math.max(lastMtime, r.lastMtime)
  }
  return { rounds, lastIter, lastMtime }
}

// ────────────────────────── 并行总览行 ──────────────────────────

export interface CourseOverviewRow {
  course: string
  /** trainingLoop 进程存活（registry 按课程键控）——「在训」的唯一判据。 */
  training: boolean
  /** 该课账本尾行的 iteration 号（无账本/无 iteration → null）。 */
  iter: number | null
  /** hub 侧把这门课标为离线（只收回传，不派活）。 */
  offline: boolean
  /** hub 是否认识这门课（false = 没在该 hub 的课程表里）。 */
  hubSeen: boolean
  queuePending: number
  inflight: number
  /** 离线段内已回传的轮数（**不在课程账本里**：那些轮由云机自己跑）。 */
  offlineRounds: number
  /** 段内已收到的最新轮号（null = 没收到任何一轮）。 */
  offlineLastIter: number | null
  /** 段内最近一件产物的 mtime（epoch 秒）；0 = 无。 */
  offlineLastMtime: number
  /** 该课被毒包熔断冻结的 job（§4.1）；空 = 没有（hub 无应答时也是空——见 `hubOnline`）。 */
  frozen: FrozenJobView[]
}

/** 恒等在训课程 ∩ hub 课程表 ∩ 查看课程的课程清单（保持入参顺序 = 服务端的新→旧）。 */
export function overviewCourseNames(input: {
  courses: string[]
  training: string[]
  hubOrder: string[]
  viewing: string
}): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const c of [...input.training, ...input.hubOrder, ...input.courses, input.viewing]) {
    if (!c || seen.has(c)) continue
    seen.add(c)
    out.push(c)
  }
  return out
}

/** 组装总览行（纯函数；hub 不可达时 queue=null ⇒ 队列列显示 `—`）。 */
export function buildCourseRows(input: {
  courses: string[]
  training: string[]
  queue: HubQueueView | null
  iters: Record<string, number | null>
  /** 逐课程的离线段进度（`parseOfflineProgress` 的产物；缺 = 没读到）。 */
  offline?: Record<string, Record<string, OfflineRunView>> | null
}): CourseOverviewRow[] {
  const training = new Set(input.training)
  return input.courses.map((course) => {
    const q = input.queue?.courses[course]
    const seg = offlineSummary(input.offline?.[course])
    return {
      course,
      training: training.has(course),
      iter: input.iters[course] ?? null,
      offline: q?.mode === 'offline',
      hubSeen: q !== undefined,
      queuePending: q?.pending ?? 0,
      inflight: q?.inflight ?? 0,
      offlineRounds: seg.rounds,
      offlineLastIter: seg.lastIter,
      offlineLastMtime: seg.lastMtime,
      frozen: q?.frozen ?? [],
    }
  })
}

/** 全 hub 范围被冻结的 job（跨课程展平，课程名有序）——面板「毒包熔断」横幅的数据源。
 *
 *  为什么展平：冻住的 job 是**事故现场**（同一份 payload 反复炸），操作员要看到的是
 *  「哪门课、哪份 job、几次、被谁」，而逐课行里的一个小角标读不出这些；解冻是**逐 job** 的
 *  动作（`POST /admin/unfreeze?job_id=`），所以列表形态最贴。 */
export function frozenJobs(
  overview: ParallelOverviewView | null,
): ({ course: string } & FrozenJobView)[] {
  if (!overview) return []
  const out: ({ course: string } & FrozenJobView)[] = []
  for (const row of overview.rows) {
    for (const f of row.frozen) out.push({ course: row.course, ...f })
  }
  return out
}

/** 整页总览视图（hub 行 + 每课行）。 */
export interface ParallelOverviewView {
  /** 命中的 hub 基址（无 = 没有任何 hub 在应答 `/admin/queue`）。 */
  hubUrl: string | null
  hubOnline: boolean
  activeCourses: number
  activeWorkers: number
  halt: boolean
  /** 最近派发到的课程（hub 轮转游标）；null = 还没派过或 hub 不可达。 */
  recentDispatch: string | null
  /** ★2026-09-22：**离线课程名**（hub 标为只收回传）——顶栏 pill / 矩阵据此走「回传」维度。 */
  offline: string[]
  rows: CourseOverviewRow[]
  /** 逐课程的离线段进度（原始形状，UI 需要按 run 展开时用；缺 = 没读到）。 */
  offlineProgress: Record<string, Record<string, OfflineRunView>> | null
}

/** 账本尾行里最后一个 `iteration` 事件的轮次（纯函数，可单测）。
 *
 *  **只认 `iteration` 事件**：同一本账本里还有 hub 追加的 `job_completed` / `job_failed`
 *  与训练侧写的 `run_start` / `run_complete`（多写者文件）——把 `job_completed` 的 it
 *  当轮次会读出「还没跑完的那一轮」，那是错的。 */
export function latestIterFromLedgerTail(lines: string[]): number | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line || !line.includes('"iteration"')) continue
    try {
      const e = JSON.parse(line) as { event?: unknown; iter?: unknown; it?: unknown }
      if (e.event !== 'iteration') continue
      const it = typeof e.iter === 'number' ? e.iter : typeof e.it === 'number' ? e.it : null
      if (it !== null) return it
    } catch {
      /* 写半行竞态：跳过该行看更早的 */
    }
  }
  return null
}

// ────────────────────────── push worker 登记 ──────────────────────────

/** 登记在册的 GPU push worker（rl-config `nodes[]` 里 `gpu_push` 的条目）。 */
export interface PushWorkerView {
  id: string
  url: string
  enabled: boolean
  concurrency: number
  /** 面板直探 `{url}/ping`（worker_server 的 `/ping`）：null = 未探（停用）。 */
  online: boolean | null
  /** 该 worker 当前是否在跑活（`/ping` 的 `busy`）；null = 未探。 */
  busy: boolean | null
  /** hub 派发器登记表里的探活结论（缺 = hub 未启用 push 派发 / 不可达）。 */
  hubOnline: boolean | null
}

export interface PushWorkerRegistryView {
  /** hub 基址（探测队列时命中的那个；null = 没有 hub 在应答）。 */
  hubUrl: string | null
  /** hub-server 是否带 `--push`（false = 只落了配置，hub 不会真派发）。 */
  mounted: boolean
  /** `rl.hub_push` 的**生效值**（缺省 true = 配了节点就走 hub 派发）。
   *  关掉则训练侧直推登记节点（`stack/push-config.ts::hubPushEnabled` 同口径）——它是
   *  「push 派发走不走中介」的唯一开关，故与登记表同屏放。 */
  hubPush: boolean
  workers: PushWorkerView[]
}

/** push worker 节点的 id 合法域（与 python `push_worker_from_node` 的校验同域：
 *  非空、无空白、无路径分隔符——它会进日志与 job 归属标记，含空格会让「谁在跑」读不出来）。 */
export function validWorkerId(id: string): boolean {
  return /^[A-Za-z0-9._-]{1,40}$/.test(id.trim())
}
