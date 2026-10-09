/** contribution.ts — 并行 worker 贡献度：视图类型 + 纯函数（plan/worker-contribution-view）。
 *
 *  两个**不相交**的贡献组（用户 2026-10-02 裁决 G9 角色隔离）：
 *    · 采样（单位：局）——身份 = `dist-agent-meta` 的 node / local；
 *    · PPO（单位：job）——身份 = hub 承接结果那一刻的 worker（push 腿带 `push:` 前缀）。
 *
 *  硬口径：
 *    · 份额分母**组内自洽**（同组、同窗口、全体身份），两组**永不合并**；
 *    · 同名不合并（归属由来源决定，不由名字决定）；本模块的两个组各自成表，
 *      不存在跨组归并的代码路径；
 *    · `share === null` = 分母 0 / 缺数据 ⇒ 渲染「—」而不是 0%。
 *
 *  本模块只有纯函数与类型（无 IO、无 `Date.now()` 直调）——服务端与浏览器共用，
 *  可单测、SSR 安全。
 */

import {
  type HubJobRefView,
  type HubQueueView,
  type OfflineRunView,
  offlineSummary,
} from './course-overview'

/** 一组采样计数（局）。 */
export interface ContributionSplit {
  rollout: number
  eval: number
  fail: number
}

export interface SamplingContributionRow {
  id: string
  split: ContributionSplit
  /** 成功局份额（`(rollout+eval)/组内成功局总和`）；分母 0 ⇒ null（渲染「—」）。 */
  share: number | null
  /** 窗口内最近一条事件的 ts（展示用；空串 = 无）。 */
  lastTs: string
}

export interface PpoContributionRow {
  worker: string
  /** 完成 job（训练侧 `job_completed` ∧ 承接归属已定格）。 */
  done: number
  /** 晚到·白算（409 被拒：早退 / 首写锁定失败；含 push 腿输家）。 */
  rejected: number
  /** 当前活租约持有数（hub `/admin/queue` 观测面）。 */
  inflight: number
  /** 完成 job 份额；组内 0 ⇒ null。 */
  share: number | null
}

export interface SamplingContribution {
  rows: SamplingContributionRow[]
  total: ContributionSplit
}

export interface PpoContribution {
  rows: PpoContributionRow[]
  totalDone: number
  totalRejected: number
}

/** 矩阵格：采样行只用 `split`，PPO 行只用 `done/rejected`（两个单位不换算）。
 *  名字带 `Contribution` 前缀：`view/` 里已有一个课程矩阵的 `MatrixCell`（course-matrix.ts）。 */
export interface ContributionCell {
  split: ContributionSplit
  done: number
  rejected: number
}

export interface ContributionMatrix {
  /** 列 = 窗口内有贡献的课（两组并集，按课名排序）。 */
  courses: string[]
  /** 行 = 采样节点（单位：局）。 */
  samplingRows: Array<{ id: string; cells: ContributionCell[]; total: number }>
  /** 行 = PPO worker（单位：job）。 */
  ppoRows: Array<{ worker: string; cells: ContributionCell[]; total: number }>
  /** 采样侧单点依赖课（该课只有一个采样身份有贡献）。 */
  soloSamplingCourses: string[]
  /** PPO 侧单点依赖课（该课只有一个 PPO 身份有贡献）。 */
  soloPpoCourses: string[]
}

export interface ContributionView {
  sampling: SamplingContribution
  ppo: PpoContribution
  matrix: ContributionMatrix
  /** 采样脚注：流数 + 截断标记（诚实标注不省略）。 */
  samplingSources: { flows: number; truncated: boolean; rollingTruncated: boolean }
  /** PPO 脚注：扫到的课程账本数。 */
  ppoSources: { ledgers: number }
}

export interface ContributionBrief {
  sampling: { total: number; top: Array<{ id: string; share: number | null; total: number }> }
  ppo: { totalDone: number; top: Array<{ worker: string; share: number | null; done: number }> }
}

function emptySplit(): ContributionSplit {
  return { rollout: 0, eval: 0, fail: 0 }
}

/** 全部局（成功 + 失败）——排序/总量用。 */
function splitTotal(s: ContributionSplit): number {
  return s.rollout + s.eval + s.fail
}

/** 成功局（rollout + eval）——采样份额的分子/分母口径。 */
function successTotal(s: ContributionSplit): number {
  return s.rollout + s.eval
}

/** 采样组：份额 = 组内成功局占比；缺数据「—」；按总量降序。 */
export function buildSamplingContribution(
  inputs: Array<{ id: string; split: ContributionSplit; lastTs?: string }>,
): SamplingContribution {
  const total = emptySplit()
  for (const i of inputs) {
    total.rollout += i.split.rollout
    total.eval += i.split.eval
    total.fail += i.split.fail
  }
  const denom = successTotal(total)
  const rows = inputs.map((i) => ({
    id: i.id,
    split: { ...i.split },
    share: denom > 0 ? successTotal(i.split) / denom : null,
    lastTs: i.lastTs ?? '',
  }))
  rows.sort((a, b) => splitTotal(b.split) - splitTotal(a.split) || (a.id < b.id ? -1 : 1))
  return { rows, total }
}

/** PPO 身份 → **机器名**：剥掉 `:pid` 后缀（无冒号则原样返回）。
 *
 *  为什么（2026-10-03 实景）：旧身份是 `hostname:pid`（`remote/job_lifecycle.py::worker_tag`），
 *  而**每个 job 都可能是一个新 pid** ⇒ 一天攒出 **332 个「身份」各 1 job**，面板被碎片淹没
 *  （截图：`113ffa2bffc6:24134 / 24931 / 25341 …` 一路排下去）。同一台机器反复重启**就是同一台机器**，
 *  「谁在干活」的单位天然是机器。
 *
 *  ⚠ 这不是「跨组归并」：`machineOf` 只在 **PPO 组内部**使用，采样身份（`node.id`，人起的名）
 *  一个字节都不碰 ⇒ WC-plan §4.1b 的角色隔离仍然成立（两组仍是两个不相交人群）。
 *  ⚠ 与 `plan/worker-name-readable.plan.md` 同向：新命名（`kaggle-c`）**不含冒号** ⇒ 本函数对它恒等，
 *  届时无需改一行（归并自动退化为恒等）。 */
export function machineOf(worker: string): string {
  const i = worker.indexOf(':')
  return i > 0 ? worker.slice(0, i) : worker
}

/** 首页 live 行里的一条 job 引用（= `HubJobRefView` 的同形别名，见 `course-overview`）。 */
export type PpoJobRef = HubJobRefView

/** 一台 worker 的「此刻在干什么」（首页 PPO 区每台一行；plan/dashboard-ppo-live-rows）。
 *
 *  三段全部**据实**：`computing` = hub 的活租约∧已开算（`computing_ago` 非空）；`dl` = hub 的
 *  「认领未开算」∪ worker 上报的下载中（按 jobId 去重）；`held` = worker 上报的软持有已下好。
 *  某段为空 ⇒ 读面整段不渲染（**不**渲染空标题）——空与不可知都不该在屏幕上占位。 */
export interface PpoWorkerLiveView {
  /** 机器名（`machineOf` 归并后，与汇总行同一身份口径）。 */
  worker: string
  /** 份额（组内完成 job 占比；无贡献/新机器 ⇒ null ⇒ 屏上「—」）。 */
  share: number | null
  /** 自主盘（持 live hold）：行上带「自主」徽标。 */
  autonomous: boolean
  /** 预取上报龄（秒）；没上报过 ⇒ null（自主盘常态如此）。 */
  ageSec: number | null
  computing: PpoJobRef[]
  held: PpoJobRef[]
  dl: PpoJobRef[]
}

/** 行内去重键：有 job_id 用 job_id，否则用「课:轮」（自主盘的引用没有 jid）。 */
function refKey(r: PpoJobRef): string {
  return r.jobId || `${r.course}:${r.it ?? '?'}`
}

/** 首页 live 行组装（纯函数）：hub 的租约事实 ⊕ worker 上报的预取事实 ⊕ 自主盘的接管。
 *
 *  输入是**同一拍**的三份观测（`/admin/queue` 一次拿全 + 已在手的 contribution 聚合行），
 *  所以首页不新增任何 HTTP 往返。三条纪律：
 *
 *  1. **分档看 `computingAgo`，不把「已认领」叫「计算中」**：认领之后还要取包/下载 payload，
 *     那段在 hub 的事实里是 `computingAgo === null`（没 `POST /start`）——它归「下载中」，
 *     否则同一份等待会在屏上被叫成两件事。
 *  2. **只留有活动的**：三段全空的机器不出行（首页不是花名册）；`share` 与它无关——
 *     新机器没有历史份额也得出行（否则「刚开工的那台」恰恰看不见）。
 *  3. **缺数据不编**：course/it 解析不出就留空/`null`（读面渲染 `?:it?`），不分派、不推断。 */
export function buildWorkerLive(
  queue: HubQueueView | null,
  ppoRows: Array<{ worker: string; share: number | null }>,
  offlineProgress: Record<string, Record<string, OfflineRunView>> | null = null,
): PpoWorkerLiveView[] {
  if (!queue) return []
  type Seg = {
    computing: PpoJobRef[]
    held: PpoJobRef[]
    dl: PpoJobRef[]
    autonomous: boolean
    ageSec: number | null
  }
  const acc = new Map<string, Seg>()
  const row = (worker: string): Seg => {
    let s = acc.get(worker)
    if (!s) {
      s = { computing: [], held: [], dl: [], autonomous: false, ageSec: null }
      acc.set(worker, s)
    }
    return s
  }

  // ① hub 侧租约：`computing_ago` 有值 = 真开算了；没有 = 还在取包/下载（同归「下载中」）。
  for (const [course, c] of Object.entries(queue.courses ?? {})) {
    for (const d of c.inflightDetail ?? []) {
      const s = row(machineOf(d.worker || '(未登记)'))
      const ref: PpoJobRef = { jobId: d.jobId, course: d.course || course, it: d.it }
      ;(d.computingAgo == null ? s.dl : s.computing).push(ref)
    }
  }

  // ② worker 上报的软持有：`held` = 下好了（hub 完全看不到的那部分）。
  for (const [worker, pf] of Object.entries(queue.workerPrefetch ?? {})) {
    const s = row(machineOf(worker))
    s.held.push(...pf.held)
    s.dl.push(...pf.dl) // 与 ① 的「认领未开算」按 jobId 去重（下方 dedupe）
    if (s.ageSec == null) s.ageSec = pf.age
  }

  // ③ 自主盘：持 live hold 的盘领整段任务包，在 PPO 归属账本里根本不出现——单独出行。
  //    `it` 是**派生读数**（`/admin/offline.progress` 里已补传产物的最大轮），落后 ≤1 轮。
  for (const [course, c] of Object.entries(queue.courses ?? {})) {
    const hold = c.hold
    if (!hold || hold.state !== 'live' || !hold.workerId) continue
    const s = row(machineOf(hold.workerId))
    s.autonomous = true
    s.computing.push({
      jobId: '',
      course,
      it: offlineSummary(offlineProgress?.[course]).lastIter,
    })
  }

  const shares = new Map(ppoRows.map((p) => [machineOf(p.worker), p.share]))
  const dedupe = (refs: PpoJobRef[]): PpoJobRef[] => {
    const seen = new Set<string>()
    return refs.filter((r) => {
      const k = refKey(r)
      if (seen.has(k)) return false
      seen.add(k)
      return true
    })
  }
  const out: PpoWorkerLiveView[] = []
  for (const [worker, s] of acc) {
    const live: PpoWorkerLiveView = {
      worker,
      share: shares.get(worker) ?? null,
      autonomous: s.autonomous,
      ageSec: s.ageSec,
      computing: dedupe(s.computing),
      held: dedupe(s.held),
      dl: dedupe(s.dl),
    }
    if (!live.computing.length && !live.held.length && !live.dl.length) continue
    out.push(live)
  }
  // 份额降序（缺份额最后），同份额按名字——**不与汇总行承诺同序**：那一行的键是
  // `done + rejected`（有「晚到·白算」的机器时两者本就不同）。
  out.sort((a, b) => (b.share ?? -1) - (a.share ?? -1) || (a.worker < b.worker ? -1 : 1))
  return out
}

/** PPO 组：份额 = 组内完成 job 占比；「实际投入 = 完成 + 晚到」由两列并看。
 *
 *  **先按机器归并，再算份额/排序**（见 `machineOf`）：输入是身份级（账本里落的就是身份），
 *  输出是**机器级**——`totalDone` / `totalRejected` 是组内总量，归并不改它们（同一批数相加）。 */
export function buildPpoContribution(
  inputs: Array<{ worker: string; done: number; rejected: number; inflight?: number }>,
): PpoContribution {
  const byMachine = new Map<string, { done: number; rejected: number; inflight: number }>()
  for (const i of inputs) {
    const m = machineOf(i.worker)
    const v = byMachine.get(m) ?? { done: 0, rejected: 0, inflight: 0 }
    v.done += i.done
    v.rejected += i.rejected
    v.inflight += i.inflight ?? 0
    byMachine.set(m, v)
  }
  let totalDone = 0
  let totalRejected = 0
  for (const v of byMachine.values()) {
    totalDone += v.done
    totalRejected += v.rejected
  }
  const rows = [...byMachine.entries()].map(([worker, v]) => ({
    worker,
    done: v.done,
    rejected: v.rejected,
    inflight: v.inflight,
    share: totalDone > 0 ? v.done / totalDone : null,
  }))
  rows.sort((a, b) => b.done + b.rejected - (a.done + a.rejected) || (a.worker < b.worker ? -1 : 1))
  return { rows, totalDone, totalRejected }
}

/** 课程矩阵（机器 × 课）：两组各自成行；单点依赖课分别标记。 */
export function buildContributionMatrix(
  samplingByCourse: Record<string, Record<string, ContributionSplit>>,
  ppoByCourse: Record<string, Record<string, { done: number; rejected: number }>>,
): ContributionMatrix {
  const courses = new Set<string>()
  for (const byCourse of Object.values(samplingByCourse))
    for (const c of Object.keys(byCourse)) courses.add(c)
  for (const byCourse of Object.values(ppoByCourse))
    for (const c of Object.keys(byCourse)) courses.add(c)
  const courseList = [...courses].sort()
  const idx = new Map(courseList.map((c, i) => [c, i]))

  const zeroCells = (): ContributionCell[] =>
    courseList.map(() => ({ split: emptySplit(), done: 0, rejected: 0 }))

  const samplingRows = Object.entries(samplingByCourse)
    .map(([id, byCourse]) => {
      const cells = zeroCells()
      let total = 0
      for (const [c, s] of Object.entries(byCourse)) {
        const i = idx.get(c)
        if (i == null) continue
        cells[i] = { split: { ...s }, done: 0, rejected: 0 }
        total += splitTotal(s)
      }
      return { id, cells, total }
    })
    .sort((a, b) => b.total - a.total || (a.id < b.id ? -1 : 1))

  const ppoRows = Object.entries(ppoByCourse)
    .map(([worker, byCourse]) => {
      const cells = zeroCells()
      let total = 0
      for (const [c, v] of Object.entries(byCourse)) {
        const i = idx.get(c)
        if (i == null) continue
        cells[i] = { split: emptySplit(), done: v.done, rejected: v.rejected }
        total += v.done + v.rejected
      }
      return { worker, cells, total }
    })
    .sort((a, b) => b.total - a.total || (a.worker < b.worker ? -1 : 1))

  const samplingActive = (c: string): number => {
    let n = 0
    for (const byCourse of Object.values(samplingByCourse)) {
      const s = byCourse[c]
      if (s && splitTotal(s) > 0) n++
    }
    return n
  }
  const ppoActive = (c: string): number => {
    let n = 0
    for (const byCourse of Object.values(ppoByCourse)) {
      const v = byCourse[c]
      if (v && v.done + v.rejected > 0) n++
    }
    return n
  }

  return {
    courses: courseList,
    samplingRows,
    ppoRows,
    soloSamplingCourses: courseList.filter((c) => samplingActive(c) === 1),
    soloPpoCourses: courseList.filter((c) => ppoActive(c) === 1),
  }
}

/** 「不截断」的 top-N 取值（`Array.prototype.slice(0, Infinity)` === 取全部）。
 *
 *  为什么需要它（2026-10-03 用户口径）：首页要**列出全部活跃 PPO worker**（云机数就那几台，
 *  看「哪台在干活」比看「前三名」有用），而采样侧仍只要 top-3（节点行已逐个列出身份，
 *  份额那行只是收尾合计）⇒ 两侧的 N 必须**分开给**。 */
export const BRIEF_ALL = Number.POSITIVE_INFINITY

/** 首页缩略投影：**同一份聚合的裁剪**（不是第二份计算）——top-N + 组总量。
 *
 *  两侧 N 分开（`samplingTop` / `ppoTop`）：两组是**两个不相交的人群、两种单位**（WC-plan §4.1b），
 *  首页对它们的展现需求也不同（采样 3 条够用；PPO 要全列）⇒ 不该被同一个 N 绑在一起。
 *  `ppoTop` 缺省跟随 `samplingTop`（保持旧调用点的行为逐字不变）。 */
export function compactSummary(
  view: ContributionView,
  samplingTop = 3,
  ppoTop: number = samplingTop,
): ContributionBrief {
  return {
    sampling: {
      total: splitTotal(view.sampling.total),
      top: view.sampling.rows.slice(0, samplingTop).map((r) => ({
        id: r.id,
        share: r.share,
        total: splitTotal(r.split),
      })),
    },
    ppo: {
      totalDone: view.ppo.totalDone,
      top: view.ppo.rows.slice(0, ppoTop).map((r) => ({
        worker: r.worker,
        share: r.share,
        done: r.done,
      })),
    },
  }
}

/** 份额格式化：缺数据「—」；极小非零「<0.1%」；其余一位小数。 */
export function fmtShare(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const pct = v * 100
  if (pct > 0 && pct < 0.1) return '<0.1%'
  return `${pct.toFixed(1)}%`
}

/** 计数千分位（不依赖 locale，SSR 与浏览器一致）。 */
export function fmtCount(n: number): string {
  if (!Number.isFinite(n)) return '—'
  return String(Math.trunc(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}
