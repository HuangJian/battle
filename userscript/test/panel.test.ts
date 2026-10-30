import { afterEach, describe, expect, it } from 'bun:test'
import { loadDom, type DomHandle } from '../happydom'
import { mountPanel } from '../src/notebook-companion/ui/panel'

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

  it('忙态禁用全部按钮，handler 完成后恢复；异常写红字', async () => {
    const w = dom()
    let release: () => void = () => {}
    const panel = mountPanel(docOf(w), {
      flush: () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
      config: () => {
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

    ;(shadow.querySelector('[data-act="config"]') as HTMLElement).click()
    await new Promise((r) => setTimeout(r, 0))
    expect(shadow.querySelector('.status')!.textContent).toContain('boom')
    expect(shadow.querySelector('.status')!.getAttribute('data-tone')).toBe('error')
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
