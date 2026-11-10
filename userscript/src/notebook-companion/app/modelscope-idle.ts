/**
 * modelscope-idle.ts — 魔搭「静默提醒」弹窗的自动点掉（2026-11-10 用户要求）。
 *
 * 魔搭在 notebook 30 分钟无操作时弹 antd Modal.confirm：「您已超过30分钟未在 notebook
 * 内执行操作，静默超过1小时会自动关闭实例，请及时处理重要文件」。不点它就一直压着页面
 * （而且实例 1 小时后照样被关）。本模块只认**这**一个弹窗：
 * 标题「静默提醒」+ 正文同时含「超过30分钟」「自动关闭实例」；其余弹窗一律不碰。
 *
 * 为什么点之前**随机等几十秒**：立刻点掉等于替站点宣告「有人在操作」——保留 20–60s 的
 * 自然延迟更像人（用户口径「随机等待几十秒」）；这个动作只重置静默计时，不改变任何
 * 训练 / 文件状态（真实操作仍由人来做）。
 *
 * 防误伤：
 *   ① 每个弹窗只排一次（WeakSet：排过程 / 点过的都不再排）；
 *   ② 排程后弹窗若已消失（人自己关了 / 站点换了）⇒ 不点；
 *   ③ 只点整段文案 = 「确定」的按钮（去掉空白后比较，「确 定」也认）；
 *   ④ `dispose()` 之后任何挂起的定时器都不再点。
 */

const IDLE_TITLE = '静默提醒'
/** 正文判据：只出现「静默提醒」不够（同标题可能被别的场景复用）。 */
const CONTENT_MARKERS = ['超过30分钟', '自动关闭实例'] as const
/** 随机延迟窗口（用户口径「几十秒」）。 */
export const IDLE_DELAY_MIN_MS = 20_000
export const IDLE_DELAY_MAX_MS = 60_000

export interface ModelscopeIdleHooks {
  /** 延迟毫秒数（默认 20–60s 均匀随机）。 */
  delayMs?: () => number
  /** 排程器（默认 `setTimeout`；单测注入可控时钟）。 */
  schedule?: (fn: () => void, ms: number) => void
  /** 真的点了（调用方可留一行日志）。 */
  onDismiss?: (delayMs: number, button: Element) => void
}

export interface ModelscopeIdleDismiss {
  /** 手动扫一次（装好时已经自动扫过一次）。 */
  scan(): void
  dispose(): void
}

interface ObserverLike {
  observe(node: Node, init: MutationObserverInit): void
  disconnect(): void
}

/** 「确 定」在 antd 里带空格；归一化后比较整段文案。 */
const norm = (s: string | null | undefined): string => (s ?? '').replace(/\s+/g, '')

function findConfirmButton(dialog: Element): HTMLElement | null {
  const scope = dialog.querySelector('.antd5-modal-confirm-btns') ?? dialog
  for (const el of scope.querySelectorAll('button')) {
    if (norm(el.textContent) === '确定') return el as HTMLElement
  }
  return null
}

/** 页面上的候选弹窗根节点（标题命中「静默提醒」的那些）。 */
function idleDialogRoots(doc: Document): Element[] {
  const roots: Element[] = []
  for (const title of doc.querySelectorAll(
    '.antd5-modal-confirm-title, [class*="modal-confirm-title"]',
  )) {
    if (norm(title.textContent) !== IDLE_TITLE) continue
    const root = title.closest('.antd5-modal-confirm, .antd5-modal-content, [role="dialog"]')
    if (root && !roots.includes(root)) roots.push(root)
  }
  return roots
}

function isIdleDialog(root: Element): boolean {
  const text = norm(root.textContent)
  return CONTENT_MARKERS.every((m) => text.includes(norm(m)))
}

/** 安装自动确认。返回的 `dispose` 会断开 observer，并让挂起的排程失效。 */
export function installModelscopeIdleDismiss(
  doc: Document,
  hooks: ModelscopeIdleHooks = {},
): ModelscopeIdleDismiss {
  const delayMs =
    hooks.delayMs ??
    ((): number =>
      IDLE_DELAY_MIN_MS + Math.floor(Math.random() * (IDLE_DELAY_MAX_MS - IDLE_DELAY_MIN_MS + 1)))
  const schedule =
    hooks.schedule ??
    ((fn: () => void, ms: number): void => {
      const view = doc.defaultView as unknown as {
        setTimeout?: (f: () => void, n: number) => unknown
      }
      if (view?.setTimeout) view.setTimeout(fn, ms)
      else setTimeout(fn, ms)
    })
  const seen = new WeakSet<Element>()
  let disposed = false
  let queued = false
  let observer: ObserverLike | null = null

  const scan = (): void => {
    if (disposed) return
    for (const root of idleDialogRoots(doc)) {
      if (seen.has(root) || !isIdleDialog(root)) continue
      const button = findConfirmButton(root)
      if (!button) continue
      seen.add(root)
      const ms = delayMs()
      schedule(() => {
        if (disposed) return
        // 到点时弹窗还在不在：人可能已经自己点了 / 站点撤了；不在就不点。
        if (!root.isConnected || !button.isConnected) return
        button.click()
        hooks.onDismiss?.(ms, button)
      }, ms)
    }
  }

  /** React 的 DOM 变更很吵：合到一拍里扫。 */
  const queue = (): void => {
    if (queued || disposed) return
    queued = true
    const run = (): void => {
      queued = false
      scan()
    }
    const view = doc.defaultView as unknown as {
      queueMicrotask?: (fn: () => void) => void
    } | null
    if (view?.queueMicrotask) view.queueMicrotask(run)
    else setTimeout(run, 0)
  }

  const view = doc.defaultView as unknown as {
    MutationObserver?: new (cb: () => void) => ObserverLike
  } | null
  const Ctor = view?.MutationObserver
  if (Ctor && doc.documentElement) {
    observer = new Ctor(queue)
    observer.observe(doc.documentElement, { childList: true, subtree: true })
  }
  scan()

  return {
    scan,
    dispose() {
      disposed = true
      observer?.disconnect()
      observer = null
    },
  }
}
