import { describe, expect, it } from 'bun:test'
import { createRuntime } from '../src/runtime'

describe('runtime', () => {
  it('缺 GM_xmlhttpRequest ⇒ null（页面脚本环境不假装可用）', () => {
    expect(createRuntime({})).toBeNull()
    expect(createRuntime({ GM_getValue: () => 1 })).toBeNull()
  })

  it('把 GM_* 收进 Runtime：请求 / 存储 / 菜单 / 剪贴板', async () => {
    const calls: Array<Record<string, unknown>> = []
    const store = new Map<string, unknown>()
    let menu = ''
    let clip = ''
    const runtime = createRuntime({
      GM_xmlhttpRequest: (details: Record<string, unknown>) => {
        calls.push(details)
        const onload = details.onload as ((r: unknown) => void) | undefined
        onload?.({
          status: 200,
          statusText: 'OK',
          responseText: '{"ok":true}',
          responseHeaders: '',
        })
      },
      GM_getValue: (key: string, fallback: unknown) => (store.has(key) ? store.get(key) : fallback),
      GM_setValue: (key: string, value: unknown) => void store.set(key, value),
      GM_deleteValue: (key: string) => void store.delete(key),
      GM_registerMenuCommand: (label: string, fn: () => void) => {
        menu = label
        fn()
      },
      GM_setClipboard: (text: string) => {
        clip = text
      },
    })
    expect(runtime).not.toBeNull()

    const res = await runtime!.gmRequest({ url: 'http://127.0.0.1:8900/api/state' })
    expect(res.status).toBe(200)
    expect(calls[0]?.url).toBe('http://127.0.0.1:8900/api/state')

    runtime!.setValue('bnc.x', 1)
    expect(runtime!.getValue('bnc.x', 0)).toBe(1)
    runtime!.deleteValue('bnc.x')
    expect(runtime!.getValue('bnc.x', 7)).toBe(7)

    runtime!.registerMenuCommand('探测', () => {
      menu += '!'
    })
    expect(menu).toBe('探测!')

    runtime!.setClipboard('hi')
    expect(clip).toBe('hi')
  })
})
