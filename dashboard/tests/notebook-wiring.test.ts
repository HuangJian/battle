/**
 * notebook-wiring.test.ts — W1 的「接线不可漂」（plan/notebook-userscript §6.2）。
 *
 * 三个容易静默退化的点：
 *   ① 日志搬运 POST 必须用 `guard({activity:false})`——否则 5s 一条会把「没人看就停刷」永久点亮；
 *   ② 虚拟键必须在 `componentLogPayload(key…)` **之前**分支（key 是 Component 联合类型）；
 *   ③ 导航读的是 `componentName`，不是 label ⇒ `notebook:` 前缀必须映射成可读名。
 */
import { afterAll, beforeEach, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  appendNotebookChunk,
  missingLogMessage,
  notebookLogPayload,
  type NotebookChunk,
} from '../src/server/api/notebook-log'
import { componentName } from '../src/web/view/component-roles'

const SERVER = readFileSync(new URL('../src/server/server.ts', import.meta.url), 'utf8')
const T0 = 1_790_000_000_000

function chunk(o: Partial<NotebookChunk> = {}): NotebookChunk {
  const text = o.text ?? 'hello\n'
  const offsetStart = o.offsetStart ?? 0
  const base = {
    v: 2 as const,
    site: 'kaggle' as const,
    session: 'kaggle-owner-rollout-run1',
    writer: 'w1',
    epoch: 0,
    cell: 'cell-a',
    cellIndex: 1,
    seq: 1,
    mode: 'reset' as const,
    offsetStart,
    offsetEnd: offsetStart + text.length,
    text,
    dropped: 0,
    at: T0,
  }
  return { ...base, ...o, text, offsetStart, offsetEnd: o.offsetEnd ?? offsetStart + text.length }
}

describe('W1 接线：活动闸 / 路由顺序 / 虚拟键', () => {
  it('空 notebook 会话的 404 说人话（不是 unknown component）', () => {
    expect(missingLogMessage('notebook')).toContain('还没有 notebook 会话')
    expect(missingLogMessage('notebook:kaggle-a-b-edit')).toContain('还没有 notebook 会话')
    expect(missingLogMessage('bogus')).toContain('bogus')
  })

  it('POST /api/notebookLog 用 guard({activity:false})；清单 GET 仍算有人看', () => {
    expect(SERVER).toContain("'/api/notebookLogs': { GET: guard(notebookLogsRoute) }")
    expect(SERVER).toContain(
      "'/api/notebookLog': { POST: guard(notebookLogRoute, { activity: false }) }",
    )
    // guard 默认路径不变：只有显式 activity:false 才跳过
    expect(SERVER).toContain('if (opts.activity !== false) markConsoleActivity()')
  })

  it('两条静态路由都在 `/api/log/*` 之前；虚拟键在 componentLogPayload 之前分支', () => {
    const logWildcard = SERVER.indexOf("'/api/log/*'")
    expect(logWildcard).toBeGreaterThan(-1)
    expect(SERVER.indexOf("'/api/notebookLogs'")).toBeLessThan(logWildcard)
    expect(SERVER.indexOf("'/api/notebookLog'")).toBeLessThan(logWildcard)
    expect(SERVER.indexOf('isNotebookLogKey(key)')).toBeLessThan(
      SERVER.indexOf('componentLogPayload(key'),
    )
    // 两处（页面 + API）都要分支，不是只改一处
    expect(SERVER.match(/isNotebookLogKey\(key\)/g)?.length ?? 0).toBeGreaterThanOrEqual(2)
  })

  it('componentName 映射 notebook 前缀（导航读它，不是 label）', () => {
    expect(componentName('notebook:kaggle-owner-rollout-run1')).toBe(
      'Notebook · kaggle-owner-rollout-run1',
    )
    expect(componentName('notebook')).toBe('Notebook')
    expect(componentName('probe')).toBe('Notebook')
    expect(componentName('trainingLoop')).toBe('管事')
    expect(componentName('unknownKey')).toBe('unknownKey') // 未知 key 的兜底不变
  })
})

describe('W1 接线：虚拟键日志载荷', () => {
  const temps: string[] = []
  beforeEach(() => {
    temps.push(mkdtempSync(path.join(tmpdir(), 'bncw-')))
  })
  afterAll(() => {
    for (const d of temps) rmSync(d, { recursive: true, force: true })
  })

  it('notebookLogPayload 与 LogPayload 同形状；`notebook` 取最近会话', () => {
    const dir = temps[temps.length - 1]!
    appendNotebookChunk(dir, chunk({ text: 'line1\nline2\n' }), T0)
    const p = notebookLogPayload(dir, 'notebook', 100)
    expect(p).not.toBeNull()
    expect(p).toMatchObject({
      component: 'notebook:kaggle-owner-rollout-run1',
      label: 'Notebook · kaggle-owner-rollout-run1',
      exists: true,
      truncated: false,
    })
    expect(p?.lines.join('\n')).toContain('line1')
    expect(p?.lines.join('\n')).toContain('line2')
    expect(p?.log).toContain('kaggle-owner-rollout-run1.log')
    expect(p?.fileSize).toBeGreaterThan(0)
  })

  it('未知 session ⇒ null（日志页 404，不显示一个空白组件）', () => {
    const dir = temps[temps.length - 1]!
    expect(notebookLogPayload(dir, 'notebook:missing', 100)).toBeNull()
    expect(notebookLogPayload(dir, 'notebook', 100)).toBeNull() // 空目录 = 没有会话
  })
})
