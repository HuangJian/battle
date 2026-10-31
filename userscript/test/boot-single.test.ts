import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { boot, type BootResult } from '../src/notebook-companion/app/boot'

/**
 * `single` 角色的接线冒烟（Colab）：面板挂上、命令本地分发、卸载干净。
 * 夹具：真实 Colab 页面存档抽出的 `#top-toolbar` + `#notebook-main`（见文件头注释）。
 */
const FIXTURE = readFileSync(join(import.meta.dir, 'fixtures/colab.html'), 'utf8')
const HREF = 'https://colab.research.google.com/drive/1VUDX_hyHVvzc2kQxrAOSgsPvzwcuXD7n'

const opened: DomHandle[] = []
const booted: BootResult[] = []

afterEach(() => {
  for (const b of booted.splice(0)) b.dispose?.()
  for (const w of opened.splice(0)) w.close()
})

/** 最小 GM 作用域：请求 200、存储走内存（面板/调度器都要经过它）。 */
function scopeWithGm(): Record<string, unknown> {
  const mem = new Map<string, unknown>()
  return {
    GM_xmlhttpRequest: async () => ({
      status: 200,
      statusText: 'OK',
      responseText: JSON.stringify({ ok: true, epoch: 1 }),
      responseHeaders: '',
    }),
    GM_getValue: (k: string, fb: unknown) => mem.get(k) ?? fb,
    GM_setValue: (k: string, v: unknown) => mem.set(k, v),
    GM_deleteValue: (k: string) => mem.delete(k),
    GM_registerMenuCommand: () => {},
    GM_setClipboard: () => {},
  } as unknown as Record<string, unknown>
}

function start(): { w: DomHandle; result: BootResult } {
  const w = loadDom(FIXTURE, HREF)
  opened.push(w)
  const result = boot(scopeWithGm(), HREF, { doc: w.document as unknown as Document })
  if (!result) throw new Error('boot 返回 null（角色没识别）')
  booted.push(result)
  return { w, result }
}

const shadowHost = (w: DomHandle): HTMLElement | null =>
  w.document.getElementById('bnc-panel-host') as HTMLElement | null

describe('boot：Colab 走 single 角色', () => {
  it('识别角色、挂面板、写出本页日志键；dispose 后面板消失', async () => {
    const { w, result } = start()
    expect(result.role).toBe('single')
    expect(result.hasRuntime).toBe(true)

    const host = shadowHost(w)
    expect(host).not.toBeNull()
    const shadow = host!.shadowRoot!
    // 日志键 = colab-<fileId> + 本页签标签（多开页签各写各的日志）
    expect(shadow.querySelector('[data-sess]')?.textContent).toMatch(
      /^日志 notebook:colab-1vudx_hyhvvzc2kqxraosgspvzwcuxd7n(-[a-z0-9]{4,8})?$/,
    )
    // 状态栏有话（初值由面板/调度器写，这里只保证不是空壳）
    expect((shadow.querySelector('.status')?.textContent ?? '').length).toBeGreaterThan(0)

    result.dispose?.()
    expect(shadowHost(w)).toBeNull()
  })

  it('写者身份跨刷新稳定：同一页签重载沿用同一个 writer（否则会被自己的旧租约挡 3 分钟）', () => {
    const first = start()
    const w1 = first.w
    const tag1 = w1.sessionStorage.getItem('bnc:writer:colab')
    expect(tag1).toMatch(/^colab-[a-z0-9]{4,8}$/)
    first.result.dispose?.()

    // 「刷新」= 新 Window + 沿用 sessionStorage（浏览器行为）
    const w2 = loadDom(FIXTURE, HREF)
    opened.push(w2)
    for (const k of ['bnc:pageTag', 'bnc:writer:colab']) {
      const v = w1.sessionStorage.getItem(k)
      if (v !== null) w2.sessionStorage.setItem(k, v)
    }
    const r2 = boot(scopeWithGm(), HREF, { doc: w2.document as unknown as Document })
    if (!r2) throw new Error('boot 返回 null（角色没识别）')
    booted.push(r2)
    expect(w2.sessionStorage.getItem('bnc:writer:colab')).toBe(tag1)

    // 另一个页签（存储不继承）⇒ 必须是另一个 writer，不能互抢租约
    const { w: w3 } = start()
    expect(w3.sessionStorage.getItem('bnc:writer:colab')).not.toBe(tag1)
  })

  it('面板按钮走本地命令分发（状态栏出现命令名与 ✓/✗）', async () => {
    const { w, result } = start()
    const shadow = shadowHost(w)!.shadowRoot!
    const btn = shadow.querySelector('[data-act="scrollTop"]') as HTMLElement
    expect(btn).not.toBeNull()
    btn.click()
    await new Promise((r) => setTimeout(r, 20))
    expect(shadow.querySelector('.status')?.textContent).toMatch(/scrollTop [✓✗]/)
    result.dispose?.()
  })
})
