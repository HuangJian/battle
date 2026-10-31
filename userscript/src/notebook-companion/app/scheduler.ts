/**
 * scheduler.ts — host 侧定时任务（plan §3.3 / W4 接线）：**一次性「下一次」** + claim + 执行编排。
 *
 * 口径（2026-10-30 用户收窄）：定时器只设「下一次」，不设重复日期。任务 = `{nextAt, action}`；
 * 到点执行一次即消耗（成功 / 失败 / 取消 / 过期都从任务表移除，occurrence 留 7 天备查）。
 *
 * 三条纪律：
 *   ① 计时只靠**墙钟轮询**（不依赖内存计时器链）：睡眠 / 刷新 / 后台节流只会延后一拍，
 *      「到没到」永远由 `now` 与 `nextAt` 现算；
 *   ② 同一个 occurrence 只执行一次：claim 先落 GM 存储，等 `claimSettleMs` 再读回，
 *      不是自己就退让；
 *   ③ claim 一旦写下就不再重试（页面崩在动作里也算「结果未知」，不自动补跑）。
 *
 * 本模块不碰 DOM / bridge：倒计时确认与真正执行由 boot 注入（面板与桥都在那边）。
 */
import {
  defaultParts,
  nextFireAt,
  normalizeAt,
  occurrenceKey,
  type DailyJob,
  type PartsFn,
} from '../schedule'

export type ScheduleAction = 'run-cell' | 'run-all' | 'stop-session' | 'interrupt'

/** 创建时捕获的 cell 目标（执行时验指纹，绝不跑错代码）。 */
export interface ScheduleTarget {
  uuid?: string
  index?: number
  fingerprint?: string
}

export interface SchedulerTask {
  id: string
  /** 下一次（也是唯一一次）触发的墙钟时刻（epoch ms；本地时区由 `parts` 决定）。 */
  nextAt: number
  action: ScheduleAction
  /** 运行类绑 notebook（`<owner>/<slug>`，跨 run 稳定）。 */
  notebookKey: string
  /** 停止/中断绑创建时的会话名（含页面标签；换 run 即失效，fail closed）。 */
  sessionAtCreate: string
  target?: ScheduleTarget
  enabled: boolean
  createdAt: number
}

export type OccurrenceState = 'claimed' | 'succeeded' | 'failed' | 'skipped'

export interface Occurrence {
  state: OccurrenceState
  /** 写下它的页面身份（fencing）。 */
  by: string
  at: number
  reason?: string
}

/** 面板一行：`text` 是任务描述，`next` 是「下一次」的人话。 */
export interface SchedulerRow {
  id: string
  text: string
  next: string
  enabled: boolean
}

export const TASKS_KEY = 'bnc:tasks'
export const OCCURRENCES_KEY = 'bnc:occurrences'

const OCCURRENCE_KEEP_MS = 7 * 24 * 3_600_000
const ACTIONS: readonly ScheduleAction[] = ['run-cell', 'run-all', 'stop-session', 'interrupt']

/** GM 存储的最小面（测试注入假实现；`Runtime` 直接满足）。 */
export interface SchedulerStore {
  getValue<T>(key: string, fallback: T): T
  setValue(key: string, value: unknown): void
}

export interface SchedulerDeps {
  store: SchedulerStore
  /** 本页身份（claim 的 `by`）。 */
  writer: string
  now: () => number
  parts?: PartsFn
  claimSettleMs: number
  /** `run-*` 的迟到窗口（plan §3.3：10 分钟）。 */
  runGraceMs: number
  /** `stop-session` / `interrupt` 的迟到窗口（config.stopGraceMs）。 */
  stopGraceMs: number
  /** 当前页面身份（创建时写进任务，执行时用来 fail closed）。 */
  identity: () => { notebookKey: string; session: string }
  /** 到点后的 10s 倒数/取消；返回 false = 用户取消（隐藏页由 boot 直接放行）。 */
  confirm: (task: SchedulerTask, occurrence: string) => Promise<boolean>
  /** 真正执行（bridge / 外层 Stop 按钮都归 boot）。 */
  run: (task: SchedulerTask) => Promise<{ ok: boolean; reason?: string }>
  onChange?: () => void
  pollMs?: number
  wait?: (ms: number) => Promise<void>
  makeId?: () => string
}

export interface Scheduler {
  tasks(): SchedulerTask[]
  /** `at` 是 `HH:MM`：取**下一个**该钟点（已过则明天），只跑一次。 */
  add(input: {
    at: string
    action: ScheduleAction
    target?: ScheduleTarget
  }): { ok: true; task: SchedulerTask } | { ok: false; reason: string }
  remove(id: string): void
  setEnabled(id: string, enabled: boolean): void
  /** 最早的可启用任务（面板「还剩 N 分钟」倒计时用它）。 */
  nextDue(now?: number): { id: string; action: ScheduleAction; nextAt: number } | null
  rows(): SchedulerRow[]
  tick(): Promise<void>
  start(): void
  stop(): void
}

export function actionLabel(action: ScheduleAction): string {
  switch (action) {
    case 'run-cell':
      return '运行 cell'
    case 'run-all':
      return '运行全部'
    case 'stop-session':
      return '停会话'
    case 'interrupt':
      return '中断内核'
  }
}

/**
 * 执行历史一行的文案：停会话成功时把「到底停掉了什么」写清楚 —— Colab 的关运行时会连同
 * `/content` 里的运行时文件一起删，事后必须能从历史里认出来（用户要求）。
 */
export function historyText(
  action: ScheduleAction,
  result: { ok: boolean; detail?: string },
): string {
  const base = actionLabel(action)
  if (action !== 'stop-session' || !result.ok) return base
  const detail = result.detail ?? ''
  if (/运行时文件/.test(detail)) return '停会话 · 关运行时（运行时文件已删除）'
  if (/内核/.test(detail)) return '停会话 · 关闭内核'
  return base
}

/**
 * 任务是否属于这一页：运行类绑 notebook；停止/中断绑创建时的会话（与 tick 的 binding 同口径）。
 * 面板的 cell 徽标/顶部倒计时用它过滤，别把另一页签的任务画到本页。
 */
export function taskBelongsTo(
  task: Pick<SchedulerTask, 'action' | 'notebookKey' | 'sessionAtCreate'>,
  ident: { notebookKey: string; session: string },
): boolean {
  if (task.action === 'run-cell' || task.action === 'run-all') {
    return task.notebookKey !== '' && task.notebookKey === ident.notebookKey
  }
  return task.sessionAtCreate !== '' && task.sessionAtCreate === ident.session
}

/** cell 徽标倒计时起点：还剩 10 分钟开始逐秒显示（更早就只给静态文案）。 */
export const COUNTDOWN_WINDOW_MS = 10 * 60_000

/**
 * cell 徽标上的倒计时文案：> 10 分钟用静态 `fallback`（如「今天 09:05」），进入窗口变成
 * 「还有 M:SS」，到点以后「即将运行」。纯函数：`now` 由调用方给，测试不碰墙钟。
 */
export function countdownLabel(nextAt: number, now: number, fallback: string): string {
  const left = nextAt - now
  if (left <= 0) return '即将运行'
  if (left > COUNTDOWN_WINDOW_MS) return fallback
  const sec = Math.ceil(left / 1000)
  return `还有 ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

/** 任务表变化后的状态栏文案：删空后必须改口，不能留着「已添加定时：…」。 */
export function scheduleStatusText(count: number): string {
  return count === 0 ? '没有定时任务' : `定时任务 ${count} 个`
}

function relativeNext(ms: number, now: number, parts: PartsFn): string {
  const p = parts(ms)
  const today = parts(now)
  const label = `${p.date === today.date ? '今天' : ''}`
  if (label) return `今天 ${p.time}`
  const dayMs = 86_400_000
  const diff = Math.round(
    (Date.parse(`${p.date}T00:00:00Z`) - Date.parse(`${today.date}T00:00:00Z`)) / dayMs,
  )
  if (diff === 1) return `明天 ${p.time}`
  return `${p.date.slice(5)} ${p.time}`
}

function timeOf(ms: number, parts: PartsFn): string {
  return parts(ms).time
}

function sanitizeTarget(o: Record<string, unknown>): ScheduleTarget {
  const t: ScheduleTarget = {}
  if (typeof o.uuid === 'string' && o.uuid) t.uuid = o.uuid
  if (typeof o.index === 'number' && Number.isInteger(o.index)) t.index = o.index
  if (typeof o.fingerprint === 'string' && o.fingerprint) t.fingerprint = o.fingerprint
  return t
}

function sanitizeTask(raw: unknown): SchedulerTask | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  const id = typeof o.id === 'string' ? o.id : ''
  const nextAt = typeof o.nextAt === 'number' && Number.isFinite(o.nextAt) ? o.nextAt : null
  const action = ACTIONS.includes(o.action as ScheduleAction) ? (o.action as ScheduleAction) : null
  if (!id || nextAt === null || !action) return null
  const rawTarget =
    typeof o.target === 'object' && o.target !== null ? (o.target as Record<string, unknown>) : null
  return {
    id,
    nextAt,
    action,
    notebookKey: typeof o.notebookKey === 'string' ? o.notebookKey : '',
    sessionAtCreate: typeof o.sessionAtCreate === 'string' ? o.sessionAtCreate : '',
    ...(rawTarget ? { target: sanitizeTarget(rawTarget) } : {}),
    enabled: o.enabled !== false,
    createdAt: typeof o.createdAt === 'number' && Number.isFinite(o.createdAt) ? o.createdAt : 0,
  }
}

function sanitizeOccurrence(raw: unknown): Occurrence | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  const state = o.state
  if (state !== 'claimed' && state !== 'succeeded' && state !== 'failed' && state !== 'skipped') {
    return null
  }
  if (typeof o.by !== 'string' || typeof o.at !== 'number' || !Number.isFinite(o.at)) return null
  return {
    state,
    by: o.by,
    at: o.at,
    ...(typeof o.reason === 'string' && o.reason ? { reason: o.reason } : {}),
  }
}

function defaultWait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 创建一个调度器。**不自动 start**：boot/测试决定何时轮询。 */
export function createScheduler(deps: SchedulerDeps): Scheduler {
  const parts = deps.parts ?? defaultParts
  const wait = deps.wait ?? defaultWait
  const makeId = deps.makeId ?? (() => `t-${Math.random().toString(36).slice(2, 8)}`)
  let timer: ReturnType<typeof setInterval> | null = null
  let ticking = false

  const readTasks = (): SchedulerTask[] => {
    const raw = deps.store.getValue<unknown>(TASKS_KEY, [])
    if (!Array.isArray(raw)) return []
    const out: SchedulerTask[] = []
    for (const item of raw) {
      const t = sanitizeTask(item)
      if (t) out.push(t)
    }
    return out
  }

  const writeTasks = (tasks: SchedulerTask[]): void => {
    deps.store.setValue(TASKS_KEY, tasks)
    deps.onChange?.()
  }

  const readOccurrences = (): Record<string, Occurrence> => {
    const raw = deps.store.getValue<unknown>(OCCURRENCES_KEY, {})
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}
    const out: Record<string, Occurrence> = {}
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const occ = sanitizeOccurrence(v)
      if (occ) out[k] = occ
    }
    return out
  }

  /** 写 occurrence：先重新读一次（别的页签可能刚写过），顺手清理 7 天前的记录。 */
  const record = (id: string, occ: Occurrence): void => {
    const now = deps.now()
    const all = readOccurrences()
    for (const [k, v] of Object.entries(all)) {
      if (now - v.at > OCCURRENCE_KEEP_MS) delete all[k]
    }
    all[id] = occ
    deps.store.setValue(OCCURRENCES_KEY, all)
  }

  const dropTask = (id: string): void => {
    writeTasks(readTasks().filter((t) => t.id !== id))
  }

  /** 运行类只看 notebook；停止/中断看会话（换 run/换页签 ⇒ 不执行）。 */
  const bindingError = (task: SchedulerTask): string | null => {
    const ident = deps.identity()
    if (task.action === 'run-cell' || task.action === 'run-all') {
      if (!task.notebookKey || task.notebookKey !== ident.notebookKey) {
        return `页面不是创建任务的 notebook（${task.notebookKey || '∅'}）`
      }
      return null
    }
    if (!task.sessionAtCreate || task.sessionAtCreate !== ident.session) {
      return `会话已换（创建时 ${task.sessionAtCreate || '∅'}，现在 ${ident.session || '∅'}）`
    }
    return null
  }

  const tickOnce = async (): Promise<void> => {
    const now = deps.now()
    for (const task of readTasks()) {
      if (!task.enabled || now < task.nextAt) continue
      const occId = `${task.id}|${occurrenceKey(parts(task.nextAt))}`
      if (readOccurrences()[occId]) {
        dropTask(task.id) // 防御：已处理过的任务不该再留着
        continue
      }
      const grace =
        task.action === 'stop-session' || task.action === 'interrupt'
          ? deps.stopGraceMs
          : deps.runGraceMs
      const bad = bindingError(task)
      const expired = now - task.nextAt > grace
      if (bad || expired) {
        record(occId, {
          state: 'skipped',
          by: deps.writer,
          at: now,
          reason: bad ?? '已过期（超出迟到窗口，不补跑）',
        })
        dropTask(task.id)
        continue
      }
      // ① claim（落盘后别的页签看得见）→ ② 沉淀窗 → ③ 读回转正。
      record(occId, { state: 'claimed', by: deps.writer, at: now })
      await wait(Math.max(0, deps.claimSettleMs))
      const settled = readOccurrences()[occId]
      if (!settled || settled.by !== deps.writer || settled.state !== 'claimed') {
        dropTask(task.id)
        continue
      }
      let proceed = false
      try {
        proceed = await deps.confirm(task, occId)
      } catch {
        proceed = false
      }
      if (!proceed) {
        record(occId, {
          state: 'skipped',
          by: deps.writer,
          at: deps.now(),
          reason: '用户取消',
        })
        dropTask(task.id)
        continue
      }
      let result: { ok: boolean; reason?: string }
      try {
        result = await deps.run(task)
      } catch (e) {
        result = { ok: false, reason: e instanceof Error ? e.message : String(e) }
      }
      record(occId, {
        state: result.ok ? 'succeeded' : 'failed',
        by: deps.writer,
        at: deps.now(),
        ...(result.reason ? { reason: result.reason } : {}),
      })
      // 一次性：执行完即消耗（成功/失败都不留待办；occurrence 留 7 天备查）
      dropTask(task.id)
    }
  }

  const tick = async (): Promise<void> => {
    if (ticking) return
    ticking = true
    try {
      await tickOnce()
    } finally {
      ticking = false
    }
  }

  return {
    tasks: readTasks,
    add(input) {
      const at = normalizeAt(input.at)
      if (!at) return { ok: false, reason: `时间非法：${input.at}（HH:MM）` }
      if (!ACTIONS.includes(input.action)) return { ok: false, reason: `动作非法：${input.action}` }
      const ident = deps.identity()
      if (!ident.notebookKey)
        return { ok: false, reason: '这不是 notebook 页面（拿不到 owner/slug）' }
      if (!ident.session) return { ok: false, reason: '笔记本还没连上（等面板显示「已连接」）' }
      if (
        input.action === 'run-cell' &&
        (!input.target || (input.target.uuid === undefined && input.target.index === undefined))
      ) {
        return { ok: false, reason: '运行 cell 需要先在笔记本里选中一段代码' }
      }
      const job: DailyJob = { id: '', at, days: null, catchUp: false, enabled: true }
      const nextAt = nextFireAt(job, deps.now(), parts)
      if (nextAt === null) return { ok: false, reason: `算不出下一次：${at}` }
      const task: SchedulerTask = {
        id: makeId(),
        nextAt,
        action: input.action,
        notebookKey: ident.notebookKey,
        sessionAtCreate: ident.session,
        ...(input.action === 'run-cell' && input.target ? { target: input.target } : {}),
        enabled: true,
        createdAt: deps.now(),
      }
      writeTasks([...readTasks(), task])
      return { ok: true, task }
    },
    remove(id) {
      writeTasks(readTasks().filter((t) => t.id !== id))
      const all = readOccurrences()
      let removed = false
      for (const k of Object.keys(all)) {
        if (!k.startsWith(`${id}|`)) continue
        delete all[k]
        removed = true
      }
      if (removed) deps.store.setValue(OCCURRENCES_KEY, all)
    },
    setEnabled(id, enabled) {
      writeTasks(readTasks().map((t) => (t.id === id ? { ...t, enabled } : t)))
    },
    nextDue() {
      let best: SchedulerTask | null = null
      for (const task of readTasks()) {
        if (!task.enabled) continue
        if (!best || task.nextAt < best.nextAt) best = task
      }
      return best ? { id: best.id, action: best.action, nextAt: best.nextAt } : null
    },
    rows() {
      const now = deps.now()
      return readTasks().map((t) => ({
        id: t.id,
        text: `${timeOf(t.nextAt, parts)} · ${actionLabel(t.action)}`,
        next: relativeNext(t.nextAt, now, parts),
        enabled: t.enabled,
      }))
    },
    tick,
    start() {
      if (timer !== null) return
      timer = setInterval(
        () => {
          void tick()
        },
        Math.max(5_000, deps.pollMs ?? 20_000),
      )
      void tick()
    },
    stop() {
      if (timer !== null) clearInterval(timer)
      timer = null
    },
  }
}
