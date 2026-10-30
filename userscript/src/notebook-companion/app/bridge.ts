/**
 * bridge.ts — host ⇄ frame 的 per-attach nonce 桥（plan §4.2）。
 *
 * 为什么不用共享存储/`MessageChannel`：多标签会互相覆盖；nonce 只经**定向** postMessage
 * 抵达该 frame，其他标签拿不到它的 WindowProxy。重复 attach 是常态（iframe 出现 + load），
 * host 只认**当前** nonce，frame 对每次 attach 都回 ready。
 */
export type BridgeKind = 'attach' | 'ready' | 'status' | 'cmd' | 'result'

export interface BridgeMessage {
  __bnc: 1
  kind: BridgeKind
  nonce?: string
  id?: string
  name?: string
  args?: unknown
  ok?: boolean
  value?: unknown
  reason?: string
  href?: string
  notebook?: boolean
}

export interface BridgeEvent {
  data: unknown
  origin: string
  source: unknown
  /** 当前窗口自身（用来判定 `e.source === parent`）。 */
  self?: unknown
  parent?: unknown
}

export interface BridgeWindow {
  addEventListener(type: 'message', fn: (e: BridgeEvent) => void): void
  /** `window.parent`（frame 侧回发目标；host 侧不用）。 */
  readonly parent?: unknown
  /** 向 `target` 定向发送：host 传 `iframe.contentWindow`，frame 传 `parent`。 */
  postMessage(target: unknown, msg: unknown, origin: string): void
}

export function isBridgeMessage(data: unknown): data is BridgeMessage {
  if (typeof data !== 'object' || data === null) return false
  const o = data as Record<string, unknown>
  return o.__bnc === 1 && typeof o.kind === 'string'
}

/** host origin 白名单（attach/ready 两个方向都用同一份）。 */
export const KAGGLE_HOST_ORIGINS = ['https://www.kaggle.com', 'https://kaggle.com']

export function randomNonce(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  if (c?.randomUUID) return c.randomUUID().slice(0, 8)
  return Math.random().toString(36).slice(2, 10)
}

export interface HostBridgeHooks {
  timeoutMs?: number
  onReady?: (msg: BridgeMessage) => void
  onStatus?: (msg: BridgeMessage) => void
  onError?: (reason: string) => void
}

export interface HostBridge {
  readonly nonce: string
  /** `target` 必须是 iframe 的 `contentWindow`（`window.postMessage` 只发给自己，不发子帧）。 */
  attach(origin: string, target: unknown, nonce?: string): void
  send(name: string, args?: unknown): Promise<{ ok: boolean; value?: unknown; reason?: string }>
  destroy(): void
}

/** host 侧：attach → ready/status → cmd/result。`self` 只用于日志，不做匹配。 */
export function createHostBridge(win: BridgeWindow, hooks: HostBridgeHooks = {}): HostBridge {
  let nonce = ''
  let frameTarget: unknown = null
  const pending = new Map<
    string,
    { resolve: (r: { ok: boolean; value?: unknown; reason?: string }) => void; timer: number }
  >()
  const timeoutMs = hooks.timeoutMs ?? 5000

  const listener = (e: BridgeEvent): void => {
    if (!isBridgeMessage(e.data)) return
    const m = e.data
    if (m.kind === 'ready' || m.kind === 'status' || m.kind === 'result') {
      if (!m.nonce || m.nonce !== nonce) {
        hooks.onError?.(`nonce 不符（拿到 ${m.nonce ?? '∅'}）`)
        return
      }
    }
    if (m.kind === 'ready') {
      hooks.onReady?.(m)
      return
    }
    if (m.kind === 'status') {
      hooks.onStatus?.(m)
      return
    }
    if (m.kind !== 'result' || !m.id) return
    const p = pending.get(m.id)
    if (!p) return
    clearTimeout(p.timer)
    pending.delete(m.id)
    p.resolve({
      ok: m.ok === true,
      ...(m.value === undefined ? {} : { value: m.value }),
      ...(m.reason ? { reason: m.reason } : {}),
    })
  }
  win.addEventListener('message', listener)

  return {
    get nonce() {
      return nonce
    },
    attach(origin: string, target: unknown, next = randomNonce()) {
      frameTarget = target
      nonce = next
      win.postMessage(target, { __bnc: 1, kind: 'attach', nonce } satisfies BridgeMessage, origin)
    },
    send(name: string, args?: unknown) {
      if (frameTarget === null) {
        return Promise.resolve({ ok: false, reason: `cmd ${name} 尚未 attach（没有 frame 目标）` })
      }
      const target = frameTarget
      const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
      return new Promise((resolve) => {
        const timer = setTimeout(() => {
          pending.delete(id)
          resolve({ ok: false, reason: `cmd ${name} 超时（${timeoutMs}ms）` })
        }, timeoutMs) as unknown as number
        pending.set(id, { resolve, timer })
        win.postMessage(
          target,
          { __bnc: 1, kind: 'cmd', nonce, id, name, args } satisfies BridgeMessage,
          '*',
        )
      })
    },
    destroy() {
      for (const [, p] of pending) clearTimeout(p.timer)
      pending.clear()
    },
  }
}

export interface FrameBridgeHooks {
  /** 允许的 host origin（attach 只认这些）。 */
  hostOrigins?: string[]
  handle: (
    name: string,
    args: unknown,
  ) => Promise<{ ok: boolean; value?: unknown; reason?: string }>
  /** ready 里的 `notebook`：宿主据此判断「frame 里真的出现笔记本了」。 */
  isReady?: () => boolean
  /** ready 后等笔记本渲染的轮询间隔（默认 500ms；测试可调小）。 */
  statusPollMs?: number
  onAttach?: (msg: BridgeMessage) => void
  onError?: (reason: string) => void
}

export interface FrameBridge {
  readonly nonce: string
  destroy(): void
}

/** frame 侧：只认 parent + 白名单 origin 的 attach；每个 cmd 带当前 nonce。 */
export function createFrameBridge(win: BridgeWindow, hooks: FrameBridgeHooks): FrameBridge {
  const origins = hooks.hostOrigins ?? KAGGLE_HOST_ORIGINS
  let nonce = ''
  let hostOrigin = ''
  let statusTimer: ReturnType<typeof setInterval> | null = null

  const stopStatus = (): void => {
    if (statusTimer !== null) clearInterval(statusTimer)
    statusTimer = null
  }

  /** attach 时 notebook 还没渲染 ⇒ 等它出现后补一条 status（探针同款时序）。 */
  const watchReady = (): void => {
    stopStatus()
    if (hooks.isReady?.() ?? false) return
    const started = Date.now()
    statusTimer = setInterval(() => {
      if (hooks.isReady?.() ?? false) {
        win.postMessage(
          win.parent,
          { __bnc: 1, kind: 'status', nonce, notebook: true, href: safeHref() },
          hostOrigin,
        )
        stopStatus()
      } else if (Date.now() - started > 30_000) {
        stopStatus()
      }
    }, hooks.statusPollMs ?? 500)
  }

  const listener = (e: BridgeEvent): void => {
    if (!isBridgeMessage(e.data)) return
    const m = e.data
    if (e.self !== undefined && e.self === e.parent) {
      hooks.onError?.('frame 顶层打开（不在 iframe 里）')
      return
    }
    if (e.source !== undefined && e.parent !== undefined && e.source !== e.parent) {
      hooks.onError?.('拒绝非 parent 的消息')
      return
    }
    if (!origins.includes(e.origin)) {
      hooks.onError?.(`拒绝 origin=${e.origin}`)
      return
    }
    if (m.kind === 'attach') {
      if (!m.nonce) return
      nonce = m.nonce
      hostOrigin = e.origin
      hooks.onAttach?.(m)
      win.postMessage(
        win.parent,
        { __bnc: 1, kind: 'ready', nonce, href: safeHref(), notebook: hooks.isReady?.() ?? false },
        hostOrigin,
      )
      watchReady()
      return
    }
    if (m.kind !== 'cmd' || !m.id) return
    if (!m.nonce || m.nonce !== nonce) {
      hooks.onError?.('拒绝非当前 nonce 的 cmd')
      return
    }
    void hooks
      .handle(m.name ?? '', m.args)
      .then((r) =>
        win.postMessage(
          win.parent,
          { __bnc: 1, kind: 'result', nonce, id: m.id, ...r },
          hostOrigin,
        ),
      )
      .catch((err: unknown) =>
        win.postMessage(
          win.parent,
          {
            __bnc: 1,
            kind: 'result',
            nonce,
            id: m.id,
            ok: false,
            reason: err instanceof Error ? err.message : String(err),
          },
          hostOrigin,
        ),
      )
  }
  win.addEventListener('message', listener)
  return {
    get nonce() {
      return nonce
    },
    destroy() {
      stopStatus()
      /* 无 removeEventListener 的最小接口；页面卸载即回收 */
    },
  }
}

/** 日志用 URL：只到 origin + pathname（`?session=` JWT 绝不外泄）。 */
function safeHref(): string {
  try {
    return `${location.origin}${location.pathname}`
  } catch {
    return ''
  }
}
