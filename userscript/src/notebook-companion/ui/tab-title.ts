/**
 * tab-title.ts — 标签页标题上的状态图标（`⛔` 会话已停 / `▶️` 有 cell 在跑 / `⏰` 有定时任务）。
 *
 * 为什么看标题：训练页大多挂在后台标签里。限额关机、hub 叫停这种事发生时，人正在看别的
 * 页面，只有**标题**一直可见。名字的来源因站点而异（Kaggle 会把标题写死成通用文案），
 * 所以本模块只管状态前缀，名字解析由调用方注入；页面自己改名也照常跟随。
 */

/** 会话已停（到达限额 / hub 叫停 / 空闲停 / 手动停）。 */
export const TAB_MARK_STOPPED = '⛔'
/** 有 code cell 正在执行（含排队）。 */
export const TAB_MARK_RUNNING = '▶️'
/** 本页有启用的定时任务（关掉 / 跑完立刻撤）。 */
export const TAB_MARK_SCHEDULED = '⏰'

export interface TabTitleStatus {
  stopped: boolean
  running: boolean
  scheduled: boolean
}

export type SessionState = 'running' | 'idle' | 'stopped' | 'unknown'

/**
 * 只剥离开头的状态图标（页面自己写同样的图标也不会丢正文）。
 * `⏹` 是旧版的停止图标：升级后页面标题里可能还留着，一并剥掉。
 */
export function stripTabMarks(title: string): string {
  return title.replace(/^(?:(?:⛔\uFE0F?|⏹\uFE0F?|▶\uFE0F?|⏰\uFE0F?)\s*)+/u, '')
}

/**
 * 状态 → 前缀。`stopped` 优先：已停的会话不该还显示「在跑」。
 * 定时任务与执行态正交，排在后面（`⛔ ⏰ 名字` / `▶️ ⏰ 名字`）。
 */
export function tabMark(status: TabTitleStatus): string {
  const marks: string[] = []
  if (status.stopped) marks.push(TAB_MARK_STOPPED)
  else if (status.running) marks.push(TAB_MARK_RUNNING)
  if (status.scheduled) marks.push(TAB_MARK_SCHEDULED)
  return marks.join(' ')
}

/** 基础名 + 状态 → 标题。基础名为空（非 notebook 页）⇒ 空串，调用方不动标题。 */
export function composeTabTitle(name: string, status: TabTitleStatus): string {
  const base = stripTabMarks(name)
  if (!base) return ''
  const mark = tabMark(status)
  return mark ? `${mark} ${base}` : base
}

export interface TabTitleSyncOptions {
  /**
   * 页面标题 + URL → 基础名（不含状态图标）。给了它就只在 URL 变化时解析一次：
   * Kaggle 会覆写标题，缓存能保住 notebook 名的大小写，也免得名字在「页面名 / slug」间跳。
   * 不给就每拍跟随页面标题（Colab 等页面自己会改名）。
   */
  resolve?: (title: string, href: string) => string
}

export interface TabTitleSync {
  /** 刷新一次标题，返回基础名（空串 = 本页不归我们管）。 */
  (): string
  /** 记下状态（下一次 tick 上屏）。 */
  setStatus(status: Partial<TabTitleStatus>): void
}

export function createTabTitleSync(
  doc: { title: string },
  getHref: () => string,
  options: TabTitleSyncOptions = {},
): TabTitleSync {
  let key: string | null = null
  let name = ''
  let status: TabTitleStatus = { stopped: false, running: false, scheduled: false }

  const tick = ((): string => {
    const href = getHref()
    if (options.resolve) {
      if (href !== key) {
        key = href
        name = options.resolve(doc.title, href)
      }
    } else {
      name = stripTabMarks(doc.title)
    }
    if (!name) return ''
    const next = composeTabTitle(name, status)
    if (doc.title !== next) doc.title = next
    return name
  }) as TabTitleSync

  tick.setStatus = (next) => {
    status = { ...status, ...next }
  }
  return tick
}

export interface TabTitleReporter {
  /** 明确知道会话停了（hub / 空闲 / 站点适配器确认）：立刻上 ⛔。 */
  markStopped(): void
  /** 最近一拍有没有 code cell 在执行。 */
  setRunning(running: boolean): void
  /** 本页有没有启用的定时任务（面板任务表变化时同步）。 */
  setScheduled(scheduled: boolean): void
  /** 会话状态读数：`stopped` 上图标；`running | idle` 撤掉（重新连上/开新会话）。 */
  observeSession(state: SessionState): void
  /** 套用当前状态并写标题。 */
  refresh(): void
  readonly stopped: boolean
  readonly running: boolean
  readonly scheduled: boolean
}

/**
 * 状态机 + 上屏。状态只住在这里（不散在 boot 的两个角色里）：
 *   · 显式停会话成功 ⇒ `stopped`，直到读到 `running/idle`（用户重连、开新会话）；
 *   · cell 执行态 / 定时任务每拍覆盖（▶️ / ⏰ 只是提示，不锁存）。
 */
export function createTabTitleReporter(sync: TabTitleSync): TabTitleReporter {
  let stopped = false
  let running = false
  let scheduled = false

  const apply = (): void => {
    sync.setStatus({ stopped, running, scheduled })
    sync()
  }

  return {
    markStopped() {
      if (stopped) return
      stopped = true
      apply()
    },
    setRunning(next) {
      if (next === running) return
      running = next
      apply()
    },
    setScheduled(next) {
      if (next === scheduled) return
      scheduled = next
      apply()
    },
    observeSession(state) {
      if (state === 'stopped') {
        if (!stopped) {
          stopped = true
          apply()
        }
        return
      }
      if ((state === 'running' || state === 'idle') && stopped) {
        stopped = false
        apply()
      }
    },
    refresh: apply,
    get stopped() {
      return stopped
    },
    get running() {
      return running
    },
    get scheduled() {
      return scheduled
    },
  }
}
