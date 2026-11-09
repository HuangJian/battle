import { createRuntime, type Runtime } from '../../runtime'
import type { FrameAdapter, SessionAdapter } from '../sites/types'
import {
  applyPageAutoSave,
  autoSaveKeyFor,
  loadConfig,
  loadStopPpo,
  saveAutoSave,
  saveStopPpo,
  type BncConfig,
} from '../config'
import { detectRole, singleSite, type Role } from '../selectors'
import {
  createTabTitleSync,
  KaggleFrameAdapter,
  KaggleSessionAdapter,
  parseKaggleNotebookKey,
  parseKaggleSession,
} from '../sites/kaggle'
import { ColabAdapter, ColabSessionAdapter, parseColabNotebookKey } from '../sites/colab'
import {
  AistudioAdapter,
  AistudioSessionAdapter,
  parseAistudioNotebookKey,
} from '../sites/aistudio'
import {
  ModelscopeAdapter,
  ModelscopeSessionAdapter,
  parseModelscopeNotebookKey,
  parseModelscopeSession,
} from '../sites/modelscope'
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
import { runFrameCommand, type CommandDeps, type CommandReply } from './commands'
import { createOfflineWatch } from './offline-watch'
import { describeHubSync, startHubUrlSync } from './hub-sync'
import {
  actionLabel,
  countdownLabel,
  createScheduler,
  eventDetail,
  eventOpName,
  historyText,
  scheduleStatusText,
  taskBelongsTo,
  type ScheduleAction,
  type ScheduleTarget,
  type Scheduler,
  type SchedulerEvent,
  type SchedulerTask,
} from './scheduler'
import { createOpLog } from './op-log'
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

  const start = (): (() => void) => {
    if (role === 'frame') return startFrame(doc, win, href, config, runtime, log)
    if (role === 'single') return startSingle(doc, win, href, config, runtime, log)
    return startHost(doc, win, href, config, runtime, log)
  }

  if (doc.body === null) {
    doc.addEventListener('DOMContentLoaded', () => start(), { once: true })
    return { role, hasRuntime: true }
  }
  return { role, hasRuntime: true, dispose: start() }
}

const PAGE_TAG_KEY = 'bnc:pageTag'
const WRITER_KEY = 'bnc:writer'

/** 4–6 位 a–z0–9；crypto 拿不到就退 Math.random（此处不是玩法随机）。 */
function randomTag(win: Window): string {
  const c = (win as unknown as { crypto?: { getRandomValues?: (a: Uint8Array) => void } }).crypto
  const buf = new Uint8Array(4)
  let n = 0
  if (typeof c?.getRandomValues === 'function') {
    c.getRandomValues(buf)
    n = ((buf[0]! << 24) | (buf[1]! << 16) | (buf[2]! << 8) | buf[3]!) >>> 0
  } else n = Math.floor(Math.random() * 0x1_0000_0000)
  return n.toString(36).slice(0, 6).padStart(4, '0')
}

/**
 * 本标签页的短标签（4–6 位 a–z0–9）：同一个 notebook 多开几个页签，各写各的日志。
 *
 * `sessionStorage` 在 iframe 里按**顶层标签页**分区 ⇒ 本页刷新后标签不变（接着写同一份
 * 日志），另一个页签必然拿到不同的值；存储拿不到（第三方帧被禁）就退成「每次加载随机」——
 * 页面之间仍分得开，只是刷新会开一份新日志。
 */
function pageTag(win: Window): string {
  try {
    const store = win.sessionStorage
    const saved = store.getItem(PAGE_TAG_KEY)
    if (saved && /^[a-z0-9]{4,8}$/.test(saved)) return saved
    const tag = randomTag(win)
    store.setItem(PAGE_TAG_KEY, tag)
    return tag
  } catch {
    return randomTag(win)
  }
}

/**
 * 写者身份：**必须跨刷新稳定**。
 *
 * 服务端按 writer 的租约（180s）fencing：同一份日志换了 writer，就得等旧租约过期才能写。
 * 若每次加载都随机一个，刷新后本页会被**自己上一次**的租约挡三分钟（面板报「另一个标签
 * 持有搬运权」）。存进 sessionStorage ⇒ 刷新沿用同一个 writer，第一笔就顺手续租；真另有
 * 页签时它有自己的标签与日志键，互不干扰。
 */
function writerTag(
  win: Window,
  role: 'host' | 'frame' | 'colab' | 'aistudio' | 'modelscope',
): string {
  const key = `${WRITER_KEY}:${role}`
  const gen = (): string => `${role}-${randomTag(win)}`
  try {
    const store = win.sessionStorage
    const saved = store.getItem(key)
    if (saved && new RegExp(`^${role}-[a-z0-9]{4,8}$`).test(saved)) return saved
    const tag = gen()
    store.setItem(key, tag)
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
  const page = pageTag(win)
  applyPageAutoSave(runtime, page, config)
  const loop = createShipLoop({
    doc,
    href,
    referrer: doc.referrer,
    config,
    runtime,
    adapter,
    pageTag: page,
    writer: writerTag(win, 'frame'),
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
    log(`配置已复制到剪贴板（自动保存按页存 ${autoSaveKeyFor(page)}；其余在 bnc:config）`)
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
      if (m.autoSave) loop.setAutoSave(m.autoSave.enabled, m.autoSave.intervalSec)
      startLoop()
      log(`bridge attach（nonce=${m.nonce ?? '∅'}）`)
    },
    onError: (reason) => log(`bridge: ${reason}`),
  })

  // 离线训练页（battle.offline.ipynb）：训练 cell 从排队转真正执行时，经桥上报一次。
  // 只读监控：不点运行、不停会话；host 负责落 `/api/notebookOp`。
  const offline = config.enabled
    ? createOfflineWatch({
        snapshot: () => ({
          cells: adapter.cells(),
          sourceText: (cell) => adapter.sourceText(cell),
          executing: (cell) => adapter.cellExecuting(cell),
          outputText: (cell) => adapter.outputText(cell),
          kernelStatus: adapter.kernelStatus(),
        }),
      })
    : null
  let offlineNoted = false
  const offlineTimer =
    offline === null
      ? null
      : setInterval(() => {
          const start = offline.poll()
          const hit = offline.detected()
          if (hit && !offlineNoted) {
            offlineNoted = true
            bridge.note('offline:detected', { value: `cell=${hit.index} id=${hit.id}` })
          }
          if (start) {
            bridge.note('offline:cell-start', {
              value: `cell=${start.cell.index} id=${start.cell.id} waited=${start.waitedMs}ms evidence=${start.evidence}`,
            })
          }
        }, 1000)

  // 页首同步 CFG['hub_url']：hub 的 cloudflared 隧道换了 URL，notebook 里的配置跟着换。
  // 结果经 bridge 送到外层面板（成功 / 未改 / 失败原因都要看得见，不能只进 console）。
  const hubSync = config.enabled
    ? startHubUrlSync({
        runtime,
        dashboardBase: config.dashboardBase,
        adapter,
        markers: config.configMarkers,
        log: (msg) => log(`hub: ${msg}`),
      })
    : null
  if (hubSync) {
    void hubSync.done.then((r) => {
      log(`hub 同步：${r.ok ? (r.detail ?? 'ok') : r.reason}`)
      bridge.note(
        'hub:sync',
        r.ok ? { ok: true, value: r.detail ?? 'ok' } : { ok: false, reason: r.reason },
      )
    })
  }

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
    if (offlineTimer !== null) clearInterval(offlineTimer)
    hubSync?.cancel()
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

  // Kaggle 把标签页标题写死成「Kaggle Notebook Editor」：多开 notebook 分不出哪个是哪个。
  // 每秒把 document.title 钉成本 notebook 的名字；URL 用当前的（SPA 换页跟着走）。
  const syncTabTitle = createTabTitleSync(doc, () => {
    try {
      return doc.location.href || href
    } catch {
      return href
    }
  })
  syncTabTitle()

  /** frame 回报的会话键（含页面标签）：多开页签时必须用**这一页**的，不能拿外层 URL 猜。 */
  let frameSession = ''
  const logKey = (): string => `notebook:${frameSession || session}`
  let ready = false
  let panel: Panel | null = null
  let offlineDetected = false

  /**
   * 本页标签：定时任务表的分桶键（`bnc:tasks:<notebook>#<page>`）与归属阀都靠它。
   * 存 sessionStorage ⇒ 刷新不变、另一个页签必然不同。
   */
  const page = pageTag(win)
  applyPageAutoSave(runtime, page, config)
  /** 操作日志 → dashboard（失败只掉队列，不影响面板）。 */
  const ops = createOpLog({
    runtime,
    dashboardBase: config.dashboardBase,
    site: 'kaggle',
    session: () => (frameSession || session).slice(0, 64),
    page,
    writer: writerTag(win, 'host'),
  })
  const op = (name: string, detail?: string): void => ops.log(name, detail)
  const opEvent = (e: SchedulerEvent): void => op(eventOpName(e), eventDetail(e))
  op('boot', `kaggle host notebook=${notebookKey || '∅'} session=${session || '∅'} page=${page}`)

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
      op('bridge:ready', `notebook=${m.notebook === true} session=${frameSession || '∅'}`)
      panel?.setStatus(m.notebook ? '已连接（笔记本就绪）' : '已连接（等待笔记本）', 'ok')
      syncMarks()
    },
    onStatus: (m) => {
      adoptSession(m)
      syncMarks()
      log(`bridge status（notebook=${m.notebook === true}）`)
      op('bridge:status', `notebook=${m.notebook === true} session=${frameSession || '∅'}`)
      if (m.notebook === true) {
        ready = true
        panel?.setStatus('已连接（笔记本就绪）', 'ok')
      } else {
        panel?.setStatus('已连接（等待笔记本）', 'warn')
      }
    },
    onNote: (m) => {
      if (m.name === 'offline:detected') {
        offlineDetected = true
        panel?.setStopPpoVisible(true)
        const detail =
          `notebook=${notebookKey || '∅'} ${typeof m.value === 'string' ? m.value : ''}`.trim()
        op('offline:detected', detail)
        return
      }
      if (m.name === 'offline:cell-start') {
        const stopOthers = loadStopPpo(runtime, page, false)
        const detail =
          `notebook=${notebookKey || '∅'} ${typeof m.value === 'string' ? m.value : ''} ` +
          `stopOthers=${stopOthers ? 1 : 0}`
        op('offline:cell-start', detail.trim())
        void ops.flush()
        panel?.setStatus(
          stopOthers ? '离线训练已开始（已上报：停其它 PPO）' : '离线训练已开始（已上报）',
          'ok',
        )
        return
      }
      if (m.name !== 'hub:sync') return
      const view = describeHubSync({
        ok: m.ok === true,
        ...(typeof m.value === 'string' ? { detail: m.value } : {}),
        ...(m.reason ? { reason: m.reason } : {}),
      })
      op('hub:sync', view.text)
      panel?.setHubSync(view.text, view.tone)
    },
    onError: (reason) => {
      ready = false
      log(`bridge: ${reason}`)
      op('bridge:error', reason)
    },
  })
  // frame 的 iframe URL 是 jupyter-proxy（解析不出 owner/slug）：把外层 notebook 身份送过去，
  // 让日志键用 notebook 名，而不是 kaggle-unknown-edit。
  bridge.setSessionBase(session)
  bridge.setAutoSave({
    enabled: config.autoSaveEnabled,
    intervalSec: config.autoSaveIntervalSec,
  })

  /** 面板按钮的唯一出口：一条命令 = 一条操作日志（成败与原因都留）。 */
  const run = async (name: string): Promise<void> => {
    const r = await bridge.send(name)
    op(`cmd:${name}`, r.ok ? 'ok' : (r.reason ?? '失败'))
    if (!r.ok) {
      panel?.setStatus(`${name} ✗ ${r.reason ?? '失败'}`, 'error')
      return
    }
    // 字符串 value 是适配器的 detail（实际滚到的容器等），打到状态栏便于定位
    const detail = typeof r.value === 'string' ? ` ${r.value}` : ''
    panel?.setStatus(`${name} ✓${detail}`, 'ok')
  }

  const fromBridge = (r: {
    ok: boolean
    reason?: string
    retry?: boolean
  }): { ok: boolean; reason?: string; retry?: boolean } =>
    r.ok
      ? { ok: true }
      : { ok: false, reason: r.reason ?? 'bridge 无应答', ...(r.retry ? { retry: true } : {}) }

  /** 停会话：先让 frame 排空日志（失败也停，但说出来），再点外层 Stop。 */
  const stopNow = async (): Promise<{ ok: boolean; reason?: string; detail?: string }> => {
    if (ready) {
      const f = await bridge.send('flush')
      if (!f.ok) {
        log(`停会话前未能保存：${f.reason ?? '未排空'}`)
        op('stop:flush:fail', f.reason ?? '未排空')
      }
    }
    return sessionAdapter.stopSession()
  }

  let sched: Scheduler | null = null

  /** 本次页面的执行历史（内存，刷新即清空）。 */
  const history: PanelHistoryRow[] = []
  const HISTORY_MAX = 5
  function recordHistory(
    task: SchedulerTask,
    result: { ok: boolean; reason?: string; detail?: string },
  ): void {
    const d = new Date()
    const pad2 = (n: number): string => String(n).padStart(2, '0')
    history.unshift({
      at: `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`,
      text: historyText(task.action, result),
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
        op('log:open', logKey())
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
      autoSave: {
        enabled: config.autoSaveEnabled,
        intervalMin: Math.max(1, Math.round(config.autoSaveIntervalSec / 60)),
      },
      onAutoSaveChange: async (value) => {
        const intervalSec = Math.min(24 * 3600, Math.max(60, Math.round(value.intervalMin * 60)))
        config.autoSaveEnabled = value.enabled
        config.autoSaveIntervalSec = intervalSec
        saveAutoSave(runtime, page, {
          enabled: config.autoSaveEnabled,
          intervalSec: config.autoSaveIntervalSec,
        })
        bridge.setAutoSave({ enabled: config.autoSaveEnabled, intervalSec })
        const r = await bridge.send('setAutoSave', {
          enabled: config.autoSaveEnabled,
          intervalSec,
        })
        op(
          'autosave',
          `${value.enabled ? 'on' : 'off'} ${Math.round(value.intervalMin)}min ${r.ok ? 'ok' : (r.reason ?? 'fail')}`,
        )
        return r.ok ? null : `自动保存已写入配置，但当前帧未连上（${r.reason ?? '无应答'}）`
      },
      stopPpo: loadStopPpo(runtime, page, false),
      onStopPpoChange: (enabled) => {
        saveStopPpo(runtime, page, enabled)
        op('stopPpo', enabled ? 'on' : 'off')
        return null
      },
      onScheduleAdd: async (input) => {
        op('schedule:add:request', `${input.action} ${input.at}`)
        if (!sched) {
          op('schedule:add:reject', '调度未就绪')
          return '调度未就绪'
        }
        let target: ScheduleTarget | undefined
        if (input.action === 'run-cell') {
          const cap = await bridge.send('captureTarget')
          if (!cap.ok) {
            const reason = `运行 cell 需要先在笔记本里选中一段代码（${cap.reason ?? '没有目标'}）`
            op('schedule:add:reject', reason)
            return reason
          }
          const v = cap.value as ScheduleTarget | undefined
          if (!v || (v.uuid === undefined && v.index === undefined)) {
            op('schedule:add:reject', '没拿到目标 cell 的位置')
            return '没拿到目标 cell 的位置'
          }
          target = v
        }
        const r = sched.add({
          at: input.at,
          action: input.action as ScheduleAction,
          ...(target ? { target } : {}),
        })
        if (!r.ok) op('schedule:add:reject', r.reason)
        return r.ok ? null : r.reason
      },
      onScheduleRemove: (id) => sched?.remove(id),
      onScheduleToggle: (id, enabled) => sched?.setEnabled(id, enabled),
    },
  )

  // 先显示外层 URL 推出的键；frame 一连上就用它回报的（含页面标签）覆盖。
  panel.setSession(logKey())
  panel.setStopPpoVisible(offlineDetected)

  // Kaggle 的「是否仍需编辑 / 配额超限」弹窗会卡住训练页：自动点白名单里的继续键
  const autoContinue = installAutoContinue(doc, {
    onConfirm: (label) => log(`[auto] 已点击「${label}」（Kaggle 弹窗）`),
  })

  // ── 定时启停（plan §3.3 / W4）：任务住 GM 存储，轮询墙钟，到点后 10s 可取消 ──
  sched = createScheduler({
    store: runtime,
    writer: writerTag(win, 'host'),
    now: () => Date.now(),
    claimSettleMs: config.claimSettleMs,
    runGraceMs: 10 * 60_000,
    stopGraceMs: config.stopGraceMs,
    // 会话必须用 frame 回报的那个（含页面标签）——停止类任务靠它 fail closed；
    // 还没连上时 identity.session 为空 ⇒ add 会响亮拒绝（「等面板显示已连接」）。
    // page = 任务表分桶键（本页只看得见自己设的任务）。
    identity: () => ({ notebookKey, session: frameSession, page }),
    onEvent: opEvent,
    confirm: async (task, occurrence) => {
      const d = new Date(task.nextAt)
      const clock = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
      const text = `${actionLabel(task.action)}（${clock} · ${occurrence}）`
      // 后台页弹不出确认框：按创建时的预授权执行，但必须留一行证据
      if (doc.hidden) {
        log(`后台按预授权执行：${text}`)
        op('schedule:confirm:background', text)
        return true
      }
      return panel ? panel.countdown(`即将${text}`, 10) : true
    },
    run: async (task) => {
      let result: { ok: boolean; reason?: string; detail?: string; retry?: boolean }
      if (task.action === 'stop-session') result = await stopNow()
      else if (!ready) result = { ok: false, retry: true, reason: '笔记本未连接（等待 iframe）' }
      else if (task.action === 'interrupt') result = fromBridge(await bridge.send('interrupt'))
      else if (task.action === 'run-all') result = fromBridge(await bridge.send('runAllCells'))
      else result = fromBridge(await bridge.send('runCell', task.target ?? {}))
      // 执行历史只留本页内存：刷新页面即清空（不写 GM 存储，跨页签不串）；
      // 可重试的「还没到时候」不进历史（否则每 20s 一行刷屏），最终结果才记。
      if (!result.retry) recordHistory(task, result)
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
  // 倒计时只上 cell 徽标：每秒把「运行 cell」的剩余时间刷进帧内（不占面板）；
  // 同一拍把标签页标题钉住（Kaggle 会把它覆写回固定文案）。
  const markTimer = setInterval(() => {
    syncMarks()
    syncTabTitle()
  }, 1000)
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
    op('dispose')
    ops.stop()
  }
}

/**
 * `single` 角色（Colab）：cell 与面板在同一份文档里 ⇒ 不建桥，命令直接调 `runFrameCommand`。
 * 面板 / 定时 / 执行历史 / 徽标的口径与 host 完全一致，差别只有「跨帧发送」变成「本地调用」。
 */
function startSingle(
  doc: Document,
  win: Window,
  href: string,
  config: BncConfig,
  runtime: Runtime,
  log: (msg: string) => void,
): () => void {
  // 同文档站点：Colab、AI Studio（BML Codelab）、魔搭 Web IDE 的 notebook 帧。cell 都在本帧里 ——
  // 不建桥，差别只在适配器、日志键与会话实现。
  const site = singleSite(href) ?? 'colab'
  const adapter: FrameAdapter =
    site === 'aistudio'
      ? new AistudioAdapter(doc)
      : site === 'modelscope'
        ? new ModelscopeAdapter(doc)
        : new ColabAdapter(doc)
  const sessionAdapter: SessionAdapter =
    site === 'aistudio'
      ? new AistudioSessionAdapter(doc, href)
      : site === 'modelscope'
        ? new ModelscopeSessionAdapter(doc)
        : new ColabSessionAdapter(doc, href)
  /** 魔搭的 notebook 名不在 URL 里，只能从工作台标签读；warm 起来之前是空串（见下面的补读）。 */
  let notebookKey =
    site === 'aistudio'
      ? parseAistudioNotebookKey(href)
      : site === 'modelscope'
        ? parseModelscopeNotebookKey(doc)
        : parseColabNotebookKey(href)
  /** 本页标签：日志键、任务表分桶、归属阀共用同一个值。 */
  const page = pageTag(win)
  applyPageAutoSave(runtime, page, config)
  const loop = createShipLoop({
    doc,
    href,
    referrer: doc.referrer,
    config,
    runtime,
    adapter,
    site,
    pageTag: page,
    // 写者身份跨刷新稳定（与 host/frame 同口径）：刷新后不被自己的旧租约挡住
    writer: writerTag(win, site),
    onError: (message) => log(`ship: ${message}`),
  })

  /** 操作日志 → dashboard（与 host 同一条通道；`.ops.jsonl` 按会话归档）。 */
  const ops = createOpLog({
    runtime,
    dashboardBase: config.dashboardBase,
    site,
    session: () => loop.session.slice(0, 64),
    page,
    writer: writerTag(win, site),
  })
  const op = (name: string, detail?: string): void => ops.log(name, detail)
  const opEvent = (e: SchedulerEvent): void => op(eventOpName(e), eventDetail(e))
  op('boot', `${site} single notebook=${notebookKey || '∅'} session=${loop.session} page=${page}`)

  // 页首同步 CFG['hub_url']（与 Kaggle 帧同一条路径；single 页没有桥，结果进操作日志）。
  const hubSync = config.enabled
    ? startHubUrlSync({
        runtime,
        dashboardBase: config.dashboardBase,
        adapter,
        markers: config.configMarkers,
        log: (msg) => log(`hub: ${msg}`),
      })
    : null
  if (hubSync) {
    void hubSync.done.then((r) => {
      op('hub:sync', r.ok ? (r.detail ?? 'ok') : r.reason)
      const view = describeHubSync(r)
      panel?.setHubSync(view.text, view.tone)
    })
  }

  const openLog = (): void => {
    op('log:open', `notebook:${loop.session}`)
    win.open(
      `${config.dashboardBase}/log/${encodeURIComponent(`notebook:${loop.session}`)}`,
      '_blank',
      'noopener',
    )
  }
  const openConfig = (): void => {
    op('config:copy')
    runtime.setClipboard(JSON.stringify(config, null, 2))
    log(`配置已复制到剪贴板（自动保存按页存 ${autoSaveKeyFor(page)}；其余在 bnc:config）`)
  }
  const deps: CommandDeps = {
    loop,
    adapter,
    session: sessionAdapter,
    openLog,
    openConfig,
    configMarkers: config.configMarkers,
  }

  let panel: Panel | null = null
  let sched: Scheduler | null = null
  let offlineDetected = false

  /** 命令分发：Kaggle 那边跨帧 postMessage，这里同一文档直接调（失败带原因，不静默）。 */
  const send = async (name: string, args?: unknown): Promise<CommandReply> => {
    try {
      return await runFrameCommand(deps, name, args)
    } catch (e) {
      return { ok: false, reason: e instanceof Error ? e.message : String(e) }
    }
  }

  /** 面板按钮的唯一出口：一条命令 = 一条操作日志（成败与原因都留）。 */
  const run = async (name: string, args?: unknown): Promise<void> => {
    const r = await send(name, args)
    op(`cmd:${name}`, r.ok ? 'ok' : (r.reason ?? '失败'))
    if (!r.ok) {
      panel?.setStatus(`${name} ✗ ${r.reason ?? '失败'}`, 'error')
      return
    }
    const detail = typeof r.value === 'string' ? ` ${r.value}` : ''
    panel?.setStatus(`${name} ✓${detail}`, 'ok')
  }

  const toCmd = (
    r: CommandReply,
  ): { ok: boolean; reason?: string; detail?: string; retry?: boolean } =>
    r.ok
      ? { ok: true, ...(typeof r.value === 'string' ? { detail: r.value } : {}) }
      : { ok: false, reason: r.reason ?? '失败', ...(r.retry ? { retry: true } : {}) }

  /** 本页的执行历史（内存，刷新即清空）。 */
  const history: PanelHistoryRow[] = []
  const HISTORY_MAX = 5
  function recordHistory(
    task: SchedulerTask,
    result: { ok: boolean; reason?: string; detail?: string },
  ): void {
    const d = new Date()
    const pad2 = (n: number): string => String(n).padStart(2, '0')
    history.unshift({
      at: `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`,
      text: historyText(task.action, result),
      ok: result.ok,
      ...(result.reason ? { reason: result.reason } : {}),
    })
    if (history.length > HISTORY_MAX) history.length = HISTORY_MAX
    panel?.setHistory(history)
  }

  /** 只显示属于这一页的任务（GM 存储跨页共享，与 host 同口径）。 */
  function belongsHere(task: SchedulerTask): boolean {
    return taskBelongsTo(task, { notebookKey, session: loop.session })
  }

  /** 定时徽标：没有桥，直接贴在本页 DOM；每秒重贴 ⇒ cell 重渲染后下一拍自愈。 */
  function syncMarks(): void {
    if (!sched) return
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
    adapter.setScheduledMarks(marks)
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
      openLog,
      jumpConfig: () => run('jumpConfig'),
    },
    {
      position: readPanelPosition(runtime),
      onMove: (position) => runtime.setValue(PANEL_POS_KEY, position),
      autoSave: {
        enabled: config.autoSaveEnabled,
        intervalMin: Math.max(1, Math.round(config.autoSaveIntervalSec / 60)),
      },
      onAutoSaveChange: async (value) => {
        const intervalSec = Math.min(24 * 3600, Math.max(60, Math.round(value.intervalMin * 60)))
        config.autoSaveEnabled = value.enabled
        config.autoSaveIntervalSec = intervalSec
        saveAutoSave(runtime, page, {
          enabled: config.autoSaveEnabled,
          intervalSec: config.autoSaveIntervalSec,
        })
        const r = await send('setAutoSave', {
          enabled: config.autoSaveEnabled,
          intervalSec,
        })
        op(
          'autosave',
          `${value.enabled ? 'on' : 'off'} ${Math.round(value.intervalMin)}min ${r.ok ? 'ok' : (r.reason ?? 'fail')}`,
        )
        return r.ok ? null : `自动保存已写入配置，但当前页未生效（${r.reason ?? '无应答'}）`
      },
      stopPpo: loadStopPpo(runtime, page, false),
      onStopPpoChange: (enabled) => {
        saveStopPpo(runtime, page, enabled)
        op('stopPpo', enabled ? 'on' : 'off')
        return null
      },
      onScheduleAdd: async (input) => {
        op('schedule:add:request', `${input.action} ${input.at}`)
        if (!sched) {
          op('schedule:add:reject', '调度未就绪')
          return '调度未就绪'
        }
        let target: ScheduleTarget | undefined
        if (input.action === 'run-cell') {
          const cap = await send('captureTarget')
          if (!cap.ok) {
            const reason = `运行 cell 需要先在笔记本里选中一段代码（${cap.reason ?? '没有目标'}）`
            op('schedule:add:reject', reason)
            return reason
          }
          const v = cap.value as ScheduleTarget | undefined
          if (!v || (v.uuid === undefined && v.index === undefined)) {
            op('schedule:add:reject', '没拿到目标 cell 的位置')
            return '没拿到目标 cell 的位置'
          }
          target = v
        }
        const r = sched.add({
          at: input.at,
          action: input.action as ScheduleAction,
          ...(target ? { target } : {}),
        })
        if (!r.ok) op('schedule:add:reject', r.reason)
        return r.ok ? null : r.reason
      },
      onScheduleRemove: (id) => sched?.remove(id),
      onScheduleToggle: (id, enabled) => sched?.setEnabled(id, enabled),
    },
  )

  panel.setSession(`notebook:${loop.session}`)
  panel.setStatus(
    adapter.ready ? '已连接（笔记本就绪）' : '已连接（等待笔记本）',
    adapter.ready ? 'ok' : 'warn',
  )
  if (site === 'modelscope') {
    // 输出在跨域 webview 里：面板一上来就说清楚，不能让人以为「保存日志」在正常工作。
    panel.setStatus('已连接（魔搭 Web IDE：cell 输出在跨域 webview，保存日志不可用）', 'warn')
  }
  panel.setStopPpoVisible(offlineDetected)

  // 魔搭的 notebook 身份在 URL 里没有（帧地址只有 `?t=&lang=`），只能等工作台把标签渲染出来；
  // 读到就换日志键/任务桶，最多等 30s（换键 = 换一份日志，与 host 的 attach 同口径）。
  let identityTimer: ReturnType<typeof setInterval> | null = null
  if (site === 'modelscope' && !notebookKey) {
    const deadline = Date.now() + 30_000
    identityTimer = setInterval(() => {
      const key = parseModelscopeNotebookKey(doc)
      if (!key) {
        if (Date.now() < deadline) return
        if (identityTimer !== null) clearInterval(identityTimer)
        identityTimer = null
        return
      }
      notebookKey = key
      loop.setSessionBase(parseModelscopeSession(doc))
      panel?.setSession(`notebook:${loop.session}`)
      op('identity', `notebook=${key} session=${loop.session}`)
      if (identityTimer !== null) clearInterval(identityTimer)
      identityTimer = null
    }, 1000)
  }

  // 离线训练页（battle.offline.ipynb）：训练 cell 从排队转真正执行时上报一次。
  // 与 host 同一通道（`/api/notebookOp`）；Colab/AI Studio 没有桥，直接落本页日志。
  const offline = config.enabled
    ? createOfflineWatch({
        snapshot: () => ({
          cells: adapter.cells(),
          sourceText: (cell) => adapter.sourceText(cell),
          executing: (cell) => adapter.cellExecuting(cell),
          outputText: (cell) => adapter.outputText(cell),
          kernelStatus: adapter.kernelStatus(),
        }),
      })
    : null
  let offlineNoted = false
  const offlineTimer =
    offline === null
      ? null
      : setInterval(() => {
          const start = offline.poll()
          const hit = offline.detected()
          if (hit && !offlineNoted) {
            offlineNoted = true
            offlineDetected = true
            panel?.setStopPpoVisible(true)
            op('offline:detected', `cell=${hit.index} id=${hit.id}`)
          }
          if (start) {
            const stopOthers = loadStopPpo(runtime, page, false)
            const detail =
              `cell=${start.cell.index} id=${start.cell.id} waited=${start.waitedMs}ms ` +
              `evidence=${start.evidence} stopOthers=${stopOthers ? 1 : 0}`
            op('offline:cell-start', detail)
            void ops.flush()
            panel?.setStatus(
              stopOthers ? '离线训练已开始（已上报：停其它 PPO）' : '离线训练已开始（已上报）',
              'ok',
            )
          }
        }, 1000)

  sched = createScheduler({
    store: runtime,
    writer: writerTag(win, site),
    now: () => Date.now(),
    claimSettleMs: config.claimSettleMs,
    runGraceMs: 10 * 60_000,
    stopGraceMs: config.stopGraceMs,
    identity: () => ({ notebookKey, session: loop.session, page }),
    onEvent: opEvent,
    confirm: async (task, occurrence) => {
      const d = new Date(task.nextAt)
      const clock = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
      const text = `${actionLabel(task.action)}（${clock} · ${occurrence}）`
      // 后台页弹不出确认框：按创建时的预授权执行，但必须留一行证据
      if (doc.hidden) {
        log(`后台按预授权执行：${text}`)
        op('schedule:confirm:background', text)
        return true
      }
      return panel ? panel.countdown(`即将${text}`, 10) : true
    },
    run: async (task) => {
      const name =
        task.action === 'stop-session'
          ? 'stopSession'
          : task.action === 'interrupt'
            ? 'interrupt'
            : task.action === 'run-all'
              ? 'runAllCells'
              : 'runCell'
      const result = toCmd(
        await send(name, task.action === 'run-cell' ? (task.target ?? {}) : undefined),
      )
      // 执行历史只留本页内存：刷新页面即清空（不写 GM 存储，跨页签不串）；
      // 可重试的「还没到时候」不进历史（否则每 20s 一行刷屏），最终结果才记。
      if (!result.retry) recordHistory(task, result)
      return result
    },
    onChange: () => {
      const rows = sched?.rows() ?? []
      panel?.setSchedule(rows)
      panel?.setStatus(scheduleStatusText(rows.length), 'ok')
      syncMarks()
    },
  })
  panel.setSchedule(sched.rows())
  sched.start()
  const markTimer = setInterval(syncMarks, 1000)
  const onVisibility = (): void => {
    void sched?.tick()
  }
  doc.addEventListener('visibilitychange', onVisibility)

  // 自动清空的「近 10s 有交互」闸：捕获阶段，不改页面行为。
  const touch = (): void => loop.lastInteractionAt(Date.now())
  const EVENTS = ['keydown', 'pointerdown', 'wheel', 'scroll'] as const
  for (const ev of EVENTS) win.addEventListener(ev, touch, { capture: true, passive: true })

  runtime.registerMenuCommand('Notebook: 保存并清空', () => {
    void loop.saveAndClear().then((r) => {
      op('menu:saveAndClear', r.ok ? `${r.bytes}B` : (r.reason ?? '保存未完成'))
      log(r.ok ? `已保存并清空（${r.bytes}B）` : `未清空：${r.reason ?? '保存未完成'}`)
    })
  })
  runtime.registerMenuCommand('Notebook: 打开日志', openLog)
  runtime.registerMenuCommand('Notebook: 复制配置', openConfig)

  loop.start()
  syncMarks()
  log(`single 启动（session=notebook:${loop.session} ready=${adapter.ready ? 'yes' : 'waiting'}）`)

  return () => {
    clearInterval(markTimer)
    if (offlineTimer !== null) clearInterval(offlineTimer)
    if (identityTimer !== null) clearInterval(identityTimer)
    hubSync?.cancel()
    doc.removeEventListener('visibilitychange', onVisibility)
    for (const ev of EVENTS) win.removeEventListener(ev, touch, { capture: true })
    sched?.stop()
    loop.stop()
    panel?.destroy()
    op('dispose')
    ops.stop()
  }
}
