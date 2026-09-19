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
  /** 在实时派发的课程数（竞速判据之一）。 */
  activeCourses: number
  /** 窗口内活跃 worker 数（竞速判据之二）。 */
  activeWorkers: number
  /** 此刻是否在竞速广播（在派发课程数 < 活跃 worker 数）。 */
  raceActive: boolean
  /** 云端停机达令（随任务同发；不停任务）。 */
  halt: boolean
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
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
    raceActive: raw.race_active === true,
    halt: raw.halt === true,
  }
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
}): CourseOverviewRow[] {
  const training = new Set(input.training)
  return input.courses.map((course) => {
    const q = input.queue?.courses[course]
    return {
      course,
      training: training.has(course),
      iter: input.iters[course] ?? null,
      offline: q?.mode === 'offline',
      hubSeen: q !== undefined,
      queuePending: q?.pending ?? 0,
      inflight: q?.inflight ?? 0,
    }
  })
}

/** 整页总览视图（hub 行 + 每课行）。 */
export interface ParallelOverviewView {
  /** 命中的 hub 基址（无 = 没有任何 hub 在应答 `/admin/queue`）。 */
  hubUrl: string | null
  hubOnline: boolean
  raceActive: boolean
  activeCourses: number
  activeWorkers: number
  halt: boolean
  /** 最近派发到的课程（hub 轮转游标）；null = 还没派过或 hub 不可达。 */
  recentDispatch: string | null
  rows: CourseOverviewRow[]
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
