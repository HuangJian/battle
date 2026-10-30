/**
 * auto-continue.ts — Kaggle 外层弹窗的自动点掉（2026-10-30 用户要求）。
 *
 * 两类弹窗会卡住正在跑的训练页：
 *   ① 「是否仍需编辑」/ 配额：点白名单里的「Continue editing」/「Continue working」；
 *   ② 「notebook 在别处被改过、保存失败」警告：**只在这种弹窗里**点「Cancel」
 *      （Download 不碰；「Cancel」这个文案别处也有，不能进通用白名单）。
 * 其余按钮一律不碰——尤其「Turn off TPU」：点错等于把训练环境拆了。
 *
 * 三条防误伤：
 *   ① 每个弹窗元素只点一次（WeakSet）；
 *   ② 同一文案 1.5s 冷却（MUI 重渲染会短时间反复插入同一个 dialog）；
 *   ③ 弹窗内按钮必须**整段文案**命中（不是子串），`span` 包裹也按 textContent 归一。
 */
const CONFIRM_LABELS = ['continue editing', 'continue working']
/** 保存冲突警告的正文片段：两个都命中才认（只出现一个太常见）。 */
const CONFLICT_MARKERS = ['unable to save your notebook', 'modified in another location']
const CANCEL_LABELS = ['cancel']
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

const normalized = (el: Element): string => (el.textContent ?? '').trim().toLowerCase()

function buttonByLabel(
  dialog: Element,
  labels: string[],
): { el: HTMLElement; label: string } | null {
  for (const el of dialog.querySelectorAll('button, [role="button"]')) {
    const label = normalized(el)
    if (labels.includes(label)) return { el: el as HTMLElement, label }
  }
  return null
}

/** 保存冲突警告：只点 Cancel；别的弹窗即使有 Cancel 也不碰。 */
function findClickTarget(dialog: Element): { el: HTMLElement; label: string } | null {
  const text = normalized(dialog)
  if (CONFLICT_MARKERS.every((m) => text.includes(m))) return buttonByLabel(dialog, CANCEL_LABELS)
  return buttonByLabel(dialog, CONFIRM_LABELS)
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
      const hit = findClickTarget(dialog)
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
