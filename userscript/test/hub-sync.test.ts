/**
 * hub-sync.test.ts — 页首 `CFG['hub_url']` 同步（plan §3.6 的同一条纪律：只能改该改的，
 * 拿不到就带原因失败）。重点：
 *   ① 只换 `hub_url` 的值——引号/缩进/注释/其它键逐字不动；
 *   ② 值相同 ⇒ 一次写都不发；URL 非法/空/没登记 ⇒ 失败而不是写空；
 *   ③ 找不到 CFG、或有多段像非 CFG 的 hub_url ⇒ 不猜；
 *   ④ Monaco 路径按 model 回读校验；CM6 的 Range 收窄到最小差异区间。
 */
import { describe, expect, it } from 'bun:test'
import { loadDom } from '../happydom'
import {
  describeHubSync,
  fetchHubUrl,
  locateHubUrlCell,
  startHubUrlSync,
  type HubSyncDeps,
} from '../src/notebook-companion/app/hub-sync'
import {
  isHubUrl,
  minimalEdit,
  normalizeHubUrl,
  planHubUrlEdit,
  replaceMonacoSource,
} from '../src/notebook-companion/sites/hub-url'
import { KaggleFrameAdapter } from '../src/notebook-companion/sites/kaggle'
import type { FrameAdapter } from '../src/notebook-companion/sites/types'
import type { Runtime } from '../src/runtime'

const CFG = [
  '# @title Battle GPU Worker',
  'CFG = {',
  '    "mode": "pull",',
  '    "hub_url": "",                # cloudflared 隧道',
  '    "registry_url": "",',
  '    "hub_token": "",',
  '}',
].join('\n')

const HUB = 'https://hub.trycloudflare.com'

function adapterWith(sources: string[]): { adapter: FrameAdapter; writes: string[] } {
  const writes: string[] = []
  let list = [...sources]
  const adapter: FrameAdapter = {
    site: 'colab',
    ready: true,
    cells: () => list.map((_, i) => ({ id: `c${i}`, index: i, kind: 'code' as const })),
    sourceText: (cell) => list[cell.index] ?? '',
    cellExecuting: () => false,
    kernelStatus: () => 'unknown',
    replaceCellSource: async (cell, source) => {
      writes.push(source)
      list[cell.index] = source
      return { ok: true, detail: 'fake' }
    },
    outputText: () => '',
    focusConfig: async () => ({ ok: true }),
    activeCell: () => null,
    ensureRendered: async () => null,
    select: async () => ({ ok: true }),
    runCell: async () => ({ ok: true }),
    clearCellOutput: async () => ({ ok: true }),
    toggleCell: async () => ({ ok: true }),
    setFoldedAll: async () => ({ ok: true }),
    setFolded: async () => ({ ok: true }),
    command: async () => ({ ok: true }),
    scroll: () => ({ ok: true }),
    scrollOuter: () => ({ ok: true }),
    flash: () => {},
    setScheduledMarks: () => ({ ok: true }),
  }
  return { adapter, writes }
}

function runtimeReturning(payload: unknown, status = 200): Pick<Runtime, 'gmRequest'> {
  return {
    gmRequest: async (details) => {
      expect(details.method).toBe('GET')
      expect(details.headers?.['X-Notebook-Companion']).toBe('1')
      return {
        status,
        statusText: '',
        responseText: JSON.stringify(payload),
        responseHeaders: '',
      }
    },
  }
}

describe('hub-url：只改值，不改别的字节', () => {
  it('空值 → 填上；注释/缩进/registry_url 逐字保留', () => {
    const plan = planHubUrlEdit(CFG, HUB)
    expect(plan.ok).toBe(true)
    if (!plan.ok) return
    expect(plan.changed).toBe(true)
    expect(plan.replaced).toBe('')
    expect(plan.text).toContain(`    "hub_url": "${HUB}",                # cloudflared 隧道`)
    expect(plan.text).toContain('    "registry_url": "",')
    expect(plan.text).toContain('    "mode": "pull",')
  })

  it('已有旧值 → 只换括号里那段；单引号保持单引号', () => {
    const old = CFG.replace('"hub_url": ""', '"hub_url": "https://old.trycloudflare.com"')
    const plan = planHubUrlEdit(old, HUB)
    expect(plan.ok && plan.changed).toBe(true)
    if (!plan.ok) return
    expect(plan.replaced).toBe('https://old.trycloudflare.com')
    expect(plan.text).toContain(`"hub_url": "${HUB}"`)
    expect(plan.text).not.toContain('old.trycloudflare.com')

    const single = planHubUrlEdit("CFG = {'hub_url': 'https://old.example'}", HUB)
    expect(single.ok && single.text).toBe(`CFG = {'hub_url': '${HUB}'}`)
  })

  it('值相同 ⇒ changed:false（调用方一次写都不发；尾部斜杠不算变化）', () => {
    const already = CFG.replace('"hub_url": ""', `"hub_url": "${HUB}"`)
    const same = planHubUrlEdit(already, `${HUB}/`)
    expect(same.ok).toBe(true)
    if (same.ok) {
      expect(same.changed).toBe(false)
      expect(same.text).toBe(already)
    }
  })

  it('没有字面量 hub_url / URL 非法 ⇒ 带原因失败（绝不新加键）', () => {
    const none = planHubUrlEdit('CFG = {"hub_ip": ""}', HUB)
    expect(none.ok).toBe(false)
    if (!none.ok) expect(none.reason).toContain('hub_url')
    expect(planHubUrlEdit(CFG, 'http://<本地TS_IP>:8787').ok).toBe(false)
    expect(planHubUrlEdit(CFG, '').ok).toBe(false)
    expect(planHubUrlEdit(CFG, 'javascript:alert(1)').ok).toBe(false)
  })

  it('URL 归一化/可信判据', () => {
    expect(normalizeHubUrl(` ${HUB}// `)).toBe(HUB)
    expect(isHubUrl(HUB)).toBe(true)
    expect(isHubUrl('http://100.64.0.1:8900')).toBe(true)
    expect(isHubUrl('ftp://x')).toBe(false)
    expect(isHubUrl('https://<host>')).toBe(false)
    expect(isHubUrl('')).toBe(false)
  })

  it('minimalEdit 收窄到真正的差异区间（CM6 只选这几个字符）', () => {
    const edit = minimalEdit('abcOLDdef', 'abcNEWdef')
    expect(edit).toEqual({ start: 3, end: 6, text: 'NEW' })
    expect(minimalEdit('same', 'same')).toBeNull()
    expect(minimalEdit('abc', 'abcd')).toEqual({ start: 3, end: 3, text: 'd' })
  })
})

describe('hub-sync：找 cell / 取 URL / 写一次', () => {
  it('优先 CFG 形状那段；多处非 CFG 不猜', () => {
    const { adapter } = adapterWith(['x = 1', CFG, 'CFG2 = {"hub_url": "https://other.example"}'])
    const hit = locateHubUrlCell(adapter, ['CFG = {'])
    expect(hit.ok && hit.cell.index).toBe(1)

    const ambiguous = adapterWith([
      'run({"hub_url": "https://a.example"})',
      'run({"hub_url": "https://b.example"})',
    ])
    const none = locateHubUrlCell(ambiguous.adapter, [])
    expect(none.ok).toBe(false)
    if (!none.ok) expect(none.retry).toBe(false)

    const pending = { ...adapter, ready: false }
    const wait = locateHubUrlCell(pending, [])
    expect(wait.ok).toBe(false)
    if (!wait.ok) expect(wait.retry).toBe(true)
  })

  it('面板文案：已更新 / 已是最新 / 失败带原因', () => {
    expect(describeHubSync({ ok: true, detail: `updated ${HUB}` })).toEqual({
      text: `hub_url 已更新 → ${HUB}`,
      tone: 'ok',
    })
    expect(describeHubSync({ ok: true, detail: `unchanged ${HUB}` })).toEqual({
      text: `hub_url 已是最新（${HUB}）`,
      tone: 'ok',
    })
    expect(describeHubSync({ ok: false, reason: 'dashboard 未起' })).toEqual({
      text: 'hub_url 未同步：dashboard 未起',
      tone: 'error',
    })
  })

  it('fetchHubUrl：GET + 自定义头；尾部斜杠归一；空/非法/HTTP 错带原因', async () => {
    const ok = await fetchHubUrl({
      runtime: runtimeReturning({ ok: true, hubUrl: `${HUB}/` }),
      dashboardBase: 'http://127.0.0.1:8900',
    })
    expect(ok).toEqual({ ok: true, url: HUB })

    const empty = await fetchHubUrl({
      runtime: runtimeReturning({ ok: true, hubUrl: '' }),
      dashboardBase: 'http://127.0.0.1:8900',
    })
    expect(empty.ok).toBe(false)
    if (!empty.ok) expect(empty.retry).toBe(true)

    const bad = await fetchHubUrl({
      runtime: runtimeReturning({ ok: true, hubUrl: 'https://<host>' }),
      dashboardBase: 'http://127.0.0.1:8900',
    })
    expect(bad.ok).toBe(false)
    if (!bad.ok) expect(bad.retry).toBeUndefined()

    const down = await fetchHubUrl({
      runtime: runtimeReturning({ ok: false }, 500),
      dashboardBase: 'http://127.0.0.1:8900',
    })
    expect(down.ok).toBe(false)
  })

  it('加载同步：值不同 ⇒ 写一次；第二次值相同 ⇒ 不再写', async () => {
    const { adapter, writes } = adapterWith(['x = 1', CFG])
    let t = 0
    const deps: HubSyncDeps = {
      runtime: runtimeReturning({ ok: true, hubUrl: HUB }),
      dashboardBase: 'http://127.0.0.1:8900',
      adapter,
      markers: ['CFG = {'],
      now: () => t,
      sleep: async (ms) => {
        t += ms
      },
      waitMs: 1000,
      pollMs: 100,
    }
    const first = await startHubUrlSync(deps).done
    expect(first.ok).toBe(true)
    if (first.ok) expect(first.detail).toBe(`updated ${HUB}`)
    expect(writes).toHaveLength(1)
    expect(writes[0]).toContain(`"hub_url": "${HUB}"`)
    expect(writes[0]).toContain('"registry_url": ""')

    const second = await startHubUrlSync({ ...deps, now: () => t }).done
    expect(second.ok).toBe(true)
    if (second.ok) expect(second.detail).toBe(`unchanged ${HUB}`)
    expect(writes).toHaveLength(1)
  })

  it('等不到 notebook ⇒ 到点带原因失败（不写）', async () => {
    const { adapter, writes } = adapterWith(['x = 1'])
    let t = 0
    const r = await startHubUrlSync({
      runtime: runtimeReturning({ ok: true, hubUrl: HUB }),
      dashboardBase: 'http://127.0.0.1:8900',
      adapter,
      markers: [],
      now: () => t,
      sleep: async (ms) => {
        t += ms
      },
      waitMs: 300,
      pollMs: 100,
    }).done
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('hub_url')
    expect(writes).toHaveLength(0)
  })
})

describe('hub-url：Monaco 写回（Colab / AI Studio）', () => {
  function monacoOf(source: string) {
    let value = source
    const model = {
      getValue: () => value,
      getFullModelRange: () => ({
        startLineNumber: 1,
        startColumn: 1,
        endLineNumber: 4,
        endColumn: 2,
      }),
      pushEditOperations: (_selections: unknown[], edits: Array<{ text: string }>) => {
        value = edits[0]!.text
      },
    }
    return {
      monaco: { editor: { getModels: () => [model] } },
      value: () => value,
    }
  }

  it('按源码认 model，整段写回后回读校验', () => {
    const plan = planHubUrlEdit(CFG, HUB)
    expect(plan.ok).toBe(true)
    if (!plan.ok) return
    const m = monacoOf(CFG)
    const r = replaceMonacoSource(m.monaco, CFG, plan.text)
    expect(r.ok).toBe(true)
    expect(m.value()).toContain(`"hub_url": "${HUB}"`)
    expect(m.value()).toContain('"registry_url": ""')
  })

  it('找不到同一段源码的 model ⇒ 带原因失败（不猜别的 cell）', () => {
    const m = monacoOf(CFG)
    const r = replaceMonacoSource(m.monaco, 'another cell', 'changed')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('Monaco model')
    expect(m.value()).toBe(CFG)
  })
})

describe('Kaggle CM6 写回：只替换最小差异区间', () => {
  const HTML =
    '<html><body><div class="jp-Notebook">' +
    '<div class="jp-Cell jp-CodeCell" data-uuid="u" data-windowed-list-index="0">' +
    '<div class="jp-InputArea"><div class="cm-content">' +
    '<div class="cm-line">CFG = {</div>' +
    '<div class="cm-line">    "hub_url": "",</div>' +
    '<div class="cm-line">}</div>' +
    '</div></div></div></div></body></html>'

  /** 模拟浏览器：把当前 DOM 选区替换成文本（CM6 收到 input 后自己 reconcile）。 */
  function insertViaSelection(doc: Document): (target: HTMLElement, text: string) => boolean {
    return (_target, text) => {
      const sel = doc.getSelection()
      if (!sel || sel.rangeCount === 0) return false
      const range = sel.getRangeAt(0)
      range.deleteContents()
      range.insertNode(doc.createTextNode(text))
      return true
    }
  }

  it('注入 insertText ⇒ 只改 URL 那几个字符，回读校验通过', async () => {
    const w = loadDom(HTML)
    const doc = w.document as unknown as Document
    const adapter = new KaggleFrameAdapter(doc, {
      wait: async () => {},
      nextFrame: async () => {},
      insertText: insertViaSelection(doc),
    })
    const cell = adapter.cells()[0]!
    const before = adapter.sourceText(cell)
    const plan = planHubUrlEdit(before, HUB)
    expect(plan.ok).toBe(true)
    if (!plan.ok) return
    const r = await adapter.replaceCellSource(cell, plan.text)
    expect(r.ok).toBe(true)
    expect(adapter.sourceText(cell)).toBe(plan.text)
    expect(adapter.sourceText(cell)).toContain(`"hub_url": "${HUB}"`)
    w.close()
  })

  it('编辑器不接受写入 ⇒ 可重试失败且内容不变（不假装成功）', async () => {
    const w = loadDom(HTML)
    const doc = w.document as unknown as Document
    const adapter = new KaggleFrameAdapter(doc, {
      wait: async () => {},
      nextFrame: async () => {},
      insertText: () => false,
    })
    const cell = adapter.cells()[0]!
    const before = adapter.sourceText(cell)
    const plan = planHubUrlEdit(before, HUB)
    if (!plan.ok) throw new Error(plan.reason)
    const r = await adapter.replaceCellSource(cell, plan.text)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.retry).toBe(true)
    expect(adapter.sourceText(cell)).toBe(before)
    w.close()
  })
})
