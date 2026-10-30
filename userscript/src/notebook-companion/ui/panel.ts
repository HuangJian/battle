/**
 * panel.ts — host 侧面板（W3）。Shadow DOM 隔离 Kaggle 样式；按钮只是 `data-act`。
 *
 * 状态机在调用方（boot）：面板只负责「点哪个 → 调哪个 handler / 显示什么状态」。
 */
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
  setBusy(busy: boolean): void
  destroy(): void
}

const BUTTONS: Array<{ act: PanelAction; label: string; title: string }> = [
  {
    act: 'saveAndClear',
    label: '保存并清空',
    title: '排空所有输出 → 再清空 cell 输出（任一失败则不清空）',
  },
  { act: 'flush', label: '保存', title: '只排空，不清空' },
  { act: 'collapseAll', label: '折叠全部代码', title: 'notebook:hide-all-cell-code' },
  { act: 'expandAll', label: '展开全部代码', title: 'notebook:show-all-cell-code' },
  { act: 'collapseCurrent', label: '折叠当前', title: '当前选中 cell 的代码' },
  { act: 'expandCurrent', label: '展开当前', title: '当前选中 cell 的代码' },
  { act: 'scrollTop', label: '顶', title: '笔记本滚动到顶' },
  { act: 'scrollBottom', label: '底', title: '笔记本滚动到底' },
  { act: 'openLog', label: '日志', title: '打开 dashboard 日志页' },
  { act: 'config', label: 'CFG', title: '复制当前配置到剪贴板' },
  { act: 'stopSession', label: '停会话', title: '停止 Kaggle session（需二次确认）' },
]

const STYLE = `
:host { all: initial; }
.bnc {
  position: fixed; right: 16px; bottom: 16px; z-index: 2147483646;
  font: 12px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #e8eef7; background: rgba(16, 22, 32, .94);
  border: 1px solid rgba(120, 170, 255, .35); border-radius: 10px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, .45); padding: 8px; width: 292px;
}
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
.status { margin-top: 6px; min-height: 16px; color: #9fb3cc; word-break: break-all; }
.status[data-tone="ok"] { color: #7fd6a2; }
.status[data-tone="warn"] { color: #e6c36a; }
.status[data-tone="error"] { color: #ff8d8d; }
`

export function mountPanel(doc: Document, handlers: PanelHandlers): Panel {
  const host = doc.createElement('div')
  host.id = 'bnc-panel-host'
  doc.body.append(host)
  const shadow = host.attachShadow({ mode: 'open' })
  shadow.innerHTML =
    `<style>${STYLE}</style><div class="bnc" role="group" aria-label="Notebook Companion">` +
    `<div class="row">${BUTTONS.map(
      (b) => `<button type="button" data-act="${b.act}" title="${b.title}">${b.label}</button>`,
    ).join('')}</div><div class="status" data-tone="warn">连接中…</div></div>`

  const status = shadow.querySelector('.status') as HTMLElement
  let busy = false

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

  return {
    host,
    setStatus,
    setBusy(next: boolean) {
      busy = next
      setButtonsDisabled(next)
    },
    destroy() {
      host.remove()
    },
  }
}
