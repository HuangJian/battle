/** course-admin.ts — 课程管理页（`/courses`）的行构建：**全部课程**（在训 / 已停 / 仅课程文件）
 *  + 封存区，每行带「这一门接下来该做什么」的判据。
 *
 *  ## 为什么不复用课程矩阵那张表（`course-matrix.ts`）
 *
 *  矩阵回答的是「**盯着正在跑的那几门**」——它按 `isTrainingRow` 把未在训的行**筛掉**
 *  （未列的课只剩一个计数 chip）。而管理页要的恰恰是**被筛掉的那些**：历史课占着盘（28 GB
 *  里的 27.3 GB 是课程目录）、已停课要不要封存、课程文件里声明了却从没跑过的课在哪。
 *  两边的问题不同，故行集不同；但**状态词表一个字都不另写**——`matrixStatus` / `waitingCell`
 *  / `pauseOp` / `kindBadge` 全部复用，一个状态只有一种说法（§4.2）。
 *
 *  ## 行的三种活体形态 + 一种档案形态（口径 = **开课标记**）
 *
 *  · `enabled`  —— 开课标记在（`training-enabled.txt`，训练侧与 hub 的**同一个闸**）；
 *  · `stopped`  —— 活体工作区还在但没有开课标记（停过课 / 从来没开过）；
 *  · `declared` —— 只有 `curricula/<课>.jsonc`，`tmp/` 下没有工作区（还没跑过）；
 *  · `archived` —— 封存档案（`archive/courses/<课>/`）——**不进活体表**，单独一段。
 *
 *  ## 排序（管理页的读序）
 *
 *  已开课在前（按 it 新→旧）→ 已停 / 未落盘（按最后写入新→旧：最久没动的排最后，那正是
 *  该封存的那批）→ 封存段单独排（按封存时刻新→旧）。
 */

import type {
  ArchivedCourseView,
  ConsoleStateView,
  CourseFactView,
  LoopComplete,
} from './console-types'
import type { CourseOverviewRow, ParallelOverviewView } from './course-overview'
import { fmtRel } from './format'
import type { LoopQueueRow, LoopQueueView } from './loop-queue'
import {
  type CourseMatrixRow,
  type MatrixCell,
  type MatrixStatus,
  matrixStatus,
  mergeCourseRows,
  offlineWaitCell,
  waitingCell,
} from './course-matrix'

/** 课程在管理页里的形态（见文件头注）。
 *
 *  ⚠ 口径只有一个：**开课标记**（`training-enabled.txt`，训练侧与 hub 的同一个闸）。
 *  它是「这一门被放进课程表了吗」，**不是**「进程在跑吗」——后者的说法彽在**状态列**上
 *  （矩阵词表的「在训 / 未在训 / 离线（只收回传）」），两列回答两个问题。 */
export type CourseAdminKind = 'enabled' | 'stopped' | 'declared' | 'archived'

export interface CourseAdminRow {
  course: string
  kind: CourseAdminKind
  /** 当前查看的那门课（高亮）。 */
  viewing: boolean
  /** 开课标记（训练侧/hub 同一个闸）。`enabled` 行恒为 true。 */
  enabled: boolean
  /** 活体工作区 `tmp/<课>/` 在不在。 */
  tmp: boolean
  /** 课程文件在 `curricula/`。 */
  declared: boolean
  /** 活体目录最后写入（ms；无活体 = null）——「它多久没被写过了」。 */
  lastWriteMs: number | null
  /** 状态列（矩阵同一套词表）。 */
  status: MatrixStatus
  /** 「在等什么」列（离线课给云机回传口径，与矩阵同源）。 */
  waiting: MatrixCell
  /** 账本/队列指针（null = 还没有任何一轮）。 */
  iter: number | null
  iterSource: CourseMatrixRow['iterSource']
  /** hub 侧两侧事实（供操作列判断哪些开关成立；缺 = 那一侧没有这门课）。 */
  ov: CourseOverviewRow | null
  lq: LoopQueueRow | null
  /** 「切离线/在线」能不能给（hub 在线 ∧ hub 认识这门课；矩阵同一判据，不另写一份）。 */
  canToggleMode: boolean
  /** BC/RL 种类徽标（`lq.kind`；缺 = 未知，不标）。 */
  kindBadge?: 'rl' | 'bc'
  /** 归档事实（仅 `archived` 行）。 */
  archive?: ArchivedCourseView
}

export interface CourseAdminInput {
  /** 全部课名（`stateView.courses`；已排除封存课）。 */
  courses: string[]
  /** 已开课课程（`stateView.trainingCourses`）。 */
  training: string[]
  /** 逐课盘上事实（`stateView.courseFacts`；缺省 = 旧视图 ⇒ 退回 courses/training 推断）。 */
  facts?: CourseFactView[] | null
  overview: ParallelOverviewView | null
  queue: LoopQueueView | null
  modeIntents?: Record<string, 'online' | 'offline'> | null
  courseRolloutSrc?: Record<string, string> | null
  /** ★2026-10-06：逐课停车态（`stateView.loopCompletes`）——已收官的课在本页也不得报「在训」（与 pill / 课程矩阵同一派生）。 */
  loopCompletes?: Record<string, LoopComplete> | null
  archived?: ArchivedCourseView[] | null
  viewing: string
  /** 当下时刻（秒）——相对时间与「段内」陈旧判据都拿它，纯函数不读墙钟。 */
  nowSec: number
}

export interface CourseAdminView {
  /** 活体课程（已开课 / 已停 / 未落盘）。 */
  rows: CourseAdminRow[]
  /** 封存档案（新→旧）。 */
  archived: CourseAdminRow[]
  counts: { enabled: number; stopped: number; declared: number; archived: number }
}

/** 行排序（见文件头注）：已开课（it 新→旧）→ 已停 / 未落盘（最后写入新→旧）→ 同刻按课名。 */
function rowOrder(a: CourseAdminRow, b: CourseAdminRow): number {
  const rank = (k: CourseAdminKind): number => (k === 'enabled' ? 0 : k === 'stopped' ? 1 : 2)
  const d = rank(a.kind) - rank(b.kind)
  if (d !== 0) return d
  if (a.kind === 'enabled') {
    const ai = a.iter ?? -1
    const bi = b.iter ?? -1
    if (ai !== bi) return bi - ai
  } else {
    const aw = a.lastWriteMs ?? -1
    const bw = b.lastWriteMs ?? -1
    if (aw !== bw) return bw - aw
  }
  return a.course < b.course ? -1 : a.course > b.course ? 1 : 0
}

/** 一门课 → 管理页行。`mr` = 矩阵合并结果（hub/队列两侧事实；缺 = 两侧都没有它）。 */
function activeRow(
  course: string,
  mr: CourseMatrixRow | null,
  fact: CourseFactView | null,
  training: boolean,
  viewing: string,
  hubOnline: boolean,
): CourseAdminRow {
  // 开课标记：盘上事实优先（`courseFacts`），旧视图退化到 `trainingCourses`（同源、逐课展开）。
  const enabled = fact?.enabled ?? training
  // 缺事实（旧服务端 / 夹具）时 `tmp` 按**有**算：「未落盘」是一个要拿旧视图看不见的
  // 事实（`tmp/<课>/` 在不在）才能做出的断言——不编它（宁可保守地说「已停」）。
  const tmp = fact?.tmp ?? true
  const declared = fact?.declared ?? false
  const kind: CourseAdminKind = enabled ? 'enabled' : tmp ? 'stopped' : 'declared'
  const ov = mr?.ov ?? null
  return {
    course,
    kind,
    viewing: course === viewing,
    enabled,
    tmp,
    declared,
    lastWriteMs: fact?.lastWriteMs ?? null,
    // 两侧都没有这门课时也走同一套词表（`matrixStatus(null, null, …)` = 「未在训」）——
    // 不为「没被任何一侧登记」另写一个词，那样同一个事实会有两种说法。
    status: mr ? mr.status : matrixStatus(null, null, hubOnline),
    waiting: ov?.offline ? offlineWaitCell(ov) : mr ? mr.waiting : waitingCell(null),
    iter: mr?.iter ?? null,
    iterSource: mr?.iterSource ?? null,
    ov,
    lq: mr?.lq ?? null,
    canToggleMode: mr?.canToggleMode ?? false,
    kindBadge: mr?.lq?.kind ?? (mr?.kind === 'bc' ? 'bc' : undefined),
  }
}

/** 封存档案 → 行（只读 manifest 的字段，不碰盘）。 */
function archivedRow(a: ArchivedCourseView, viewing: string): CourseAdminRow {
  return {
    course: a.course,
    kind: 'archived',
    viewing: a.course === viewing,
    enabled: false,
    tmp: false,
    declared: true,
    lastWriteMs: null,
    status: {
      text: '已封存',
      tone: 'off',
      title:
        '档案在 archive/courses/<课>/（只读 manifest 渲染）——已从活体发现里排除：' +
        '不进课程选择器、不进开课弹窗、hub 与训练侧都不再认它',
    },
    waiting: { text: '—', title: '封存课不进任何队列' },
    iter: a.finalIt,
    iterSource: null,
    ov: null,
    lq: null,
    canToggleMode: false,
    archive: a,
  }
}

/** 构建课程管理页的全部行（纯函数、零 IO ⇒ 可单测）。 */
export function buildCourseAdmin(input: CourseAdminInput): CourseAdminView {
  const archived = input.archived ?? []
  const archivedNames = new Set(archived.map((a) => a.course))
  const hubOnline = input.overview !== null
  // 矩阵合并（hub × 训练队列的 outer join）：状态/等待/指针三列的**唯一**来源。
  const matrix = mergeCourseRows({
    overview: input.overview,
    queue: input.queue,
    modeIntents: input.modeIntents,
    courseRolloutSrc: input.courseRolloutSrc,
    loopCompletes: input.loopCompletes,
    viewing: input.viewing,
    nowSec: input.nowSec,
  })
  const byCourse = new Map<string, CourseMatrixRow>()
  for (const r of matrix) byCourse.set(r.course, r)
  const facts = new Map<string, CourseFactView>()
  for (const f of input.facts ?? []) facts.set(f.course, f)

  // 课名并集：courses（tmp 有账本 + curricula 回填）∪ facts ∪ 两侧任一侧登记过的课。
  // 已封存的课**不进活体表**（它们单独一段）——否则同一门课在两段里各出一次。
  const names: string[] = []
  const seen = new Set<string>()
  const put = (c: string): void => {
    if (!c || seen.has(c) || archivedNames.has(c)) return
    seen.add(c)
    names.push(c)
  }
  for (const c of input.training) put(c)
  for (const c of input.courses) put(c)
  for (const r of matrix) put(r.course)
  for (const f of input.facts ?? []) put(f.course)

  const rows = names.map((c) =>
    activeRow(
      c,
      byCourse.get(c) ?? null,
      facts.get(c) ?? null,
      input.training.includes(c),
      input.viewing,
      hubOnline,
    ),
  )
  rows.sort(rowOrder)

  const arch = archived.slice().sort((a, b) => (a.archivedAt < b.archivedAt ? 1 : -1))
  const archRows = arch.map((a) => archivedRow(a, input.viewing))
  return {
    rows,
    archived: archRows,
    counts: {
      enabled: rows.filter((r) => r.kind === 'enabled').length,
      stopped: rows.filter((r) => r.kind === 'stopped').length,
      declared: rows.filter((r) => r.kind === 'declared').length,
      archived: archRows.length,
    },
  }
}

/** 从整页视图直接构建（面板一行调用；缺字段全部走退化路径，不编事实）。
 *
 *  `viewing` 缺省取 `s.course`，但面板传**本地**那个（`viewCourse`）：服务端那格是**上一次
 *  轮询**时 stamp 的，切课后最多滞后一个刷新间隔，而高亮要跟着手走。 */
export function courseAdminFromState(
  s: ConsoleStateView,
  nowSec: number,
  viewing: string = s.course,
): CourseAdminView {
  return buildCourseAdmin({
    courses: s.courses ?? [],
    training: s.trainingCourses ?? [],
    facts: s.courseFacts ?? null,
    overview: s.overview ?? null,
    queue: s.loopQueue ?? null,
    modeIntents: s.courseModeIntents ?? null,
    courseRolloutSrc: s.courseRolloutSrc ?? null,
    loopCompletes: s.loopCompletes ?? null,
    archived: s.archived ?? null,
    viewing,
    nowSec,
  })
}

/** 「最后写入」单元格：无活体 = `—`（不是「刚刚」）。 */
export function lastWriteCell(row: CourseAdminRow, nowSec: number): MatrixCell {
  if (row.lastWriteMs == null)
    return {
      text: '—',
      title: row.declared
        ? '没有活体工作区（tmp/<课>/ 不存在）——只有课程文件；这一门还没跑过，或已经封存'
        : '没有活体工作区',
    }
  return {
    text: fmtRel(row.lastWriteMs, nowSec * 1000),
    title: `活体目录最后被写：${new Date(row.lastWriteMs).toLocaleString()}（tmp/<课>/ 的 mtime）`,
  }
}

/** 能不能封存 + 为什么不能（**展示层判据**；真正的闸在 python 侧，控制台不自己算第二份）。
 *
 *  这里只做一件事：把「点了会发生什么」说清楚。在训课照常给按钮——服务端会响亮拒绝
 *  （409 + 原因），这与「只读不禁用」同一条理：真假由服务端定，UI 不替它编。 */
export function archiveOp(row: CourseAdminRow): { label: string; title: string } | null {
  // 已封存的（档案本身）与**没有活体工作区**的（只有 curricula/<课>.jsonc——盘上没有任何东西
  // 可搬）都不给这个键：按钮点下去只会得一句「没什么可封存」，那是假承诺。
  if (row.kind === 'archived' || !row.tmp) return null
  if (row.enabled)
    return {
      label: '封存',
      title:
        `${row.course} 现在还开着课（training-enabled.txt 在）——封存会被拒（在训课不得封存）。` +
        '先「停课」（删开课标记），等这一轮写完再封存：封存要的是**已经静止**的目录',
    }
  return {
    label: '封存',
    title:
      `把 ${row.course} 从活体工作区（tmp/）搬成只读档案（archive/courses/${row.course}/）：` +
      '保留账本/判决行/关键轮权重（文本件 gzip），删掉 shards 与派发中间物，然后移走空壳。' +
      // ★ 「先停课 → 再封存」正好落进新鲜度闸：停课只删开课标记、**不杀**在飞的那一轮，
      //   所以刚停完的目录是「无 marker 但新鲜」⇒ 默认拒（plan §1.3-1 / §3.3-①）。
      //   这一条必须写在「已停」那一行上——它才是正规流程的下一步，别让人把它当故障。
      '★ 停课不杀在飞的那一轮：刚停完的目录仍在被写（新鲜）⇒ 封存会被拒；等它静止下来' +
      '（「最后写入」不再变）再封。--force 只越陈旧残留，越不过新鲜目录。' +
      '先跑预演（--dry-run）看清单与字节账，确认后才真删',
  }
}

/** 表的过滤档（默认「有活体」：几十门历史课的档案条目会把在跑的那几行挤出首屏，
 *  而「有哪些课」这个问句的答案本来就分层——`全部` 那档随时可切）。 */
export type CourseAdminFilter = 'live' | 'enabled' | 'stopped' | 'declared' | 'all'

export function filterCourseAdminRows(
  rows: CourseAdminRow[],
  filter: CourseAdminFilter,
): CourseAdminRow[] {
  if (filter === 'all') return rows
  // 「有活体」= tmp/ 还在（能不能封存就看它）；其余三档按形态（开课标记口径）。
  if (filter === 'live') return rows.filter((r) => r.tmp)
  return rows.filter((r) => r.kind === filter)
}

/** 过滤档的标签（带计数；面板与测试共用同一份，不两处各写一遍）。 */
export function courseAdminFilterOptions(
  view: CourseAdminView,
): Array<{ value: CourseAdminFilter; label: string }> {
  const live = view.rows.filter((r) => r.tmp).length
  return [
    { value: 'live', label: `有活体 ${live}` },
    { value: 'enabled', label: `已开课 ${view.counts.enabled}` },
    { value: 'stopped', label: `已停 ${view.counts.stopped}` },
    { value: 'declared', label: `未落盘 ${view.counts.declared}` },
    { value: 'all', label: `全部 ${view.rows.length}` },
  ]
}

/** 表头一句话（页面计数行用它，别在面板里另写一份）。 */
export function courseAdminSummary(v: CourseAdminView): string {
  const { enabled, stopped, declared, archived } = v.counts
  return `已开课 ${enabled} · 已停 ${stopped} · 未落盘 ${declared} · 已封存 ${archived}`
}
