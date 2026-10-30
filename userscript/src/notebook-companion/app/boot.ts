import { createRuntime, type Runtime } from '../../runtime'
import { loadConfig, type BncConfig } from '../config'
import { detectRole, type Role } from '../selectors'
import { KaggleFrameAdapter, KaggleSessionAdapter, parseKaggleSession } from '../sites/kaggle'
import { mountPanel, type Panel } from '../ui/panel'
import { createFrameBridge, createHostBridge, type BridgeWindow } from './bridge'
import { runFrameCommand } from './commands'
import { createShipLoop } from './ship-loop'

export interface BootEnv {
  doc?: Document
  referrer?: string
}

export interface BootResult {
  role: Role
  hasRuntime: boolean
  dispose?: () => void
}

/**
 * 唯一入口（index.user.ts 调）。只做角色分流；真正的启动在 DOM 有 body 后
 * （`@run-at document-start` 时 body 还不存在，面板/观察者都要等）。
 */
export function boot(
  scope: Record<string, unknown>,
  href: string,
  env: BootEnv = {},
): BootResult | null {
  const role = detectRole(href)
  if (role === null) return null
  const runtime = createRuntime(scope)
  const doc = env.doc ?? (scope.document as Document | undefined)
  if (!runtime || !doc) return { role, hasRuntime: runtime !== null }
  const config = loadConfig(runtime)
  const log = makeLog(scope)
  const win = (scope.window ?? (doc.defaultView as Window | null)) as Window | null
  if (!win) return { role, hasRuntime: true }

  const start = (): (() => void) =>
    role === 'frame'
      ? startFrame(doc, win, href, config, runtime, log)
      : startHost(doc, win, href, config, runtime, log)

  if (doc.body === null) {
    doc.addEventListener('DOMContentLoaded', () => start(), { once: true })
    return { role, hasRuntime: true }
  }
  return { role, hasRuntime: true, dispose: start() }
}

function makeLog(scope: Record<string, unknown>): (msg: string) => void {
  const info = (scope.console as { info?: (msg: string) => void } | undefined)?.info
  return (msg) => info?.(`[bnc] ${msg}`)
}

/** host 侧：监听自己的 window，但**定向发给 iframe.contentWindow**。 */
function hostBridgeWindow(win: Window): BridgeWindow {
  return {
    addEventListener: (_type, fn) =>
      win.addEventListener('message', (e: MessageEvent) => {
        fn({ data: e.data, origin: e.origin, source: e.source, self: win, parent: win.parent })
      }),
    postMessage: (target, msg, origin) => (target as Window | null)?.postMessage(msg, origin),
  }
}

/** frame 侧：监听自己的 window，所有回发都走 `window.parent`。 */
function frameBridgeWindow(win: Window): BridgeWindow {
  return {
    parent: win.parent,
    addEventListener: (_type, fn) =>
      win.addEventListener('message', (e: MessageEvent) => {
        fn({ data: e.data, origin: e.origin, source: e.source, self: win, parent: win.parent })
      }),
    postMessage: (_target, msg, origin) => (win.parent as Window).postMessage(msg, origin),
  }
}

function startFrame(
  doc: Document,
  win: Window,
  href: string,
  config: BncConfig,
  runtime: Runtime,
  log: (msg: string) => void,
): () => void {
  const adapter = new KaggleFrameAdapter(doc)
  const session = new KaggleSessionAdapter(doc, href)
  const loop = createShipLoop({
    doc,
    href,
    referrer: doc.referrer,
    config,
    runtime,
    adapter,
    onError: (message) => log(`ship: ${message}`),
  })
  loop.start()

  const openLog = (): void => {
    win.open(
      `${config.dashboardBase}/log/${encodeURIComponent(`notebook:${loop.session}`)}`,
      '_blank',
      'noopener',
    )
  }
  const openConfig = (): void => {
    runtime.setClipboard(JSON.stringify(config, null, 2))
    log('配置已复制到剪贴板（改完写回 GM 存储 bnc:config）')
  }

  const bridge = createFrameBridge(frameBridgeWindow(win), {
    handle: (name, args) =>
      runFrameCommand({ loop, adapter, session, openLog, openConfig }, name, args),
    isReady: () => adapter.ready,
    onAttach: (m) => log(`bridge attach（nonce=${m.nonce ?? '∅'}）`),
    onError: (reason) => log(`bridge: ${reason}`),
  })

  // 自动清空的「近 10s 有交互」闸：捕获阶段，不改页面行为。
  const touch = (): void => loop.lastInteractionAt(Date.now())
  for (const ev of ['keydown', 'pointerdown', 'wheel', 'scroll'] as const) {
    win.addEventListener(ev, touch, { capture: true, passive: true })
  }

  runtime.registerMenuCommand('Notebook: 保存并清空', () => {
    void loop.saveAndClear().then((r) => {
      log(r.ok ? `已保存并清空（${r.bytes}B）` : `未清空：${r.reason ?? '保存未完成'}`)
    })
  })
  runtime.registerMenuCommand('Notebook: 打开日志', openLog)
  runtime.registerMenuCommand('Notebook: 复制配置', openConfig)
  log(`frame 启动（notebook=${adapter.ready ? 'yes' : 'waiting'}）`)

  return () => {
    loop.stop()
    bridge.destroy()
  }
}

function startHost(
  doc: Document,
  win: Window,
  href: string,
  config: BncConfig,
  runtime: Runtime,
  log: (msg: string) => void,
): () => void {
  const session = parseKaggleSession(href)
  let ready = false
  let panel: Panel | null = null

  const bridge = createHostBridge(hostBridgeWindow(win), {
    timeoutMs: Math.max(5000, config.claimSettleMs + 4000),
    onReady: (m) => {
      ready = true
      log(`bridge ready（nonce=${m.nonce ?? '∅'} notebook=${m.notebook === true}）`)
      panel?.setStatus(m.notebook ? '已连接（笔记本就绪）' : '已连接（等待笔记本）', 'ok')
    },
    onStatus: (m) => panel?.setStatus(m.ok === false ? 'frame 报错' : '状态更新', 'warn'),
    onError: (reason) => {
      ready = false
      log(`bridge: ${reason}`)
    },
  })

  const run = async (name: string): Promise<void> => {
    const r = await bridge.send(name)
    panel?.setStatus(r.ok ? `${name} ✓` : `${name} ✗ ${r.reason ?? '失败'}`, r.ok ? 'ok' : 'error')
  }

  panel = mountPanel(doc, {
    saveAndClear: () => run('saveAndClear'),
    flush: () => run('flush'),
    collapseAll: () => run('collapseAll'),
    expandAll: () => run('expandAll'),
    collapseCurrent: () => run('collapseCurrent'),
    expandCurrent: () => run('expandCurrent'),
    scrollTop: () => run('scrollTop'),
    scrollBottom: () => run('scrollBottom'),
    openLog: () => {
      win.open(
        `${config.dashboardBase}/log/${encodeURIComponent(`notebook:${session}`)}`,
        '_blank',
        'noopener',
      )
    },
    config: () => {
      runtime.setClipboard(JSON.stringify(config, null, 2))
      panel?.setStatus('配置已复制到剪贴板', 'ok')
    },
    stopSession: () => run('stopSession'),
  })

  // 外层 iframe 可能在 open shadow root 里（探针同款 deepQuery）；只查 light DOM 会漏。
  const findFrame = (): HTMLIFrameElement | null => {
    const sel = 'iframe[src*="jupyter-proxy.kaggle.net"]'
    const direct = doc.querySelector(sel)
    if (direct) return direct as HTMLIFrameElement
    const stack: ParentNode[] = [doc]
    while (stack.length > 0) {
      const root = stack.pop()!
      for (const el of root.querySelectorAll('*')) {
        if (!el.shadowRoot) continue
        const hit = el.shadowRoot.querySelector(sel)
        if (hit) return hit as HTMLIFrameElement
        stack.push(el.shadowRoot)
      }
    }
    return null
  }

  let current: HTMLIFrameElement | null = null
  const attach = (): void => {
    const frame = findFrame()
    if (!frame) return
    let origin = 'https://kkb-production.jupyter-proxy.kaggle.net'
    try {
      origin = new URL(frame.src).origin
    } catch {
      /* 相对/空 src：保留默认 */
    }
    if (frame !== current) {
      current = frame
      ready = false
      frame.addEventListener('load', () => {
        ready = false
        log('iframe load → 重新 attach')
        if (frame.contentWindow) bridge.attach(origin, frame.contentWindow)
      })
    }
    if (!ready && frame.contentWindow) bridge.attach(origin, frame.contentWindow)
  }
  attach()
  // iframe 出现/更换是常事（换 run 会重建）；1s 轮询只在未就绪时真的发 attach。
  const timer = setInterval(attach, 1000)
  return () => {
    clearInterval(timer)
    bridge.destroy()
    panel?.destroy()
  }
}
