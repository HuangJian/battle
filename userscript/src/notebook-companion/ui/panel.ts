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
  | 'config'
  | 'stopSession'

export type PanelHandlers = Partial<Record<PanelAction, () => void | Promise<unknown>>>

export interface Panel {
  readonly host: HTMLElement
  setStatus(text: string, tone?: 'ok' | 'warn' | 'error'): void
  /** 本页写的是哪一份日志（`notebook:<session>`；多开页签时只有它能把页面和日志对上）。 */
  setSession(text: string): void
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
}

interface ButtonSpec {
  act: PanelAction
  label: string
  title: string
}

/** 按功能分区：一行一组，组名在上；按钮顺序 = 使用频率。 */
const GROUPS: Array<{ label: string; buttons: ButtonSpec[] }> = [
  {
    label: '日志',
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
    ],
  },
  {
    label: '配置',
    buttons: [{ act: 'config', label: 'CFG', title: '复制当前配置到剪贴板' }],
  },
  {
    label: '会话',
    buttons: [{ act: 'stopSession', label: '停会话', title: '停止 Kaggle session（需二次确认）' }],
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
button[data-act="stopSession"] { background: #4a1f26; border-color: #7d3340; }
.sess { margin: 0 0 6px; color: #7f93ad; font-size: 10px; line-height: 1.3; word-break: break-all; }
.sess:empty { display: none; }
.status { margin-top: 6px; min-height: 16px; color: #9fb3cc; word-break: break-all; }
.status[data-tone="ok"] { color: #7fd6a2; }
.status[data-tone="warn"] { color: #e6c36a; }
.status[data-tone="error"] { color: #ff8d8d; }
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
        `<div class="group"><div class="glabel">${g.label}</div><div class="row">${g.buttons
          .map(
            (b) =>
              `<button type="button" data-act="${b.act}" title="${b.title}">${b.label}</button>`,
          )
          .join('')}</div></div>`,
    ).join('') +
    `<div class="status" data-tone="warn">连接中…</div></div>`

  const bnc = shadow.querySelector('.bnc') as HTMLElement
  const handle = shadow.querySelector('[data-drag]') as HTMLElement
  const status = shadow.querySelector('.status') as HTMLElement
  const sess = shadow.querySelector('[data-sess]') as HTMLElement
  let busy = false

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

  return {
    host,
    setStatus,
    setSession,
    setBusy(next: boolean) {
      busy = next
      setButtonsDisabled(next)
    },
    destroy() {
      handle.removeEventListener('pointerdown', onPointerDown)
      moveTarget.removeEventListener('pointermove', onPointerMove)
      moveTarget.removeEventListener('pointerup', endDrag)
      moveTarget.removeEventListener('pointercancel', endDrag)
      view?.removeEventListener('resize', onResize)
      host.remove()
    },
  }
}
