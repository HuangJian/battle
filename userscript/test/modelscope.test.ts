import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { runFrameCommand } from '../src/notebook-companion/app/commands'
import { createShipLoop } from '../src/notebook-companion/app/ship-loop'
import { DEFAULT_CONFIG } from '../src/notebook-companion/config'
import {
  ModelscopeAdapter,
  ModelscopeSessionAdapter,
  MS_OUTPUT_UNAVAILABLE,
  parseModelscopeNotebookKey,
  parseModelscopeSession,
  modelscopeNotebookName,
} from '../src/notebook-companion/sites/modelscope'
import type { Runtime } from '../src/runtime'

/**
 * 夹具来自真实魔搭存档（2026-11-09 `D:\github\modelscope.mhtml`）：notebook-ide 帧里的
 * VS Code 工作台 DOM（class / data-* / aria-label 原样），源码与日志换成合成文本。
 */
const FIXTURE = readFileSync(join(import.meta.dir, 'fixtures/modelscope.html'), 'utf8')
const HREF = 'https://modelscope.cn/notebook/main.html?t=1794233573825&lang=zh-CN'

const opened: DomHandle[] = []
/** happy-dom 的 Document 与 lib.dom 的 Document 结构不兼容（v21 类型）；测试边界一次强转。 */
const docOf = (w: DomHandle): Document => w.document as unknown as Document

function dom(): DomHandle {
  const w = loadDom(FIXTURE, HREF)
  opened.push(w)
  return w
}

afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

const fast = {
  wait: async (): Promise<void> => {},
  nextFrame: async (): Promise<void> => {},
  selectTimeoutMs: 60,
  runStartTimeoutMs: 200,
}

const adapter = (w: DomHandle): ModelscopeAdapter => new ModelscopeAdapter(docOf(w), fast)
const cellRow = (w: DomHandle, index: number): HTMLElement =>
  docOf(w).querySelectorAll<HTMLElement>('.monaco-list-row')[index]!

describe('魔搭：notebook 身份（URL 里没有，只能读工作台标签）', () => {
  it('活动标签的 data-resource-name ⇒ notebook 名 / 任务键 / 日志键', () => {
    const w = dom()
    expect(modelscopeNotebookName(docOf(w))).toBe('rollout.ipynb')
    expect(parseModelscopeNotebookKey(docOf(w))).toBe('modelscope/rollout.ipynb')
    expect(parseModelscopeSession(docOf(w))).toBe('modelscope-rollout.ipynb')
  })

  it('没有标签 ⇒ 退到 aria-label 的路径/标题；都没有 ⇒ unknown；非 Document 不抛', () => {
    const w = dom()
    docOf(w).querySelector('[data-resource-name]')!.removeAttribute('data-resource-name')
    expect(modelscopeNotebookName(docOf(w))).toBe('rollout.ipynb') // aria-label="\mnt\workspace\rollout.ipynb"
    expect(modelscopeNotebookName({} as unknown as Document)).toBe('')
    expect(parseModelscopeSession({} as unknown as Document)).toBe('modelscope-unknown')
    expect(parseModelscopeNotebookKey(null)).toBe('')
  })
})

describe('魔搭适配器：结构来自真实存档', () => {
  it('ready / cells：虚拟化的行按 data-index 编号，code 与 markdown 分得开', () => {
    const a = adapter(dom())
    expect(a.ready).toBe(true)
    expect(a.cells()).toEqual([
      { id: 'cell-0', index: 0, kind: 'markdown' },
      { id: 'cell-1', index: 1, kind: 'code' },
      { id: 'cell-2', index: 2, kind: 'code' },
    ])
  })

  it('sourceText：Monaco 逐行 + NBSP 还原成空格', () => {
    const a = adapter(dom())
    const src = a.sourceText(a.cells()[1]!)
    expect(src.split('\n')).toHaveLength(4)
    expect(src).toContain("'hub_url': ''")
    expect(src).not.toContain('\u00a0')
    expect(src.split('\n')[0]).toBe('CFG = {')
  })

  it('outputText 读不到（输出在跨域 webview）⇒ outputUnavailable 非空', () => {
    const a = adapter(dom())
    expect(a.outputUnavailable).toBe(MS_OUTPUT_UNAVAILABLE)
    for (const cell of a.cells()) expect(a.outputText(cell)).toBe('')

    // 主文档里真有 `.rendered-output` 文本时照读（轻量/未来版本内联时可用）
    const w = dom()
    docOf(w).querySelectorAll('.rendered-output')[0]!.textContent = 'inline\r\nout'
    const inline = adapter(w)
    expect(inline.outputText(inline.cells()[1]!)).toBe('inline\nout')
  })

  it('执行态：aria-label / 执行图标 / 停止按钮任一成立；内核状态据此 busy|idle', () => {
    const w = dom()
    const a = adapter(w)
    const cells = a.cells()
    expect(a.cellExecuting(cells[1]!)).toBe(true)
    expect(a.cellExecuting(cells[2]!)).toBe(false)
    expect(a.kernelStatus()).toBe('busy')

    // 清掉三个证据 ⇒ idle
    const row = cellRow(w, 1)
    row.setAttribute('aria-label', 'code cell')
    row.querySelectorAll('.codicon-notebook-state-executing').forEach((el) => el.remove())
    row.querySelector('.codicon-notebook-stop')!.classList.remove('codicon-notebook-stop')
    expect(a.cellExecuting(cells[1]!)).toBe(false)
    expect(a.kernelStatus()).toBe('idle')

    // notebook 不在 ⇒ unknown
    docOf(w).querySelector('.notebookOverlay')!.remove()
    expect(a.kernelStatus()).toBe('unknown')
    expect(a.ready).toBe(false)
  })

  it('activeCell：行上的 selected / aria-selected', () => {
    const w = dom()
    const a = adapter(w)
    expect(a.activeCell()).toMatchObject({ id: 'cell-1', index: 1 })
    cellRow(w, 1).classList.remove('selected')
    cellRow(w, 1).setAttribute('aria-selected', 'false')
    cellRow(w, 2).setAttribute('aria-selected', 'true')
    expect(a.activeCell()).toMatchObject({ id: 'cell-2', index: 2 })
  })

  it('runCell：点主操作（不是下拉箭头）；执行中用停止按钮不算运行', async () => {
    const w = dom()
    const a = adapter(w)
    const idle = cellRow(w, 2)
    const actions = idle.querySelectorAll<HTMLElement>('.run-button-container a.action-label')
    let primary = 0
    let dropdown = 0
    actions[0]!.addEventListener('click', () => {
      primary += 1
      idle.setAttribute('aria-label', 'code cell, executing')
      idle
        .querySelector('.cell-statusbar-container .cell-contributed-items')
        ?.insertAdjacentHTML(
          'afterbegin',
          '<div class="cell-status-item"><span class="codicon codicon-notebook-state-executing"></span></div>',
        )
    })
    actions[1]!.addEventListener('click', () => {
      dropdown += 1
    })
    expect(await a.runCell(a.cells()[2]!)).toMatchObject({ ok: true, detail: 'running' })
    expect(primary).toBe(1)
    expect(dropdown).toBe(0)

    // 已经在跑：再点就是「停止」⇒ 只认账，不点
    const running = cellRow(w, 1)
    let stopClicks = 0
    running.querySelector('.run-button-container a.action-label')!.addEventListener('click', () => {
      stopClicks += 1
    })
    expect(await a.runCell(a.cells()[1]!)).toMatchObject({ ok: true, detail: 'already-running' })
    expect(stopClicks).toBe(0)
  })

  it('runCell：点了但没进入执行态 ⇒ 带 retry 失败（不假装跑了）', async () => {
    const a = adapter(dom())
    const r = await a.runCell(a.cells()[2]!)
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.retry).toBe(true)
      expect(r.reason).toContain('没进入执行状态')
    }
  })

  it('工具栏命令：中断 / Restart Kernel / 清除所有输出（后者要后置条件）', async () => {
    const w = dom()
    const a = adapter(w)
    const doc = docOf(w)
    const clicked: string[] = []
    for (const label of ['中断', 'Restart Kernel', '清除所有输出']) {
      doc
        .querySelector(`[aria-label="${label}"]`)!
        .addEventListener('click', () => clicked.push(label))
    }
    expect(await a.command('notebook:interrupt-kernel')).toMatchObject({ ok: true })
    expect(await a.command('notebook:restart-kernel')).toMatchObject({ ok: true })
    expect(clicked).toEqual(['中断', 'Restart Kernel'])

    // 没有后置条件（输出容器还在）⇒ 失败
    const miss = await a.command('notebook:clear-all-cell-outputs')
    expect(miss.ok).toBe(false)
    if (!miss.ok) expect(miss.reason).toContain('输出容器仍在')

    // 真的清空了 ⇒ 成功
    doc.querySelector('[aria-label="清除所有输出"]')!.addEventListener('click', () => {
      doc.querySelectorAll('.output-inner-container').forEach((el) => el.remove())
    })
    expect(await a.command('notebook:clear-all-cell-outputs')).toMatchObject({
      ok: true,
      detail: 'cleared:all',
    })
  })

  it('未实测的入口一律带原因失败（运行全部 / 单 cell 清空 / 折叠输入输出 / 未知命令）', async () => {
    const a = adapter(dom())
    const cell = a.cells()[2]!
    for (const r of [
      await a.command('notebook:run-all-cells'),
      await a.clearCellOutput(cell),
      await a.toggleCell(cell, 'input'),
      await a.toggleCell(cell, 'output'),
      await a.command('notebook:shutdown-kernel'),
    ]) {
      expect(r.ok).toBe(false)
      if (!r.ok) expect(r.reason.length).toBeGreaterThan(0)
    }
  })

  it('focusConfig：命中 CFG 行并聚焦；没标记 / 没源码 ⇒ 带原因失败', async () => {
    const w = dom()
    const a = adapter(w)
    const line = docOf(w).querySelectorAll('.cell-editor-container .view-line')[0]!
    const seen: string[] = []
    for (const t of ['mousedown', 'mouseup', 'click']) line.addEventListener(t, () => seen.push(t))
    const hit = await a.focusConfig(['CFG = {'])
    expect(hit).toMatchObject({ ok: true })
    if (hit.ok) {
      expect(hit.detail).toContain('第 1 段第 1 行')
      expect(hit.detail).toContain('CFG 行')
    }
    expect(seen).toEqual([]) // 不派发指针事件（不抢选区）
    expect((await a.focusConfig([])).ok).toBe(false)

    docOf(w)
      .querySelectorAll('.view-line')
      .forEach((el) => el.remove())
    const miss = await a.focusConfig(['CFG = {'])
    expect(miss.ok).toBe(false)
    if (!miss.ok) expect(miss.reason).toContain('找不到 CFG 行')
  })

  it('折叠：脚本自己的层（行属性 + 注入样式 + 常驻按键），状态住适配器', async () => {
    const w = dom()
    const a = adapter(w)
    a.cells()
    expect(docOf(w).getElementById('bnc-ms-style')?.textContent).toContain('max-height')
    expect(docOf(w).querySelectorAll('.bnc-ms-fold').length).toBe(3)

    expect(await a.setFoldedAll(true)).toMatchObject({ ok: true, detail: 'folded:all' })
    expect(cellRow(w, 1).getAttribute('data-bnc-fold')).toBe('on')
    const btn = cellRow(w, 1).querySelector('.bnc-ms-fold') as HTMLButtonElement
    expect(btn.textContent).toBe('展开')
    btn.click()
    expect(cellRow(w, 1).getAttribute('data-bnc-fold')).toBeNull()
    expect(cellRow(w, 2).getAttribute('data-bnc-fold')).toBe('on')

    expect(await a.setFolded(a.cells()[2]!, false)).toMatchObject({ ok: true, detail: 'unfolded' })
    expect(cellRow(w, 2).getAttribute('data-bnc-fold')).toBeNull()

    expect(await a.setFoldedAll(false)).toMatchObject({ ok: true })
    expect(docOf(w).querySelector('[data-bnc-fold]')).toBeNull()
  })

  it('定时徽标：run-cell 目标挂行，其它动作挂顶部悬浮条；清空即移除', () => {
    const w = dom()
    const a = adapter(w)
    expect(
      a.setScheduledMarks([
        { id: 't1', label: '还剩 10s', uuid: 'cell-1', index: 1 },
        { id: 't2', label: '运行全部 · 还剩 5s', action: 'run-all' },
      ]),
    ).toMatchObject({ ok: true, detail: 'marks:1+1' })
    expect(cellRow(w, 1).getAttribute('data-bnc-sched')).toBe('1')
    expect(cellRow(w, 1).querySelector('.bnc-sched')?.textContent).toContain('还剩 10s')
    expect(docOf(w).getElementById('bnc-sched-banner')?.textContent).toContain('运行全部')

    expect(a.setScheduledMarks([])).toMatchObject({ ok: true, detail: 'marks:0+0' })
    expect(cellRow(w, 1).hasAttribute('data-bnc-sched')).toBe(false)
    expect(docOf(w).getElementById('bnc-sched-banner')).toBeNull()
  })

  it('顶/底：读到可滚容器就设 scrollTop；没有可滚容器 ⇒ 带原因失败', () => {
    const w = dom()
    const a = adapter(w)
    const scroller = docOf(w).querySelector('.cell-list-container .monaco-scrollable-element')!
    let top = 0
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 3000 })
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 600 })
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (v: number) => {
        top = v
      },
    })
    expect(a.scroll('bottom')).toMatchObject({ ok: true })
    expect(top).toBe(2400)
    expect(a.scroll('top')).toMatchObject({ ok: true })
    expect(top).toBe(0)

    scroller.remove()
    const miss = a.scroll('bottom')
    expect(miss.ok).toBe(false)
    if (!miss.ok) expect(miss.reason).toContain('没有可滚动容器')
  })
})

describe('魔搭会话', () => {
  it('执行中 = running；无执行 = idle；没有 notebook = unknown', () => {
    const w = dom()
    const s = new ModelscopeSessionAdapter(docOf(w))
    expect(s.sessionId()).toBe('modelscope-rollout.ipynb')
    expect(s.sessionState()).toBe('running')

    const row = docOf(w).querySelectorAll<HTMLElement>('.code-cell-row')[0]!
    row.setAttribute('aria-label', 'code cell')
    row.querySelectorAll('.codicon-notebook-state-executing').forEach((el) => el.remove())
    row.querySelector('.codicon-notebook-stop')!.classList.remove('codicon-notebook-stop')
    expect(s.sessionState()).toBe('idle')

    docOf(w).querySelector('.notebookOverlay')!.remove()
    expect(s.sessionState()).toBe('unknown')
  })

  it('停实例不在 notebook 帧里 ⇒ 带原因失败（不假装停机）', async () => {
    const r = await new ModelscopeSessionAdapter(docOf(dom())).stopSession()
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('实例')
  })
})

describe('ship-loop：输出读不到时 fail closed（绝不清空）', () => {
  it('保存/排空都带原因失败，且一个请求都不发、不清空输出', async () => {
    const w = dom()
    const doc = docOf(w)
    let requests = 0
    let clears = 0
    doc.querySelector('[aria-label="清除所有输出"]')!.addEventListener('click', () => {
      clears += 1
    })
    const runtime = {
      gmRequest: async () => {
        requests += 1
        return { status: 200, statusText: 'OK', responseText: '{"ok":true}', responseHeaders: '' }
      },
    } as unknown as Runtime
    const loop = createShipLoop({
      doc,
      href: HREF,
      config: DEFAULT_CONFIG,
      runtime,
      adapter: adapter(w),
      site: 'modelscope',
      pageTag: 'ab12cd',
      writer: 'w1',
      now: () => 1_000_000,
    })
    expect(loop.session).toBe('modelscope-rollout.ipynb-ab12cd')

    const flush = await loop.flushAll()
    expect(flush.ok).toBe(false)
    expect(flush.reason).toBe(MS_OUTPUT_UNAVAILABLE)

    const saved = await loop.saveAndClear()
    expect(saved.ok).toBe(false)
    expect(saved.reason).toBe(MS_OUTPUT_UNAVAILABLE)
    expect(clears).toBe(0)
    expect(requests).toBe(0)
    expect(doc.querySelectorAll('.output-inner-container').length).toBe(2)
    loop.stop()
  })

  it('start() 不挂观察者/定时器，只把原因报一次', () => {
    const w = dom()
    const errors: string[] = []
    const loop = createShipLoop({
      doc: docOf(w),
      href: HREF,
      config: DEFAULT_CONFIG,
      runtime: {} as unknown as Runtime,
      adapter: adapter(w),
      site: 'modelscope',
      writer: 'w1',
      onError: (m) => errors.push(m),
    })
    loop.start()
    expect(errors).toEqual([MS_OUTPUT_UNAVAILABLE])
    expect(loop.dirty.size).toBe(0)
    loop.stop()
  })

  it('setAutoSave：配置照写，但面板必须看到失败（自动保存并清空在此页不可能）', async () => {
    const w = dom()
    const doc = docOf(w)
    const loop = createShipLoop({
      doc,
      href: HREF,
      config: DEFAULT_CONFIG,
      runtime: {} as unknown as Runtime,
      adapter: adapter(w),
      site: 'modelscope',
      writer: 'w1',
    })
    const reply = await runFrameCommand(
      {
        loop,
        adapter: adapter(w),
        session: new ModelscopeSessionAdapter(doc),
        openLog: () => {},
        openConfig: () => {},
        configMarkers: [],
      },
      'setAutoSave',
      { enabled: true, intervalSec: 300 },
    )
    expect(reply.ok).toBe(false)
    expect(reply.reason).toBe(MS_OUTPUT_UNAVAILABLE)
    loop.stop()
  })
})
