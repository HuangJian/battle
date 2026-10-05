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

/** hub 的毒包熔断阈值（`hub/server.py::FREEZE_AFTER_RECLAIMS` 的**镜像常量**）。
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

/** 一条在飞 job 的观测明细（hub `queue_state` 的 inflight 行，2026-10-02 pill 精确化）。
 *
 *  `claimed_ago` / `computing_ago` 是 hub 新增的观测字段（旧 hub 没有 ⇒ `null`）——
 *  **null 不是 0**：「没有这条记录」与「刚刚认领」是两件事，编 0 会把卡死读成刚刚开始。 */
export interface HubInflightView {
  /** hub 侧 job_id（与训练侧 `LoopInflightView.jid` 对号用）。 */
  jobId: string
  /** 持有人（worker 身份）；空串 = 无身份（旧 worker / 手写 curl）。 */
  worker: string
  /** 心跳龄（秒）；null = 缺失（旧 hub）——不编 0。 */
  heartbeatAgo: number | null
  /** 认领龄（秒）；null = 旧 hub 没这一条 ⇒ 不升级「卡住」（不编龄）。 */
  claimedAgo: number | null
  /** 开算龄（秒，`POST /jobs/{id}/start` 起算）；null = 未开算 / 旧 hub。 */
  computingAgo: number | null
}

/** 权威三态 + 两个正交维（hub `queue_state` 每课的 `authority`；plan §3.1）。
 *
 *  dashboard 的 `courseStatus` **从这里读**，不自己派生第二份（P0-2/P1-10 的接线契约）：
 *  派生实现在 hub（`queue_offline.authority_of`），控制台只是把答案摆上屏。
 *  `null` = 旧 hub 没上报 ⇒ 读面标注「未知」，退化成 hub authority 之前的行为（不编）。 */
export type CourseAuthority =
  | 'auto'
  | 'pinned_online'
  | 'pinned_offline'
  | 'stopped'
  | 'not_offline'

/** 合法 authority 值（解析用；与 python `AUTHORITY_*` 常量逐字同域）。 */
const AUTHORITIES: readonly CourseAuthority[] = [
  'auto',
  'pinned_online',
  'pinned_offline',
  'stopped',
  'not_offline',
]

/** 读面新鲜度标注（P1-11）：读失败时保留上一拍值并带它上屏；`null` = 本拍读成功。
 *
 *  `since` = **连续失败的起点**（epoch ms）。超窗（一个刷新周期）后不再保旧值，整块显示
 *  「未知（读面失败 Ns）」——保旧值有限度，不无限保（plan §3.10-3 / R4-e）。 */
export interface ReadStaleView {
  since: number
  reason: string
}

/** 单课程队列行（hub `queue_state()` 的一行）。 */
export interface HubQueueCourseView {
  /** `online` = 参与实时派发；`offline` = 只收回传，不派活。 */
  mode: 'online' | 'offline'
  /** 权威三态（hub `authority_of`；`null` = 旧 hub 没上报 ⇒ 未知，不猜）。 */
  authority: CourseAuthority | null
  /** 「人固定过」（hub 派发记录的 `pinned`；`null` = 旧 hub）。 */
  pinned: boolean | null
  /** 可领取 job 数（= 队列深度）。 */
  pending: number
  /** 在飞（租约未过期）条数。 */
  inflight: number
  /** 队首 job_id（无 → null）。 */
  nextJob: string | null
  /** 在飞持有人（worker 身份）；空串 = 无身份（旧 worker / 手写 curl）。 */
  holders: string[]
  /** 该课停机达令（按课程；旧 hub 无此字段 ⇒ false）。 */
  halt: boolean
  /** 在飞明细（holder / 认领龄 / 心跳龄 / job_id）——jid 连接与悬停全因靠它。 */
  inflightDetail: HubInflightView[]
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
  /** 近期（`PEEKED_WINDOW_SEC`）被 worker `peek` 扫到过的课程集；
   *  **null = hub 未上报（旧版 hub）** ⇒ 不区分「排队·无人取」与「预取中」。 */
  peekedCourses: string[] | null
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** 可空数字（观测面的「缺失」必须是 null，不是 0——见 `HubInflightView`）。 */
function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
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
    const inflightDetail: HubInflightView[] = []
    for (const it of inflightRaw) {
      if (it && typeof it === 'object') {
        const i = it as Record<string, unknown>
        const w = i.worker
        holders.push(typeof w === 'string' ? w : '')
        inflightDetail.push({
          jobId: typeof i.job_id === 'string' ? i.job_id : '',
          worker: typeof w === 'string' ? w : '',
          heartbeatAgo: numOrNull(i.heartbeat_ago),
          claimedAgo: numOrNull(i.claimed_ago),
          computingAgo: numOrNull(i.computing_ago),
        })
      }
    }
    const authority = AUTHORITIES.includes(c.authority as CourseAuthority)
      ? (c.authority as CourseAuthority)
      : null
    courses[name] = {
      mode: c.mode === 'offline' ? 'offline' : 'online',
      authority,
      pinned: typeof c.pinned === 'boolean' ? c.pinned : null,
      pending: num(c.pending_n),
      inflight: inflightRaw.length,
      nextJob: typeof c.next_job === 'string' ? c.next_job : null,
      holders,
      halt: c.halt === true,
      inflightDetail,
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
    // null = 旧版 hub 没上报（不可知 ≠ 不在窗口——前者退化成「排队中」）。
    peekedCourses: Array.isArray(raw.peeked_courses)
      ? (raw.peeked_courses as unknown[]).filter((x): x is string => typeof x === 'string')
      : null,
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

/** 一次离线段（run）在 hub 侧的**段末摘要**（`/admin/offline.results[课][run_id]`）。
 *
 *  它是 T6 转交的判决输入：控制台在导入后按 `runId` 对齐它，只有在「自报跑满 ∧ 末轮号
 *  也一致」时才让 python 落 `run_complete`（半段导入不得亮横幅）。 */
export interface OfflineResultView {
  /** 云机自报的末轮号。 */
  itEnd: number
  /** 云机自报的停止态（complete/budget/failed/noop/…）。 */
  state: string
  /** 云机自报**跑满计划区间**（`end_it_reached`；只有它才是「已完成」）。 */
  endItReached: boolean
  /** hub 收到这份摘要的时刻（epoch 秒；0 = 旧 hub 不带）。 */
  receivedAt: number
}

/** 一门课的停滞告警（`/admin/offline.stalled`；T8）。
 *
 *  判据全部来自已有事实（租约龄 / 补传进度 mtime / 翻 mode 时刻）——自动交接把本机停采后，
 *  这是「云机根本没跑」与「一切正常」的唯一分界线。 */
export interface OfflineStalledView {
  course: string
  /** `running-stale`（有租约但进度超阈值）· `pending-export`（已翻 offline 无人跑超阈值）。 */
  why: 'running-stale' | 'pending-export' | ''
  /** 当前持有人 worker_id（没有 = 空串）。 */
  holder: string
  /** 最近一件补传产物的 mtime（epoch 秒；0 = 没读到）。 */
  lastMtime: number
  /** hub 把该课翻成 offline 的时刻（epoch 秒；0 = 无记录）。 */
  flippedAt: number
  /** 距最近一件事（进度 mtime 或翻 mode 时刻）的秒数。 */
  ageSec: number
}

/** `/admin/offline.leases[课]`：该课离线租约的持有人事实（P0-11 / P1-6）。
 *
 *  `stale` = 连续静默超阈（新盘可直接接管，hub 自动回收——**不是故障**）；`revoked` = 墓碑
 *  （切在线/交还自动时撤销，旧 worker 心跳收 409 revoked）。两块徽标的唯一事实源。 */
export interface OfflineLeaseView {
  workerId: string
  /** 连续静默秒数（心跳停多久了）。 */
  silentSec: number
  /** 静默超阈 ⇒ 新盘可直接接管（hub 自动回收；不是故障）。 */
  stale: boolean
  /** 墓碑（撤销后保留条目便于排障；旧 worker 心跳收 409 revoked）。 */
  revoked: boolean
  /** 距到期（秒）。 */
  expiresIn: number
}

/** 解析 `/admin/offline` 的 `leases`；整块缺 → null（旧 hub / 端点不存在）。
 *
 *  与其余观测面同规的宽容解析：坏条目跳过，不让整页 /api/state 500。 */
export function parseOfflineLeases(body: unknown): Record<string, OfflineLeaseView> | null {
  if (!body || typeof body !== 'object') return null
  const raw = (body as Record<string, unknown>).leases
  if (!raw || typeof raw !== 'object') return null
  const out: Record<string, OfflineLeaseView> = {}
  for (const [course, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!course || !v || typeof v !== 'object') continue
    const r = v as Record<string, unknown>
    out[course] = {
      workerId: typeof r.worker_id === 'string' ? r.worker_id : '',
      silentSec: num(r.silent_sec),
      stale: r.stale === true,
      revoked: r.revoked === true,
      expiresIn: num(r.expires_in),
    }
  }
  return out
}

/** `/admin/offline` 的完整观测面（进度 + 租约 + 段末摘要 + 停滞告警）。 */
export interface OfflineAdminView {
  progress: Record<string, Record<string, OfflineRunView>>
  /** 离线租约一览（`/admin/offline.leases`；`null`/缺省 = 旧 hub 没上报）。 */
  leases?: Record<string, OfflineLeaseView> | null
  results: Record<string, Record<string, OfflineResultView>>
  stalled: OfflineStalledView[]
}

/** 解析 hub `/admin/offline` 的 `results`（段末摘要）；缺/坏形状 → null（旧版 hub）。 */
export function parseOfflineResults(
  body: unknown,
): Record<string, Record<string, OfflineResultView>> | null {
  if (!body || typeof body !== 'object') return null
  const raw = (body as Record<string, unknown>).results
  if (!raw || typeof raw !== 'object') return null
  const out: Record<string, Record<string, OfflineResultView>> = {}
  for (const [course, runsRaw] of Object.entries(raw as Record<string, unknown>)) {
    if (!course || !runsRaw || typeof runsRaw !== 'object') continue
    const runs: Record<string, OfflineResultView> = {}
    for (const [runId, v] of Object.entries(runsRaw as Record<string, unknown>)) {
      if (!runId || !v || typeof v !== 'object') continue
      const r = v as Record<string, unknown>
      runs[runId] = {
        itEnd: typeof r.it_end === 'number' && Number.isFinite(r.it_end) ? r.it_end : 0,
        state: typeof r.state === 'string' ? r.state : '',
        endItReached: r.end_it_reached === true,
        receivedAt: num(r.received_at),
      }
    }
    if (Object.keys(runs).length) out[course] = runs
  }
  return out
}

/** 解析 hub `/admin/offline` 的 `stalled`（停滞告警）；缺/坏形状 → null（旧版 hub）。
 *
 *  宽容解析（与 `parseHubQueue` 同规）：hub 可能比控制台新/旧一个版本；**坏条目跳过**
 *  而不是让整页 /api/state 500。 */
export function parseOfflineStalled(body: unknown): OfflineStalledView[] | null {
  if (!body || typeof body !== 'object') return null
  const raw = (body as Record<string, unknown>).stalled
  if (!Array.isArray(raw)) return null
  const out: OfflineStalledView[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const r = item as Record<string, unknown>
    const course = typeof r.course === 'string' ? r.course : ''
    if (!course) continue
    const holderRaw = r.holder
    const holder =
      holderRaw &&
      typeof holderRaw === 'object' &&
      typeof (holderRaw as Record<string, unknown>).worker_id === 'string'
        ? String((holderRaw as Record<string, unknown>).worker_id ?? '')
        : ''
    out.push({
      course,
      why: r.why === 'running-stale' || r.why === 'pending-export' ? r.why : '',
      holder,
      lastMtime: num(r.last_progress_mtime),
      flippedAt: num(r.flipped_at),
      ageSec: num(r.age_sec),
    })
  }
  return out
}

/** 完整读面（progress + results + stalled）；`progress` 缺失 → null（不是 hub 的应答）。 */
export function parseOfflineAdmin(body: unknown): OfflineAdminView | null {
  const progress = parseOfflineProgress(body)
  if (progress === null) return null
  return {
    progress,
    leases: parseOfflineLeases(body),
    results: parseOfflineResults(body) ?? {},
    stalled: parseOfflineStalled(body) ?? [],
  }
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
  /** 该课 hub 停机达令（按课程；hub 不可达时恒 false）。 */
  halt: boolean
  /** 在飞明细（hub 观测；空 = 没有在飞 / 旧 hub 无明细）——pill 的 jid 连接与 title 靠它。 */
  inflightDetail: HubInflightView[]
  /** 最老在飞的认领龄（秒）；null = 没有在飞 / 旧 hub 缺 `claimed_ago`（⇒ 不升级「卡住」）。 */
  stuckSec: number | null
  /** 该课是否在近期预取窗口内；null = hub 未上报（旧 hub）⇒ 退化为「排队中」。 */
  peeked: boolean | null
  /** 待领队首 job_id（hub 观测；null = 没有待领 / hub 不可达）——悬停「队首 jid」。 */
  nextJob: string | null
  /** 权威三态（hub `/admin/queue` 每课行；`null`/缺省 = 旧 hub / hub 不可达 ⇒ 未知，不猜）。 */
  authority?: CourseAuthority | null
  /** 「人固定过」（hub 派发记录；`null`/缺省 = 未知）。 */
  pinned?: boolean | null
  /** 离线租约（`/admin/offline.leases`；`null`/缺省 = 没有租约 / 旧 hub）——stale/墓碑徽标用。 */
  lease?: OfflineLeaseView | null
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
  /** 逐课程的离线租约（`parseOfflineLeases` 的产物；缺 = 没读到 / 旧 hub）。 */
  leases?: Record<string, OfflineLeaseView> | null
}): CourseOverviewRow[] {
  const training = new Set(input.training)
  return input.courses.map((course) => {
    const q = input.queue?.courses[course]
    const seg = offlineSummary(input.offline?.[course])
    const inflightDetail = q?.inflightDetail ?? []
    // 最老那份的认领龄（「卡住」的判据输入）；全缺 `claimed_ago` ⇒ null（不编龄）。
    const claimed = inflightDetail.map((d) => d.claimedAgo).filter((x): x is number => x !== null)
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
      halt: q?.halt ?? false,
      inflightDetail,
      nextJob: q?.nextJob ?? null,
      authority: q?.authority ?? null,
      pinned: q?.pinned ?? null,
      lease: input.leases?.[course] ?? null,
      stuckSec: claimed.length ? Math.max(...claimed) : null,
      peeked:
        input.queue && input.queue.peekedCourses !== null
          ? input.queue.peekedCourses.includes(course)
          : null,
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
  /** 停滞告警（`/admin/offline.stalled`；T8）：`running` 无进度 / 已翻 offline 无人跑。
   *
   *  `null` = hub 不可达或旧版 hub（**不可知 ≠ 没停**——告警坞对 null 什么都不画，
   *  但课程矩阵的「hub 无应答」自会占位）。 */
  offlineStalled?: OfflineStalledView[] | null
  /** 逐课程离线租约（`/admin/offline.leases`；P2-1：告警坞文案补 `revoked`/`stale-holder`
   *  两态用——stale = 可接管、revoked = 已撤租。`null`/缺省 = 旧 hub 没上报，不猜。 */
  offlineLeases?: Record<string, OfflineLeaseView> | null
  /** 读面新鲜度（P1-11）：hub 探测失败但还在保旧值 ⇒ 带它；`null`/缺省 = 本拍读成功。 */
  stale?: ReadStaleView | null
}

/** 账本尾行里最后一个 `iteration` 事件的轮次（纯函数，可单测）。
 *
 *  **只认 `iteration` 事件**：同一本账本里还有 hub 追加的 `job_completed` / `job_failed`
 *  与训练侧写的 `run_start` / `run_complete`（多写者文件）——把 `job_completed` 的 it
 *  当轮次会读出「还没跑完的那一轮」，那是错的。 */
export function latestIterFromLedgerTail(lines: string[]): number | null {
  return latestIterationFromLedgerTail(lines)?.iter ?? null
}

/** 账本尾行里最后一个 `iteration` 事件的**轮次 + `time`**（纯函数，可单测）。
 *
 *  `latestIterFromLedgerTail` 只要轮次；池历史还要「这一轮是**什么时候**完成的」
 *  （跨课按完成时刻选最新完成轮，见 `server/pool-history` §3.4）——`time` 由
 *  `biz/events.write_iteration` 写成 `strftime("%Y-%m-%d %H:%M:%S")`（训练机本地、无时区）。
 *  两者共用这里的一条读法，避免「只认 iteration」的判据出现第二份实现。 */
export function latestIterationFromLedgerTail(
  lines: string[],
): { iter: number; time: string } | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line || !line.includes('"iteration"')) continue
    try {
      const e = JSON.parse(line) as {
        event?: unknown
        iter?: unknown
        it?: unknown
        time?: unknown
      }
      if (e.event !== 'iteration') continue
      const it = typeof e.iter === 'number' ? e.iter : typeof e.it === 'number' ? e.it : null
      if (it !== null) return { iter: it, time: typeof e.time === 'string' ? e.time : '' }
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
