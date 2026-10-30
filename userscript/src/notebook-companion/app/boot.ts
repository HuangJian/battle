import { createRuntime, type Runtime } from '../../runtime'
import { loadConfig, type BncConfig } from '../config'
import { detectRole, type Role } from '../selectors'
import {
  KaggleFrameAdapter,
  KaggleSessionAdapter,
  parseKaggleNotebookKey,
  parseKaggleSession,
} from '../sites/kaggle'
import {
  mountPanel,
  PANEL_POS_KEY,
  readPanelPosition,
  type Panel,
  type PanelHistoryRow,
} from '../ui/panel'
import {
  createFrameBridge,
  createHostBridge,
  type BridgeMessage,
  type BridgeWindow,
} from './bridge'
import { installAutoContinue } from './auto-continue'
import { runFrameCommand } from './commands'
import {
  actionLabel,
  countdownLabel,
  createScheduler,
  scheduleStatusText,
  taskBelongsTo,
  type ScheduleAction,
  type ScheduleTarget,
  type Scheduler,
  type SchedulerTask,
} from './scheduler'
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

const PAGE_TAG_KEY = 'bnc:pageTag'

/**
 * 本标签页的短标签（4–6 位 a–z0–9）：同一个 notebook 多开几个页签，各写各的日志。
 *
 * `sessionStorage` 在 iframe 里按**顶层标签页**分区 ⇒ 本页刷新后标签不变（接着写同一份
 * 日志），另一个页签必然拿到不同的值；存储拿不到（第三方帧被禁）就退成「每次加载随机」——
 * 页面之间仍分得开，只是刷新会开一份新日志。
 */
function pageTag(win: Window): string {
  const gen = (): string => {
    const c = (win as unknown as { crypto?: { getRandomValues?: (a: Uint8Array) => void } }).crypto
    const buf = new Uint8Array(4)
    let n = 0
    if (typeof c?.getRandomValues === 'function') {
      c.getRandomValues(buf)
      n = ((buf[0]! << 24) | (buf[1]! << 16) | (buf[2]! << 8) | buf[3]!) >>> 0
    } else n = Math.floor(Math.random() * 0x1_0000_0000)
    return n.toString(36).slice(0, 6).padStart(4, '0')
  }
  try {
    const store = win.sessionStorage
    const saved = store.getItem(PAGE_TAG_KEY)
    if (saved && /^[a-z0-9]{4,8}$/.test(saved)) return saved
    const tag = gen()
    store.setItem(PAGE_TAG_KEY, tag)
    return tag
  } catch {
    return gen()
  }
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
    pageTag: pageTag(win),
    onError: (message) => log(`ship: ${message}`),
  })

  // 先别 start：等 host 在 attach 里送进 notebook 身份再搬运，否则首拍会写进
  // kaggle-unknown-edit 的日志（改名后那份日志还缺开头）。兜底 3s 让单独打开的 frame 照常跑。
  let started = false
  const startLoop = (): void => {
    if (started) return
    started = true
    loop.start()
  }
  const startFallback = setTimeout(startLoop, 3000)

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
      runFrameCommand(
        { loop, adapter, session, openLog, openConfig, configMarkers: config.configMarkers },
        name,
        args,
      ),
    isReady: () => adapter.ready,
    session: () => loop.session,
    onAttach: (m) => {
      if (m.sessionBase) loop.setSessionBase(m.sessionBase)
      startLoop()
      log(`bridge attach（nonce=${m.nonce ?? '∅'}）`)
    },
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
  log(
    `frame 启动（session=notebook:${loop.session} notebook=${adapter.ready ? 'yes' : 'waiting'}）`,
  )

  return () => {
    clearTimeout(startFallback)
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
  const sessionAdapter = new KaggleSessionAdapter(doc, href)
  const notebookKey = parseKaggleNotebookKey(href)
  /** frame 回报的会话键（含页面标签）：多开页签时必须用**这一页**的，不能拿外层 URL 猜。 */
  let frameSession = ''
  const logKey = (): string => `notebook:${frameSession || session}`
  let ready = false
  let panel: Panel | null = null

  const adoptSession = (m: BridgeMessage): void => {
    if (!m.session || m.session === frameSession) return
    frameSession = m.session
    panel?.setSession(logKey())
  }

  const bridge = createHostBridge(hostBridgeWindow(win), {
    timeoutMs: Math.max(5000, config.claimSettleMs + 4000),
    onReady: (m) => {
      ready = true
      adoptSession(m)
      log(
        `bridge ready（nonce=${m.nonce ?? '∅'} notebook=${m.notebook === true} session=${frameSession || '∅'}）`,
      )
      panel?.setStatus(m.notebook ? '已连接（笔记本就绪）' : '已连接（等待笔记本）', 'ok')
      syncMarks()
    },
    onStatus: (m) => {
      adoptSession(m)
      syncMarks()
      log(`bridge status（notebook=${m.notebook === true}）`)
      if (m.notebook === true) {
        ready = true
        panel?.setStatus('已连接（笔记本就绪）', 'ok')
      } else {
        panel?.setStatus('已连接（等待笔记本）', 'warn')
      }
    },
    onError: (reason) => {
      ready = false
      log(`bridge: ${reason}`)
    },
  })
  // frame 的 iframe URL 是 jupyter-proxy（解析不出 owner/slug）：把外层 notebook 身份送过去，
  // 让日志键用 notebook 名，而不是 kaggle-unknown-edit。
  bridge.setSessionBase(session)

  const run = async (name: string): Promise<void> => {
    const r = await bridge.send(name)
    if (!r.ok) {
      panel?.setStatus(`${name} ✗ ${r.reason ?? '失败'}`, 'error')
      return
    }
    // 字符串 value 是适配器的 detail（实际滚到的容器等），打到状态栏便于定位
    const detail = typeof r.value === 'string' ? ` ${r.value}` : ''
    panel?.setStatus(`${name} ✓${detail}`, 'ok')
  }

  const fromBridge = (r: { ok: boolean; reason?: string }): { ok: boolean; reason?: string } =>
    r.ok ? { ok: true } : { ok: false, reason: r.reason ?? 'bridge 无应答' }

  /** 停会话：先让 frame 排空日志（失败也停，但说出来），再点外层 Stop。 */
  const stopNow = async (): Promise<{ ok: boolean; reason?: string }> => {
    if (ready) {
      const f = await bridge.send('flush')
      if (!f.ok) log(`停会话前未能保存：${f.reason ?? '未排空'}`)
    }
    return sessionAdapter.stopSession()
  }

  let sched: Scheduler | null = null

  /** 本次页面的执行历史（内存，刷新即清空）。 */
  const history: PanelHistoryRow[] = []
  const HISTORY_MAX = 5
  function recordHistory(task: SchedulerTask, result: { ok: boolean; reason?: string }): void {
    const d = new Date()
    const pad2 = (n: number): string => String(n).padStart(2, '0')
    history.unshift({
      at: `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`,
      text: actionLabel(task.action),
      ok: result.ok,
      ...(result.reason ? { reason: result.reason } : {}),
    })
    if (history.length > HISTORY_MAX) history.length = HISTORY_MAX
    panel?.setHistory(history)
  }

  /** 只显示属于这一页的任务：运行类看 notebook，停止/中断看本页会话（与调度器同口径）。 */
  function belongsHere(task: SchedulerTask): boolean {
    return taskBelongsTo(task, { notebookKey, session: frameSession })
  }

  /**
   * 把定时任务 + 倒计时下发到帧内（跨域 DOM 宿主碰不到）：
   * `run-cell` 挂目标 cell 的徽标，其余动作挂帧顶部悬浮徽标。
   */
  function syncMarks(): void {
    if (!sched || !ready) return
    const now = Date.now()
    const nextById = new Map(sched.rows().map((r) => [r.id, r.next]))
    const marks = sched
      .tasks()
      .filter(belongsHere)
      .map((t) => {
        const left = countdownLabel(t.nextAt, now, nextById.get(t.id) ?? '已定时')
        if (t.action === 'run-cell' && t.target) {
          return {
            id: t.id,
            label: left,
            ...(t.target.uuid ? { uuid: t.target.uuid } : {}),
            ...(t.target.index !== undefined ? { index: t.target.index } : {}),
          }
        }
        return { id: t.id, label: `${actionLabel(t.action)} · ${left}`, action: t.action }
      })
    void bridge.send('setScheduleMarks', { marks })
  }

  panel = mountPanel(
    doc,
    {
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
          `${config.dashboardBase}/log/${encodeURIComponent(logKey())}`,
          '_blank',
          'noopener',
        )
      },
      jumpConfig: () => run('jumpConfig'),
    },
    {
      position: readPanelPosition(runtime),
      onMove: (position) => runtime.setValue(PANEL_POS_KEY, position),
      onScheduleAdd: async (input) => {
        if (!sched) return '调度未就绪'
        let target: ScheduleTarget | undefined
        if (input.action === 'run-cell') {
          const cap = await bridge.send('captureTarget')
          if (!cap.ok) {
            return `运行 cell 需要先在笔记本里选中一段代码（${cap.reason ?? '没有目标'}）`
          }
          const v = cap.value as ScheduleTarget | undefined
          if (!v || (v.uuid === undefined && v.index === undefined)) return '没拿到目标 cell 的位置'
          target = v
        }
        const r = sched.add({
          at: input.at,
          action: input.action as ScheduleAction,
          ...(target ? { target } : {}),
        })
        return r.ok ? null : r.reason
      },
      onScheduleRemove: (id) => sched?.remove(id),
      onScheduleToggle: (id, enabled) => sched?.setEnabled(id, enabled),
    },
  )

  // 先显示外层 URL 推出的键；frame 一连上就用它回报的（含页面标签）覆盖。
  panel.setSession(logKey())

  // Kaggle 的「是否仍需编辑 / 配额超限」弹窗会卡住训练页：自动点白名单里的继续键
  const autoContinue = installAutoContinue(doc, {
    onConfirm: (label) => log(`[auto] 已点击「${label}」（Kaggle 弹窗）`),
  })

  // ── 定时启停（plan §3.3 / W4）：任务住 GM 存储，轮询墙钟，到点后 10s 可取消 ──
  sched = createScheduler({
    store: runtime,
    writer: `host-${Math.random().toString(36).slice(2, 8)}`,
    now: () => Date.now(),
    claimSettleMs: config.claimSettleMs,
    runGraceMs: 10 * 60_000,
    stopGraceMs: config.stopGraceMs,
    // 会话必须用 frame 回报的那个（含页面标签）——停止类任务靠它 fail closed；
    // 还没连上时 identity.session 为空 ⇒ add 会响亮拒绝（「等面板显示已连接」）。
    identity: () => ({ notebookKey, session: frameSession }),
    confirm: async (task, occurrence) => {
      const d = new Date(task.nextAt)
      const clock = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
      const text = `${actionLabel(task.action)}（${clock} · ${occurrence}）`
      // 后台页弹不出确认框：按创建时的预授权执行，但必须留一行证据
      if (doc.hidden) {
        log(`后台按预授权执行：${text}`)
        return true
      }
      return panel ? panel.countdown(`即将${text}`, 10) : true
    },
    run: async (task) => {
      let result: { ok: boolean; reason?: string }
      if (task.action === 'stop-session') result = await stopNow()
      else if (!ready) result = { ok: false, reason: '笔记本未连接（iframe 不可达）' }
      else if (task.action === 'interrupt') result = fromBridge(await bridge.send('interrupt'))
      else if (task.action === 'run-all') result = fromBridge(await bridge.send('runAllCells'))
      else result = fromBridge(await bridge.send('runCell', task.target ?? {}))
      // 执行历史只留本页内存：刷新页面即清空（不写 GM 存储，跨页签不串）
      recordHistory(task, result)
      return result
    },
    onChange: () => {
      const rows = sched?.rows() ?? []
      panel?.setSchedule(rows)
      // 增删/执行后状态栏跟着任务表改口：删空后不再留着「已添加定时：HH:MM」
      panel?.setStatus(scheduleStatusText(rows.length), 'ok')
      syncMarks()
    },
  })
  panel.setSchedule(sched.rows())
  sched.start()
  // 倒计时只上 cell 徽标：每秒把「运行 cell」的剩余时间刷进帧内（不占面板）
  const markTimer = setInterval(syncMarks, 1000)
  const onVisibility = (): void => {
    void sched?.tick()
  }
  doc.addEventListener('visibilitychange', onVisibility)

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
    clearInterval(markTimer)
    autoContinue.dispose()
    sched?.stop()
    doc.removeEventListener('visibilitychange', onVisibility)
    bridge.destroy()
    panel?.destroy()
  }
}
