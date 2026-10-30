/**
 * panel.ts — host 侧面板（W3）。Shadow DOM 隔离 Kaggle 样式；按钮只是 `data-act`。
 *
 * 状态机在调用方（boot）：面板只负责「点哪个 → 调哪个 handler / 显示什么状态」。
 * 卡片可拖动（标题栏是手柄）：按钮区不参与拖拽，否则「拖一下」和「点一下」会打架。
 */
import type { Runtime } from '../../runtime'
import { clamp } from '../../utils'

export type PanelAction =
  | 'saveAndClear'
  | 'flush'
  | 'collapseAll'
  | 'expandAll'
  | 'collapseCurrent'
  | 'expandCurrent'
  | 'scrollTop'
  | 'scrollBottom'
  | 'openLog'
  | 'jumpConfig'

export type PanelHandlers = Partial<Record<PanelAction, () => void | Promise<unknown>>>

export interface PanelScheduleRow {
  id: string
  text: string
  next: string
  enabled: boolean
}

export interface PanelScheduleInput {
  /** `HH:MM`（下一次；已过则明天）。只跑一次。 */
  at: string
  action: string
}

export interface PanelHistoryRow {
  /** `HH:MM:SS` */
  at: string
  /** 动作名（如「运行全部」） */
  text: string
  ok: boolean
  reason?: string
}

export interface Panel {
  readonly host: HTMLElement
  setStatus(text: string, tone?: 'ok' | 'warn' | 'error'): void
  /** 本页写的是哪一份日志（`notebook:<session>`；多开页签时只有它能把页面和日志对上）。 */
  setSession(text: string): void
  /** 定时任务列表（存储与调度在 app/scheduler，面板只负责上屏）。 */
  setSchedule(rows: PanelScheduleRow[]): void
  /** 本次页面内的执行历史（内存；刷新即清空）。 */
  setHistory(rows: PanelHistoryRow[]): void
  /** 到点倒数 + 「取消」；返回 false = 用户取消（面板销毁也算取消）。 */
  countdown(text: string, seconds: number): Promise<boolean>
  setBusy(busy: boolean): void
  destroy(): void
}

export interface PanelPosition {
  left: number
  top: number
}

export const PANEL_POS_KEY = 'bnc:panelPos'

/** 存储值 → 位置；形状/数值非法一律 null（回默认右下角，绝不抛）。 */
export function parsePanelPosition(raw: unknown): PanelPosition | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const { left, top } = raw as Record<string, unknown>
  if (typeof left !== 'number' || typeof top !== 'number') return null
  if (!Number.isFinite(left) || !Number.isFinite(top)) return null
  return { left, top }
}

/** 从 GM 存储读上次拖到的位置（读本身也不抛）。 */
export function readPanelPosition(runtime: Runtime): PanelPosition | null {
  return parsePanelPosition(runtime.getValue(PANEL_POS_KEY, null))
}

export interface PanelOptions {
  /** 上次拖到的位置；挂载时会重新夹进当前视口。 */
  position?: PanelPosition | null
  /** 拖拽结束（真的移动过）回报**夹取后**的坐标，调用方负责落存储。 */
  onMove?: (position: PanelPosition) => void
  /** 添加定时任务：返回错误文案（null/void = 成功）。run-cell 的目标由调用方捕获。 */
  onScheduleAdd?: (
    input: PanelScheduleInput,
  ) => string | null | void | Promise<string | null | void>
  onScheduleRemove?: (id: string) => void
  onScheduleToggle?: (id: string, enabled: boolean) => void
}

interface ButtonSpec {
  act: PanelAction
  label: string
  title: string
}

/** 按功能分区：一行一组，组名在上；按钮顺序 = 使用频率。首组不写组名（日志键已说明用途）。 */
const GROUPS: Array<{ label?: string; buttons: ButtonSpec[] }> = [
  {
    buttons: [
      {
        act: 'saveAndClear',
        label: '保存并清空',
        title: '排空所有输出 → 再清空 cell 输出（任一失败则不清空）',
      },
      { act: 'flush', label: '保存', title: '只排空，不清空' },
      { act: 'openLog', label: '打开日志', title: '打开 dashboard 日志页' },
    ],
  },
  {
    label: '折叠',
    buttons: [
      {
        act: 'collapseAll',
        label: '折叠全部',
        title: '每段保留顶部两行：代码 = 蓝、Markdown = 琥珀',
      },
      { act: 'expandAll', label: '展开全部', title: '代码 + Markdown 段全部展开' },
      { act: 'collapseCurrent', label: '折叠当前', title: '当前 cell 只留顶部两行' },
      { act: 'expandCurrent', label: '展开当前', title: '展开当前 cell' },
    ],
  },
  {
    label: '跳转',
    buttons: [
      { act: 'scrollTop', label: '顶', title: '笔记本滚动到顶' },
      { act: 'scrollBottom', label: '底', title: '笔记本滚动到底' },
      { act: 'jumpConfig', label: 'CFG', title: '跳转并聚焦到代码里的 CFG 行（不复制）' },
    ],
  },
]

const DRAG_MARGIN = 6

/** 视口内夹取：拖出边界时留一条可抓的边，窗口缩小时也不会把面板藏到屏幕外。 */
function clampToViewport(
  panelEl: HTMLElement,
  view: Window | null,
  left: number,
  top: number,
): { left: number; top: number } {
  const vw = view?.innerWidth ?? 0
  const vh = view?.innerHeight ?? 0
  if (vw <= 0 || vh <= 0) return { left, top }
  return {
    left: clamp(left, DRAG_MARGIN, Math.max(DRAG_MARGIN, vw - panelEl.offsetWidth - DRAG_MARGIN)),
    top: clamp(top, DRAG_MARGIN, Math.max(DRAG_MARGIN, vh - panelEl.offsetHeight - DRAG_MARGIN)),
  }
}

const STYLE = `
:host { all: initial; }
.bnc {
  position: fixed; right: 16px; bottom: 16px; z-index: 2147483646;
  font: 12px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #e8eef7; background: rgba(16, 22, 32, .94);
  border: 1px solid rgba(120, 170, 255, .35); border-radius: 10px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, .45); padding: 8px; width: 292px;
}
.bnc[data-dragging="1"] { box-shadow: 0 12px 34px rgba(0, 0, 0, .6); user-select: none; }
.hd {
  display: flex; align-items: center; gap: 6px; margin: -2px -2px 6px;
  padding: 2px; cursor: move; touch-action: none; user-select: none;
  color: #9fb3cc; font-size: 11px; letter-spacing: .02em;
}
.bnc[data-dragging="1"] .hd { cursor: grabbing; }
.hd .grip { opacity: .55; }
.hd .title { flex: 1 1 auto; }
.hd .hint { opacity: .45; }
.group { margin-top: 8px; }
.group:first-of-type { margin-top: 0; }
.glabel { color: #7f93ad; font-size: 10px; letter-spacing: .08em; margin-bottom: 3px; }
.row { display: flex; flex-wrap: wrap; gap: 4px; }
button {
  flex: 1 1 auto; min-width: 56px; padding: 5px 7px; cursor: pointer;
  color: #dbe7ff; background: #1b2738; border: 1px solid #2d4a70; border-radius: 6px;
  font: inherit;
}
button:hover { background: #24354b; }
button:disabled { opacity: .45; cursor: not-allowed; }
button[data-act="saveAndClear"] { background: #1e5a3a; border-color: #2f8a58; flex-basis: 100px; }
.sess { margin: 0 0 6px; color: #7f93ad; font-size: 10px; line-height: 1.3; word-break: break-all; }
.sess:empty { display: none; }
.status { margin-top: 6px; min-height: 16px; color: #9fb3cc; word-break: break-all; }
.status[data-tone="ok"] { color: #7fd6a2; }
.status[data-tone="warn"] { color: #e6c36a; }
.status[data-tone="error"] { color: #ff8d8d; }
.srow { display: flex; gap: 4px; margin-bottom: 4px; }
.srow input[data-sched="at"], .srow select {
  padding: 3px 4px; color: #dbe7ff;
  background: #1b2738; border: 1px solid #2d4a70; border-radius: 6px; font: inherit;
}
/* 只要 HH:MM：定宽居中，别把动作下拉挤到下一行 */
.srow input[data-sched="at"] {
  flex: 0 0 auto; box-sizing: border-box; width: 60px; min-width: 0; text-align: center;
}
.srow select { flex: 1 1 auto; min-width: 0; }
.srow button { flex: 0 0 auto; min-width: 48px; }
.sack { display: flex; gap: 4px; align-items: center; color: #e6c36a; font-size: 10px; margin-bottom: 4px; }
.sack[hidden] { display: none; }
.slist { display: flex; flex-direction: column; gap: 3px; }
.sitem { display: flex; gap: 4px; align-items: center; font-size: 11px; }
.sitem .stext { flex: 1 1 auto; word-break: break-all; }
.sitem .snext { color: #7f93ad; font-size: 10px; white-space: nowrap; }
.sitem button { flex: 0 0 auto; min-width: 0; padding: 1px 5px; }
.snote { margin-top: 3px; color: #7f93ad; font-size: 10px; }
.hist { margin-top: 6px; padding-top: 4px; border-top: 1px solid rgba(120, 170, 255, .18); }
.hist[hidden] { display: none; }
.hist .hlabel { color: #7f93ad; font-size: 10px; letter-spacing: .08em; margin-bottom: 3px; }
.hitem { display: flex; flex-wrap: wrap; gap: 4px; align-items: baseline; font-size: 11px; }
.hitem .hat { color: #7f93ad; font-size: 10px; white-space: nowrap; }
.hitem[data-ok="0"] { color: #ff8d8d; }
.hitem .hwhy { color: #7f93ad; font-size: 10px; word-break: break-all; }
.ask {
  display: flex; gap: 6px; align-items: center; margin-top: 6px; padding: 4px 6px;
  border: 1px solid #7d6b33; border-radius: 6px; background: rgba(125, 107, 51, .18);
  color: #e6c36a; font-size: 11px;
}
.ask[hidden] { display: none; }
.ask span { flex: 1 1 auto; }
.ask button { flex: 0 0 auto; min-width: 44px; }
`

export function mountPanel(
  doc: Document,
  handlers: PanelHandlers,
  options: PanelOptions = {},
): Panel {
  const host = doc.createElement('div')
  host.id = 'bnc-panel-host'
  doc.body.append(host)
  const shadow = host.attachShadow({ mode: 'open' })
  shadow.innerHTML =
    `<style>${STYLE}</style><div class="bnc" role="group" aria-label="Notebook Companion">` +
    `<div class="hd" data-drag title="拖动移动面板"><span class="grip">⠿</span>` +
    `<span class="title">Notebook Companion</span><span class="hint">拖动</span></div>` +
    `<div class="sess" data-sess></div>` +
    GROUPS.map(
      (g) =>
        `<div class="group">${g.label ? `<div class="glabel">${g.label}</div>` : ''}<div class="row">${g.buttons
          .map(
            (b) =>
              `<button type="button" data-act="${b.act}" title="${b.title}">${b.label}</button>`,
          )
          .join('')}</div></div>`,
    ).join('') +
    `<div class="group"><div class="glabel">定时（一次性 · <span data-tz></span> · 页面开着才跑）</div>` +
    `<div class="srow"><input type="text" data-sched="at" value="09:00" maxlength="5" inputmode="numeric"` +
    ` title="24 小时制 HH:MM；早于现在就是明天" placeholder="HH:MM">` +
    `<select data-sched="action" title="到点做什么">` +
    `<option value="run-cell">运行选中的 cell</option><option value="run-all">运行全部</option>` +
    `<option value="stop-session">停会话</option><option value="interrupt">中断内核</option></select></div>` +
    `<div class="srow"><button type="button" data-sched="add">设为下一次</button></div>` +
    `<label class="sack" data-ack hidden><input type="checkbox" data-sched="ack">` +
    ` 我确认到点会停止会话</label>` +
    `<div class="slist" data-sched="list"></div>` +
    `<div class="hist" data-hist hidden></div>` +
    `<div class="snote">只跑一次（执行后自动移除）；24 小时制，早于现在 = 明天；` +
    `运行 cell = 添加时选中的那一段（内容变了不跑）。</div></div>` +
    `<div class="status" data-tone="warn">连接中…</div>` +
    `<div class="ask" data-ask hidden><span data-ask-text></span>` +
    `<button type="button" data-ask-cancel>取消</button></div></div>`

  const bnc = shadow.querySelector('.bnc') as HTMLElement
  const handle = shadow.querySelector('[data-drag]') as HTMLElement
  const status = shadow.querySelector('.status') as HTMLElement
  const sess = shadow.querySelector('[data-sess]') as HTMLElement
  const tzEl = shadow.querySelector('[data-tz]') as HTMLElement
  const schedAt = shadow.querySelector('[data-sched="at"]') as HTMLInputElement
  const schedAction = shadow.querySelector('[data-sched="action"]') as HTMLSelectElement
  const schedAckWrap = shadow.querySelector('[data-ack]') as HTMLElement
  const schedAck = shadow.querySelector('[data-sched="ack"]') as HTMLInputElement
  const schedAdd = shadow.querySelector('[data-sched="add"]') as HTMLElement
  const schedList = shadow.querySelector('[data-sched="list"]') as HTMLElement
  const hist = shadow.querySelector('[data-hist]') as HTMLElement
  const ask = shadow.querySelector('[data-ask]') as HTMLElement
  const askText = shadow.querySelector('[data-ask-text]') as HTMLElement
  const askCancel = shadow.querySelector('[data-ask-cancel]') as HTMLElement
  let askFinish: ((ok: boolean) => void) | null = null
  let busy = false

  // 本地时区（任务用墙钟，不存 UTC）：显示出来，操作员不用猜
  const offsetMin = -new Date().getTimezoneOffset()
  const offSign = offsetMin >= 0 ? '+' : '-'
  const offAbs = Math.abs(offsetMin)
  tzEl.textContent = `UTC${offSign}${String(Math.floor(offAbs / 60)).padStart(2, '0')}:${String(
    offAbs % 60,
  ).padStart(2, '0')}`

  // 拖拽只在标题栏起手；move/up 挂在 document 上（配合指针捕获），指针移出面板也能继续拖。
  const view = doc.defaultView
  const moveTarget = doc as EventTarget
  let drag: { pointerId: number | undefined; dx: number; dy: number } | null = null
  let current: PanelPosition | null = null
  let pinned = false
  let moved = false

  function placeAt(left: number, top: number): void {
    const box = clampToViewport(bnc, view, left, top)
    bnc.style.left = `${box.left}px`
    bnc.style.top = `${box.top}px`
    bnc.style.right = 'auto'
    bnc.style.bottom = 'auto'
    current = box
    pinned = true
  }

  function onPointerDown(e: Event): void {
    const ev = e as PointerEvent
    if (ev.button !== 0) return
    const rect = bnc.getBoundingClientRect()
    // 已经有坐标（恢复的位置或上一次拖拽）就直接用：happy-dom 无布局、真实页面也可能有小数
    const origin = current ?? { left: rect.left, top: rect.top }
    drag = { pointerId: ev.pointerId, dx: ev.clientX - origin.left, dy: ev.clientY - origin.top }
    placeAt(origin.left, origin.top) // right/bottom 锚点切成 left/top（位置不变）
    moved = false
    bnc.setAttribute('data-dragging', '1')
    if (typeof ev.pointerId === 'number') handle.setPointerCapture?.(ev.pointerId)
    ev.preventDefault?.()
  }

  function onPointerMove(e: Event): void {
    if (!drag) return
    const ev = e as PointerEvent
    if (drag.pointerId !== undefined && ev.pointerId !== undefined) {
      if (ev.pointerId !== drag.pointerId) return
    }
    moved = true
    placeAt(ev.clientX - drag.dx, ev.clientY - drag.dy)
  }

  function endDrag(): void {
    if (!drag) return
    if (typeof drag.pointerId === 'number') handle.releasePointerCapture?.(drag.pointerId)
    drag = null
    bnc.removeAttribute('data-dragging')
    // 单点标题栏（没移动）不写存储，避免每点一下都碰 GM 存储
    if (moved && current) options.onMove?.(current)
  }

  function onResize(): void {
    if (!pinned) return
    const rect = bnc.getBoundingClientRect()
    placeAt(rect.left, rect.top)
  }

  // 恢复上次拖到的位置：先夹进当前视口，再进正常拖拽状态（resize 也会继续盯住）
  if (options.position) placeAt(options.position.left, options.position.top)

  handle.addEventListener('pointerdown', onPointerDown)
  moveTarget.addEventListener('pointermove', onPointerMove)
  moveTarget.addEventListener('pointerup', endDrag)
  moveTarget.addEventListener('pointercancel', endDrag)
  view?.addEventListener('resize', onResize)

  shadow.addEventListener('click', (e) => {
    if (busy) return
    const target = e.target as unknown as Element | null
    if (!target || typeof target.closest !== 'function') return
    const btn = target.closest('[data-act]') as HTMLElement | null
    if (!btn || btn.hasAttribute('disabled')) return
    const act = btn.getAttribute('data-act') as PanelAction | null
    const fn = act ? handlers[act] : undefined
    if (!fn) {
      setStatus(`未接线：${act ?? '?'}`, 'warn')
      return
    }
    busy = true
    setButtonsDisabled(true)
    let ret: void | Promise<unknown>
    try {
      ret = fn() // 同步调用：按钮点击的副作用当拍发生（返回 Promise 时才等）
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err), 'error')
      busy = false
      setButtonsDisabled(false)
      return
    }
    void Promise.resolve(ret)
      .catch((err: unknown) => setStatus(err instanceof Error ? err.message : String(err), 'error'))
      .finally(() => {
        busy = false
        setButtonsDisabled(false)
      })
  })

  function setButtonsDisabled(disabled: boolean): void {
    for (const b of shadow.querySelectorAll('button')) {
      if (disabled) b.setAttribute('disabled', '')
      else b.removeAttribute('disabled')
    }
  }

  function setStatus(text: string, tone: 'ok' | 'warn' | 'error' = 'warn'): void {
    status.textContent = text
    status.setAttribute('data-tone', tone)
  }

  function setSession(text: string): void {
    sess.textContent = text ? `日志 ${text}` : ''
    sess.title = text
  }

  function setSchedule(rows: PanelScheduleRow[]): void {
    schedList.textContent = ''
    for (const row of rows) {
      const item = doc.createElement('div')
      item.className = 'sitem'
      const cb = doc.createElement('input')
      cb.type = 'checkbox'
      cb.checked = row.enabled
      cb.title = row.enabled ? '停用（到点不再执行）' : '启用'
      cb.addEventListener('change', () => options.onScheduleToggle?.(row.id, cb.checked))
      const text = doc.createElement('span')
      text.className = 'stext'
      text.textContent = row.text
      const next = doc.createElement('span')
      next.className = 'snext'
      next.textContent = row.next
      const del = doc.createElement('button')
      del.type = 'button'
      del.textContent = '✕'
      del.title = '删除这个定时任务'
      del.addEventListener('click', () => options.onScheduleRemove?.(row.id))
      item.append(cb, text, next, del)
      schedList.append(item)
    }
    if (rows.length === 0) {
      const empty = doc.createElement('div')
      empty.className = 'snote'
      empty.textContent = '没有定时任务'
      schedList.append(empty)
    }
  }

  /** 执行历史：只在内存里（刷新页面即清空），条数由调用方控制。 */
  function setHistory(rows: PanelHistoryRow[]): void {
    hist.textContent = ''
    hist.hidden = rows.length === 0
    if (rows.length === 0) return
    const label = doc.createElement('div')
    label.className = 'hlabel'
    label.textContent = '执行历史（本页）'
    hist.append(label)
    for (const row of rows) {
      const item = doc.createElement('div')
      item.className = 'hitem'
      item.setAttribute('data-ok', row.ok ? '1' : '0')
      const at = doc.createElement('span')
      at.className = 'hat'
      at.textContent = row.at
      const text = doc.createElement('span')
      text.textContent = `${row.text}${row.ok ? ' ✓' : ' ✗'}`
      item.append(at, text)
      if (!row.ok && row.reason) {
        const why = doc.createElement('span')
        why.className = 'hwhy'
        why.textContent = row.reason
        item.append(why)
      }
      hist.append(item)
    }
  }

  /** 到点倒数 + 取消：`seconds<=0` 立即放行（测试与「隐藏页预授权」都走这条）。 */
  function countdown(text: string, seconds: number): Promise<boolean> {
    askFinish?.(false) // 同拍只留一个倒计时（新的覆盖旧的）
    ask.hidden = false
    let left = Math.max(0, Math.floor(seconds))
    askText.textContent = left > 0 ? `${text}（${left}s）` : text
    return new Promise<boolean>((resolve) => {
      let timer: ReturnType<typeof setInterval> | null = null
      const finish = (ok: boolean): void => {
        if (askFinish !== finish) return
        askFinish = null
        if (timer !== null) clearInterval(timer)
        ask.hidden = true
        askCancel.removeEventListener('click', onCancel)
        resolve(ok)
      }
      const onCancel = (): void => finish(false)
      askFinish = finish
      askCancel.addEventListener('click', onCancel)
      if (left <= 0) {
        finish(true)
        return
      }
      timer = setInterval(() => {
        left -= 1
        if (left <= 0) {
          finish(true)
          return
        }
        askText.textContent = `${text}（${left}s）`
      }, 1000)
    })
  }

  schedAction.addEventListener('change', () => {
    schedAckWrap.hidden = schedAction.value !== 'stop-session'
  })
  schedAdd.addEventListener('click', () => {
    const input: PanelScheduleInput = {
      at: schedAt.value,
      action: schedAction.value,
    }
    if (input.action === 'stop-session' && !schedAck.checked) {
      setStatus('先勾选「我确认到点会停止会话」', 'error')
      return
    }
    const ret = options.onScheduleAdd?.(input)
    if (ret === undefined) {
      setStatus('定时未接线', 'warn')
      return
    }
    void Promise.resolve(ret)
      .then((err) => {
        if (typeof err === 'string' && err) setStatus(err, 'error')
        else setStatus(`已添加定时：${input.at}`, 'ok')
      })
      .catch((e: unknown) => setStatus(e instanceof Error ? e.message : String(e), 'error'))
  })

  return {
    host,
    setStatus,
    setSession,
    setSchedule,
    setHistory,
    countdown,
    setBusy(next: boolean) {
      busy = next
      setButtonsDisabled(next)
    },
    destroy() {
      askFinish?.(false) // 销毁时把待确认的倒计时按「取消」收口（不留下永不 resolve 的 Promise）
      handle.removeEventListener('pointerdown', onPointerDown)
      moveTarget.removeEventListener('pointermove', onPointerMove)
      moveTarget.removeEventListener('pointerup', endDrag)
      moveTarget.removeEventListener('pointercancel', endDrag)
      view?.removeEventListener('resize', onResize)
      host.remove()
    },
  }
}
