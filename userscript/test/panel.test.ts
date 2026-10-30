import { afterEach, describe, expect, it } from 'bun:test'
import { loadDom, type DomHandle } from '../happydom'
import type { Runtime } from '../src/runtime'
import {
  mountPanel,
  parsePanelPosition,
  readPanelPosition,
  type PanelPosition,
} from '../src/notebook-companion/ui/panel'

const opened: DomHandle[] = []
function dom(): DomHandle {
  const w = loadDom('<html><body></body></html>')
  opened.push(w)
  return w
}
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const docOf = (w: DomHandle): Document => w.document as unknown as Document

/** happy-dom 有 PointerEvent 就用它，没有就退回 MouseEvent（拖拽只读坐标/按键/pointerId）。 */
function pointer(w: DomHandle, type: string, init: Record<string, unknown>): Event {
  const Ctor = (w as unknown as { PointerEvent?: new (t: string, i: unknown) => Event })
    .PointerEvent
  if (Ctor) return new Ctor(type, init)
  // happy-dom 的 MouseEventInit 与 DOM 类型不同名（view 不容 null）⇒ 逐字段定义，不硬转类型。
  const ev = new w.MouseEvent(type)
  for (const [key, value] of Object.entries(init)) {
    Object.defineProperty(ev, key, { value, configurable: true })
  }
  return ev as unknown as Event
}

describe('面板：按钮 → handler，状态/忙态可见', () => {
  it('每个 data-act 都接到同名 handler；未知 action 不静默', async () => {
    const w = dom()
    const calls: string[] = []
    const panel = mountPanel(docOf(w), {
      saveAndClear: () => {
        calls.push('saveAndClear')
      },
      scrollTop: () => {
        calls.push('scrollTop')
      },
    })
    const shadow = panel.host.shadowRoot!
    for (const act of ['saveAndClear', 'scrollTop']) {
      ;(shadow.querySelector(`[data-act="${act}"]`) as HTMLElement).click()
      await new Promise((r) => setTimeout(r, 0))
    }
    expect(calls).toEqual(['saveAndClear', 'scrollTop'])
    panel.destroy()
    expect(w.document.querySelector('#bnc-panel-host')).toBeNull()
  })

  it('setSession：面板上能看见本页写哪份日志（多开页签时靠它认页面）', () => {
    const w = dom()
    const panel = mountPanel(docOf(w), {})
    const sess = panel.host.shadowRoot!.querySelector('[data-sess]') as HTMLElement
    expect(sess.textContent).toBe('') // 未连上 frame 之前不编名字
    panel.setSession('notebook:kaggle-a-b-edit-ab12cd')
    expect(sess.textContent).toBe('日志 notebook:kaggle-a-b-edit-ab12cd')
    expect(sess.title).toBe('notebook:kaggle-a-b-edit-ab12cd')
    panel.destroy()
  })

  it('setSchedule：行上屏；勾选/删除回调带 id', () => {
    const w = dom()
    const removed: string[] = []
    const toggled: Array<[string, boolean]> = []
    const panel = mountPanel(
      docOf(w),
      {},
      {
        onScheduleRemove: (id) => removed.push(id),
        onScheduleToggle: (id, enabled) => toggled.push([id, enabled]),
      },
    )
    const shadow = panel.host.shadowRoot!
    panel.setSchedule([])
    expect(shadow.querySelector('.slist')!.textContent).toContain('没有定时任务')
    panel.setSchedule([
      { id: 't1', text: '09:00 每天 · 运行全部', next: '今天 09:00', enabled: true },
    ])
    const item = shadow.querySelector('.sitem')!
    expect(item.textContent).toContain('09:00 每天 · 运行全部')
    expect(item.textContent).toContain('今天 09:00')
    const cb = item.querySelector('input') as HTMLInputElement
    cb.checked = false
    cb.dispatchEvent(new w.Event('change') as unknown as Event)
    expect(toggled).toEqual([['t1', false]])
    ;(item.querySelector('button') as HTMLButtonElement).click()
    expect(removed).toEqual(['t1'])
    panel.destroy()
  })

  it('setHistory：执行历史上屏（成功/失败+原因），清空后隐藏', () => {
    const panel = mountPanel(docOf(dom()), {})
    const shadow = panel.host.shadowRoot!
    const hist = shadow.querySelector('[data-hist]') as HTMLElement
    expect(hist.hidden).toBe(true)
    panel.setHistory([
      { at: '22:45:03', text: '运行全部', ok: true },
      { at: '22:46:10', text: '停会话', ok: false, reason: '找不到 Stop session 按钮' },
    ])
    expect(hist.hidden).toBe(false)
    expect(hist.textContent).toContain('执行历史')
    expect(hist.textContent).toContain('22:45:03')
    expect(hist.textContent).toContain('运行全部 ✓')
    expect(hist.textContent).toContain('停会话 ✗')
    expect(hist.textContent).toContain('找不到 Stop session 按钮')
    expect(hist.querySelector('.hitem[data-ok="0"]')).not.toBeNull()
    panel.setHistory([])
    expect(hist.hidden).toBe(true)
    expect(hist.textContent).toBe('')
    panel.destroy()
  })

  it('countdown：0 秒立即放行；点「取消」⇒ false', async () => {
    const w = dom()
    const panel = mountPanel(docOf(w), {})
    const shadow = panel.host.shadowRoot!
    expect(await panel.countdown('x', 0)).toBe(true)
    const pending = panel.countdown('即将停会话', 30)
    ;(shadow.querySelector('[data-ask-cancel]') as HTMLButtonElement).click()
    expect(await pending).toBe(false)
    panel.destroy()
  })

  it('添加定时：stop-session 未勾确认 ⇒ 红字不回调；勾了才回调', () => {
    const w = dom()
    const calls: Array<{ at: string; action: string }> = []
    const panel = mountPanel(
      docOf(w),
      {},
      {
        onScheduleAdd: (input) => {
          calls.push(input)
          return null
        },
      },
    )
    const shadow = panel.host.shadowRoot!
    const action = shadow.querySelector('[data-sched="action"]') as HTMLSelectElement
    action.value = 'stop-session'
    action.dispatchEvent(new w.Event('change') as unknown as Event)
    ;(shadow.querySelector('[data-sched="add"]') as HTMLButtonElement).click()
    expect(calls).toEqual([])
    expect(shadow.querySelector('.status')!.textContent).toContain('勾选')
    ;(shadow.querySelector('[data-sched="ack"]') as HTMLInputElement).checked = true
    ;(shadow.querySelector('[data-sched="add"]') as HTMLButtonElement).click()
    expect(calls).toEqual([{ at: '09:00', action: 'stop-session' }])
    panel.destroy()
  })

  it('忙态禁用全部按钮，handler 完成后恢复；异常写红字', async () => {
    const w = dom()
    let release: () => void = () => {}
    const panel = mountPanel(docOf(w), {
      flush: () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
      jumpConfig: () => {
        throw new Error('boom')
      },
    })
    const shadow = panel.host.shadowRoot!
    const flush = shadow.querySelector('[data-act="flush"]') as HTMLElement
    flush.click()
    expect(flush.hasAttribute('disabled')).toBe(true)
    release()
    await new Promise((r) => setTimeout(r, 0))
    expect(flush.hasAttribute('disabled')).toBe(false)

    ;(shadow.querySelector('[data-act="jumpConfig"]') as HTMLElement).click()
    await new Promise((r) => setTimeout(r, 0))
    expect(shadow.querySelector('.status')!.textContent).toContain('boom')
    expect(shadow.querySelector('.status')!.getAttribute('data-tone')).toBe('error')
    panel.destroy()
  })

  it('标题栏拖拽：改 left/top、夹在视口内、松手后不再跟手', () => {
    const w = dom()
    const panel = mountPanel(docOf(w), {})
    const shadow = panel.host.shadowRoot!
    const bnc = shadow.querySelector('.bnc') as HTMLElement
    const handle = shadow.querySelector('[data-drag]') as HTMLElement
    const doc = docOf(w) as unknown as EventTarget

    handle.dispatchEvent(
      pointer(w, 'pointerdown', {
        bubbles: true,
        button: 0,
        clientX: 400,
        clientY: 300,
        pointerId: 1,
      }),
    )
    // 锚点从 right/bottom 换成 left/top，视觉位置不变
    expect(bnc.style.right).toBe('auto')
    expect(bnc.style.bottom).toBe('auto')
    expect(bnc.getAttribute('data-dragging')).toBe('1')

    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: 500, clientY: 400, pointerId: 1 }))
    expect(bnc.style.left).toBe('100px')
    expect(bnc.style.top).toBe('100px')

    // 拖出视口 ⇒ 夹到边距（happy-dom 无布局，宽高按 0 算 ⇒ 上限 = 视口 - 6）
    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: 5000, clientY: 5000, pointerId: 1 }))
    expect(bnc.style.left).toBe('1018px')
    expect(bnc.style.top).toBe('762px')
    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: -5000, clientY: -5000, pointerId: 1 }))
    expect(bnc.style.left).toBe('6px')
    expect(bnc.style.top).toBe('6px')

    // 别的指针不干扰
    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: 300, clientY: 200, pointerId: 2 }))
    expect(bnc.style.left).toBe('6px')

    doc.dispatchEvent(pointer(w, 'pointerup', { pointerId: 1 }))
    expect(bnc.hasAttribute('data-dragging')).toBe(false)
    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: 300, clientY: 200, pointerId: 1 }))
    expect(bnc.style.left).toBe('6px')

    panel.destroy()
  })

  it('按钮按功能分区：首组无组名 / 折叠 / 跳转（顶 · 底 · CFG）+ 定时', () => {
    const panel = mountPanel(docOf(dom()), {})
    const shadow = panel.host.shadowRoot!
    const labels = [...shadow.querySelectorAll('.glabel')].map((el) => el.textContent)
    // 首组（保存/打开日志）不再有「日志」组标题；时区标签随时区变，只钉「有这一组」
    expect(labels.slice(0, 2)).toEqual(['折叠', '跳转'])
    expect(labels[2]).toContain('定时（一次性')
    const groups = [...shadow.querySelectorAll('.group')]
    expect(groups.map((g) => g.querySelectorAll('button').length)).toEqual([3, 4, 3, 1])
    expect(groups[0]!.querySelector('.glabel')).toBeNull()
    const jump = [...groups[2]!.querySelectorAll('[data-act]')].map((el) =>
      el.getAttribute('data-act'),
    )
    expect(jump).toEqual(['scrollTop', 'scrollBottom', 'jumpConfig'])
    expect(shadow.querySelectorAll('[data-act]').length).toBe(10)
    expect(shadow.querySelector('[data-act="stopSession"]')).toBeNull()
    panel.destroy()
  })

  it('setStatus 写文本与 tone（ok/warn/error）', () => {
    const panel = mountPanel(docOf(dom()), {})
    panel.setStatus('已连接', 'ok')
    const status = panel.host.shadowRoot!.querySelector('.status') as HTMLElement
    expect(status.textContent).toBe('已连接')
    expect(status.getAttribute('data-tone')).toBe('ok')
    panel.setBusy(true)
    expect(
      (panel.host.shadowRoot!.querySelector('[data-act="flush"]') as HTMLElement).hasAttribute(
        'disabled',
      ),
    ).toBe(true)
    panel.destroy()
  })
})

describe('面板位置持久化（GM 存储）', () => {
  it('parsePanelPosition：合法坐标才收，坏值一律 null', () => {
    expect(parsePanelPosition({ left: 12, top: 34 })).toEqual({ left: 12, top: 34 })
    const bad: unknown[] = [
      null,
      undefined,
      'x',
      [],
      { left: 1 },
      { left: '1', top: 2 },
      { left: Number.NaN, top: 0 },
      { left: 0, top: Number.POSITIVE_INFINITY },
    ]
    for (const raw of bad) expect(parsePanelPosition(raw)).toBeNull()
  })

  it('readPanelPosition：读 GM 存储；没有 / 坏值 ⇒ null', () => {
    const stored = { getValue: () => ({ left: 7, top: 9 }) } as unknown as Runtime
    expect(readPanelPosition(stored)).toEqual({ left: 7, top: 9 })
    const empty = {
      getValue: (_key: string, fallback: unknown) => fallback,
    } as unknown as Runtime
    expect(readPanelPosition(empty)).toBeNull()
  })

  it('挂载时恢复上次位置；超出视口 ⇒ 夹回可见区', () => {
    const w = dom()
    const panel = mountPanel(docOf(w), {}, { position: { left: 200, top: 150 } })
    const bnc = panel.host.shadowRoot!.querySelector('.bnc') as HTMLElement
    expect(bnc.style.left).toBe('200px')
    expect(bnc.style.top).toBe('150px')
    expect(bnc.style.right).toBe('auto')
    expect(bnc.style.bottom).toBe('auto')
    panel.destroy()

    const panel2 = mountPanel(docOf(dom()), {}, { position: { left: 99_999, top: -50 } })
    const bnc2 = panel2.host.shadowRoot!.querySelector('.bnc') as HTMLElement
    expect(bnc2.style.left).toBe('1018px')
    expect(bnc2.style.top).toBe('6px')
    panel2.destroy()
  })

  it('拖拽结束才写回夹取后的坐标；只点标题栏不写', () => {
    const w = dom()
    const saved: PanelPosition[] = []
    const panel = mountPanel(
      docOf(w),
      {},
      { position: { left: 200, top: 150 }, onMove: (p) => saved.push(p) },
    )
    const shadow = panel.host.shadowRoot!
    const handle = shadow.querySelector('[data-drag]') as HTMLElement
    const doc = docOf(w) as unknown as EventTarget

    // 点一下标题栏（没移动）→ 不写存储
    handle.dispatchEvent(
      pointer(w, 'pointerdown', {
        bubbles: true,
        button: 0,
        clientX: 300,
        clientY: 250,
        pointerId: 1,
      }),
    )
    doc.dispatchEvent(pointer(w, 'pointerup', { pointerId: 1 }))
    expect(saved).toEqual([])

    handle.dispatchEvent(
      pointer(w, 'pointerdown', {
        bubbles: true,
        button: 0,
        clientX: 300,
        clientY: 250,
        pointerId: 1,
      }),
    )
    doc.dispatchEvent(pointer(w, 'pointermove', { clientX: 350, clientY: 300, pointerId: 1 }))
    doc.dispatchEvent(pointer(w, 'pointerup', { pointerId: 1 }))
    expect(saved).toEqual([{ left: 250, top: 200 }])
    panel.destroy()
  })
})
