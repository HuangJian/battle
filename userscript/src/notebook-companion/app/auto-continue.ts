/**
 * auto-continue.ts — Kaggle 外层弹窗的自动确认（2026-10-30 用户要求）。
 *
 * 「是否仍需编辑」这类弹窗会卡住正在跑的训练页，必须点掉才能继续。这里只点
 * **白名单文案**：「Continue editing」，以及配额弹窗里同族的「Continue working」。
 * 其余按钮一律不碰——尤其「Turn off TPU」：点错等于把训练环境拆了。
 *
 * 三条防误伤：
 *   ① 每个弹窗元素只点一次（WeakSet）；
 *   ② 同一文案 1.5s 冷却（MUI 重渲染会短时间反复插入同一个 dialog）；
 *   ③ 弹窗内按钮必须**整段文案**命中（不是子串），`span` 包裹也按 textContent 归一。
 */
const CONFIRM_LABELS = ['continue editing', 'continue working']
const CLICK_COOLDOWN_MS = 1500

export interface AutoContinueHooks {
  /** 真的点了（boot 用来留一行日志）。 */
  onConfirm?: (label: string, dialog: Element) => void
  now?: () => number
}

export interface AutoContinue {
  /** 手动扫一次（挂载时先处理已经堆在页面上的弹窗）。 */
  scan(): void
  dispose(): void
}

interface ObserverLike {
  observe(node: Node, init: MutationObserverInit): void
  disconnect(): void
}

function findConfirm(dialog: Element): { el: HTMLElement; label: string } | null {
  for (const el of dialog.querySelectorAll('button, [role="button"]')) {
    const label = (el.textContent ?? '').trim().toLowerCase()
    if (CONFIRM_LABELS.includes(label)) return { el: el as HTMLElement, label }
  }
  return null
}

/** 安装自动确认。返回的 `dispose` 会断开 observer 并让后续 scan 变成 no-op。 */
export function installAutoContinue(doc: Document, hooks: AutoContinueHooks = {}): AutoContinue {
  const now = hooks.now ?? (() => Date.now())
  const seen = new WeakSet<Element>()
  const lastClickAt = new Map<string, number>()
  let disposed = false
  let queued = false
  let observer: ObserverLike | null = null

  const scan = (): void => {
    if (disposed) return
    for (const dialog of doc.querySelectorAll('[role="dialog"]')) {
      if (seen.has(dialog)) continue
      const hit = findConfirm(dialog)
      if (!hit) continue
      const stamp = now()
      const prev = lastClickAt.get(hit.label)
      // 只有「真的点过」才进冷却窗（首次点击不能因为 now 恰好很小而被挡掉）
      if (prev !== undefined && stamp - prev < CLICK_COOLDOWN_MS) continue
      lastClickAt.set(hit.label, stamp)
      seen.add(dialog)
      hit.el.click()
      hooks.onConfirm?.(hit.label, dialog)
    }
  }

  /** 每次 DOM 变更都扫太贵（Kaggle 的 React 很吵）：合到一拍里。 */
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
